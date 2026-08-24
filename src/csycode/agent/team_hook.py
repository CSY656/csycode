"""Team Hook —— Agent 工具与 Team 模块的桥接层。

定义 TeamHook Protocol、TeamSpawnRequest、TeammateContext，
避免 agent 包直接依赖 team 包（避免循环导入）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


# ── TeamHook Protocol ─────────────────────────────────────────────

class TeamHook(Protocol):
    """Agent 工具委托 Team spawn 的接口。

    Manager 实现此 Protocol，AgentTool 通过它委托 team_name 分支。
    """

    async def spawn_teammate(self, req: TeamSpawnRequest) -> str:
        """在 Team 中 spawn 一名队员。

        Args:
            req: spawn 请求参数。

        Returns:
            JSON 字符串描述 spawn 结果。
        """
        ...

    def is_teammate_context(self, ctx: dict | None) -> tuple[str | None, str | None, bool]:
        """判断当前上下文是否在某队员的执行上下文中。

        Args:
            ctx: 上下文 dict。

        Returns:
            (team_name, member_name, is_in_process)
        """
        ...


# ── TeamSpawnRequest ──────────────────────────────────────────────

@dataclass
class TeamSpawnRequest:
    """Agent 工具向 Team Hook 传递的 spawn 参数。"""
    team_name: str
    member_name: str = ""
    subagent_type: str = ""
    model: str = ""
    prompt: str = ""
    description: str = ""
    plan_mode_required: bool = False
    isolation: str = ""  # 本期无用，team spawn 永远走 worktree


# ── TeammateContext ───────────────────────────────────────────────

@dataclass
class IncomingMessage:
    """轻量级 incoming message，避免 agent 包依赖 mailbox.Message。"""
    from_: str
    type: str
    summary: str
    content: str
    payload: dict[str, Any] | None = None


@dataclass
class TeammateContext:
    """队员上下文 —— spawn 时注入到子 Agent。

    Attributes:
        team_name: Team sanitized name。
        member_name: 队员名。
        agent_id: 队员 agent_id。
        worktree_path: worktree 绝对路径。
        backend_type: 后端类型字符串。
        read_unread: 读未读消息的回调（由 team 包注入闭包）。
        mark_read: 标记已读的回调（由 team 包注入闭包）。
        send_message_wake: 发送消息后的唤醒回调。
    """
    team_name: str
    member_name: str
    agent_id: str
    worktree_path: str = ""
    backend_type: str = "in-process"

    # 回调闭包（由 team 包在 spawn 时注入）
    read_unread: Callable[[], Awaitable[tuple[list[int], list[IncomingMessage]]]] | None = None
    mark_read: Callable[[list[int]], Awaitable[None]] | None = None
    send_message_wake: Callable[[str], Awaitable[None]] | None = None


# ── 上下文存取辅助 ────────────────────────────────────────────────

# 使用简单的 dict 作为 context 容器（跟 Agent 的 ctx 参数对齐）
TEAMMATE_CTX_KEY = "__teammate_context__"


def with_teammate_context(ctx: dict, tc: TeammateContext) -> dict:
    """把 TeammateContext 注入到 ctx dict。"""
    ctx[TEAMMATE_CTX_KEY] = tc
    return ctx


def teammate_context_from_ctx(ctx: dict | None) -> TeammateContext | None:
    """从 ctx dict 中提取 TeammateContext。"""
    if ctx is None:
        return None
    return ctx.get(TEAMMATE_CTX_KEY)


# ── 系统提示词附录 ────────────────────────────────────────────────

TEAMMATE_SYSTEM_PROMPT_SUFFIX = """
IMPORTANT: You are running as an agent in a team.
Just writing a response in text is not visible to others
on your team - you MUST use the SendMessage tool.
The user interacts primarily with the team lead.
Your work is coordinated through the task system
and teammate messaging.
"""


def build_team_context_reminder(
    team_name: str,
    member_name: str,
    agent_id: str,
    worktree_path: str,
    members_summary: str = "",
) -> str:
    """构造 <team-context> system reminder。

    Args:
        team_name: Team sanitized name。
        member_name: 队员名。
        agent_id: 队员 agent_id。
        worktree_path: Worktree 绝对路径。
        members_summary: 团队成员摘要文本。

    Returns:
        <team-context> XML 字符串。
    """
    lines = [
        "<team-context>",
        f"team: {team_name}",
        f"你的成员名: {member_name}",
        f"你的 agent_id: {agent_id}",
        f"worktree 目录: {worktree_path}",
    ]
    if members_summary:
        lines.append(f"当前团队成员: {members_summary}")
    lines.append("</team-context>")
    return "\n".join(lines)


def build_incoming_messages_reminder(messages: list[IncomingMessage]) -> str:
    """构造 <incoming-messages> system reminder。

    Args:
        messages: 未读消息列表。

    Returns:
        <incoming-messages> XML 字符串。
    """
    lines = [
        "<incoming-messages>",
        f"收到 {len(messages)} 条新消息:",
    ]
    for i, m in enumerate(messages, 1):
        content_preview = m.content[:200] if m.content else "(无内容)"
        lines.append(
            f"[{i}] 来自 {m.from_} (type={m.type}): {m.summary}"
        )
        lines.append(f"    {content_preview}")

        # plan_approval_response 特殊处理
        if m.type == "plan_approval_response" and m.payload:
            approve = m.payload.get("approve", False)
            feedback = m.payload.get("feedback", "")
            if approve:
                lines.append("    ✅ Lead 已批准计划，权限模式已切换到 default，可以开始执行。")
            else:
                lines.append(f"    ❌ Lead 驳回了计划。反馈: {feedback}")
                lines.append("    请根据反馈调整后重新提交计划。")

    lines.append("</incoming-messages>")
    return "\n".join(lines)


def truncate_for_summary(text: str, max_len: int = 80) -> str:
    """把任务文本截断为 mailbox 消息摘要。

    Args:
        text: 原始任务文本。
        max_len: 最大长度。

    Returns:
        截断后的摘要。
    """
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
