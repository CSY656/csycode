"""Agent 会话运行时状态容器。

ch10: 将会话生命周期状态从 Agent 中提取出来，方便 /clear 等操作时一次性重置。
ch12: 添加 pending_reminders 和 hook_engine 字段，支持 Hook 系统的 prompt 注入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from csycode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)

if TYPE_CHECKING:
    from csycode.compact.state import SessionContext
    from csycode.hook.engine import Engine


class SessionRuntime:
    """Agent 会话运行时状态。

    集中管理 compact 相关的子状态、会话上下文、回合计数，
    以及 ch12 的 Hook prompt 注入队列。
    """

    def __init__(self, workspace: str) -> None:
        """创建新的会话运行时状态。

        Args:
            workspace: 工作目录路径，用于初始化 SessionContext。
        """
        self.replacement = ContentReplacementState()
        self.recovery = RecoveryState()
        self.auto_tracking = CompactCircuitBreaker()
        self.session: SessionContext = new_session_context(workspace)
        self.turn_count: int = 0

        # ── ch12: Hook 系统 ──
        self.hook_engine: "Engine | None" = None
        """Hook 运行时引擎引用（由 App 注入）。"""
        self.pending_reminders: list[str] = []
        """待注入的 prompt 文本队列（每轮取出后清空）。"""

    def reset_for_new_session(self, ses_ctx: SessionContext) -> None:
        """原子重置所有 compact 子状态和会话计数，指向新的 SessionContext。

        ch12: 同时清空 pending_reminders 并调用 hook_engine.reset_for_new_session。

        Args:
            ses_ctx: 新会话的 SessionContext。
        """
        self.replacement = ContentReplacementState()
        self.recovery = RecoveryState()
        self.auto_tracking = CompactCircuitBreaker()
        self.session = ses_ctx
        self.turn_count = 0

        # ch12: 清空 reminder 队列
        self.pending_reminders.clear()

    # ── ch12: Reminder 管理 ────────────────────────────────────────────

    def append_reminders(self, prompts: list[str]) -> None:
        """追加 prompt 文本到待注入队列。

        Args:
            prompts: prompt 文本列表（通常来自 Hook dispatch 结果）。
        """
        self.pending_reminders.extend(prompts)

    def take_reminders(self) -> list[str]:
        """取出并清空当前的所有待注入 prompt 文本。

        Returns:
            本轮待注入的 prompt 列表（调用后队列清空）。
        """
        result = self.pending_reminders.copy()
        self.pending_reminders.clear()
        return result
