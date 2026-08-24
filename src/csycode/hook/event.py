"""Hook 生命周期事件定义。

ch12: 11 个事件枚举，对应 Agent 生命周期的固定时刻。
"""

from __future__ import annotations

import enum


class Event(str, enum.Enum):
    """Hook 生命周期事件（11 个）。

    YAML 字面量（SessionStart 等）与 enum value 直接对应。
    """

    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    SESSION_RESUME = "SessionResume"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    PRE_USER_MESSAGE = "PreUserMessage"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    NOTIFICATION = "Notification"


# 拦截类事件集合（PreToolUse / UserPromptSubmit）
BLOCKING_EVENTS: frozenset[Event] = frozenset({
    Event.PRE_TOOL_USE,
    Event.USER_PROMPT_SUBMIT,
})


def is_blocking(e: Event) -> bool:
    """判定事件是否属于拦截类（可表达拒绝信号）。"""
    return e in BLOCKING_EVENTS


def parse_event(s: str) -> Event | None:
    """从字符串解析 Event 枚举。

    Args:
        s: 事件名字符串（如 "SessionStart"）。

    Returns:
        Event 枚举值，或 None（未知名）。
    """
    try:
        return Event(s)
    except ValueError:
        return None
