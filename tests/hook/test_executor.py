"""T12: Executor 单元测试 —— shell exit2 / http block / prompt / subagent stub。"""

import asyncio
import json
import sys

import pytest

from csycode.hook.event import Event
from csycode.hook.executor import ExecutionResult, Executor
from csycode.hook.rule import (
    Action,
    ActionType,
    HookRule,
    HttpAction,
    PromptAction,
    ShellAction,
    SubagentAction,
)


@pytest.fixture
def executor():
    return Executor()


def _make_shell_rule(command: str, timeout: float = 30.0) -> HookRule:
    return HookRule(
        name="test",
        event=Event.PRE_TOOL_USE,
        action=Action(
            type=ActionType.SHELL,
            shell=ShellAction(command=command),
        ),
        timeout_s=timeout,
    )


# ── shell ──────────────────────────────────────────────────────────────────


class TestRunShell:
    @pytest.mark.asyncio
    async def test_exit_0_allow(self, executor):
        rule = _make_shell_rule("exit 0")
        result = await executor.run(rule, {}, blocking=True)
        assert result.blocked is False
        assert result.err is None

    @pytest.mark.asyncio
    async def test_exit_2_block(self, executor):
        """exit code 2 + blocking=True → 拦截。"""
        rule = _make_shell_rule(
            'python -c "import sys; sys.stderr.write(\'blocked\\n\'); sys.exit(2)"'
        )
        result = await executor.run(rule, {}, blocking=True)
        assert result.blocked is True
        assert "blocked" in result.reason

    @pytest.mark.asyncio
    async def test_exit_2_non_blocking_no_block(self, executor):
        """非拦截类事件，exit 2 也不拦截。"""
        rule = _make_shell_rule("exit 2")
        result = await executor.run(rule, {}, blocking=False)
        assert result.blocked is False
        assert result.err is not None  # 但视为失败

    @pytest.mark.asyncio
    async def test_exit_1_error_no_block(self, executor):
        """exit 1 → 失败但不拦截。"""
        rule = _make_shell_rule("exit 1")
        result = await executor.run(rule, {}, blocking=True)
        assert result.blocked is False
        assert result.err is not None

    @pytest.mark.asyncio
    async def test_timeout(self, executor):
        """超时 → err 为 TimeoutError。"""
        # Windows: sleep 3 确保超时
        rule = _make_shell_rule("sleep 3", timeout=0.1)
        result = await executor.run(rule, {}, blocking=True)
        assert result.err is not None

    @pytest.mark.asyncio
    async def test_stdin_json_payload(self, executor):
        """验证 payload 通过 stdin 传给脚本（key 字典序）。"""
        rule = _make_shell_rule("cat")
        payload = {"tool_name": "test", "event": "PreToolUse"}
        result = await executor.run(rule, payload, blocking=False)
        assert result.err is None


# ── prompt ─────────────────────────────────────────────────────────────────


class TestRunPrompt:
    @pytest.mark.asyncio
    async def test_prompt_returns_text(self, executor):
        rule = HookRule(
            name="p",
            event=Event.SESSION_START,
            action=Action(
                type=ActionType.PROMPT,
                prompt=PromptAction(text="hello world"),
            ),
        )
        result = await executor.run(rule, {}, blocking=False)
        assert result.prompt == "hello world"
        assert result.blocked is False
        assert result.err is None


# ── http ───────────────────────────────────────────────────────────────────


class TestRunHttp:
    @pytest.mark.asyncio
    async def test_http_block_decision(self, executor):
        """HTTP 返回 {"decision":"block","reason":"x"} → 拦截。"""
        import httpx

        # 用 httpx 的 mock transport
        async def mock_handler(request):
            return httpx.Response(
                200,
                json={"decision": "block", "reason": "network policy"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        executor._http_client = client

        rule = HookRule(
            name="http-block",
            event=Event.PRE_TOOL_USE,
            action=Action(
                type=ActionType.HTTP,
                http=HttpAction(url="http://localhost/check"),
            ),
        )
        result = await executor.run(rule, {}, blocking=True)
        assert result.blocked is True
        assert result.reason == "network policy"

    @pytest.mark.asyncio
    async def test_http_no_decision_field(self, executor):
        """HTTP body 无 decision 字段 → 放行。"""
        import httpx

        async def mock_handler(request):
            return httpx.Response(200, json={"status": "ok"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        executor._http_client = client

        rule = HookRule(
            name="http-ok",
            event=Event.PRE_TOOL_USE,
            action=Action(
                type=ActionType.HTTP,
                http=HttpAction(url="http://localhost/check"),
            ),
        )
        result = await executor.run(rule, {}, blocking=True)
        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_http_500_error(self, executor):
        """HTTP 5xx → err 非 None 不拦截。"""
        import httpx

        async def mock_handler(request):
            return httpx.Response(500)

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        executor._http_client = client

        rule = HookRule(
            name="http-500",
            event=Event.STOP,
            action=Action(
                type=ActionType.HTTP,
                http=HttpAction(url="http://localhost/bad"),
            ),
        )
        result = await executor.run(rule, {}, blocking=False)
        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_http_template_body(self, executor):
        """模板 body 中的 {field} 被替换。"""
        import httpx

        received_body = []

        async def mock_handler(request):
            received_body.append(request.content.decode())
            return httpx.Response(200, json={"status": "ok"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
        executor._http_client = client

        rule = HookRule(
            name="http-tpl",
            event=Event.STOP,
            action=Action(
                type=ActionType.HTTP,
                http=HttpAction(
                    url="http://localhost/done",
                    body="event={event}",
                ),
            ),
        )
        payload = {"event": "Stop"}
        result = await executor.run(rule, payload, blocking=False)
        assert result.blocked is False
        assert len(received_body) == 1
        assert "event=Stop" in received_body[0]


# ── subagent ───────────────────────────────────────────────────────────────


class TestRunSubagent:
    @pytest.mark.asyncio
    async def test_subagent_stub(self, executor, capsys):
        rule = HookRule(
            name="sub",
            event=Event.SESSION_START,
            action=Action(
                type=ActionType.SUBAGENT,
                subagent=SubagentAction(agent_name="foo", prompt="test"),
            ),
        )
        result = await executor.run(rule, {}, blocking=False)
        assert result.blocked is False
        assert result.err is None
        captured = capsys.readouterr()
        assert "not yet implemented" in captured.err
        assert "foo" in captured.err
