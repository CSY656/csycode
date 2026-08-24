"""Input bar — user text input with command recognition."""

from __future__ import annotations

from textual.containers import Horizontal
from textual.widgets import Input, Static


class InputBar(Horizontal):
    """Bottom input area for user messages and commands.

    Features:
    - Enter to submit, Shift+Enter for newline (via Input default behavior).
    - Recognizes /quit and /clear commands.
    - Can be set to read-only during streaming.
    """

    DEFAULT_CSS = """
    InputBar {
        dock: bottom;
        background: #16213e;
        padding: 1 2;
        border-top: solid #0f3460;
        height: auto;
    }
    InputBar Input {
        background: #1a1a2e;
        color: #e0e0e0;
        border: none;
        width: 1fr;
    }
    InputBar Input:focus {
        border: solid #53d769;
    }
    InputBar .input-hint {
        color: #888;
        padding: 0 1;
        width: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._submit_callback = None
        self._command_callbacks: dict[str, callable] = {}

    def compose(self):
        yield Static(">", classes="input-hint")
        yield Input(placeholder="Type a message... (/quit, /clear)")

    def on_mount(self) -> None:
        """Focus the input when the bar is mounted."""
        self.query_one(Input).focus()

    @property
    def input(self) -> Input:
        """Return the Input widget."""
        return self.query_one(Input)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in the input widget."""
        text = event.value.strip()
        if not text:
            return

        event.input.clear()

        if text.startswith("/"):
            self._handle_command(text)
        elif self._submit_callback is not None:
            self._submit_callback(text)

    def set_submit_callback(self, callback: callable) -> None:
        """Register a callback for when the user submits a message.

        Args:
            callback: Called as ``callback(text: str)`` for non-command input.
        """
        self._submit_callback = callback

    def set_command_callback(self, command: str, callback: callable) -> None:
        """Register a callback for a specific slash command.

        Args:
            command: The command without leading slash, e.g. ``"quit"``.
            callback: Called as ``callback()`` when the command is entered.
        """
        self._command_callbacks[command] = callback

    def set_readonly(self, disabled: bool) -> None:
        """Enable or disable input (used during streaming).

        Args:
            disabled: If True, input is read-only.
        """
        self.input.read_only = disabled
        if disabled:
            self.add_class("readonly")
        else:
            self.remove_class("readonly")
            self.input.focus()

    def _handle_command(self, text: str) -> None:
        """Parse and dispatch a slash command."""
        parts = text.split(maxsplit=1)
        cmd = parts[0][1:]  # Strip leading '/'

        callback = self._command_callbacks.get(cmd)
        if callback is not None:
            callback()
