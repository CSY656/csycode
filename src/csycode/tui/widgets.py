"""自定义 Textual 控件 — SubmitTextArea 等."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable

from textual import events
from textual.binding import Binding
from textual.widgets import TextArea


class SubmitTextArea(TextArea):
    """在 Enter 时触发提交回调的 TextArea 子类.

    按键处理分层：
    1. _on_key（最先触发）— 处理需要上下文判断的复杂按键：
       - 补全菜单激活时：Enter/Tab/Up/Down/Esc → 操作补全菜单
       - Ctrl+↑/Ctrl+↓ → 历史导航
       - Shift+Tab/Ctrl+P → 模式切换
       - Ctrl+C/Ctrl+D → 中断/退出
    2. BINDINGS（_on_key 之后触发）— 声明式绑定：
       - Enter → action_submit（提交消息）
       - Shift+Enter/Ctrl+Enter/Ctrl+J → action_newline（插入换行）
         （对齐 mewcode-python: Enter 提交，Shift+Enter/Ctrl+J 换行）

    关键设计决策（对齐 mewcode-python）：
    - action_newline 直接调用 self.insert("\\n")，而非 super()._on_key()，
      因为 TextArea._on_key 对非纯 enter 键不会正确插入换行
    - 补全拦截在 _on_key 层阻止事件冒泡，防止 BINDINGS 抢先处理
    """

    # ── 声明式按键绑定（对齐 mewcode-python ChatInput.BINDINGS） ──────
    # 注意：Shift+Enter / Ctrl+Enter 仅在支持 Kitty keyboard protocol
    # 的终端上有效（Kitty、WezTerm、Windows Terminal ≥ 1.22）。
    # Ctrl+J 是唯一所有终端通用的换行键（本质就是 \n 字符）。
    BINDINGS = [
        # Enter 提交（priority=True 确保优先于 TextArea 默认行为）
        Binding("enter", "submit", "提交", priority=True),
        # Ctrl+J → 插入换行（所有终端通用，优先展示）
        Binding("ctrl+j", "newline", "换行", priority=True),
        # Shift+Enter / Ctrl+Enter → 插入换行（仅 Kitty 协议终端有效）
        Binding("shift+enter", "newline", "换行", priority=True, show=False),
        Binding("ctrl+enter", "newline", "换行", priority=True, show=False),
    ]

    def __init__(
        self,
        on_submit: Callable[[str], None],
        on_history_up: Callable[[], str | None] | None = None,
        on_history_down: Callable[[], str | None] | None = None,
        on_cycle_mode: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._on_submit = on_submit
        self._on_history_up = on_history_up
        self._on_history_down = on_history_down
        self._on_cycle_mode = on_cycle_mode

    # ── BINDINGS action 方法 ─────────────────────────────────────────

    def action_submit(self) -> None:
        """Enter 键 → 提交输入框内容（对齐 mewcode-python ChatInput.action_submit）."""
        self._on_submit(self.text)

    def action_newline(self) -> None:
        """修饰键+Enter / Ctrl+J → 在光标处插入换行符.

        直接调用 self.insert("\\n") 而非 super()._on_key()，
        因为 TextArea._on_key 对 shift+enter / ctrl+enter 等
        修饰键组合不会正确插入换行。
        对齐 mewcode-python ChatInput.action_newline。
        """
        self.insert("\n")

    # ── _on_key 覆写：BINDINGS 之前的最早拦截层 ──────────────────────

    async def _on_key(self, event: events.Key) -> None:
        """在 BINDINGS 之前拦截补全菜单按键、历史导航、模式切换等."""
        # ── 补全菜单激活时的按键拦截（必须在 BINDINGS 之前） ──
        if self._completion_active():
            if event.key in ("up", "down", "escape"):
                event.stop()
                event.prevent_default()
                self._completion_handle_key(event.key)
                return
            if event.key in ("enter", "tab"):
                if self._completion_has_selection():
                    # 有选中项 → 执行补全，阻止 BINDINGS 的 submit
                    event.stop()
                    event.prevent_default()
                    self._completion_execute_selected()
                    return
                else:
                    # 零匹配 → 关闭弹窗
                    self._completion_hide()
                    if event.key == "tab":
                        event.stop()
                        event.prevent_default()
                        return
                    # Enter 零匹配 → 不阻止事件，BINDINGS 会调用 action_submit
                    return

        # ── Enter 家族按键：阻止 TextArea 默认行为，交给 BINDINGS ──
        # event.key == "enter" 时 TextArea._on_key 会插入换行，必须阻止。
        # 对 shift+enter / ctrl+enter 等修饰键组合，TextArea 行为未知，
        # 统一阻止后由 BINDINGS 分发到 action_submit / action_newline。
        #
        # 注意：部分终端不支持区分修饰键组合的 Enter。
        # Ctrl+J (发送 \n) 是所有终端通用的换行键，已在 BINDINGS 中绑定。
        # 如果终端不支持 Shift+Enter / Ctrl+Enter，请使用 Ctrl+J 换行。
        if event.key in ("enter", "shift+enter", "ctrl+enter", "ctrl+j"):
            # 辅助检测：部分终端上 Ctrl+Enter 发送 \n 但被识别为 "enter"
            # 此时 event.character 仍然是 \n，我们可以据此判断
            if event.key == "enter" and event.character == "\n":
                # 这很可能是 Ctrl+Enter 发送的 \n → 插入换行
                self.action_newline()
                event.stop()
                event.prevent_default()
                return
            # 阻止 TextArea 默认行为，但不 stop 事件 → BINDINGS 仍会触发
            event.prevent_default()
            return

        # ── Shift+Tab / Ctrl+P → 模式切换 ──
        if event.key in ("shift+tab", "ctrl+p"):
            if self._on_cycle_mode is not None:
                event.stop()
                event.prevent_default()
                result = self._on_cycle_mode()
                if inspect.iscoroutine(result):
                    asyncio.create_task(result)
            return

        # ── Ctrl+C / Ctrl+D → 直接触发 App 层动作 ──
        if event.key == "ctrl+d":
            event.stop()
            event.prevent_default()
            await self.app.action_quit()
            return
        if event.key == "ctrl+c":
            event.stop()
            event.prevent_default()
            await self.app.action_interrupt()
            return

        # ── Ctrl+↑ / Ctrl+↓ → 历史导航 ──
        if event.key == "ctrl+up" and self._on_history_up is not None:
            event.stop()
            event.prevent_default()
            text = self._on_history_up()
            if text is not None:
                self.text = text
                self.move_cursor(self.document.end)
            return

        if event.key == "ctrl+down" and self._on_history_down is not None:
            event.stop()
            event.prevent_default()
            text = self._on_history_down()
            if text is not None:
                self.text = text
                self.move_cursor(self.document.end)
            return

        # 其他按键 → 父类默认行为（或 BINDINGS 处理）
        await super()._on_key(event)

    # ── 补全菜单桥接方法（通过 App 访问） ────────────────────────────

    def _completion_active(self) -> bool:
        """检查补全菜单是否激活."""
        app = self.app
        try:
            return app.completion.active
        except AttributeError:
            return False

    def _completion_has_selection(self) -> bool:
        """检查补全菜单是否有选中项."""
        app = self.app
        try:
            return app.completion.selected() is not None
        except AttributeError:
            return False

    def _completion_handle_key(self, key: str) -> None:
        """处理补全菜单导航键（up/down/escape）."""
        app = self.app
        try:
            if key == "up":
                app.completion.move_up()
            elif key == "down":
                app.completion.move_down()
            elif key == "escape":
                app.completion.hide()
            app._render_completion()
        except AttributeError:
            pass

    def _completion_execute_selected(self) -> None:
        """执行补全菜单当前选中项."""
        app = self.app
        try:
            sel = app.completion.selected()
            if sel is not None:
                asyncio.create_task(app._execute_selected_completion(sel))
        except AttributeError:
            pass

    def _completion_hide(self) -> None:
        """关闭补全菜单."""
        app = self.app
        try:
            app.completion.hide()
            app._render_completion()
        except AttributeError:
            pass
