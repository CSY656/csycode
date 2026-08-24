"""Abstract base for all LLM providers."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from csycode.config import ProviderConfig


@dataclass
class Message:
    """A single chat message."""

    role: Literal["user", "assistant"]
    content: str


@dataclass
class StreamDelta:
    """A single incremental chunk from a streaming response."""

    type: Literal["text", "thinking"]
    content: str


class BaseProvider(ABC):
    """Abstract interface for all LLM backends.

    Each concrete provider implements ``chat_stream()`` to handle
    protocol-specific API calls and SSE parsing.
    """

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    @property
    def config(self) -> ProviderConfig:
        """The provider configuration."""
        return self._config

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[Message],
    ) -> AsyncIterator[StreamDelta]:
        """Send messages to the LLM and stream the response.

        Args:
            messages: Complete conversation history.

        Yields:
            StreamDelta chunks as they arrive from the API.
        """
        ...
