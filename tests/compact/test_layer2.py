"""第 2 层摘要（auto_compact / pick_recent_tail / group_by_user_turn）单元测试。"""

from __future__ import annotations

import math

import pytest

from csycode.compact.layer2 import (
    compute_compact_threshold,
    group_by_user_turn,
    pick_recent_tail,
    should_auto_compact,
)
from csycode.llm import Message, ToolCall

from .conftest import make_assistant_msg, make_tool_result, make_user_msg


# ── compute_compact_threshold ─────────────────────────────────────────


class TestComputeCompactThreshold:
    def test_auto_margin(self):
        threshold = compute_compact_threshold(200000, manual=False)
        assert threshold == 200000 - 20000 - 13000  # 167000

    def test_manual_margin(self):
        threshold = compute_compact_threshold(200000, manual=True)
        assert threshold == 200000 - 20000 - 3000  # 177000

    def test_small_window(self):
        """小窗口也能正确计算阈值。"""
        threshold = compute_compact_threshold(50000, manual=False)
        assert threshold == 50000 - 20000 - 13000  # 17000


# ── should_auto_compact ───────────────────────────────────────────────


class TestShouldAutoCompact:
    def test_above_threshold(self):
        assert should_auto_compact(170000, 200000) is True

    def test_below_threshold(self):
        assert should_auto_compact(50000, 200000) is False


# ── pick_recent_tail ──────────────────────────────────────────────────


class TestPickRecentTail:
    def test_empty_messages(self):
        assert pick_recent_tail([]) == []

    def test_small_conversation_all_kept(self):
        """消息总数少于阈值时全部保留。"""
        msgs = [make_user_msg("hi"), make_assistant_msg("hello")]
        result = pick_recent_tail(msgs)
        assert len(result) == 2

    def test_large_conversation_keeps_recent(self):
        """大对话保留尾部最近消息。"""
        msgs = []
        for i in range(50):
            msgs.append(make_user_msg(f"question {i}"))
            msgs.append(make_assistant_msg(f"answer {i}"))
        result = pick_recent_tail(msgs)
        assert len(result) > 0
        assert len(result) < len(msgs)
        # 保留的是尾部消息
        assert result[-1].content == "answer 49"

    def test_pair_fix_prevents_orphan_tool_result(self):
        """截断点夹在 tool_use/tool_result 中间时前推到 assistant 处。"""
        tc = ToolCall(id="tc1", name="read_file", arguments={"path": "/f"})
        msgs = [
            make_user_msg("read this"),
            make_assistant_msg("ok", tool_calls=[tc]),
            make_tool_result("tc1", "file content"),
            make_user_msg("next question"),
            make_assistant_msg("answer"),
        ]
        # 构造场景使得截断点正好落在 tool_result 上（即保留 [tool_result, user, assistant]）
        # pick_recent_tail 应把起点前推到 assistant tool_use 之前
        result = pick_recent_tail(msgs)
        # 结果首条不应该是 tool_result
        if len(result) > 0:
            assert result[0].tool_call_id is None or result[0].role == "assistant"


# ── group_by_user_turn ────────────────────────────────────────────────


class TestGroupByUserTurn:
    def test_standard_grouping(self):
        msgs = [
            make_user_msg("q1"),
            make_assistant_msg("a1"),
            make_user_msg("q2"),
            make_assistant_msg("a2"),
        ]
        groups = group_by_user_turn(msgs)
        assert len(groups) == 2
        assert groups[0][0].content == "q1"
        assert groups[1][0].content == "q2"

    def test_tool_result_messages_included(self):
        tc = ToolCall(id="tc1", name="bash", arguments={"cmd": "ls"})
        msgs = [
            make_user_msg("list files"),
            make_assistant_msg("ok", tool_calls=[tc]),
            make_tool_result("tc1", "file1\nfile2"),
            make_user_msg("good"),
        ]
        groups = group_by_user_turn(msgs)
        # 所有消息都被分组，没有丢失
        total = sum(len(g) for g in groups)
        assert total == 4
