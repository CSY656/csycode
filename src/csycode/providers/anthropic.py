"""Anthropic Messages API provider with SSE streaming and extended thinking."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from csycode.config import ProviderConfig
from csycode.provider import BaseProvider, Message, StreamDelta
from csycode.registry import register_provider


class AnthropicError(Exception):
    """Raised when the Anthropic API returns an error."""
    pass


class AnthropicProvider(BaseProvider):
    """Provider for the Anthropic Messages API.

    Supports streaming via SSE and extended thinking when enabled in config.
    """

    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._base_url = config.base_url.rstrip("/")
        self._api_key = config.api_key
        self._model = config.model
        self._thinking = config.thinking

    async def chat_stream(
        self,
        messages: list[Message],
    ) -> AsyncIterator[StreamDelta]:
        """Send messages to Anthropic and stream the response."""
        url = f"{self._base_url}/v1/messages"

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        body = self._build_request_body(messages)

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            try:
                async with client.stream(
                    "POST", url, json=body, headers=headers
                ) as response:
                    if response.status_code != 200:
                        await self._handle_error_response(response)

                    async for line in response.aiter_lines():
                        delta = self._parse_sse_line(line)
                        if delta is not None:
                            yield delta

            except httpx.TimeoutException:
                raise AnthropicError(
                    "Request timed out. Please check your network connection "
                    "or try again later."
                )
            except httpx.ConnectError as e:
                raise AnthropicError(
                    f"Cannot connect to {self._base_url}. "
                    f"Please check your 'base_url' configuration and network connection."
                ) from e
            except httpx.HTTPError as e:
                raise AnthropicError(
                    f"Network error while communicating with Anthropic API: {e}"
                ) from e

    def _build_request_body(self, messages: list[Message]) -> dict:
        """Build the JSON request body for the Anthropic Messages API."""
        anthropic_messages = []
        for msg in messages:
            anthropic_messages.append({
                "role": msg.role,
                "content": msg.content,
            })

        body: dict = {
            "model": self._model,
            "messages": anthropic_messages,
            "stream": True,
            "max_tokens": 4096,
        }

        if self._thinking:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": 4000,
            }

        return body

    @staticmethod
    def _parse_sse_line(line: str) -> StreamDelta | None:
        """Parse a single SSE line and return a StreamDelta if applicable."""
        # Skip empty lines, comments, and non-data lines
        if not line or not line.startswith("data: "):
            return None

        data_str = line[6:]  # Strip "data: " prefix

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return None

        return AnthropicProvider._extract_delta(data)

    @staticmethod
    def _extract_delta(data: dict) -> StreamDelta | None:
        """Extract a StreamDelta from a parsed SSE event object."""
        event_type = data.get("type")

        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            delta_type = delta.get("type")

            if delta_type == "text_delta":
                text = delta.get("text", "")
                if text:
                    return StreamDelta(type="text", content=text)

            elif delta_type == "thinking_delta":
                thinking_text = delta.get("thinking", "")
                if thinking_text:
                    return StreamDelta(type="thinking", content=thinking_text)

        return None

    async def _handle_error_response(self, response: httpx.Response) -> None:
        """Read error response and raise an AnthropicError."""
        try:
            body = await response.aread()
            error_data = json.loads(body)
            error_msg = error_data.get("error", {}).get("message", body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            error_msg = f"HTTP {response.status_code}"

        if response.status_code == 401:
            raise AnthropicError(
                "Authentication failed. Please check your 'api_key' in the configuration."
            )
        elif response.status_code == 403:
            raise AnthropicError(
                f"Access denied: {error_msg}"
            )
        elif response.status_code == 429:
            raise AnthropicError(
                "Rate limit exceeded. Please wait and try again."
            )
        else:
            raise AnthropicError(
                f"Anthropic API error (HTTP {response.status_code}): {error_msg}"
            )


# Self-register on import
register_provider("anthropic", AnthropicProvider)
