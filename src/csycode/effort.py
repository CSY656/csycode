"""统一管理思考强度等级。"""

from __future__ import annotations

from typing import Literal


ReasoningEffort = Literal["low", "medium", "high", "xhigh"]

REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "low",
    "medium",
    "high",
    "xhigh",
)
DEFAULT_REASONING_EFFORT: ReasoningEffort = "high"


def parse_reasoning_effort(value: str) -> ReasoningEffort | None:
    """标准化并校验用户输入的思考强度等级。"""
    normalized = value.strip().lower()
    if normalized in REASONING_EFFORTS:
        return normalized  # type: ignore[return-value]
    return None


def reasoning_effort_help() -> str:
    """返回 /effort 命令的统一用法说明。"""
    levels = "|".join(REASONING_EFFORTS)
    return f"用法: /effort <{levels}>"
