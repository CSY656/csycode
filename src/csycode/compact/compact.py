"""manage_context 主入口 —— 编排两层压缩调用。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .layer2 import auto_compact

if TYPE_CHECKING:
    from csycode.conversation import Conversation
    from csycode.llm import Message, Provider, ToolDefinition

    from .state import (
        CompactCircuitBreaker,
        ContentReplacementState,
        RecoveryState,
        SessionContext,
    )

_logger = logging.getLogger(__name__)


class TriggerKind(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"


@dataclass
class ManageInput:
    conv: Conversation
    provider: Provider
    model: str
    context_window: int
    tool_defs: list[ToolDefinition]
    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    trigger: TriggerKind = TriggerKind.AUTO
    budget_messages: list[Message] | None = None


@dataclass
class ManageOutput:
    before_tokens: int
    after_tokens: int


async def manage_context(in_: ManageInput) -> ManageOutput:
    """Agent 每轮请求前调用的上下文管理入口。

    委托给 auto_compact。
    """
    manual = in_.trigger != TriggerKind.AUTO

    result = await auto_compact(
        conversation=in_.conv,
        provider=in_.provider,
        model=in_.model,
        context_window=in_.context_window,
        replacement=in_.replacement,
        recovery=in_.recovery,
        auto_tracking=in_.auto_tracking,
        session=in_.session,
        tool_defs=in_.tool_defs,
        manual=manual,
        budget_messages=in_.budget_messages,
    )

    if result is None:
        return ManageOutput(
            before_tokens=in_.conv.current_tokens(),
            after_tokens=in_.conv.current_tokens(),
        )

    before_tokens, after_tokens = result
    return ManageOutput(before_tokens=before_tokens, after_tokens=after_tokens)
