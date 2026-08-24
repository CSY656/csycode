"""上下文管理子包 —— csycode.compact。

提供两层压缩 + 恢复 + 手动/紧急入口的完整实现。
对齐 mewcode 的 context 包架构。
"""

from __future__ import annotations

from .compact import ManageInput, ManageOutput, TriggerKind, manage_context
from .layer1 import offload_and_snip
from .layer2 import (
    auto_compact,
    compute_compact_threshold,
    group_by_user_turn,
    pick_recent_tail,
    should_auto_compact,
)
from .state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    FileReadRecord,
    RecoveryState,
    SessionContext,
    SkillInvocationRecord,
    new_session_context,
)
from .token import estimate_tokens

__all__ = [
    "CompactCircuitBreaker",
    "ContentReplacementState",
    "FileReadRecord",
    "ManageInput",
    "ManageOutput",
    "RecoveryState",
    "SessionContext",
    "TriggerKind",
    "auto_compact",
    "compute_compact_threshold",
    "estimate_tokens",
    "group_by_user_turn",
    "manage_context",
    "new_session_context",
    "offload_and_snip",
    "pick_recent_tail",
    "should_auto_compact",
]
