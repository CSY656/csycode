"""第 1 层压缩（offload_and_snip / spill_single / build_preview）单元测试。"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from csycode.compact.layer1 import (
    _head_preview,
    build_preview,
    offload_and_snip,
    spill_single,
)
from csycode.compact.state import ContentReplacementState, new_session_context
from csycode.llm import Message

from .conftest import make_tool_result


# ── spill_single ──────────────────────────────────────────────────────


class TestSpillSingle:
    def test_writes_content_to_disk(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            spill_single(ctx, "test-id", "hello world")
            path = Path(ctx.spill_dir) / "test-id"
            assert path.exists()
            assert path.read_text(encoding="utf-8") == "hello world"

    def test_idempotent_second_call_no_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            spill_single(ctx, "test-id", "first")
            mtime1 = os.stat(Path(ctx.spill_dir) / "test-id").st_mtime_ns
            spill_single(ctx, "test-id", "should not overwrite")
            mtime2 = os.stat(Path(ctx.spill_dir) / "test-id").st_mtime_ns
            assert mtime1 == mtime2
            content = (Path(ctx.spill_dir) / "test-id").read_text(encoding="utf-8")
            assert content == "first"


# ── build_preview ─────────────────────────────────────────────────────


class TestBuildPreview:
    def test_contains_four_required_parts(self):
        preview = build_preview(50000, "head content", "/path/to/spill")
        assert "original size: 50000 bytes" in preview
        assert "/path/to/spill" in preview
        assert "head preview" in preview
        assert "head content" in preview
        assert "不要凭头部预览猜测全文" in preview

    def test_stable_across_calls(self):
        """相同入参两次调用返回逐字节相等。"""
        p1 = build_preview(1000, "abc", "/p")
        p2 = build_preview(1000, "abc", "/p")
        assert p1 == p2


# ── _head_preview ─────────────────────────────────────────────────────


class TestHeadPreview:
    def test_short_content_passed_through(self):
        assert _head_preview("short") == "short"

    def test_line_cutoff(self):
        """超过 PREVIEW_HEAD_LINES 行只保留前 N 行。"""
        content = "\n".join([f"line {i}" for i in range(50)])
        head = _head_preview(content)
        # 保留不超过 20 行（PREVIEW_HEAD_LINES）
        assert len(head.splitlines()) <= 22  # keepends=True may retain trailing newline

    def test_byte_cutoff(self):
        """超过 PREVIEW_HEAD_BYTES 字节时截断。"""
        content = "x" * 3000  # 超过 2048 字节
        head = _head_preview(content)
        encoded = head.encode("utf-8")
        assert len(encoded) <= 2048


# ── offload_and_snip ──────────────────────────────────────────────────


class TestOffloadAndSnip:
    def test_single_result_above_limit_replaced(self):
        """单条工具结果超过 50000 字节 → 被替换为预览体。"""
        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            state = ContentReplacementState()

            big_content = "x" * 60000
            msgs = [make_tool_result("call-1", big_content)]

            out = offload_and_snip(msgs, state, ctx)

            assert len(out) == 1
            assert out[0].tool_call_id == "call-1"
            assert "original size" in out[0].content
            assert "不要凭头部预览猜测全文" in out[0].content
            # 落盘文件存在
            assert (Path(ctx.spill_dir) / "call-1").exists()

    def test_small_result_kept(self):
        """小工具结果保留原文。"""
        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            state = ContentReplacementState()

            msgs = [make_tool_result("call-1", "small result")]
            out = offload_and_snip(msgs, state, ctx)

            assert len(out) == 1
            assert out[0].content == "small result"

    def test_aggregate_above_limit_replaced(self):
        """单轮聚合超过 200000 字节 → 按倒序落盘直到聚合回落到阈值以下。"""
        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            state = ContentReplacementState()

            # 3 条各 80000 字节，合计 240000 > 200000
            msgs = [
                make_tool_result("call-a", "a" * 80000),
                make_tool_result("call-b", "b" * 80000),
                make_tool_result("call-c", "c" * 80000),
            ]

            out = offload_and_snip(msgs, state, ctx)

            # 至少 1 条被替换（按倒序处理，各条大小相同，第一条 80000 不超单条阈值但聚合超限→落盘）
            replaced_count = sum(
                1 for m in out if "original size" in m.content
            )
            assert replaced_count >= 1  # 至少落盘 1 条让聚合 ≤ 200000

    def test_non_tool_messages_passthrough(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            state = ContentReplacementState()

            msgs = [
                Message(role="user", content="hello"),
                make_tool_result("call-1", "a" * 60000),
            ]

            out = offload_and_snip(msgs, state, ctx)
            assert out[0].role == "user"
            assert out[0].content == "hello"
            assert "original size" in out[1].content

    def test_decision_freeze_replaced_id_reused(self):
        """已替换的 id 第二次 offload 复用同一份 preview。"""
        with tempfile.TemporaryDirectory() as td:
            ctx = new_session_context(td)
            state = ContentReplacementState()

            big_content = "y" * 60000
            msgs = [make_tool_result("call-1", big_content)]

            out1 = offload_and_snip(msgs, state, ctx)
            out2 = offload_and_snip(msgs, state, ctx)

            assert out1[0].content == out2[0].content  # 逐字节一致

    def test_spill_failure_retryable(self):
        """落盘目录不可写 → 降级为不替换，下次重试。

        通过把 spill_dir 设为一个文件的路径（而非目录），让 spill_single 中的
        Path.write_bytes 失败（无法在非目录下创建文件）。
        """
        with tempfile.TemporaryDirectory() as td:
            # 创建一个临时文件，把它的路径赋给 spill_dir，
            # 这样后续 spill_single 尝试在该"目录"下创建文件时会失败
            blocker = Path(td) / "blocker.txt"
            blocker.write_text("block")

            # 直接构造 SessionContext，把 spill_dir 设为文件路径
            from csycode.compact.state import SessionContext
            ctx = SessionContext(
                session_id="test-session",
                spill_dir=str(blocker),  # 这是个文件，不是目录
            )

            state = ContentReplacementState()
            big_content = "z" * 60000
            msgs = [make_tool_result("call-1", big_content)]

            out = offload_and_snip(msgs, state, ctx)

            # 落盘失败 → 保留原文
            assert big_content in out[0].content
            # 账本中未 Seen
            assert "call-1" not in state._seen_ids

            # 恢复正确的 spill_dir 后重试
            real_ctx = new_session_context(td)
            out2 = offload_and_snip(msgs, state, real_ctx)
            # 现在可以被替换了
            assert "original size" in out2[0].content
