"""自动补全菜单状态机 + 渲染 + 键位处理。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from textual import events

if TYPE_CHECKING:
    from csycode.command.command import Command
    from csycode.command.registry import Registry

MAX_ROWS = 8


@dataclass(slots=True)
class CompletionMenu:
    """自动补全菜单状态机。

    Attributes:
        items: 当前候选 Command 列表，已按 name 字典序。
        cursor: 当前高亮索引。
        offset: 滚动偏移（候选数 > MAX_ROWS 时）。
        active: 是否激活。
    """

    items: list[Command] = field(default_factory=list)
    cursor: int = 0
    offset: int = 0
    active: bool = False

    def update(self, input_text: str, reg: Registry) -> None:
        """根据当前输入刷新候选。"""
        text = input_text.strip()
        if not text.startswith("/"):
            self.hide()
            return

        self.items = reg.prefix_match(text)
        self.active = True

        # 夹紧 cursor / offset
        if self.items:
            if self.cursor >= len(self.items):
                self.cursor = len(self.items) - 1
            if self.offset > self.cursor:
                self.offset = self.cursor
            if self.offset + MAX_ROWS <= self.cursor:
                self.offset = max(0, self.cursor - MAX_ROWS + 1)
        else:
            self.cursor = 0
            self.offset = 0

    def move_up(self) -> None:
        """高亮上移一行。"""
        if not self.items:
            return
        self.cursor = max(0, self.cursor - 1)
        if self.cursor < self.offset:
            self.offset = self.cursor

    def move_down(self) -> None:
        """高亮下移一行。"""
        if not self.items:
            return
        self.cursor = min(len(self.items) - 1, self.cursor + 1)
        if self.cursor >= self.offset + MAX_ROWS:
            self.offset = self.cursor - MAX_ROWS + 1

    def selected(self) -> Command | None:
        """返回当前高亮的 Command。"""
        if not self.items:
            return None
        return self.items[self.cursor]

    def hide(self) -> None:
        """关闭菜单并重置状态。"""
        self.active = False
        self.items = []
        self.cursor = 0
        self.offset = 0

    def render(self, width: int) -> str:
        """渲染补全菜单为 rich markup 字符串。

        Returns:
            多行 markup 字符串，供 Static widget 写入。
        """
        if not self.active:
            return ""

        if not self.items:
            return "[dim]无匹配[/]"

        lines: list[str] = []
        visible = self.items[self.offset : self.offset + MAX_ROWS]

        # 上方溢出提示
        if self.offset > 0:
            lines.append(f"[dim]↑ {self.offset} more[/]")

        max_name = max(len(c.name) for c in visible) if visible else 0
        w = max_name + 3

        for i, cmd in enumerate(visible):
            actual_index = self.offset + i
            name_part = f"/{cmd.name.ljust(w)}"
            desc = cmd.description
            full_line = f"{name_part}{desc}"

            if actual_index == self.cursor:
                lines.append(f"[bold reverse] {full_line} [/]")
            else:
                lines.append(f"  [dim]{full_line}[/]")

        # 下方溢出提示
        remaining = len(self.items) - self.offset - len(visible)
        if remaining > 0:
            lines.append(f"[dim]↓ {remaining} more[/]")

        return "\n".join(lines)


# ── App 方法（mixin 形式，在 app.py 中绑定）─────────────────────────


async def _handle_completion_key(self, event: events.Key) -> bool:
    """处理补全菜单键位。

    Returns:
        True 表示键已被菜单消费，False 表示透传 TextArea。
    """
    if not self.completion.active:
        return False

    key = event.key

    if key == "up":
        self.completion.move_up()
        self._render_completion()
        event.stop()
        return True
    elif key == "down":
        self.completion.move_down()
        self._render_completion()
        event.stop()
        return True
    elif key == "escape":
        self.completion.hide()
        self._render_completion()
        event.stop()
        return True
    elif key in ("enter", "tab"):
        sel = self.completion.selected()
        if sel is not None:
            await self._execute_selected_completion(sel)
            event.stop()
            return True
        else:
            # 零匹配时：Tab/ESC 关菜单，Enter 走未命中
            self.completion.hide()
            self._render_completion()
            if key == "enter":
                return False  # 让 submit 走未命中分支
            event.stop()
            return True

    return False


async def _execute_selected_completion(self, sel: Command) -> None:
    """执行补全菜单选中的命令。"""
    from textual.widgets import TextArea

    # 把命令文本写入输入框并提交
    input_widget = self.query_one("#input", TextArea)
    input_widget.text = "/" + sel.name
    # 隐藏补全菜单
    self.completion.hide()
    self._render_completion()
    # 走 dispatch_slash 分发
    await self.dispatch_slash("/" + sel.name)
    input_widget.text = ""


def _sync_completion_from_input(self) -> None:
    """每次 TextArea 内容变化后同步补全菜单。"""
    from textual.widgets import TextArea

    if self.cmd_registry is None:
        return
    # 多行输入 → 强制关闭补全
    input_widget = self.query_one("#input", TextArea)
    text = input_widget.text
    if "\n" in text:
        self.completion.hide()
        self._render_completion()
        return
    self.completion.update(text, self.cmd_registry)
    self._render_completion()


def _render_completion(self) -> None:
    """刷新补全菜单 Static widget。"""
    from textual.widgets import Static

    try:
        comp_widget = self.query_one("#completion", Static)
        if self.completion.active:
            comp_widget.update(self.completion.render(self.size.width))
        else:
            comp_widget.update("")
    except Exception:
        pass
