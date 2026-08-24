"""Token 估算模块单元测试。"""

from __future__ import annotations

import math

from csycode.compact.const import ESTIMATE_CHARS_PER_TOKEN
from csycode.compact.token import estimate_tokens, message_chars, usage_anchor
from csycode.llm import Message, Usage

from .conftest import make_user_msg


class TestEstimateTokens:
    def test_empty_messages_zero_anchor(self):
        """空消息 + 0 锚点 → 0。"""
        assert estimate_tokens(0, [], 0) == 0

    def test_pure_char_estimate_with_zero_anchor(self):
        """anchor=0 → 退化为纯字符估算。"""
        msgs = [make_user_msg("hello world")]  # 11 chars
        expected = math.ceil(11 / ESTIMATE_CHARS_PER_TOKEN)
        assert estimate_tokens(0, msgs, 0) == expected

    def test_anchor_only_tail_estimated(self):
        """锚点 > 0 时只估算 anchor_msg_len 之后的消息。"""
        m1 = make_user_msg("a" * 100)  # 100 chars
        m2 = make_user_msg("b" * 200)  # 200 chars
        msgs = [m1, m2]

        anchor = 1000
        # 只估算 m2（anchor_msg_len=1，跳过了 m1）
        expected = 1000 + math.ceil(200 / ESTIMATE_CHARS_PER_TOKEN)
        assert estimate_tokens(anchor, msgs, 1) == expected

    def test_anchor_msg_len_beyond_list(self):
        """anchor_msg_len 超过消息数 → 防崩溃返回 anchor。"""
        msgs = [make_user_msg("a")]
        assert estimate_tokens(500, msgs, 10) == 500

    def test_large_anchor_no_overflow(self):
        """大 anchor 值不溢出（Python int 无上限）。"""
        result = estimate_tokens(2_000_000_000, [make_user_msg("x" * 10000)], 0)
        assert result > 2_000_000_000


class TestMessageChars:
    def test_content_only(self):
        msg = make_user_msg("hello")
        assert message_chars([msg]) == 5

    def test_with_tool_calls(self):
        from csycode.llm import ToolCall

        msg = Message(
            role="assistant",
            content="text",
            tool_calls=[ToolCall(id="1", name="read", arguments={"path": "/f"})],
        )
        chars = message_chars([msg])
        assert chars > len("text")  # 含工具名和参数的字符

    def test_chinese_content(self):
        """中文字符按 UTF-8 字节计算。"""
        msg = make_user_msg("你好")
        assert message_chars([msg]) == 6  # 每个中文 3 字节


class TestUsageAnchor:
    def test_sum_all_fields(self):
        """cache_read 已包含在 input_tokens 中，不应重复计入。"""
        u = Usage(input_tokens=100, output_tokens=50, cache_write=20, cache_read=30)
        assert usage_anchor(u) == 170  # 100 + 50 + 20 (不含 cache_read)

    def test_zero_fields(self):
        u = Usage()
        assert usage_anchor(u) == 0
