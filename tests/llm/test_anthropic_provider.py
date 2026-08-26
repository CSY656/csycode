"""Anthropic provider PTL 错误包装单元测试。"""

from __future__ import annotations

import pytest

from csycode.llm import PromptTooLongError, Request
from csycode.llm.anthropic_provider import AnthropicProvider, _wrap_ptl_error


class TestWrapPtlError:
    def test_prompt_too_long_wrapped(self):
        """prompt is too long → 包装为 PromptTooLongError。"""

        class FakeBadRequest(Exception):
            pass

        orig = FakeBadRequest("prompt is too long: your message has 250K tokens...")
        ev = _wrap_ptl_error(orig)
        assert ev.err is not None
        assert isinstance(ev.err, PromptTooLongError)
        assert ev.err.__cause__ is orig

    def test_context_length_wrapped(self):
        """含 context_length 关键词 → 包装为 PTL。"""

        class FakeBadRequest(Exception):
            pass

        orig = FakeBadRequest("context_length exceeded maximum")
        ev = _wrap_ptl_error(orig)
        assert isinstance(ev.err, PromptTooLongError)

    def test_other_error_not_wrapped(self):
        """其他 4xx/5xx 不被错误包装为 PTL。"""

        class FakeBadRequest(Exception):
            pass

        orig = FakeBadRequest("invalid API key")
        ev = _wrap_ptl_error(orig)
        assert not isinstance(ev.err, PromptTooLongError)

    def test_plain_exception_not_wrapped(self):
        orig = ValueError("something went wrong")
        ev = _wrap_ptl_error(orig)
        assert not isinstance(ev.err, PromptTooLongError)


class _EmptyAnthropicStream:
    final_message = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeAnthropicContext:
    def __init__(self, owner, params):
        self._owner = owner
        self._params = params

    async def __aenter__(self):
        self._owner.params = self._params
        return _EmptyAnthropicStream()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeMessages:
    def __init__(self):
        self.params = None

    def stream(self, **params):
        return _FakeAnthropicContext(self, params)


class _FakeAnthropicClient:
    def __init__(self):
        self.messages = _FakeMessages()


def _make_anthropic_provider(thinking: bool) -> AnthropicProvider:
    provider = object.__new__(AnthropicProvider)
    provider._client = _FakeAnthropicClient()
    provider._model = "mock-model"
    provider._name = "mock"
    provider._thinking = thinking
    provider._max_tokens = 4096
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effort,budget",
    [("low", 1024), ("medium", 2048), ("high", 4096), ("xhigh", 8192)],
)
async def test_anthropic_thinking_budget_maps_effort(effort: str, budget: int):
    """Anthropic thinking 预算按四个等级映射。"""
    provider = _make_anthropic_provider(thinking=True)

    events = [event async for event in provider.stream(Request(reasoning_effort=effort))]

    assert events[-1].done
    params = provider._client.messages.params
    assert params["thinking"] == {"type": "enabled", "budget_tokens": budget}
    assert params["max_tokens"] > budget


@pytest.mark.asyncio
async def test_anthropic_thinking_disabled_omits_parameter():
    """thinking=false 时不发送 thinking 参数。"""
    provider = _make_anthropic_provider(thinking=False)

    _ = [event async for event in provider.stream(Request(reasoning_effort="xhigh"))]

    assert "thinking" not in provider._client.messages.params

    def test_plain_exception_not_wrapped(self):
        orig = ValueError("something went wrong")
        ev = _wrap_ptl_error(orig)
        assert not isinstance(ev.err, PromptTooLongError)
