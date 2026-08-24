"""Chat view — renders the conversation history as a scrollable list."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static


class ChatView(VerticalScroll):
    """Scrollable view displaying the conversation history.

    Each message is rendered as a widget with CSS classes that
    distinguish user messages, assistant responses, and errors.
    """

    def __init__(self) -> None:
        super().__init__()
        self._current_assistant_widget: Markdown | None = None
        self._current_assistant_text: list[str] = []

    def add_user_message(self, content: str) -> None:
        """Display a user message in the chat.

        Args:
            content: The user's message text.
        """
        widget = Static(f"You:\n{content}", classes="user-message")
        self.mount(widget)
        self.scroll_end(animate=False)

    def begin_streaming_reply(self) -> None:
        """Prepare for a streaming assistant response.

        Creates an empty placeholder that will be updated
        as deltas arrive via ``append_to_current()``.
        """
        self._current_assistant_text = []
        self._current_assistant_widget = Markdown("", classes="assistant-message")
        self.mount(self._current_assistant_widget)

    def append_to_current(self, content: str) -> None:
        """Append text to the current streaming assistant response.

        Args:
            content: The text delta to append.
        """
        self._current_assistant_text.append(content)
        if self._current_assistant_widget is not None:
            full_text = "".join(self._current_assistant_text)
            self._current_assistant_widget.update(full_text)
        self.scroll_end(animate=False)

    def finalize_reply(self) -> None:
        """Mark the current streaming reply as complete."""
        self._current_assistant_widget = None
        self._current_assistant_text = []

    def add_error(self, message: str) -> None:
        """Display an error message in the chat.

        Args:
            message: The error text to display.
        """
        widget = Static(f"❌ {message}", classes="error-message")
        self.mount(widget)
        self.scroll_end(animate=False)

    def add_info(self, message: str) -> None:
        """Display an informational message in the chat.

        Args:
            message: The info text (e.g., "Conversation cleared").
        """
        widget = Static(message, classes="info-message")
        self.mount(widget)
        self.scroll_end(animate=False)

    def clear(self) -> None:
        """Remove all messages from the chat view."""
        self.remove_children()
        self._current_assistant_widget = None
        self._current_assistant_text = []
