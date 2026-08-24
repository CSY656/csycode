"""内联审批组件 — 人在回路权限确认对话框。

对齐 mewcode InlinePermissionWidget：工具名 + 原因 + 带编号的选项，
支持方向键导航 + 回车确认 + 数字快捷键。
通过 Textual Message 回调通知 App，由 App resolve Future 解除 Agent 阻塞。
"""

from __future__ import annotations

import uuid

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from csycode.permission import Outcome

# ── 三选一菜单项 ──────────────────────────────────────────────
_APPROVAL_OPTIONS: list[tuple[str, Outcome]] = [
    ("允许（会话内不再询问）", Outcome.ALLOW_ONCE),
    ("永久允许（写入本地配置）", Outcome.ALLOW_FOREVER),
    ("拒绝本次", Outcome.DENY_ONCE),
]


class InlineApprovalWidget(Vertical, can_focus=True):
    """渲染在聊天区域内部的内联权限确认组件。

    对齐 mewcode InlinePermissionWidget：
    - 使用 Static widget + content.update() 替换内容（而非 RichLog 追加）
    - 自带 BINDINGS 处理所有按键（↑↓导航 / Enter确认 / Esc拒绝 / 数字快捷键）
    - 通过 Responded Message 回调 App
    """

    BINDINGS = [
        Binding("up", "cursor_up", "上移", priority=True),
        Binding("k", "cursor_up", "上移 (vim)", priority=True),
        Binding("down", "cursor_down", "下移", priority=True),
        Binding("j", "cursor_down", "下移 (vim)", priority=True),
        Binding("enter", "select", "确认", priority=True),
        Binding("space", "select", "确认", priority=True),
        Binding("escape", "deny", "拒绝", priority=True),
        Binding("1", "select_1", "允许本次", priority=True),
        Binding("2", "select_2", "永久允许", priority=True),
        Binding("3", "select_3", "拒绝本次", priority=True),
        Binding("y", "select_1", "允许本次", priority=True),
        Binding("n", "select_3", "拒绝本次", priority=True),
        Binding("d", "select_3", "拒绝本次", priority=True),
        # 拦截全局快捷键，防止审批期间意外退出/切换模式
        Binding("ctrl+d", "deny", "", priority=True),
        Binding("ctrl+c", "deny", "", priority=True),
        Binding("ctrl+p", "deny", "", priority=True),
    ]

    class Responded(Message):
        """用户做出选择后发出的消息，由 App.on_inline_approval_widget_responded 处理。"""

        def __init__(self, outcome: Outcome) -> None:
            super().__init__()
            self.outcome = outcome

    def __init__(
        self,
        tool_name: str,
        args_preview: str,
        reason: str,
        default_cursor: int = 2,
        **kwargs,
    ) -> None:
        # 每次实例化生成唯一 ID，避免同一轮次多个审批请求时 ID 冲突
        super().__init__(id=f"approval-inline-{uuid.uuid4().hex[:8]}", **kwargs)
        self._tool_name = tool_name
        self._args_preview = args_preview
        self._reason = reason
        self._cursor = default_cursor

    # ── 布局 ──────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        # 使用 class selector 而非固定 ID，避免多实例冲突
        yield Static(self._build_content(), classes="approval-content")

    def on_mount(self) -> None:
        self.focus()
        # 二次确认焦点：在某些 Textual 版本 / 嵌套容器场景中，
        # mount 内的 focus() 可能被后续布局刷新覆盖，用
        # call_after_refresh 确保焦点最终落在此组件上。
        self.call_after_refresh(self.focus)

    # ── 内容构建 ──────────────────────────────────────────────

    def _build_content(self) -> str:
        """构建当前状态的富文本内容字符串。"""
        lines: list[str] = []
        lines.append(
            f"\n  [bold yellow]● {self._tool_name}({self._args_preview})[/bold yellow]\n"
        )
        lines.append(f"    [dim]{self._reason}[/dim]\n")
        lines.append("    [bold]是否继续?[/bold]\n")

        for i, (label, _outcome) in enumerate(_APPROVAL_OPTIONS):
            if i == self._cursor:
                lines.append(
                    f" [bold cyan]❯[/bold cyan] {i + 1}. [bold]{label}[/bold]"
                )
            else:
                lines.append(f"   {i + 1}. [dim]{label}[/dim]")

        lines.append("")
        lines.append("  [dim]↑↓ 选择 · 回车确认 · Esc 取消[/dim]")
        return "\n".join(lines)

    def _refresh(self) -> None:
        """更新 Static 内容（替换而非追加）。"""
        content = self.query_one(".approval-content", Static)
        content.update(self._build_content())

    # ── 动作 ──────────────────────────────────────────────────

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < len(_APPROVAL_OPTIONS) - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        """Enter/Space：提交当前光标所在选项。"""
        _, outcome = _APPROVAL_OPTIONS[self._cursor]
        self.post_message(self.Responded(outcome))

    def action_select_1(self) -> None:
        """快捷键 1 / y：允许本次。"""
        self.post_message(self.Responded(Outcome.ALLOW_ONCE))

    def action_select_2(self) -> None:
        """快捷键 2：永久允许。"""
        self.post_message(self.Responded(Outcome.ALLOW_FOREVER))

    def action_select_3(self) -> None:
        """快捷键 3 / n / d：拒绝本次。"""
        self.post_message(self.Responded(Outcome.DENY_ONCE))

    def action_deny(self) -> None:
        """Esc：拒绝。"""
        self.post_message(self.Responded(Outcome.DENY_ONCE))
