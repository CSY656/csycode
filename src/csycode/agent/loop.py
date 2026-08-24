"""Agent 主循环模块 —— 对齐 mewcode 架构。

ch08: compact 状态由 Agent 直接持有，工具结果即时持久化，
       压缩事件通过 CompactNotification 通知 TUI。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from csycode.config import AgentConfig
from csycode.conversation import Conversation
from csycode.llm import Message, Provider, Request, System, ToolCall

from csycode import prompt
from csycode.tools.registry import ToolRegistry

from .batcher import classify_tool, SafetyLabel
from .collector import StreamCollector
from .events import (
    AgentEvent,
    ApprovalRequest,
    CompactNotification,
    CompactPhase,
    LoopEnd,
    LoopProgress,
    TokenUsage,
    ToolCallEnd,
    ToolCallStart,
    ToolUseEvent,
)
from .stop_checker import StopChecker

if TYPE_CHECKING:
    from csycode.hook.engine import DispatchResult  # noqa: F401
    from csycode.permission import Engine, Mode, Outcome
    from .plan_mode import PlanModeFilter

PLAN_REMINDER_INTERVAL: int = 4
_logger = logging.getLogger(__name__)

# Layer 1 单条工具结果落盘阈值（字符数）
SINGLE_RESULT_CHAR_LIMIT = 50_000
MAX_OUTPUT_CHARS = 200_000
PERSISTED_TAG = "<persisted-output>"


def _args_preview(args: Any) -> str:
    if isinstance(args, dict):
        return json.dumps(args, ensure_ascii=False)
    if isinstance(args, str):
        return args
    return str(args)


# ── 流式执行器（对齐 mewcode StreamingExecutor）─────────────────────


@dataclass
class _ToolExecResult:
    """流式工具执行结果。"""

    tool_id: str
    tool_name: str
    result: Any  # ToolCallEnd
    elapsed: float
    is_unknown: bool


class StreamingExecutor:
    """流式工具执行器：在 LLM streaming 期间通过 asyncio.create_task 后台执行工具。

    对齐 mewcode StreamingExecutor：
    - submit(coro) 立即启动后台任务
    - collect_results() 等待所有任务完成并按提交顺序返回结果
    """

    def __init__(self) -> None:
        self._tasks: list[tuple[int, asyncio.Task[_ToolExecResult]]] = []
        self._order = 0

    def submit(self, coro: Any) -> None:
        """提交一个后台工具执行任务。"""
        task = asyncio.create_task(coro)
        self._tasks.append((self._order, task))
        self._order += 1

    async def collect_results(self) -> list[_ToolExecResult]:
        """等待所有已提交任务完成，按提交顺序返回结果。"""
        if not self._tasks:
            return []
        tasks = [t for _, t in sorted(self._tasks, key=lambda x: x[0])]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[_ToolExecResult] = []
        for r in results:
            if isinstance(r, Exception):
                out.append(
                    _ToolExecResult(
                        tool_id="",
                        tool_name="",
                        result=ToolCallEnd(
                            tool_name="",
                            success=False,
                            content="",
                            index=-1,
                            error=f"工具执行异常: {r}",
                        ),
                        elapsed=0.0,
                        is_unknown=False,
                    )
                )
            else:
                out.append(r)
        return out


class Agent:
    """ReAct 模式 Agent Loop 编排器（对齐 mewcode）。"""

    def __init__(
        self,
        provider: Provider,
        tool_registry: ToolRegistry,
        conversation: Conversation,
        config: AgentConfig,
        *,
        version: str = "0.0.0",
        engine: "Engine | None" = None,
        tools_override: list[dict] | None = None,
        plan_mode_filter: "PlanModeFilter | None" = None,
        context_window: int = 200000,
        work_dir: str = ".",
        instructions_content: str = "",
        mem_mgr: Any = None,
        hook_engine: Any = None,  # ch12: HookEngine | None
        # ── ch13: SubAgent 扩展参数 ──
        system_prompt: str | None = None,    # 子 Agent 系统提示覆盖
        max_turns: int = 0,                  # 0 = 用全局 MAX_ITERATIONS
        permission_mode: "Mode | None" = None,  # 子 Agent 权限模式
        dont_ask: bool = False,              # 子 Agent dontAsk 兜底
        approval_upgrader: Any = None,       # 子 Agent 审批升级回调
    ) -> None:
        self._provider = provider
        self._tool_registry = tool_registry
        self._conversation = conversation
        self._config = config
        self._version = version
        self._tools_override = tools_override
        self._plan_mode = plan_mode_filter
        self._engine = engine
        self.context_window = context_window

        # ── ch13: SubAgent 专用字段 ──
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._permission_mode = permission_mode
        self._dont_ask = dont_ask
        self._approval_upgrader = approval_upgrader
        # 子 Agent 身份标记（供 Agent 工具嵌套阻断）
        self.is_sub_agent: bool = False
        self.agent_id: str = ""  # 唯一 ID（TraceManager 分配）
        self.parent_id: str | None = None

        # ── ch12: Hook 引擎 ──
        self._hook_engine = hook_engine

        # ── ch09: instructions / memory（对齐 mewcode）──
        self._instructions_content = instructions_content
        self._mem_mgr = mem_mgr
        self._recent_tools: list[str] = []  # 用于记忆召回的工具过滤
        self._surfaced_memories: set[str] = set()  # 本轮已召回的记忆路径

        # ── ch10: 会话运行时状态（提取到 SessionRuntime）──
        from .runtime import SessionRuntime

        self.runtime = SessionRuntime(work_dir)

        # ── ch11: Skill 系统 ──
        self.active_skills: dict[str, str] = {}  # name → prompt_body
        self._skill_catalog: str = ""  # 格式化后的 catalog 文本

        # ── ch13: Agent Catalog ──
        self._agent_catalog: str = ""

        self._run_lock = asyncio.Lock()
        effective_max = max_turns if max_turns > 0 else config.max_iterations
        self._stop_checker = StopChecker(
            max_rounds=effective_max,
            max_consecutive_unknown=config.max_consecutive_unknown_tools,
        )
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_cache_write = 0
        self._total_cache_read = 0

    # ── 公开属性（供 TUI / commands 使用）─────────────────────────

    @property
    def recovery(self) -> Any:
        return self.runtime.recovery

    @property
    def total_input_tokens(self) -> int:
        """ch13: 总输入 token（供 TaskManager 使用）。"""
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        """ch13: 总输出 token（供 TaskManager 使用）。"""
        return self._total_output_tokens

    # ── ch13: run_to_completion ────────────────────────────────────

    async def run_to_completion(
        self,
        conv: "Conversation",
        task: str,
        events: "asyncio.Queue | None" = None,
    ) -> str:
        """子 Agent 跑到底循环（对齐 mewcode）。

        Args:
            conv: 子对话（可能已预装填 fork 消息）。
            task: 任务文本。若非空则作为 user 消息追加。
            events: 可选的外部事件队列。

        Returns:
            最后一条 assistant 文本。

        Raises:
            MaxTurnsReached: 触达 max_turns 上限。
        """
        from .run_to_completion import _run_to_completion_impl

        return await _run_to_completion_impl(self, conv, task, events)

    # ── ch13: Agent Catalog ─────────────────────────────────────────

    def set_agent_catalog(self, catalog_text: str) -> None:
        """设置 Agent Catalog 文本（对齐 mewcode）。

        用于在 system prompt 中注入可用的 subagent_type 列表。
        """
        self._agent_catalog = catalog_text

    # ── ch11: Skill 管理方法 ─────────────────────────────────────

    def activate_skill(self, name: str, prompt_body: str) -> None:
        """激活一个 Skill，将其 SOP 钉到 active_skills 字典。

        激活后，每轮 env context 都会注入该 SOP。
        重复激活同名 skill 会覆盖旧内容。
        """
        self.active_skills[name] = prompt_body

    def clear_active_skills(self) -> None:
        """清空所有已激活的 Skill SOP。"""
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        """设置 Skill Catalog 文本（启动时 / reload 时调用）。

        catalog 文本会在每轮 env context 中注入，
        格式为 "Available Skills" 段。
        """
        self._skill_catalog = catalog

    # ── ch12: Hook dispatch ──────────────────────────────────────────

    async def _dispatch_hook(
        self, event: str, payload: dict
    ) -> "DispatchResult":
        """向 Hook 引擎分派事件并处理结果。

        把 injected_prompts 写入 runtime.pending_reminders，
        供下一轮 reminder 构建时取出。

        参照 mewcode：所有异常静默捕获，确保 hook 失败不中断 Agent 主流程。
        """
        from csycode.hook.engine import DispatchResult as DR
        from csycode.hook.event import Event as HE

        if self._hook_engine is None:
            return DR()

        try:
            hook_event = HE(event)
        except ValueError:
            return DR()

        try:
            result = await self._hook_engine.dispatch(hook_event, payload)
        except Exception:
            # hook 引擎自身异常不中断 Agent
            return DR()

        if result.injected_prompts:
            self.runtime.append_reminders(result.injected_prompts)
        return result

    # ── run ──────────────────────────────────────────────────────────

    async def run(self, mode: "Mode") -> AsyncIterator[AgentEvent]:
        """Agent Loop 主入口。"""
        async with self._run_lock:
            async for event in self._run_impl(mode):
                yield event

    async def _run_impl(self, mode: "Mode") -> AsyncIterator[AgentEvent]:
        """Agent Loop 主循环（对齐 mewcode 架构）。

        每轮迭代：
        1. 应用 Layer 1 budget（落盘大工具结果）
        2. 必要时执行 Layer 2 压缩
        3. 流式收集 LLM 响应
        4. 处理错误（PTL → 紧急压缩重试）
        5. 执行工具调用
        6. 更新 token 计数和记忆
        """
        from csycode.compact import auto_compact
        from csycode.llm import PromptTooLongError
        from csycode.permission import Mode as PMode

        self._stop_checker.reset()
        self._conversation.remove_orphaned_tool_calls()

        # 注入环境与长期记忆（对齐 mewcode）
        env = prompt.gather_environment(self._version, self._provider.model)
        self._conversation.inject_environment(self._build_env_text(env))

        if self._instructions_content or self._mem_mgr is not None:
            mem_content = self._mem_mgr.load() if self._mem_mgr else ""
            self._conversation.inject_long_term_memory(
                self._instructions_content, mem_content
            )

        # 稳定的 system prompt（不包含 instructions/memory，保证可缓存）
        stable_system = prompt.build_system_prompt()
        plan_mode_active = mode == PMode.PLAN or (
            self._plan_mode is not None and self._plan_mode.is_plan_mode
        )

        # ── max_tokens 恢复状态（对齐 mewcode）──
        max_tokens_escalated = False
        output_recoveries = 0
        MAX_OUTPUT_TOKENS_RECOVERIES = 3
        MAX_TOKENS_CEILING = 64000

        while not self._stop_checker.should_stop:
            round_num = self._stop_checker.round_count + 1
            yield LoopProgress(
                round_num=round_num,
                max_rounds=self._config.max_iterations,
                status="thinking",
            )

            tools = self._get_tools(mode)
            defs: list[dict] = tools if tools else []

            reminder = ""
            if plan_mode_active:
                full = round_num == 1 or (round_num - 1) % PLAN_REMINDER_INTERVAL == 0
                reminder = prompt.plan_reminder(full)

            # ch12: 注入 hook 的 prompt 文本（置于 plan reminder 之后）
            hook_reminders = self.runtime.take_reminders()
            if hook_reminders:
                hook_text = "\n\n".join(hook_reminders)
                if reminder:
                    reminder += "\n\n" + hook_text
                else:
                    reminder = hook_text

            # ── ch12: PreCompact emit ────────────────────────────
            await self._dispatch_hook("PreCompact", {"trigger": "auto"})

            # ── Layer 1: 应用工具结果 budget ──────────────────
            # 将超大工具结果落盘并替换为预览体，减小上下文占用。
            # 对齐 mewcode：每轮始终应用 budget，不再恢复旧消息。
            budget_msgs = self._apply_tool_result_budget(self._conversation.messages())
            self._conversation.replace_history(budget_msgs)

            # ── Layer 2: 自动压缩（必要时）────────────────────
            # auto_compact 内部做阈值判断；需要压缩时用 budget_msgs
            # 估算 token，然后替换 conversation history。
            compact_result = await auto_compact(
                conversation=self._conversation,
                provider=self._provider,
                model=self._provider.model,
                context_window=self.context_window,
                replacement=self.runtime.replacement,
                recovery=self.runtime.recovery,
                auto_tracking=self.runtime.auto_tracking,
                session=self.runtime.session,
                tool_defs=defs,
                budget_messages=budget_msgs,
            )

            if compact_result is not None:
                before_tok, after_tok = compact_result
                # ch12: PostCompact emit
                await self._dispatch_hook("PostCompact", {
                    "trigger": "auto",
                    "before_tokens": before_tok,
                    "after_tokens": after_tok,
                })
                yield CompactNotification(
                    before_tokens=before_tok,
                    after_tokens=after_tok,
                    message=f"已压缩，token 从 {before_tok:,} 降至 {after_tok:,}",
                    phase=CompactPhase.AFTER_AUTO,
                )
                # 压缩后重新注入环境与长期记忆（对齐 mewcode）
                self._conversation.inject_environment(self._build_env_text(env))
                if self._instructions_content or self._mem_mgr is not None:
                    mem_content = self._mem_mgr.load() if self._mem_mgr else ""
                    self._conversation.inject_long_term_memory(
                        self._instructions_content, mem_content
                    )

            # ── ch12: PreUserMessage emit ────────────────────────
            # 取 conversation 末尾 user 消息作为 payload
            msgs = self._conversation.messages()
            last_user = ""
            for m in reversed(msgs):
                if m.role == "user":
                    last_user = m.content
                    break
            await self._dispatch_hook("PreUserMessage", {"prompt": last_user})

            # ── 流式收集 LLM 响应（集成流式工具执行，对齐 mewcode）──
            collector = StreamCollector(self._provider)
            executor = StreamingExecutor()

            # 需要用户审批的工具调用（流期间检测到 ask → 延迟到流结束后处理）
            deferred_tue_tcs: list[
                tuple[ToolUseEvent, ToolCall, str]
            ] = []  # (ToolUseEvent, ToolCall, reason)
            stream_error: Exception | None = None

            try:
                async for event in collector.collect(
                    Request(
                        messages=self._conversation.messages(),
                        tools=defs,
                        system=System(stable=stable_system, environment=env.render()),
                        reminder=reminder,
                    )
                ):
                    if isinstance(event, LoopEnd):
                        yield event
                        return

                    # ── 流期间工具调用：直通执行或延迟（对齐 mewcode）──
                    if isinstance(event, ToolUseEvent):
                        from csycode.permission import Decision as PD2

                        tc = ToolCall(
                            id=event.tool_id,
                            name=event.tool_name,
                            arguments=event.arguments,
                        )
                        needs_ask = False
                        ask_reason = ""
                        if self._engine is not None:
                            decision, reason = self._engine.check(
                                mode, tc, self._resolve_readonly(tc)
                            )
                            if decision == PD2.ASK:
                                needs_ask = True
                                ask_reason = reason

                        if needs_ask:
                            deferred_tue_tcs.append((event, tc, ask_reason))
                        else:
                            executor.submit(
                                self._execute_single_tool_direct(tc, event.tool_id)
                            )

                    yield event
            except asyncio.CancelledError:
                self._stop_checker.record_user_cancel()
                yield LoopEnd(
                    reason="user_cancel",
                    final_text="",
                    total_rounds=self._stop_checker.round_count,
                    total_input_tokens=self._total_input_tokens,
                    total_output_tokens=self._total_output_tokens,
                )
                return
            except Exception as e:
                stream_error = e
            finally:
                # 收集流期间后台执行的工具结果
                streaming_results = await executor.collect_results()

            result = collector.last_result

            # ── 合并错误源：stream 异常 + collector 内部捕获 ──
            error = stream_error or (result.error if result else None)

            if error is not None:
                if isinstance(error, PromptTooLongError):
                    _logger.info("检测到 PTL 错误，触发紧急压缩")
                    yield CompactNotification(
                        before_tokens=self._conversation.current_tokens(),
                        message="上下文撞墙，自动压缩中...",
                        phase=CompactPhase.BEFORE_EMERGENCY,
                    )

                    em_result = await self._emergency_compact(defs, env)
                    if em_result is not None:
                        before_tok, after_tok = em_result
                        # ch12: PostCompact emit（紧急压缩后）
                        await self._dispatch_hook("PostCompact", {
                            "trigger": "emergency",
                            "before_tokens": before_tok,
                            "after_tokens": after_tok,
                        })
                        yield CompactNotification(
                            before_tokens=before_tok,
                            after_tokens=after_tok,
                            message=f"已压缩，token 从 {before_tok:,} 降至 {after_tok:,}",
                            phase=CompactPhase.AFTER_EMERGENCY,
                        )
                        continue  # 重试本轮
                    else:
                        yield CompactNotification(
                            before_tokens=self._conversation.current_tokens(),
                            error="紧急压缩后仍无法继续",
                            phase=CompactPhase.AFTER_EMERGENCY,
                        )

                _logger.warning("LLM 流式响应异常: %s", error)
                yield LoopEnd(
                    reason="stream_error",
                    final_text="",
                    total_rounds=self._stop_checker.round_count,
                    total_input_tokens=self._total_input_tokens,
                    total_output_tokens=self._total_output_tokens,
                    error_msg=str(error),
                )
                return

            # ── max_tokens 恢复（对齐 mewcode）────────────────
            if result.stop_reason == "length":
                if not max_tokens_escalated:
                    max_tokens_escalated = True
                    # 通知 provider 提高 max_tokens 上限
                    escalate = getattr(self._provider, 'set_max_output_tokens', None)
                    if escalate is not None:
                        escalate(MAX_TOKENS_CEILING)
                    if result.text:
                        self._conversation.add_assistant(result.text)
                        self._conversation.add_supplement(
                            "Output token limit hit. Resume directly from where you stopped. "
                            "Do not apologize or repeat previous content. Pick up mid-thought if needed.",
                            "max_tokens_resume",
                        )
                    continue
                elif output_recoveries < MAX_OUTPUT_TOKENS_RECOVERIES:
                    output_recoveries += 1
                    self._conversation.add_assistant(result.text)
                    self._conversation.add_supplement(
                        "Output token limit hit. Resume directly from where you stopped. "
                        "Break remaining work into smaller pieces.",
                        "max_tokens_resume",
                    )
                    continue
            else:
                output_recoveries = 0

            # ── 更新 token 锚点 ────────────────────────────────
            if result.usage is not None:
                round_input = result.usage.input_tokens
                round_output = result.usage.output_tokens
                cache_write = result.usage.cache_write
                cache_read = result.usage.cache_read

                self._conversation.record_usage_anchor(
                    input_tokens=round_input,
                    output_tokens=round_output,
                    cache_read=cache_read,
                    cache_creation=cache_write,
                )
            else:
                round_input = self._estimate_input_tokens(
                    self._conversation.messages(), defs
                )
                round_output = self._estimate_output_tokens(
                    result.text, result.tool_calls
                )
                cache_write = 0
                cache_read = 0

            self._total_input_tokens += round_input
            self._total_output_tokens += round_output
            self._total_cache_write += cache_write
            self._total_cache_read += cache_read

            yield TokenUsage(
                input_tokens=round_input,
                output_tokens=round_output,
                round_num=round_num,
                cache_write=cache_write,
                cache_read=cache_read,
            )

            if not result.tool_calls:
                self._stop_checker.record_model_done()
                self._conversation.add_assistant(result.text)

                # ── ch09: 记忆更新触发 ──
                self.runtime.turn_count += 1
                self._maybe_trigger_memory_update()

                # ── ch12: Stop emit ──
                await self._dispatch_hook("Stop", {"iter": round_num})

                yield LoopEnd(
                    reason="model_done",
                    final_text=result.text,
                    total_rounds=self._stop_checker.round_count,
                    total_input_tokens=self._total_input_tokens,
                    total_output_tokens=self._total_output_tokens,
                )
                return

            self._conversation.add_assistant_with_tools(result.text, result.tool_calls)

            # ── token 锚点：assistant 消息已在历史中 ──────────
            self._conversation.record_usage_anchor(
                round_input, round_output, cache_read, cache_write,
            )

            yield LoopProgress(
                round_num=round_num,
                max_rounds=self._config.max_iterations,
                status="executing",
            )

            tool_results: list[tuple[str, str]] = []
            plan_submitted = False

            # ── 流期间后台执行的工具结果（对齐 mewcode streaming_results）──
            total_tools = len(streaming_results) + len(deferred_tue_tcs)
            for idx, br in enumerate(streaming_results):
                if br.is_unknown:
                    self._stop_checker.record_unknown_tool()
                else:
                    self._stop_checker.reset_unknown_count()

                tce = br.result
                # 产出 ToolCallStart（向后兼容，TUI 用于展示工具调用状态）
                yield ToolCallStart(
                    tool_name=tce.tool_name,
                    tool_args={},
                    index=idx,
                    total=total_tools,
                )
                tool_results.append((br.tool_id, tce.content))
                if tce.tool_name:
                    self._track_tool_usage(tce.tool_name)
                if tce.success and tce.tool_name:
                    self._snapshot_for_recovery(
                        ToolCall(
                            id=br.tool_id,
                            name=tce.tool_name,
                            arguments={},
                        )
                    )
                if tce.exit_plan_mode:
                    plan_submitted = True
                yield tce

            # ── 延迟工具：需要用户审批（对齐 mewcode deferred_tool_calls）──
            deferred_total = len(deferred_tue_tcs)
            for idx, (tue, tc, ask_reason) in enumerate(deferred_tue_tcs):
                yield ToolCallStart(
                    tool_name=tc.name,
                    tool_args=tc.arguments,
                    index=idx,
                    total=deferred_total,
                )

                # ── 去重：流式输出会把同一命令拆成多个 tool_use 块，
                # 或跨轮重发。延迟队列在流期间就已入队（此时 session 缓存
                # 尚未写入），所以这里必须重新查询一次会话级缓存——
                # 若本批次前面已审批过相同 (friendly, target)，直接放行，
                # 不再二次弹窗（default 模式"相同命令只问一次"）。
                skip_ask = False
                if self._engine is not None:
                    from csycode.permission import Decision as PD3

                    try:
                        decision, _ = self._engine.check(
                            mode, tc, self._resolve_readonly(tc)
                        )
                        if decision != PD3.ASK:
                            skip_ask = True
                    except Exception:
                        pass

                if skip_ask:
                    # 命中会话级缓存 / 白名单 / 规则放行 → 直接执行，不再询问
                    tce = await self._execute_one_tool(idx, tc)
                    tool_results.append((tc.id, tce.content))
                    self._track_tool_usage(tc.name)
                    if tce.success:
                        self._snapshot_for_recovery(tc)
                    if tce.exit_plan_mode:
                        plan_submitted = True
                    if tce.error and "未知工具" in (tce.error or ""):
                        self._stop_checker.record_unknown_tool()
                    else:
                        self._stop_checker.reset_unknown_count()
                    yield tce
                    continue

                req, respond = self._prepare_approval(tc, ask_reason)
                yield req
                try:
                    outcome = await respond
                except asyncio.CancelledError:
                    raise

                from csycode.permission import Outcome as O2

                if outcome == O2.ALLOW_ONCE or outcome == O2.ALLOW_FOREVER:
                    # 会话级缓存：无论 ALLOW_ONCE 还是 ALLOW_FOREVER，
                    # 都将本次操作加入 session_allowed，后续相同操作不再弹窗。
                    # 对齐 mewcode 的双写策略（session_allow + persist_local_allow）。
                    try:
                        if self._engine is not None:
                            self._engine.session_allow_tc(tc)
                    except Exception:
                        pass  # 会话缓存失败不影响工具执行

                    if outcome == O2.ALLOW_FOREVER:
                        try:
                            if self._engine is not None:
                                self._engine.persist_local_allow(tc)
                        except Exception as exc:
                            _logger.warning("持久化永久放行规则失败: %s", exc)

                    tce = await self._execute_one_tool(idx, tc)
                    tool_results.append((tc.id, tce.content))
                    self._track_tool_usage(tc.name)
                    if tce.success:
                        self._snapshot_for_recovery(tc)
                    if tce.exit_plan_mode:
                        plan_submitted = True
                    if tce.error and "未知工具" in (tce.error or ""):
                        self._stop_checker.record_unknown_tool()
                    else:
                        self._stop_checker.reset_unknown_count()
                    yield tce
                else:
                    tce = ToolCallEnd(
                        tool_name=tc.name,
                        success=False,
                        content="用户拒绝了此次操作",
                        original_output="",
                        error="用户拒绝",
                        index=idx,
                    )
                    tool_results.append((tc.id, tce.content))
                    yield tce

            # ── Plan Mode 回退路径：非只读工具被拦截的场景 ──
            # 如果 result.tool_calls 中有工具未出现在 streaming_results 或
            # deferred_tue_tcs 中（如 plan mode 拦截），通过 execute_batched 处理。
            processed_ids: set[str] = set()
            for br in streaming_results:
                processed_ids.add(br.tool_id)
            for tue, tc, _reason in deferred_tue_tcs:
                processed_ids.add(tue.tool_id)

            remaining_calls = [
                tc for tc in result.tool_calls if tc.id not in processed_ids
            ]
            if remaining_calls:
                # 回退到 execute_batched（处理 plan mode 等场景）
                index_to_tc: dict[int, ToolCall] = {
                    i: tc for i, tc in enumerate(remaining_calls)
                }
                async for event in self.execute_batched(remaining_calls, mode):
                    yield event
                    if isinstance(event, ToolCallEnd):
                        tc = index_to_tc.get(event.index)
                        call_id = tc.id if tc else ""
                        if tc is not None:
                            self._track_tool_usage(tc.name)
                        tool_results.append((call_id, event.content))
                        if tc is not None and event.success:
                            self._snapshot_for_recovery(tc)
                        if event.exit_plan_mode:
                            plan_submitted = True
                        if event.error and "未知工具" in (event.error or ""):
                            self._stop_checker.record_unknown_tool()
                        else:
                            self._stop_checker.reset_unknown_count()

            self._conversation.add_tool_results(tool_results)

            if plan_submitted and self._plan_mode is not None:
                self._plan_mode.enter_do_mode()
                plan_mode_active = False
                yield LoopEnd(
                    reason="plan_submitted",
                    final_text=result.text,
                    total_rounds=self._stop_checker.round_count + 1,
                    total_input_tokens=self._total_input_tokens,
                    total_output_tokens=self._total_output_tokens,
                )
                return

            self._stop_checker.record_round()

            if self._stop_checker.should_stop:
                yield LoopEnd(
                    reason=self._stop_checker.stop_reason or "unknown",
                    final_text=result.text,
                    total_rounds=self._stop_checker.round_count,
                    total_input_tokens=self._total_input_tokens,
                    total_output_tokens=self._total_output_tokens,
                )
                return

        yield LoopEnd(
            reason=self._stop_checker.stop_reason or "max_rounds",
            final_text="",
            total_rounds=self._stop_checker.round_count,
            total_input_tokens=self._total_input_tokens,
            total_output_tokens=self._total_output_tokens,
        )

    # ── 紧急压缩（PTL 错误恢复）─────────────────────────────────────

    async def _emergency_compact(
        self, defs: list[dict], env
    ) -> tuple[int, int] | None:
        """紧急压缩：PTL 错误时强制压缩上下文。

        1. 先跑 layer1 落盘大工具结果
        2. 无条件 force compact（跳过熔断器，manual=True）
        3. 成功后重新注入环境与长期记忆，重置 token 锚点

        Returns:
            (before_tokens, after_tokens) 或 None（压缩失败）。
        """
        from csycode.compact import auto_compact

        try:
            # 先强制跑 layer1 把大工具结果挪走
            l1_msgs = self._apply_tool_result_budget(
                self._conversation.messages()
            )
            self._conversation.replace_history(l1_msgs)

            # 无条件 force compact（跳过熔断器）
            em_result = await auto_compact(
                conversation=self._conversation,
                provider=self._provider,
                model=self._provider.model,
                context_window=self.context_window,
                replacement=self.runtime.replacement,
                recovery=self.runtime.recovery,
                auto_tracking=self.runtime.auto_tracking,
                session=self.runtime.session,
                tool_defs=defs,
                manual=True,
            )

            if em_result is not None:
                # 压缩后重新注入环境与长期记忆
                self._conversation.inject_environment(self._build_env_text(env))
                if self._instructions_content or self._mem_mgr is not None:
                    mem_content = self._mem_mgr.load() if self._mem_mgr else ""
                    self._conversation.inject_long_term_memory(
                        self._instructions_content, mem_content
                    )
                # 重置 token 锚点，重启流式请求
                self._conversation.record_usage_anchor(
                    input_tokens=0, output_tokens=0,
                    cache_read=0, cache_creation=0,
                )
                return em_result
            return None
        except Exception as em_err:
            _logger.warning("紧急压缩失败: %s", em_err)
            return None

    # ── ch08: Layer 1 — 工具结果持久化（对齐 mewcode _maybe_persist_or_truncate）──

    def _apply_tool_result_budget(self, msgs: list[Message]) -> list[Message]:
        """对消息列表应用工具结果 budget（layer1）。

        对齐 mewcode 的 apply_tool_result_budget：
        已 Seen 的 id 复用替换；新 id 按阈值落盘。
        """
        from csycode.compact.layer1 import offload_and_snip

        return offload_and_snip(msgs, self.runtime.replacement, self.runtime.session)

    def _maybe_persist_or_truncate(
        self, tool_use_id: str, text: str
    ) -> tuple[str, bool, str]:
        """单条工具结果的持久化/截断处理。

        对齐 mewcode 的 _maybe_persist_or_truncate。
        返回 (content_for_conv, offloaded, offload_path)。

        - 超 SINGLE_RESULT_CHAR_LIMIT → 落盘 + 返回预览体
        - 超 MAX_OUTPUT_CHARS → 截断
        - 否则原文返回
        """
        from csycode.compact.layer1 import (
            build_preview,
            spill_single,
            _head_preview,
        )

        if len(text) > SINGLE_RESULT_CHAR_LIMIT:
            try:
                spill_single(self.runtime.session, tool_use_id, text)
            except OSError:
                _logger.warning("落盘失败 %s，返回截断原文", tool_use_id)
                if len(text) > MAX_OUTPUT_CHARS:
                    return text[:MAX_OUTPUT_CHARS] + "\n… (output truncated)", False, ""
                return text, False, ""

            spill_path = str(Path(self.runtime.session.spill_dir) / tool_use_id)
            preview = build_preview(
                len(text.encode("utf-8")),
                _head_preview(text),
                spill_path,
            )
            return preview, True, spill_path

        if len(text) > MAX_OUTPUT_CHARS:
            return text[:MAX_OUTPUT_CHARS] + "\n… (output truncated)", False, ""

        return text, False, ""

    # ── ch08: 文件快照（用于恢复段）─────────────────────────────

    def _snapshot_for_recovery(self, tc: ToolCall) -> None:
        """ReadFile 成功后记录文件内容快照。"""
        if tc.name != "read_file":
            return
        args = tc.arguments
        if not isinstance(args, dict):
            return
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return
        try:
            abs_path = str(Path(path).resolve())
        except OSError:
            return
        try:
            data = Path(abs_path).read_bytes()
        except OSError:
            return
        self.runtime.recovery.record_file(abs_path, data.decode("utf-8", errors="replace"))

    # ── ch08: manual_compact ────────────────────────────────────────

    async def manual_compact(self) -> tuple[int, int] | None:
        """手动 /compact 入口。"""
        from csycode.compact import auto_compact

        async with self._run_lock:
            # 先获取工具定义
            tools = (
                self._tool_registry.to_anthropic_tools() if self._tool_registry else []
            )

            result = await auto_compact(
                conversation=self._conversation,
                provider=self._provider,
                model=self._provider.model,
                context_window=self.context_window,
                replacement=self.runtime.replacement,
                recovery=self.runtime.recovery,
                auto_tracking=self.runtime.auto_tracking,
                session=self.runtime.session,
                tool_defs=tools,
                manual=True,
            )
            return result

    # ── 权限感知的工具分批执行 ──────────────────────────────────

    async def execute_batched(
        self, tool_calls: list[ToolCall], mode: "Mode"
    ) -> AsyncIterator[AgentEvent]:
        from csycode.permission import Decision as PD, Outcome

        if not tool_calls:
            return

        total = len(tool_calls)

        # 0. Plan Mode 拦截
        allowed_calls: list[tuple[int, ToolCall]] = []
        for i, tc in enumerate(tool_calls):
            if self._plan_mode is not None and not self._plan_mode.is_tool_allowed(
                tc.name
            ):
                yield ToolCallStart(
                    tool_name=tc.name, tool_args=tc.arguments, index=i, total=total
                )
                yield ToolCallEnd(
                    tool_name=tc.name,
                    success=False,
                    content="",
                    original_output="",
                    error=f"⛔ Plan Mode 拦截: 工具 '{tc.name}' 在计划模式下不可用。",
                    index=i,
                    blocked_by_plan_mode=True,
                )
                continue
            allowed_calls.append((i, tc))

        if not allowed_calls:
            return

        safe_calls: list[tuple[int, ToolCall]] = []
        side_effect_calls: list[tuple[int, ToolCall]] = []
        for i, tc in allowed_calls:
            label = classify_tool(tc.name, self._tool_registry)
            if label == SafetyLabel.SAFE:
                safe_calls.append((i, tc))
            else:
                side_effect_calls.append((i, tc))

        if safe_calls:
            for i, tc in safe_calls:
                yield ToolCallStart(
                    tool_name=tc.name, tool_args=tc.arguments, index=i, total=total
                )

            results: dict[int, ToolCallEnd] = {}
            pending_execs: list[tuple[int, ToolCall]] = []

            for i, tc in safe_calls:
                if self._engine is not None:
                    decision, reason = self._engine.check(mode, tc, True)
                else:
                    decision, reason = PD.ALLOW, ""

                if decision == PD.DENY:
                    results[i] = ToolCallEnd(
                        tool_name=tc.name,
                        success=False,
                        content=reason,
                        original_output="",
                        error=reason,
                        index=i,
                    )
                else:
                    pending_execs.append((i, tc))

            async def _execute_safe(i: int, tc: ToolCall) -> ToolCallEnd:
                return await self._execute_one_tool(i, tc)

            if pending_execs:
                safe_results = await asyncio.gather(
                    *[_execute_safe(i, tc) for i, tc in pending_execs],
                )
                for r in safe_results:
                    results[r.index] = r

            for i, tc in safe_calls:
                yield results[i]

        for i, tc in side_effect_calls:
            yield ToolCallStart(
                tool_name=tc.name, tool_args=tc.arguments, index=i, total=total
            )

            tool = self._tool_registry.get(tc.name)
            if tool is None:
                yield ToolCallEnd(
                    tool_name=tc.name,
                    success=False,
                    content="",
                    original_output="",
                    error=f"未知工具: '{tc.name}'",
                    index=i,
                )
                continue

            if self._engine is not None:
                decision, reason = self._engine.check(mode, tc, False)
            else:
                decision, reason = PD.ALLOW, ""

            if decision == PD.ALLOW:
                yield await self._execute_one_tool(i, tc)
            elif decision == PD.DENY:
                yield ToolCallEnd(
                    tool_name=tc.name,
                    success=False,
                    content=reason,
                    original_output="",
                    error=reason,
                    index=i,
                )
            elif decision == PD.ASK:
                req, respond = self._prepare_approval(tc, reason)
                yield req
                try:
                    outcome = await respond
                except asyncio.CancelledError:
                    raise

                if outcome == Outcome.ALLOW_ONCE:
                    # 会话级缓存：允许本次后，会话内相同操作不再弹窗
                    try:
                        if self._engine is not None:
                            self._engine.session_allow_tc(tc)
                    except Exception:
                        pass
                    yield await self._execute_one_tool(i, tc)
                elif outcome == Outcome.ALLOW_FOREVER:
                    # 双写策略：session_allow（立即生效）+ persist_local_allow（跨会话）
                    try:
                        if self._engine is not None:
                            self._engine.session_allow_tc(tc)
                    except Exception:
                        pass
                    try:
                        if self._engine is not None:
                            self._engine.persist_local_allow(tc)
                    except Exception as exc:
                        _logger.warning("持久化永久放行规则失败: %s", exc)
                    yield await self._execute_one_tool(i, tc)
                elif outcome == Outcome.DENY_ONCE:
                    yield ToolCallEnd(
                        tool_name=tc.name,
                        success=False,
                        content="用户拒绝了此次操作",
                        original_output="",
                        error="用户拒绝",
                        index=i,
                    )

    async def _execute_single_tool_direct(
        self, tc: ToolCall, tool_id: str = ""
    ) -> _ToolExecResult:
        """流期间直通工具执行（对齐 mewcode _execute_single_tool_direct）。

        权限检查已在调用方完成，此方法直接执行工具。
        """
        import time as _time

        start = _time.monotonic()
        tool = self._tool_registry.get(tc.name)
        is_unknown = False

        if tool is None:
            is_unknown = True
            return _ToolExecResult(
                tool_id=tool_id,
                tool_name=tc.name,
                result=ToolCallEnd(
                    tool_name=tc.name,
                    success=False,
                    content="",
                    index=-1,
                    error=f"未知工具: '{tc.name}'",
                ),
                elapsed=_time.monotonic() - start,
                is_unknown=is_unknown,
            )

        try:
            result = await tool.execute(**tc.arguments)
        except Exception as e:
            return _ToolExecResult(
                tool_id=tool_id,
                tool_name=tc.name,
                result=ToolCallEnd(
                    tool_name=tc.name,
                    success=False,
                    content="",
                    index=-1,
                    error=f"工具执行异常: {e}",
                ),
                elapsed=_time.monotonic() - start,
                is_unknown=False,
            )

        original_output = result.content
        call_id = tc.id
        content_for_conv, offloaded, offload_path = self._maybe_persist_or_truncate(
            call_id, original_output
        )

        return _ToolExecResult(
            tool_id=tool_id,
            tool_name=tc.name,
            result=ToolCallEnd(
                tool_name=tc.name,
                success=result.success,
                content=content_for_conv,
                original_output=original_output,
                error=result.error,
                index=-1,
                exit_plan_mode=result.exit_plan_mode,
                blocked_by_plan_mode=result.blocked_by_plan_mode,
                show_result_to_user=tool.show_result_to_user,
                offloaded=offloaded,
                offload_path=offload_path,
            ),
            elapsed=_time.monotonic() - start,
            is_unknown=is_unknown,
        )

    async def _execute_one_tool(self, index: int, tc: ToolCall) -> ToolCallEnd:
        """执行单个工具并返回 ToolCallEnd。包含持久化处理。

        ch12: 在工具实际执行前做 PreToolUse hook 检查（权限/HITL 之后），
        避免 hook dispatch 干扰 execute_batched 内的 HITL 流程。
        """
        tool = self._tool_registry.get(tc.name)
        if tool is None:
            return ToolCallEnd(
                tool_name=tc.name,
                success=False,
                content="",
                original_output="",
                error=f"未知工具: '{tc.name}'",
                index=index,
            )

        # ── ch12: PreToolUse hook 检查（权限之后、执行之前）──
        pre_result = await self._dispatch_hook("PreToolUse", {
            "tool_name": tc.name,
            "tool_input": tc.arguments if isinstance(tc.arguments, dict) else {},
        })
        if pre_result.blocked:
            return ToolCallEnd(
                tool_name=tc.name,
                success=False,
                content=f"[hook {pre_result.blocking_hook_name}] {pre_result.reason}",
                original_output="",
                error=f"[hook {pre_result.blocking_hook_name}] {pre_result.reason}",
                index=index,
            )

        try:
            result = await tool.execute(**tc.arguments)
        except Exception as e:
            return ToolCallEnd(
                tool_name=tc.name,
                success=False,
                content="",
                original_output="",
                error=f"工具执行异常: {e}",
                index=index,
            )

        # 原始输出（供 TUI 展示）
        original_output = result.content
        success = result.success
        error = result.error

        # 持久化/截断处理
        call_id = tc.id
        content_for_conv, offloaded, offload_path = self._maybe_persist_or_truncate(
            call_id, original_output
        )

        return ToolCallEnd(
            tool_name=tc.name,
            success=success,
            content=content_for_conv,
            original_output=original_output,
            error=error,
            index=index,
            exit_plan_mode=result.exit_plan_mode,
            blocked_by_plan_mode=result.blocked_by_plan_mode,
            show_result_to_user=tool.show_result_to_user,
            offloaded=offloaded,
            offload_path=offload_path,
        )

    # ── 人在回路 ──────────────────────────────────────────────────

    def _resolve_readonly(self, tc: ToolCall) -> bool:
        """流式权限检查用：判定工具调用是否只读。

        优先取工具实例的 is_readonly（权威来源）；工具未注册时按名字
        分类兜底；再兜底 False（保守按副作用处理，触发审批）。

        修复：流式路径原先硬编码 read_only=False，导致 read_file/glob/grep
        等只读工具在 DEFAULT 模式下被误判为 EXEC 而 ASK。改用真实只读属性后，
        只读工具归为 READ → 直接放行，与 batch 路径行为一致。
        """
        tool = self._tool_registry.get(tc.name)
        if tool is not None:
            return bool(getattr(tool, "is_readonly", False))
        try:
            from csycode.agent.batcher import SafetyLabel, classify_tool

            return classify_tool(tc.name) == SafetyLabel.SAFE
        except Exception:
            return False

    def _prepare_approval(
        self, call: ToolCall, reason: str
    ) -> "tuple[ApprovalRequest, asyncio.Future[Outcome]]":
        loop = asyncio.get_running_loop()
        respond: asyncio.Future[Outcome] = loop.create_future()
        req = ApprovalRequest(
            name=call.name,
            args=_args_preview(call.arguments),
            reason=reason,
            respond=respond,
        )
        return req, respond

    # ── ch09: 记忆更新触发 ──────────────────────────────────────

    def _maybe_trigger_memory_update(self) -> None:
        """检查是否需要触发记忆提取（对齐 mewcode）。

        条件：① 每 5 轮触发一次；② 检测到记忆关键词立即触发。
        提取在后台 asyncio task 中执行，不阻塞主会话。
        """
        if self._mem_mgr is None:
            return

        should_update = False

        # 每 5 轮自动触发
        if self.runtime.turn_count % 5 == 0:
            should_update = True

        # 检测记忆关键词
        if not should_update:
            recent_msgs = self._conversation.messages()
            for msg in recent_msgs[-6:]:
                content = msg.content.lower() if msg.content else ""
                for kw in ("记住", "记忆", "别忘", "remember", "memo"):
                    if kw in content:
                        should_update = True
                        break

        if should_update:
            recent = self._conversation.messages()[-10:]
            asyncio.create_task(self._mem_mgr.update_async(recent))

    def _track_tool_usage(self, tool_name: str) -> None:
        """记录工具使用（供记忆召回过滤用）。最多保留 10 个。"""
        self._recent_tools.append(tool_name)
        if len(self._recent_tools) > 10:
            self._recent_tools = self._recent_tools[-10:]

    # ── ch11: Skill 环境注入 ─────────────────────────────────

    def _build_env_text(self, env) -> str:
        """构建完整环境文本（env.render() + Skill Catalog + Active Skills）。"""
        return self._append_skills_to_env(env.render())

    def _append_skills_to_env(self, env_text: str) -> str:
        """将 Skill Catalog、Agent Catalog 和 Active Skills 拼接到环境文本后。

        Returns:
            追加了 skill/agent 段的环境文本。
        """
        parts = [env_text]

        # Skill Catalog（Available Skills 列表）
        if self._skill_catalog:
            parts.append("\n## Available Skills\n")
            parts.append(self._skill_catalog)

        # ch13: Agent Catalog（Available Sub-Agent Types 列表）
        agent_cat = getattr(self, "_agent_catalog", "")
        if agent_cat:
            parts.append("\n" + agent_cat)

        # Active Skills（已激活的 SOP）
        if self.active_skills:
            parts.append("\n## Active Skills\n")
            for name, body in self.active_skills.items():
                parts.append(f"### Skill: {name}\n\n{body}\n")

        return "\n".join(parts)

    # ── helpers ──────────────────────────────────────────────────

    def _get_tools(self, mode: "Mode") -> list[dict] | None:
        from csycode.permission import Mode as PMode

        plan_active = mode == PMode.PLAN or (
            self._plan_mode is not None and self._plan_mode.is_plan_mode
        )

        protocol = self._detect_protocol()

        if self._plan_mode is not None:
            return self._plan_mode.get_active_tools(protocol)

        if plan_active:
            read_only_tools = [
                t
                for t in self._tool_registry.list_all()
                if t.is_readonly or t.allowed_in_plan_mode
            ]
            if protocol == "anthropic":
                return [
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.parameters,
                    }
                    for t in read_only_tools
                ]
            return [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in read_only_tools
            ]

        if self._tools_override is not None:
            return self._tools_override

        return self._tool_registry.get_all_schemas(protocol)

    def _detect_protocol(self) -> str:
        """检测当前 provider 使用的协议。

        优先通过类名判断，OpenAIProvider → "openai"，AnthropicProvider → "anthropic"。
        """
        provider_name = type(self._provider).__name__
        if "Anthropic" in provider_name:
            return "anthropic"
        return "openai"  # OpenAIProvider or compatible

    @staticmethod
    def _estimate_input_tokens(
        messages: list[Message], tools: list[dict] | None
    ) -> int:
        total_chars = 0
        for m in messages:
            total_chars += len(m.content)
            if m.tool_calls:
                for tc in m.tool_calls:
                    total_chars += len(tc.name) + len(str(tc.arguments))
        if tools:
            total_chars += len(str(tools))
        return max(1, total_chars // 4)

    @staticmethod
    def _estimate_output_tokens(text: str, tool_calls: list[ToolCall] | None) -> int:
        chars = len(text)
        if tool_calls:
            for tc in tool_calls:
                chars += len(tc.name) + len(str(tc.arguments))
        return max(1, chars // 4)
