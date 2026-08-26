"""OpenAI provider PTL 错误包装单元测试。"""

from __future__ import annotations

import pytest

from csycode.llm import PromptTooLongError
from csycode.llm import Request
from csycode.llm.openai_provider import OpenAIProvider, _wrap_ptl_error


class TestWrapPtlError:
    def test_context_length_exceeded_code(self):
        """code == 'context_length_exceeded' → 包装为 PromptTooLongError。"""

        class FakeBadRequest(Exception):
            pass

        orig = FakeBadRequest("context_length_exceeded")
        orig.code = "context_length_exceeded"
        ev = _wrap_ptl_error(orig)
        assert ev.err is not None
        assert isinstance(ev.err, PromptTooLongError)
        assert ev.err.__cause__ is orig

    def test_context_length_in_message(self):
        """message 中含 context_length_exceeded → 包装为 PTL（兜底）。"""

        class FakeBadRequest(Exception):
            pass

        orig = FakeBadRequest("error: context_length_exceeded for model gpt-4")
        ev = _wrap_ptl_error(orig)
        assert isinstance(ev.err, PromptTooLongError)

    def test_other_error_not_wrapped(self):
        class FakeBadRequest(Exception):
            pass

        orig = FakeBadRequest("rate limit exceeded")
        ev = _wrap_ptl_error(orig)
        assert not isinstance(ev.err, PromptTooLongError)


class _EmptyOpenAIStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeCompletions:
    def __init__(self):
        self.params = None

    async def create(self, **params):
        self.params = params
        return _EmptyOpenAIStream()


class _FakeOpenAIClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _FakeCompletions()})()


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
async def test_openai_forwards_reasoning_effort(effort: str):
    """OpenAI 和 OpenAI-compatible 请求透传 reasoning_effort。"""
    provider = object.__new__(OpenAIProvider)
    client = _FakeOpenAIClient()
    provider._client = client
    provider._model = "mock-model"
    provider._max_tokens = 128
    provider._is_openai = False

    events = [event async for event in provider.stream(Request(reasoning_effort=effort))]

    assert events[-1].done
    assert client.chat.completions.params["reasoning_effort"] == effort

    def test_plain_exception_not_wrapped(self):
        orig = KeyError("missing key")
        ev = _wrap_ptl_error(orig)
        assert not isinstance(ev.err, PromptTooLongError)
