"""T13: Engine 单元测试 —— 事件分派、拦截、reminder、once。"""

import asyncio

import pytest

from csycode.hook.engine import DispatchResult, Engine
from csycode.hook.event import Event
from csycode.hook.rule import (
    Action,
    ActionType,
    Condition,
    CombineMode,
    AtomCondition,
    HookRule,
    PromptAction,
    ShellAction,
)
from csycode.permission.matcher import ExactMatcher, compile_matcher


def _make_shell_rule(
    name: str,
    event: Event,
    command: str,
    **kwargs,
) -> HookRule:
    return HookRule(
        name=name,
        event=event,
        action=Action(type=ActionType.SHELL, shell=ShellAction(command=command)),
        **kwargs,
    )


def _make_prompt_rule(
    name: str,
    event: Event,
    text: str,
    **kwargs,
) -> HookRule:
    return HookRule(
        name=name,
        event=event,
        action=Action(type=ActionType.PROMPT, prompt=PromptAction(text=text)),
        **kwargs,
    )


# ── 基础分派 ──────────────────────────────────────────────────────────────


class TestDispatchBasics:
    @pytest.mark.asyncio
    async def test_empty_rules(self):
        engine = Engine([], [])
        result = await engine.dispatch(Event.STOP, {"event": "Stop"})
        assert result.blocked is False
        assert result.injected_prompts == []

    @pytest.mark.asyncio
    async def test_event_not_matched(self):
        rules = [_make_shell_rule("h", Event.SESSION_START, "exit 0")]
        engine = Engine(rules, [])
        result = await engine.dispatch(Event.STOP, {})
        assert result.blocked is False  # rule 不匹配 Stop 事件

    @pytest.mark.asyncio
    async def test_blocking_stops_subsequent(self):
        """拦截类事件下首个 blocked 的 rule 中断后续。"""
        rules = [
            _make_shell_rule("block", Event.PRE_TOOL_USE,
                             'python -c "import sys; sys.stderr.write(\'x\\n\'); sys.exit(2)"'),
            _make_shell_rule("never-run", Event.PRE_TOOL_USE,
                             'python -c "import sys; sys.stderr.write(\'y\\n\'); sys.exit(2)"'),
        ]
        engine = Engine(rules, [])
        result = await engine.dispatch(Event.PRE_TOOL_USE, {})
        assert result.blocked is True
        assert result.blocking_hook_name == "block"

    @pytest.mark.asyncio
    async def test_non_blocking_event_no_block_even_with_exit_2(self):
        """非拦截事件即使 exit 2 也不 set blocked。"""
        rules = [_make_shell_rule("h", Event.STOP,
                                  'python -c "import sys; sys.exit(2)"')]
        engine = Engine(rules, [])
        result = await engine.dispatch(Event.STOP, {})
        assert result.blocked is False


# ── prompt 注入 ────────────────────────────────────────────────────────────


class TestPromptInjection:
    @pytest.mark.asyncio
    async def test_prompt_collected(self):
        rules = [
            _make_prompt_rule("p1", Event.SESSION_START, "text1"),
            _make_prompt_rule("p2", Event.SESSION_START, "text2"),
        ]
        engine = Engine(rules, [])
        result = await engine.dispatch(Event.SESSION_START, {})
        assert result.injected_prompts == ["text1", "text2"]

    @pytest.mark.asyncio
    async def test_prompt_with_condition(self):
        """条件不满足的 hook 不触发。"""
        cond = Condition(
            mode=CombineMode.ALL_OF,
            atoms=[AtomCondition(field="tool_name",
                                 matcher=ExactMatcher("write_file"))],
        )
        rules = [
            _make_prompt_rule("p", Event.PRE_TOOL_USE, "text",
                              condition=cond),
        ]
        engine = Engine(rules, [])
        # tool_name 不匹配，不触发
        result = await engine.dispatch(
            Event.PRE_TOOL_USE, {"tool_name": "read_file"}
        )
        assert result.injected_prompts == []


# ── only_once ──────────────────────────────────────────────────────────────


class TestOnlyOnce:
    @pytest.mark.asyncio
    async def test_only_once_fires_once(self):
        rules = [
            _make_shell_rule("once", Event.PRE_USER_MESSAGE, "exit 0", only_once=True),
        ]
        engine = Engine(rules, [])
        # 第一次触发
        result = await engine.dispatch(Event.PRE_USER_MESSAGE, {})
        # 检查 _once_fired
        assert "once" in engine._once_fired

        # 第二次不再触发
        engine2 = Engine(rules, [])
        engine2._once_fired.add("once")  # 模拟已触发
        result2 = await engine2.dispatch(Event.PRE_USER_MESSAGE, {})
        assert result2.injected_prompts == []

    @pytest.mark.asyncio
    async def test_reset_clears_once(self):
        rules = [
            _make_shell_rule("once", Event.PRE_USER_MESSAGE, "exit 0", only_once=True),
        ]
        engine = Engine(rules, [])
        await engine.dispatch(Event.PRE_USER_MESSAGE, {})
        assert "once" in engine._once_fired

        await engine.reset_for_new_session()
        assert "once" not in engine._once_fired


# ── async ──────────────────────────────────────────────────────────────────


class TestAsync:
    @pytest.mark.asyncio
    async def test_async_does_not_block(self):
        """async hook 不应进入 blocked 判定。"""
        rules = [
            _make_shell_rule(
                "async-h", Event.POST_TOOL_USE,
                'python -c "import sys; sys.exit(2)"',
                asyncio_mode=True,
            ),
        ]
        engine = Engine(rules, [])
        result = await engine.dispatch(Event.POST_TOOL_USE, {})
        # async 不参与 blocked
        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_async_task_started(self):
        """async hook 起 asyncio Task。"""
        event = asyncio.Event()

        # 使用闭包捕获 event
        async def tracked_run(self_ref, rule, payload, *, blocking):
            event.set()
            from csycode.hook.executor import ExecutionResult
            return ExecutionResult()

        rules = [
            _make_shell_rule("async-h", Event.POST_TOOL_USE, "exit 0",
                             asyncio_mode=True),
        ]
        engine = Engine(rules, [])
        # Monkey-patch 实例方法
        engine._executor.run = tracked_run.__get__(engine._executor)

        await engine.dispatch(Event.POST_TOOL_USE, {})
        # 等待 async task 启动
        await asyncio.wait_for(event.wait(), timeout=2.0)
        assert event.is_set()


# ── 条件求值整合 ──────────────────────────────────────────────────────────


class TestConditionEvaluation:
    @pytest.mark.asyncio
    async def test_condition_blocks_execution(self):
        """条件不匹配时 hook 不执行。"""
        cond = Condition(
            mode=CombineMode.ALL_OF,
            atoms=[AtomCondition(
                field="tool_name",
                matcher=compile_matcher("=write_file", is_command=False),
            )],
        )
        rules = [
            _make_prompt_rule("cond", Event.PRE_TOOL_USE, "should-not-appear",
                              condition=cond),
        ]
        engine = Engine(rules, [])
        result = await engine.dispatch(
            Event.PRE_TOOL_USE, {"tool_name": "read_file"}
        )
        assert result.injected_prompts == []

    @pytest.mark.asyncio
    async def test_condition_allows_execution(self):
        """条件匹配时 hook 执行。"""
        cond = Condition(
            mode=CombineMode.ALL_OF,
            atoms=[AtomCondition(
                field="tool_name",
                matcher=compile_matcher("=write_file", is_command=False),
            )],
        )
        rules = [
            _make_prompt_rule("cond", Event.PRE_TOOL_USE, "should-appear",
                              condition=cond),
        ]
        engine = Engine(rules, [])
        result = await engine.dispatch(
            Event.PRE_TOOL_USE, {"tool_name": "write_file"}
        )
        assert "should-appear" in result.injected_prompts
