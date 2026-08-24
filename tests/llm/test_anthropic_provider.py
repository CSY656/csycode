"""Anthropic provider PTL 错误包装单元测试。"""

from __future__ import annotations

import pytest

from csycode.llm import PromptTooLongError
from csycode.llm.anthropic_provider import _wrap_ptl_error


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
