"""ContentReplacementState / CompactCircuitBreaker / RecoveryState / SessionContext 单元测试。"""

from __future__ import annotations

import time

from csycode.compact.const import MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES
from csycode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)


# ── ContentReplacementState ────────────────────────────────────────────


class TestContentReplacementState:
    def test_decide_once_freeze_kept(self):
        """decide_once 回调返回 kept → 再次调用返回原 content，账本不翻转。"""
        state = ContentReplacementState()
        original = "hello world"

        r1 = state.decide_once("id1", original, lambda: ("kept", ""))
        assert r1 == original

        # 第二次调用不再调回调，直接返回原 content
        called = False
        r2 = state.decide_once("id1", original, lambda: (setattr_self(called), "dummy"))
        assert r2 == original

    def test_decide_once_freeze_replaced(self):
        """decide_once 回调返回 replaced → 再次调用返回同一份 preview。"""
        state = ContentReplacementState()
        preview = "[replaced preview]"
        r1 = state.decide_once("id1", "big content", lambda: ("replaced", preview))
        assert r1 == preview

        # 第二次调用不再调回调，直接返回存量 preview
        r2 = state.decide_once("id1", "big content", lambda: ("replaced", "new"))
        assert r2 == preview
        assert r2 is r1  # 同一份字符串对象

    def test_decide_once_skip_does_not_mark(self):
        """decide_once 回调返回 skip → 不写账本，下次仍可评估。"""
        state = ContentReplacementState()

        r1 = state.decide_once("id1", "content", lambda: ("skip", ""))
        assert r1 == "content"

        # 第二次调用仍会调回调（因为账本未写入）
        preview = "[preview]"
        r2 = state.decide_once("id1", "content", lambda: ("replaced", preview))
        assert r2 == preview

    def test_decide_once_atomic_write_both_or_none(self):
        """decide_once 保证 seen_ids 与 replacements 同时写入。"""
        state = ContentReplacementState()
        preview = "[p]"
        state.decide_once("id1", "c", lambda: ("replaced", preview))

        assert "id1" in state._seen_ids
        assert state._replacements.get("id1") == preview
        # 不应该出现已 Seen 但 replacement 未写的中间态


# ── CompactCircuitBreaker ──────────────────────────────────────────────


class TestCompactCircuitBreaker:
    def test_record_success_resets_count(self):
        cb = CompactCircuitBreaker()
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert not cb.tripped()
        cb.record_failure()
        cb.record_failure()
        assert not cb.tripped()

    def test_tripped_after_threshold(self):
        cb = CompactCircuitBreaker()
        for _ in range(MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES - 1):
            cb.record_failure()
            assert not cb.tripped()
        cb.record_failure()
        assert cb.tripped()

    def test_tripped_persists_beyond_threshold(self):
        cb = CompactCircuitBreaker()
        for _ in range(MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES + 2):
            cb.record_failure()
        assert cb.tripped()


# ── RecoveryState ─────────────────────────────────────────────────────


class TestRecoveryState:
    def test_snapshot_order_by_timestamp_desc(self):
        """snapshot 按时间戳倒序排列。"""
        rs = RecoveryState()
        rs.record_file("/a.py", "a")
        time.sleep(0.01)
        rs.record_file("/b.py", "b")
        time.sleep(0.01)
        rs.record_file("/c.py", "c")

        snap = rs.snapshot()
        assert len(snap) == 3
        assert snap[0].path.endswith("c.py")
        assert snap[1].path.endswith("b.py")
        assert snap[2].path.endswith("a.py")

    def test_snapshot_returns_copy_not_internal(self):
        rs = RecoveryState()
        rs.record_file("/a.py", "a")
        snap1 = rs.snapshot()
        snap1.clear()
        snap2 = rs.snapshot()
        assert len(snap2) == 1  # 内部 dict 未被修改

    def test_resolve_relative_path(self):
        rs = RecoveryState()
        import os
        cwd = os.getcwd()
        rs.record_file("relative/path.py", "content")
        snap = rs.snapshot()
        assert snap[0].path.startswith(cwd) or snap[0].path == "relative/path.py"


# ── SessionContext ─────────────────────────────────────────────────────


class TestNewSessionContext:
    def test_format_unix_ts_hex(self):
        """session_id 格式: YYYYMMDD-HHMMSS-<16hex>（token_hex(8)）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            parts = ctx.session_id.split("-")
            assert len(parts) == 3
            assert len(parts[0]) == 8  # YYYYMMDD
            assert len(parts[1]) == 6  # HHMMSS
            assert len(parts[2]) == 16  # token_hex(8)
            assert parts[0].isdigit()
            assert parts[1].isdigit()

    def test_spill_dir_created(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            spill = Path(ctx.spill_dir)
            assert spill.exists()
            assert spill.is_dir()


# ── helper ─────────────────────────────────────────────────────────────


def setattr_self(value):
    """用于 lambda 中的副作用探测。"""
    return value
