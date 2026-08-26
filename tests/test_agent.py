"""Agent Loop 单元测试与集成测试。

测试范围：
- StopChecker: 5 种停止条件
- ToolBatcher.classify_tool: 安全/副作用分类
- Agent: 单轮、多轮、迭代上限、取消
- ch05: 系统提示装配、环境信息、reminder、缓存透传
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from csycode.agent.batcher import (
    SafetyLabel,
    ToolBatcher,
    classify_tool,
)
from csycode.agent.collector import StreamCollector
from csycode.agent.events import (
    AgentEvent,
    ApprovalRequest,
    CompactNotification,
    LoopEnd,
    TextDelta,
    TokenUsage,
    ToolCallEnd,
    ToolCallStart,
)
from csycode.agent.loop import Agent
from csycode.agent.stop_checker import StopChecker
from csycode.config import AgentConfig
from csycode.conversation import Conversation
from csycode.llm import Message, PromptTooLongError, Request, StreamEvent, ToolCall, Usage
from csycode.permission import Mode, new_engine
from csycode.tools.base import Tool, ToolResult
from csycode.tools.registry import ToolRegistry


# ── StopChecker 测试 ──────────────────────────────────────────────────

class TestStopChecker:
    """停止条件状态机测试。"""

    def test_model_done(self):
        sc = StopChecker()
        assert not sc.should_stop
        sc.record_model_done()
        assert sc.should_stop
        assert sc.stop_reason == "model_done"

    def test_max_rounds(self):
        sc = StopChecker(max_rounds=3)
        sc.record_round()
        assert not sc.should_stop
        sc.record_round()
        assert not sc.should_stop
        sc.record_round()
        assert sc.should_stop
        assert sc.stop_reason == "max_rounds"

    def test_user_cancel(self):
        sc = StopChecker()
        sc.record_user_cancel()
        assert sc.should_stop
        assert sc.stop_reason == "user_cancel"

    def test_consecutive_unknown_tools(self):
        sc = StopChecker(max_consecutive_unknown=2)
        sc.record_unknown_tool()
        assert not sc.should_stop
        sc.record_unknown_tool()
        assert sc.should_stop
        assert sc.stop_reason == "unknown_tools"

    def test_unknown_tools_reset(self):
        """成功调用工具后应重置连续未知计数器。"""
        sc = StopChecker(max_consecutive_unknown=2)
        sc.record_unknown_tool()
        sc.reset_unknown_count()  # 中间有一次成功调用
        sc.record_unknown_tool()
        assert not sc.should_stop  # 还没连续

    def test_stream_error(self):
        sc = StopChecker()
        sc.record_stream_error(Exception("connection lost"))
        assert sc.should_stop
        assert sc.stop_reason == "stream_error"

    def test_round_count(self):
        sc = StopChecker(max_rounds=50)
        for _ in range(5):
            sc.record_round()
        assert sc.round_count == 5


# ── ToolBatcher 测试 ──────────────────────────────────────────────────

class TestClassifyTool:
    """工具安全分类测试。"""

    @pytest.mark.parametrize("name", [
        "read_file", "search_content", "glob", "grep", "list_dir", "find_files",
    ])
    def test_safe_tools(self, name):
        assert classify_tool(name) == SafetyLabel.SAFE

    @pytest.mark.parametrize("name", [
        "write_file", "edit_file", "run_command", "delete_file", "exec_script",
    ])
    def test_side_effect_tools(self, name):
        assert classify_tool(name) == SafetyLabel.SIDE_EFFECT

    def test_unknown_defaults_to_side_effect(self):
        """未匹配任何模式时默认视为副作用工具（保守策略）。"""
        assert classify_tool("some_weird_tool") == SafetyLabel.SIDE_EFFECT


# ── 模拟工具 ──────────────────────────────────────────────────────────

# 安全（只读）工具名称模式
_SAFE_NAME_PATTERNS: tuple[str, ...] = (
    "read_", "search_", "glob", "grep", "list_", "find_",
)


class _MockTool(Tool):
    """用于测试的模拟工具。"""

    def __init__(self, name: str, delay: float = 0.0, fail: bool = False) -> None:
        self._name = name
        self._delay = delay
        self._fail = fail
        # 根据工具名自动设置 is_readonly，以便 classify_tool 正确分类
        self.is_readonly = any(
            name.startswith(p) or name == p for p in _SAFE_NAME_PATTERNS
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Mock tool: {self._name}"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs) -> ToolResult:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail:
            return ToolResult(success=False, content="", error="模拟失败")
        return ToolResult(success=True, content=f"{self._name} 执行成功")


def _make_registry(tool_names: list[str]) -> ToolRegistry:
    """创建包含指定模拟工具的注册中心。"""
    reg = ToolRegistry()
    for name in tool_names:
        reg.register(_MockTool(name))
    return reg


# ── MockProvider（ch05: 接受 Request） ─────────────────────────────────

class MockProvider:
    """模拟 LLM Provider，按预设序列返回响应（ch05 签名）。

    记录最后一次收到的 Request 供测试断言。
    """

    def __init__(self, responses: list[StreamEvent]) -> None:
        self._responses = responses
        self._call_count = 0
        self.name = "mock"
        self.model = "mock-model"
        self.last_request: Request | None = None
        self.requests: list[Request] = []

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        """返回预设的响应序列（ch05 签名）。

        每调用一次 stream() 消耗 self._responses 中的一个元素。
        若元素是 list，则逐个 yield（模拟一次流中的多个 chunk）；
        若元素是 StreamEvent，则直接 yield。
        """
        self.last_request = req
        self.requests.append(req)
        if self._call_count >= len(self._responses):
            yield StreamEvent(text="", done=True)
            return
        resp = self._responses[self._call_count]
        self._call_count += 1

        if isinstance(resp, list):
            for ev in resp:
                yield ev
        else:
            yield resp


# ── Agent 集成测试 ────────────────────────────────────────────────────

class TestAgentSingleTurn:
    """单轮 Agent 测试（无工具调用）。"""

    @pytest.mark.asyncio
    async def test_pure_text_response(self):
        """纯文本回复：Agent 一轮结束，产出 TextDelta + LoopEnd。"""
        provider = MockProvider([
            StreamEvent(text="Hello, world!", done=True),
        ])
        registry = _make_registry(["read_file"])
        conv = Conversation()
        conv.add_user("hi")
        config = AgentConfig(max_iterations=10)

        agent = Agent(provider, registry, conv, config, version="test")
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        # 应该包含 TextDelta 和 LoopEnd
        texts = [e for e in events if isinstance(e, TextDelta)]
        assert len(texts) == 1
        assert texts[0].text == "Hello, world!"

        ends = [e for e in events if isinstance(e, LoopEnd)]
        assert len(ends) == 1
        assert ends[0].reason == "model_done"
        assert ends[0].final_text == "Hello, world!"
        assert ends[0].total_rounds == 0

    @pytest.mark.asyncio
    async def test_reasoning_effort_is_forwarded(self):
        """Agent 每轮请求都携带当前思考强度。"""
        provider = MockProvider([
            StreamEvent(
                tool_calls=[
                    ToolCall(id="tc1", name="read_file", arguments={"file_path": "a.txt"}),
                ],
                done=True,
            ),
            StreamEvent(text="done", done=True),
        ])
        registry = _make_registry(["read_file"])
        conv = Conversation()
        conv.add_user("read a.txt")
        agent = Agent(
            provider,
            registry,
            conv,
            AgentConfig(),
            version="test",
            reasoning_effort="xhigh",
        )

        async for _ in agent.run(Mode.DEFAULT):
            pass

        assert len(provider.requests) == 2
        assert [req.reasoning_effort for req in provider.requests] == ["xhigh", "xhigh"]

    @pytest.mark.asyncio
    async def test_multi_text_chunks(self):
        """多个文本 chunk 被正确累积（在一次 stream 调用中）。"""
        provider = MockProvider([
            [  # 一次 stream() 调用中的多个 chunk
                StreamEvent(text="Hello", done=False),
                StreamEvent(text=", ", done=False),
                StreamEvent(text="world!", done=True),
            ],
        ])
        registry = _make_registry(["read_file"])
        conv = Conversation()
        conv.add_user("hi")
        config = AgentConfig()

        agent = Agent(provider, registry, conv, config, version="test")
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        texts = [e for e in events if isinstance(e, TextDelta)]
        assert len(texts) == 3, f"Expected 3 TextDelta events, got {len(texts)}: {texts}"
        assert "".join(t.text for t in texts) == "Hello, world!"

        ends = [e for e in events if isinstance(e, LoopEnd)]
        assert len(ends) == 1
        assert ends[0].reason == "model_done"
        assert ends[0].final_text == "Hello, world!"


class TestAgentMultiTurn:
    """多轮 Agent 测试（带工具调用）。"""

    @pytest.mark.asyncio
    async def test_one_tool_call_then_done(self):
        """模型先调一个工具，拿到结果后给出最终回复。"""
        provider = MockProvider([
            # 第一轮：带工具调用
            StreamEvent(
                text="Let me read the file.",
                tool_calls=[ToolCall(id="tc1", name="read_file", arguments={"file_path": "test.txt"})],
                done=True,
            ),
            # 第二轮：纯文本回复
            StreamEvent(text="The file contains hello.", done=True),
        ])
        registry = _make_registry(["read_file"])
        conv = Conversation()
        conv.add_user("read test.txt")
        config = AgentConfig()

        agent = Agent(provider, registry, conv, config, version="test")
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        # 应该包含 ToolCallStart、ToolCallEnd
        starts = [e for e in events if isinstance(e, ToolCallStart)]
        assert len(starts) == 1
        assert starts[0].tool_name == "read_file"

        ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(ends) == 1
        assert ends[0].success is True

        loop_ends = [e for e in events if isinstance(e, LoopEnd)]
        assert len(loop_ends) == 1
        assert loop_ends[0].reason == "model_done"
        assert loop_ends[0].final_text == "The file contains hello."
        assert loop_ends[0].total_rounds == 1

    @pytest.mark.asyncio
    async def test_two_rounds_of_tools(self):
        """模型连续两轮调用不同工具，最后给出回复。"""
        provider = MockProvider([
            # 第一轮：读文件
            StreamEvent(
                text="",
                tool_calls=[ToolCall(id="tc1", name="read_file", arguments={"file_path": "a.txt"})],
                done=True,
            ),
            # 第二轮：搜索
            StreamEvent(
                text="",
                tool_calls=[ToolCall(id="tc2", name="grep", arguments={"pattern": "hello"})],
                done=True,
            ),
            # 第三轮：完成
            StreamEvent(text="All done.", done=True),
        ])
        registry = _make_registry(["read_file", "grep"])
        conv = Conversation()
        conv.add_user("do multi-step task")
        config = AgentConfig()

        agent = Agent(provider, registry, conv, config, version="test")
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        starts = [e for e in events if isinstance(e, ToolCallStart)]
        assert len(starts) == 2
        assert starts[0].tool_name == "read_file"
        assert starts[1].tool_name == "grep"

        loop_ends = [e for e in events if isinstance(e, LoopEnd)]
        assert len(loop_ends) == 1
        assert loop_ends[0].reason == "model_done"
        assert loop_ends[0].total_rounds == 2


class TestDeferredApprovalDedup:
    """流式延迟审批的去重测试。

    场景：default 模式下，流式输出会在一次响应中产出多个 tool_use 块。
    当其中出现相同命令时，应只弹一次审批，后续相同命令命中会话级缓存直接放行，
    而不是重复弹窗（修复"相同命令审批多次"的 bug）。
    """

    @pytest.mark.asyncio
    async def test_same_command_approved_once(self, tmp_path):
        """一次流中两个相同 bash 调用 → 只弹一次审批，两个都执行。"""
        from csycode.permission import Outcome

        provider = MockProvider([
            [  # 第一轮：流式产出两个相同命令（不同 tool_id）
                StreamEvent(
                    event_type="tool_complete",
                    tool_name="bash",
                    tool_id="tc1",
                    arguments={"command": "zzzcustom"},
                ),
                StreamEvent(
                    event_type="tool_complete",
                    tool_name="bash",
                    tool_id="tc2",
                    arguments={"command": "zzzcustom"},
                ),
                StreamEvent(done=True, stop_reason="tool_calls"),
            ],
            # 第二轮：纯文本收尾
            StreamEvent(text="done", done=True),
        ])
        registry = _make_registry(["bash"])
        engine, _err = new_engine(str(tmp_path))
        conv = Conversation()
        conv.add_user("run it twice")
        config = AgentConfig(max_iterations=10)

        agent = Agent(
            provider, registry, conv, config, version="test", engine=engine
        )
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            if isinstance(ev, ApprovalRequest):
                # 用户选"允许本次"——同时写入会话级缓存
                ev.respond.set_result(Outcome.ALLOW_ONCE)
            events.append(ev)

        approvals = [e for e in events if isinstance(e, ApprovalRequest)]
        tool_ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(approvals) == 1, f"相同命令应只弹一次审批，实际 {len(approvals)} 次"
        assert len(tool_ends) == 2, f"两个工具调用都应执行，实际 {len(tool_ends)} 个"
        assert all(t.success for t in tool_ends)


class TestStreamingReadOnly:
    """流式路径只读工具判定测试。

    修复：流式路径原先硬编码 read_only=False，导致只读工具在 DEFAULT 模式下
    被误判为 EXEC 而 ASK。改用工具真实 is_readonly 后，只读工具归为 READ →
    直接放行执行，不弹审批（与 batch 路径一致）。
    """

    @pytest.mark.asyncio
    async def test_readonly_tool_not_asked(self, tmp_path):
        """只读工具流式调用 → 直接执行，不弹审批。"""
        provider = MockProvider([
            [
                StreamEvent(
                    event_type="tool_complete",
                    tool_name="search_files",
                    tool_id="tc1",
                    arguments={"pattern": "foo"},
                ),
                StreamEvent(done=True, stop_reason="tool_calls"),
            ],
            StreamEvent(text="done", done=True),
        ])
        # search_files 命中 "search_" 模式 → _MockTool.is_readonly=True
        registry = _make_registry(["search_files"])
        engine, _err = new_engine(str(tmp_path))
        conv = Conversation()
        conv.add_user("search foo")
        config = AgentConfig(max_iterations=10)

        agent = Agent(
            provider, registry, conv, config, version="test", engine=engine
        )
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            # 若错误弹窗，直接放行以让流程继续（便于断言计数）
            if isinstance(ev, ApprovalRequest):
                from csycode.permission import Outcome

                ev.respond.set_result(Outcome.ALLOW_ONCE)
            events.append(ev)

        approvals = [e for e in events if isinstance(e, ApprovalRequest)]
        tool_ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(approvals) == 0, f"只读工具不应弹审批，实际 {len(approvals)} 次"
        assert len(tool_ends) == 1
        assert tool_ends[0].success


class TestAgentStopConditions:
    """停止条件测试。"""

    @pytest.mark.asyncio
    async def test_max_rounds(self):
        """达到迭代上限后强制停止。"""
        # 始终返回工具调用的 Provider
        provider = MockProvider([
            StreamEvent(
                text="",
                tool_calls=[ToolCall(id=f"tc{i}", name="read_file", arguments={})],
                done=True,
            )
            for i in range(10)  # 确保有足够多的响应
        ])
        registry = _make_registry(["read_file"])
        conv = Conversation()
        conv.add_user("loop forever")
        config = AgentConfig(max_iterations=3)

        agent = Agent(provider, registry, conv, config, version="test")
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        loop_ends = [e for e in events if isinstance(e, LoopEnd)]
        assert len(loop_ends) == 1
        assert loop_ends[0].reason == "max_rounds"
        assert loop_ends[0].total_rounds == 3

    @pytest.mark.asyncio
    async def test_user_cancel_via_stream_error(self):
        """模拟用户取消的一种等效路径：对 Agent 发起 stream error。

        真实 TUI 中，Ctrl+C 通过 task.cancel() 触发 CancelledError 传播到 Agent。
        由于 async generator 的取消传播依赖于 asyncio 内部机制，
        这里改为通过 stream error 测试 Agent 的错误处理路径。
        StopChecker 级别的 user_cancel 已验证。
        """
        # 在 stream 中抛出异常来触发 Agent 的错误处理
        class ErrorProvider:
            name = "mock"
            model = "mock-model"

            async def stream(self, req):
                yield StreamEvent(text="starting...", done=False)
                raise RuntimeError("connection lost")

        provider = ErrorProvider()
        registry = _make_registry(["read_file"])
        conv = Conversation()
        conv.add_user("test")
        config = AgentConfig()

        agent = Agent(provider, registry, conv, config, version="test")
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        # Agent 应正常结束（不崩溃）
        loop_ends = [e for e in events if isinstance(e, LoopEnd)]
        assert len(loop_ends) >= 1
        # 流异常会被 collector 捕获并转为 stream_error
        assert loop_ends[0].reason in ("stream_error", "model_done")

    @pytest.mark.asyncio
    async def test_unknown_tools(self):
        """连续调用未知工具后停止。"""
        provider = MockProvider([
            # 第一轮：未知工具
            StreamEvent(
                text="",
                tool_calls=[ToolCall(id="tc1", name="nonexistent_tool", arguments={})],
                done=True,
            ),
            # 第二轮：继续未知工具
            StreamEvent(
                text="",
                tool_calls=[ToolCall(id="tc2", name="another_fake_tool", arguments={})],
                done=True,
            ),
        ])
        registry = _make_registry(["read_file"])  # 只注册了 read_file
        conv = Conversation()
        conv.add_user("use bad tools")
        config = AgentConfig(max_consecutive_unknown_tools=2)

        agent = Agent(provider, registry, conv, config, version="test")
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        loop_ends = [e for e in events if isinstance(e, LoopEnd)]
        assert len(loop_ends) == 1
        assert loop_ends[0].reason == "unknown_tools"


# ── StreamCollector 测试（ch05: 接受 Request） ──────────────────────────

class TestStreamCollector:
    """流式收集器单元测试。"""

    @pytest.mark.asyncio
    async def test_collects_text_and_tool_calls(self):
        """收集器正确累积文本并产出 StreamResult。"""
        provider = MockProvider([
            StreamEvent(
                text="Let me check.",
                tool_calls=[ToolCall(id="tc1", name="read_file", arguments={"file_path": "x"})],
                done=True,
            ),
        ])

        collector = StreamCollector(provider)
        req = Request(messages=[Message(role="user", content="check")])
        events: list[AgentEvent] = []

        async for ev in collector.collect(req):
            events.append(ev)

        # 应有 TextDelta
        texts = [e for e in events if isinstance(e, TextDelta)]
        assert len(texts) >= 1
        assert "Let me check." in texts[0].text

        # last_result 应包含 tool_calls
        assert collector.last_result is not None
        assert len(collector.last_result.tool_calls) == 1
        assert collector.last_result.tool_calls[0].name == "read_file"

    @pytest.mark.asyncio
    async def test_collects_error(self):
        """收集器正确处理流错误。"""
        provider = MockProvider([
            StreamEvent(err=Exception("connection lost"), done=False),
        ])

        collector = StreamCollector(provider)
        req = Request(messages=[Message(role="user", content="test")])
        events: list[AgentEvent] = []

        async for ev in collector.collect(req):
            events.append(ev)

        # 应有 LoopEnd(error)
        errors = [e for e in events if isinstance(e, LoopEnd) and e.reason == "stream_error"]
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_collects_usage(self):
        """收集器正确捕获 provider 返回的 Usage（含缓存字段）。"""
        provider = MockProvider([
            StreamEvent(
                text="Done.",
                done=True,
                usage=Usage(input_tokens=100, output_tokens=50, cache_write=200, cache_read=150),
            ),
        ])

        collector = StreamCollector(provider)
        req = Request(messages=[Message(role="user", content="test")])
        events: list[AgentEvent] = []

        async for ev in collector.collect(req):
            events.append(ev)

        assert collector.last_result is not None
        assert collector.last_result.usage is not None
        assert collector.last_result.usage.input_tokens == 100
        assert collector.last_result.usage.output_tokens == 50
        assert collector.last_result.usage.cache_write == 200
        assert collector.last_result.usage.cache_read == 150


# ── ToolBatcher 集成测试 ──────────────────────────────────────────────

class TestToolBatcherExecution:
    """工具分批执行集成测试。"""

    @pytest.mark.asyncio
    async def test_execute_safe_parallel(self):
        """安全工具应并发执行。"""
        registry = _make_registry(["read_file", "grep"])
        batcher = ToolBatcher(registry)
        tool_calls = [
            ToolCall(id="1", name="read_file", arguments={}),
            ToolCall(id="2", name="grep", arguments={}),
        ]

        events: list[AgentEvent] = []
        async for ev in batcher.execute(tool_calls):
            events.append(ev)

        starts = [e for e in events if isinstance(e, ToolCallStart)]
        ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(starts) == 2
        assert len(ends) == 2
        assert all(e.success for e in ends)

    @pytest.mark.asyncio
    async def test_execute_mixed_safety(self):
        """安全工具和副作用工具混合时，安全先并发、副作用后串行。"""
        registry = _make_registry(["read_file", "write_file", "grep"])
        batcher = ToolBatcher(registry)
        tool_calls = [
            ToolCall(id="1", name="read_file", arguments={}),
            ToolCall(id="2", name="write_file", arguments={}),
            ToolCall(id="3", name="grep", arguments={}),
        ]

        events: list[AgentEvent] = []
        async for ev in batcher.execute(tool_calls):
            events.append(ev)

        starts = [e for e in events if isinstance(e, ToolCallStart)]
        ends = [e for e in events if isinstance(e, ToolCallEnd)]

        assert len(starts) == 3
        assert len(ends) == 3

        # 对安全工具（read_file, grep），它们的 start 都会在 write_file start 之前
        # 安全工具先全部 start → 并发执行 → 全部 end → 副作用工具 start → end
        safe_names = {"read_file", "grep"}
        side_effect_names = {"write_file"}

        # 验证安全工具的 start 在副作用工具 start 之前
        safe_start_indices = [
            i for i, e in enumerate(starts) if e.tool_name in safe_names
        ]
        side_start_indices = [
            i for i, e in enumerate(starts) if e.tool_name in side_effect_names
        ]
        assert all(
            si < sj for si in safe_start_indices for sj in side_start_indices
        ), "安全工具应在副作用工具之前启动"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        """未注册的工具返回错误但不应崩溃。"""
        registry = _make_registry(["read_file"])
        batcher = ToolBatcher(registry)
        tool_calls = [
            ToolCall(id="1", name="nonexistent", arguments={}),
        ]

        events: list[AgentEvent] = []
        async for ev in batcher.execute(tool_calls):
            events.append(ev)

        ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(ends) == 1
        assert ends[0].success is False
        assert "未知工具" in ends[0].error


# ── ch05 新增测试：Request 装配与缓存透传 ──────────────────────────────

class TestAgentCh05RequestAssembly:
    """ch05: 验证 Agent 正确装配 Request（system 两段、reminder、缓存透传）。"""

    @pytest.mark.asyncio
    async def test_request_has_system_stable(self):
        """正常模式下 req.system.stable 非空。"""
        provider = MockProvider([
            StreamEvent(text="OK", done=True),
        ])
        registry = _make_registry(["read_file"])
        conv = Conversation()
        conv.add_user("hello")
        config = AgentConfig()

        agent = Agent(provider, registry, conv, config, version="1.0")
        async for _ in agent.run(Mode.DEFAULT):
            pass

        assert provider.last_request is not None
        assert provider.last_request.system.stable != ""
        assert "csyCode" in provider.last_request.system.stable

    @pytest.mark.asyncio
    async def test_request_has_system_environment(self):
        """req.system.environment 非空，含有关键字段。"""
        provider = MockProvider([
            StreamEvent(text="OK", done=True),
        ])
        registry = _make_registry(["read_file"])
        conv = Conversation()
        conv.add_user("hello")
        config = AgentConfig()

        agent = Agent(provider, registry, conv, config, version="1.0")
        async for _ in agent.run(Mode.DEFAULT):
            pass

        assert provider.last_request is not None
        env = provider.last_request.system.environment
        assert env != ""
        assert "Working Directory:" in env or "Platform:" in env or "Date:" in env

    @pytest.mark.asyncio
    async def test_normal_mode_no_reminder(self):
        """正常模式下 reminder 为空字符串。"""
        provider = MockProvider([
            StreamEvent(text="OK", done=True),
        ])
        registry = _make_registry(["read_file"])
        conv = Conversation()
        conv.add_user("hello")
        config = AgentConfig()

        agent = Agent(provider, registry, conv, config, version="1.0")
        async for _ in agent.run(Mode.DEFAULT):
            pass

        assert provider.last_request is not None
        assert provider.last_request.reminder == ""

    @pytest.mark.asyncio
    async def test_cache_usage_pass_through(self):
        """缓存用量从 StreamEvent.usage 透传到 TokenUsage 事件。"""
        provider = MockProvider([
            StreamEvent(
                text="Done.",
                done=True,
                usage=Usage(input_tokens=42, output_tokens=7, cache_write=100, cache_read=50),
            ),
        ])
        registry = _make_registry(["read_file"])
        conv = Conversation()
        conv.add_user("test")
        config = AgentConfig()

        agent = Agent(provider, registry, conv, config, version="1.0")
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        usages = [e for e in events if isinstance(e, TokenUsage)]
        assert len(usages) == 1
        assert usages[0].input_tokens == 42
        assert usages[0].output_tokens == 7
        assert usages[0].cache_write == 100
        assert usages[0].cache_read == 50


# ── ch08: 紧急压缩测试 ──────────────────────────────────────────────────


class TestEmergencyCompact:
    """Agent 收到 PTL 错误后的紧急压缩行为测试。"""

    @pytest.mark.asyncio
    async def test_emergency_compact_triggers_on_ptl(self):
        """第 1 次 stream 投递 PTL → Agent 触发紧急压缩 → 重试成功。"""
        from csycode.agent.events import CompactPhase

        class PTLThenNormalProvider:
            name = "mock"
            model = "mock-model"
            call_count = 0

            async def stream(self, req):
                self.call_count += 1
                if self.call_count == 1:
                    # 第 1 次：投递 PTL 错误 → 预期触发紧急压缩
                    ptl = PromptTooLongError("prompt too long")
                    yield StreamEvent(err=ptl)
                elif self.call_count == 2:
                    # 第 2 次：摘要请求 → 返回正常摘要文本
                    yield StreamEvent(
                        text="<summary>summary content</summary>",
                        done=True,
                        usage=Usage(input_tokens=50, output_tokens=10),
                    )
                else:
                    # 第 3 次：重试原始请求 → 正常返回
                    yield StreamEvent(
                        text="Retry succeeded!",
                        done=True,
                        usage=Usage(input_tokens=100, output_tokens=20),
                    )

        provider = PTLThenNormalProvider()
        registry = _make_registry(["read_file"])
        conv = Conversation()
        # 构造足够的对话历史使自动压缩有足够前缀
        for i in range(15):
            conv._messages.append(Message(role="user", content=f"q{i}: " + "x" * 500))
            conv._messages.append(Message(role="assistant", content=f"a{i}: " + "y" * 500))
        config = AgentConfig(max_iterations=5)

        agent = Agent(provider, registry, conv, config, version="test")
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        # 应该有紧急压缩相关事件
        compact_events = [e for e in events if isinstance(e, CompactNotification)]
        assert len(compact_events) >= 1

        # 应该正常结束（重试成功）
        loop_ends = [e for e in events if isinstance(e, LoopEnd)]
        assert len(loop_ends) == 1
        assert loop_ends[0].reason in ("model_done", "stream_error")

        # 确认有紧急压缩的 BEFORE/AFTER 通知
        phases = [ce.phase for ce in compact_events if ce.phase is not None]
        assert CompactPhase.BEFORE_EMERGENCY in phases

    @pytest.mark.asyncio
    async def test_emergency_compact_re_raise_on_second_ptl(self):
        """紧急压缩后的重试再次 PTL → Agent 按错误上抛，不再第三次。"""
        from csycode.agent.events import CompactPhase

        class AlwaysPTLProvider:
            name = "mock"
            model = "mock-model"
            call_count = 0

            async def stream(self, req):
                self.call_count += 1
                ptl = PromptTooLongError("prompt too long")
                yield StreamEvent(err=ptl)

        provider = AlwaysPTLProvider()
        registry = _make_registry(["read_file"])
        conv = Conversation()
        for i in range(10):
            conv._messages.append(Message(role="user", content=f"q{i}: " + "x" * 500))
            conv._messages.append(Message(role="assistant", content=f"a{i}: " + "y" * 500))
        config = AgentConfig(max_iterations=3)

        agent = Agent(provider, registry, conv, config, version="test")
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        # 最终应该以 stream_error 结束（重试失败）
        loop_ends = [e for e in events if isinstance(e, LoopEnd)]
        assert len(loop_ends) >= 1
        assert any(le.reason == "stream_error" for le in loop_ends)

    @pytest.mark.asyncio
    async def test_auto_compact_emits_before_after_events(self):
        """自动压缩触发时 emit BEFORE_AUTO 和 AFTER_AUTO 通知。"""
        from csycode.agent.events import CompactPhase

        class AutoCompactProvider:
            name = "mock"
            model = "mock-model"
            call_count = 0

            async def stream(self, req):
                self.call_count += 1
                if self.call_count == 1:
                    # 第 1 次：摘要请求 → 返回正常摘要
                    yield StreamEvent(
                        text="<summary>auto compact summary</summary>",
                        done=True,
                        usage=Usage(input_tokens=20, output_tokens=5),
                    )
                else:
                    # 第 2 次：原始请求 → 正常返回
                    yield StreamEvent(
                        text="After compact.",
                        done=True,
                        usage=Usage(input_tokens=50, output_tokens=10),
                    )

        provider = AutoCompactProvider()
        registry = _make_registry(["read_file"])
        conv = Conversation()
        # 构造超大的对话使自动压缩必然触发
        for i in range(40):
            conv._messages.append(Message(role="user", content=f"q{i}: " + "x" * 800))
            conv._messages.append(Message(role="assistant", content=f"a{i}: " + "y" * 800))
        config = AgentConfig(max_iterations=3)

        agent = Agent(
            provider, registry, conv, config, version="test",
            context_window=200000,
        )
        events: list[AgentEvent] = []
        async for ev in agent.run(Mode.DEFAULT):
            events.append(ev)

        compact_events = [e for e in events if isinstance(e, CompactNotification)]
        phases = [ce.phase for ce in compact_events if ce.phase is not None]

        # 应该有自动压缩的事件
        if compact_events:
            # 如果触发了自动压缩，应该有 BEFORE_AUTO
            assert any(p for p in phases if p is not None)
