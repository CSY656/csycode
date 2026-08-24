"""队员 Loop incoming-messages 注入。

在队员 Agent Loop 每轮迭代开头（调 LLM 前），
检查是否有 TeammateContext，若有则读未读消息并注入 reminder。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from csycode.agent.team_hook import (
    teammate_context_from_ctx,
    build_incoming_messages_reminder,
)

if TYPE_CHECKING:
    from csycode.agent.loop import Agent

log = logging.getLogger(__name__)


async def ingest_team_mailbox(
    agent: Agent,
    ctx: dict | None,
    pending_reminders: list[str],
) -> bool:
    """在 Agent Loop 迭代头部检查并注入邮箱消息。

    若当前 Agent 有 TeammateContext，则：
    1. 读取未读消息
    2. 构造 <incoming-messages> reminder 追加到 pending_reminders
    3. 标记已读
    4. 处理 plan_approval_response：切换权限模式

    Args:
        agent: 当前 Agent 实例。
        ctx: 执行上下文。
        pending_reminders: 待注入的 system reminders 列表（原地修改）。

    Returns:
        True 如果有新消息被注入。
    """
    tc = teammate_context_from_ctx(ctx)
    if tc is None:
        return False

    if tc.read_unread is None:
        return False

    try:
        indices, messages = await tc.read_unread()
    except Exception as e:
        log.warning("队员 %s 读邮箱失败: %s", tc.member_name, e)
        return False

    if not messages:
        return False

    # 构造 reminder
    reminder = build_incoming_messages_reminder(messages)
    pending_reminders.append(reminder)

    # 标记已读
    if tc.mark_read and indices:
        try:
            await tc.mark_read(indices)
        except Exception as e:
            log.warning("队员 %s 标记已读失败: %s", tc.member_name, e)

    # 处理 plan_approval_response
    for m in messages:
        if m.type == "plan_approval_response" and m.payload:
            if m.payload.get("approve", False):
                # 切换权限模式到 default
                try:
                    agent.set_permission_mode(0)  # DEFAULT
                    log.info(
                        "队员 %s: plan 已批准，权限模式切换到 default",
                        tc.member_name,
                    )
                except Exception as e:
                    log.warning("切换权限模式失败: %s", e)

    return True
