"""Session management — conversation history and provider coordination."""

from __future__ import annotations

from collections.abc import AsyncIterator

from csycode.provider import BaseProvider, Message, StreamDelta


class Session:
    """Manages a single conversation session.

    Holds the message history and coordinates between the TUI
    and the LLM provider for streaming responses.
    """

    def __init__(self, provider: BaseProvider) -> None:
        self._provider = provider
        self._history: list[Message] = []

    @property
    def history(self) -> list[Message]:
        """Return a copy of the current conversation history."""
        return list(self._history)

    def add_user_message(self, content: str) -> None:
        """Append a user message to the conversation history.

        Args:
            content: The user's message text.
        """
        self._history.append(Message(role="user", content=content))

    async def send_to_provider(self) -> AsyncIterator[StreamDelta]:
        """Send the full conversation history to the provider.

        Yields:
            StreamDelta chunks as they arrive from the LLM.

        After the stream completes, the full assistant response is
        assembled from all text deltas and appended to history.
        """
        text_parts: list[str] = []

        async for delta in self._provider.chat_stream(self._history):
            if delta.type == "text":
                text_parts.append(delta.content)
            yield delta

        # Assemble and store the complete assistant response
        full_response = "".join(text_parts)
        if full_response:
            self._history.append(Message(role="assistant", content=full_response))

    def clear(self) -> None:
        """Clear all conversation history."""
        self._history.clear()
