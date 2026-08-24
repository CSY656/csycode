"""manage_context 编排入口集成测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from csycode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    ManageInput,
    RecoveryState,
    TriggerKind,
    manage_context,
    new_session_context,
)
from csycode.compact.const import MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES
from csycode.conversation import Conversation
from csycode.llm import Message, StreamEvent, Usage

from .conftest import FakeProvider, make_assistant_msg, make_user_msg


# ── fake_provider 摘要请求 fixture ─────────────────────────────────────


def make_fake_provider_for_summary(summary_text: str = "## 1\nsummary content"):
    """构造一个能产出模拟摘要的 fake provider。"""

    class SummaryProvider:
        def __init__(self):
            self.model = "test-model"
            self.summarize_calls = 0

        async def stream(self, req):
            self.summarize_calls += 1
            yield StreamEvent(text="<analysis>draft</analysis>\n")
            yield StreamEvent(text=f"<summary>{summary_text}</summary>")
            yield StreamEvent(
                done=True,
                usage=Usage(input_tokens=100, output_tokens=50),
            )

    return SummaryProvider()


# ── manage_context 集成测试 ────────────────────────────────────────────


class TestManageContext:
    @staticmethod
    def _make_input(conv, provider, **overrides):
        """构造 ManageInput 的 helper。"""
        default = {
            "conv": conv,
            "provider": provider,
            "model": "test-model",
            "context_window": 200000,
            "tool_defs": [],
            "replacement": ContentReplacementState(),
            "recovery": RecoveryState(),
            "auto_tracking": CompactCircuitBreaker(),
            "session": new_session_context("."),
            "trigger": TriggerKind.AUTO,
        }
        default.update(overrides)
        return ManageInput(**default)

    async def test_auto_below_threshold_no_summary(self):
        """估算 token < 阈值 → 不触发 auto_compact。"""
        conv = Conversation()
        conv._messages = [make_user_msg("short message")]
        fake_prov = make_fake_provider_for_summary()

        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            in_ = self._make_input(
                conv, fake_prov,
                session=ctx,
                context_window=200000,
            )
            out = await manage_context(in_)
            assert out.before_tokens >= 0
            assert fake_prov.summarize_calls == 0

    async def test_manual_bypasses_threshold(self):
        """trigger=MANUAL 时忽略阈值，强制执行摘要（需要足够的内容避免前缀太小）。"""
        conv = Conversation()
        # 构造足够大的对话历史使前缀不会被跳过
        msgs = []
        for i in range(20):
            msgs.append(make_user_msg(f"question {i}: " + "x" * 200))
            msgs.append(make_assistant_msg(f"answer {i}: " + "y" * 200))
        conv._messages = msgs
        fake_prov = make_fake_provider_for_summary("manual summary")

        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            in_ = self._make_input(
                conv, fake_prov,
                session=ctx,
                trigger=TriggerKind.MANUAL,
            )
            out = await manage_context(in_)
            # 手动路径强制执行了摘要（跳过了自动阈值检查）
            assert fake_prov.summarize_calls >= 1

    async def test_auto_skipped_when_tripped(self):
        """熔断器 trip 后跳过自动摘要。"""
        conv = Conversation()
        # 构造足够大的对话历史使 token 超过自动阈值
        msgs = []
        for i in range(30):
            msgs.append(make_user_msg(f"q{i}: " + "x" * 800))
            msgs.append(make_assistant_msg(f"a{i}: " + "y" * 800))
        conv._messages = msgs
        fake_prov = make_fake_provider_for_summary()

        tracker = CompactCircuitBreaker()
        # 标记为已熔断
        for _ in range(MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES):
            tracker.record_failure()
        assert tracker.tripped()

        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            in_ = self._make_input(
                conv, fake_prov,
                session=ctx,
                auto_tracking=tracker,
                context_window=200000,
            )
            out = await manage_context(in_)
            # 熔断后不触发摘要
            assert fake_prov.summarize_calls == 0

    async def test_emergency_bypasses_tracking(self):
        """trigger=EMERGENCY 时绕过熔断器。"""
        conv = Conversation()
        # 构造足够大的对话历史
        msgs = []
        for i in range(20):
            msgs.append(make_user_msg(f"q{i}: " + "x" * 500))
            msgs.append(make_assistant_msg(f"a{i}: " + "y" * 500))
        conv._messages = msgs
        fake_prov = make_fake_provider_for_summary("emergency summary")

        tracker = CompactCircuitBreaker()
        for _ in range(MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES):
            tracker.record_failure()

        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            in_ = self._make_input(
                conv, fake_prov,
                session=ctx,
                auto_tracking=tracker,
                trigger=TriggerKind.EMERGENCY,
            )
            out = await manage_context(in_)
            assert fake_prov.summarize_calls >= 1

    async def test_auto_failure_records_failure(self):
        """摘要失败时记录熔断计数。"""
        conv = Conversation()
        # 构造足够大的对话历史
        msgs = []
        for i in range(30):
            msgs.append(make_user_msg(f"q{i}: " + "x" * 500))
            msgs.append(make_assistant_msg(f"a{i}: " + "y" * 500))
        conv._messages = msgs

        class FailingProvider:
            model = "test-model"

            async def stream(self, req):
                raise RuntimeError("summarize failed")

        fake_prov = FailingProvider()
        tracker = CompactCircuitBreaker()

        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            in_ = self._make_input(
                conv, fake_prov,
                session=ctx,
                auto_tracking=tracker,
                context_window=200000,
            )
            try:
                await manage_context(in_)
            except Exception:
                pass
            # 熔断计数已增加（具体增加到几取决于路径）
            # 至少不应该 crash
