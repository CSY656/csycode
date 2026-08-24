"""OpenAI Chat Completions API provider with SSE streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from csycode.config import ProviderConfig
from csycode.provider import BaseProvider, Message, StreamDelta
from csycode.registry import register_provider


class OpenAIError(Exception):
    """Raised when the OpenAI API returns an error."""
    pass


class OpenAIProvider(BaseProvider):
    """Provider for the OpenAI Chat Completions API.

    Supports streaming via SSE. Does not support extended thinking
    (OpenAI has no equivalent feature).
    """

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._base_url = config.base_url.rstrip("/")
        self._api_key = config.api_key
        self._model = config.model

    async def chat_stream(
        self,
        messages: list[Message],
    ) -> AsyncIterator[StreamDelta]:
        """Send messages to OpenAI and stream the response."""
        url = f"{self._base_url}/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {self._api_key}",
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
                raise OpenAIError(
                    "Request timed out. Please check your network connection "
                    "or try again later."
                )
            except httpx.ConnectError as e:
                raise OpenAIError(
                    f"Cannot connect to {self._base_url}. "
                    f"Please check your 'base_url' configuration and network connection."
                ) from e
            except httpx.HTTPError as e:
                raise OpenAIError(
                    f"Network error while communicating with OpenAI API: {e}"
                ) from e

    def _build_request_body(self, messages: list[Message]) -> dict:
        """Build the JSON request body for the OpenAI Chat Completions API."""
        openai_messages = []
        for msg in messages:
            openai_messages.append({
                "role": msg.role,
                "content": msg.content,
            })

        return {
            "model": self._model,
            "messages": openai_messages,
            "stream": True,
        }

    @staticmethod
    def _parse_sse_line(line: str) -> StreamDelta | None:
        """Parse a single SSE line and return a StreamDelta if applicable."""
        if not line or not line.startswith("data: "):
            return None

        data_str = line[6:]  # Strip "data: " prefix

        if data_str.strip() == "[DONE]":
            return None

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return None

        # OpenAI streaming format: {"choices": [{"delta": {"content": "..."}}]}
        choices = data.get("choices", [])
        if not choices:
            return None

        delta = choices[0].get("delta", {})
        content = delta.get("content")

        if content:
            return StreamDelta(type="text", content=content)

        return None

    async def _handle_error_response(self, response: httpx.Response) -> None:
        """Read error response and raise an OpenAIError."""
        try:
            body = await response.aread()
            error_data = json.loads(body)
            error_msg = error_data.get("error", {}).get("message", body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            error_msg = f"HTTP {response.status_code}"

        if response.status_code == 401:
            raise OpenAIError(
                "Authentication failed. Please check your 'api_key' in the configuration."
            )
        elif response.status_code == 429:
            raise OpenAIError(
                "Rate limit exceeded. Please wait and try again."
            )
        else:
            raise OpenAIError(
                f"OpenAI API error (HTTP {response.status_code}): {error_msg}"
            )


# Self-register on import
register_provider("openai", OpenAIProvider)
