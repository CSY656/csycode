"""子 Agent 的 RunToCompletion 循环实现 —— 对齐 mewcode agent.py。

提供 _run_to_completion_impl，由 Agent.run_to_completion 调用。
与主循环 run 共用 _run_impl 的大部分逻辑，区别：
- 不通过队列返回事件（内部消费），最终返回 final_text
- max_turns 由 self._max_turns 决定（若 0 则用全局 MAX_ITERATIONS）
- 不触发 memory update / 不触发 plan mode reminder
- 子 Agent 使用独立的 Conversation（不污染主对话）
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.conversation import Conversation

_logger = logging.getLogger(__name__)


class MaxTurnsReached(Exception):
    """子 Agent 触达 max_turns 上限时抛出。

    Attributes:
        final_text: 最后一条 assistant 文本（可能为空）。
    """

    def __init__(self, final_text: str = "") -> None:
        super().__init__(f"max_turns reached: {final_text[:100]}")
        self.final_text = final_text


async def _run_to_completion_impl(
    agent,
    conv: "Conversation",
    task: str,
    events_queue: "asyncio.Queue | None" = None,
) -> str:
    """子 Agent 跑到底循环的内部实现。

    参数:
        agent: Agent 实例（已设置好 sub-agent 参数）。
        conv: 子对话（可能已预装填 fork 消息）。
        task: 任务文本。若非空则作为 user 消息追加；若空则假设 conv 已含任务。
        events_queue: 可选的外部事件队列，用于转发 Text/Tool/Approval 事件。

    返回:
        最后一条 assistant 文本。

    异常:
        MaxTurnsReached: 触达 max_turns 上限。
        asyncio.CancelledError: 被取消（透传）。
    """
    from csycode.llm import PromptTooLongError
    from csycode.agent.events import (
        LoopEnd,
    )

    # 追加 task（若 conv 尚未包含任务）
    if task:
        conv.add_user(task)

    # 注入环境
    from csycode import prompt
    env = prompt.gather_environment(agent._version, agent._provider.model)
    conv.inject_environment(agent._build_env_text(env))

    # 注入 system_prompt / instructions
    sys_prompt = agent._system_prompt or agent._instructions_content
    if sys_prompt:
        conv.inject_long_term_memory(sys_prompt, "")

    stable_system = prompt.build_system_prompt()

    max_turns = agent._max_turns if agent._max_turns > 0 else agent._config.max_iterations
    turn_count = 0

    while turn_count < max_turns:
        turn_count += 1

        # 发送进度事件
        if events_queue is not None:
            try:
                events_queue.put_nowait(
                    {"type": "progress", "round": turn_count, "max": max_turns}
                )
            except asyncio.QueueFull:
                pass

        # 获取工具定义
        from csycode.permission import Mode as PMode
        mode = agent._permission_mode or PMode.DEFAULT
        tools = agent._get_tools(mode)
        defs = tools if tools else []

        # ── Layer 1: 工具结果 budget ──
        budget_msgs = agent._apply_tool_result_budget(conv.messages())
        conv.replace_history(budget_msgs)

        # ── 流式收集 ──
        from .collector import StreamCollector
        from csycode.llm import Request, System

        collector = StreamCollector(agent._provider)
        stream_error = None

        try:
            async for event in collector.collect(
                Request(
                    messages=conv.messages(),
                    tools=defs,
                    system=System(
                        stable=stable_system,
                        environment=env.render(),
                    ),
                    reminder="",
                )
            ):
                # 转发事件到外部队列
                if events_queue is not None:
                    try:
                        if hasattr(event, "text"):
                            events_queue.put_nowait({"type": "text", "text": event.text})
                    except asyncio.QueueFull:
                        pass

                if isinstance(event, LoopEnd):
                    return event.final_text
        except asyncio.CancelledError:
            raise
        except Exception as e:
            stream_error = e

        result = collector.last_result
        error = stream_error or (result.error if result else None)

        if error is not None:
            if isinstance(error, PromptTooLongError):
                _logger.info("子 Agent PTL 错误，中止")
            return ""  # 空结果

        # 更新 token
        if result.usage is not None:
            agent._total_input_tokens += result.usage.input_tokens
            agent._total_output_tokens += result.usage.output_tokens

        if not result.tool_calls:
            # 模型不再调工具 → 结束
            conv.add_assistant(result.text)
            return result.text

        conv.add_assistant_with_tools(result.text, result.tool_calls)

        # 执行工具调用（权限感知）
        tool_results: list[tuple[str, str]] = []

        for i, tc in enumerate(result.tool_calls):
            tool = agent._tool_registry.get(tc.name)
            if tool is None:
                tool_results.append((tc.id, f"未知工具: {tc.name}"))
                continue

            # 权限检查
            if agent._engine is not None:
                decision, reason = agent._engine.check(
                    mode, tc, tool.is_concurrency_safe
                )
            else:
                from csycode.permission import Decision as PD
                decision, reason = PD.ALLOW, ""

            if decision.name == "DENY":
                tool_results.append((tc.id, reason or "denied"))
                continue

            if decision.name == "ASK":
                # dontAsk 短路
                if agent._dont_ask:
                    pass  # 放行
                elif agent._approval_upgrader is not None:
                    # 升级到父 TUI
                    req, respond = agent._prepare_approval(tc, reason or "")
                    outcome, ok = await agent._approval_upgrader(req)
                    if not ok:
                        # 走默认路径：直接 Allow（子 Agent 内无 HITL）
                        pass
                    else:
                        from csycode.permission import Outcome
                        if outcome == Outcome.DENY_ONCE:
                            tool_results.append((tc.id, "用户拒绝"))
                            continue
                        elif outcome in (Outcome.ALLOW_ONCE, Outcome.ALLOW_FOREVER):
                            # 会话级缓存：子 Agent 审批后也加入 session_allowed
                            if agent._engine is not None:
                                try:
                                    agent._engine.session_allow_tc(tc)
                                except Exception:
                                    pass
                            if outcome == Outcome.ALLOW_FOREVER and agent._engine is not None:
                                try:
                                    agent._engine.persist_local_allow(tc)
                                except Exception:
                                    pass
                else:
                    # 子 Agent 内无 HITL 通道，dontAsk 关闭 → 放行
                    pass

            # 执行工具
            try:
                r = await tool.execute(**tc.arguments)
            except Exception as e:
                tool_results.append((tc.id, f"工具执行异常: {e}"))
                continue

            # ch08: 截断/落盘处理
            call_id = tc.id
            content, _, _ = agent._maybe_persist_or_truncate(call_id, r.content)
            tool_results.append((call_id, content))

            # 转发工具事件
            if events_queue is not None:
                try:
                    events_queue.put_nowait({
                        "type": "tool",
                        "name": tc.name,
                        "success": r.success,
                    })
                except asyncio.QueueFull:
                    pass

        conv.add_tool_results(tool_results)

    # 触达 max_turns
    last_text = ""
    for msg in reversed(conv.messages()):
        if msg.role == "assistant" and msg.content:
            last_text = msg.content
            break

    raise MaxTurnsReached(last_text)
