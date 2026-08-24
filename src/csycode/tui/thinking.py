"""Collapsible thinking panel for Claude's extended thinking display."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Static


class ThinkingPanel(Vertical):
    """A collapsible panel showing the model's extended thinking process.

    Default state is collapsed, showing only a label like "🤔 Thinking".
    The user can expand it to view the full thinking content.
    """

    DEFAULT_CSS = """
    ThinkingPanel {
        display: block;
    }
    ThinkingPanel .thinking-label {
        color: #f0a500;
        text-style: italic;
        padding: 0 2;
    }
    ThinkingPanel .thinking-content {
        color: #a0a0c0;
        text-style: italic;
        padding: 1 2;
        display: none;
    }
    ThinkingPanel.expanded .thinking-content {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._thinking_parts: list[str] = []
        self._expanded = False

    def compose(self):
        yield Static("🤔 Thinking", classes="thinking-label")

    def add_delta(self, content: str) -> None:
        """Append a chunk of thinking text.

        Args:
            content: The thinking delta text from the API.
        """
        self._thinking_parts.append(content)

    def get_content(self) -> str:
        """Get the full thinking text accumulated so far."""
        return "".join(self._thinking_parts)

    def is_expanded(self) -> bool:
        """Return whether the panel is currently expanded."""
        return self._expanded

    def toggle(self) -> None:
        """Toggle the panel between collapsed and expanded states."""
        self._expanded = not self._expanded

        # Remove old content widget if any
        for child in list(self.children):
            if "thinking-content" in str(child.classes):
                child.remove()

        self.remove_class("expanded")

        if self._expanded:
            self.add_class("expanded")
            content = self.get_content()
            label = self.query_one(".thinking-label", Static)
            if label:
                label.update(f"🤔 Thinking (expanded)")
            self.mount(Static(content, classes="thinking-content"))
        else:
            label = self.query_one(".thinking-label", Static)
            if label:
                label.update(f"🤔 Thinking")

    def has_content(self) -> bool:
        """Return True if any thinking content has been accumulated."""
        return len(self._thinking_parts) > 0
