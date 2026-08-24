"""Token 估算模块。

锚定最近一次 provider 返回的真实 usage，对其后新增的消息内容按
"字节数 / chars_per_token" 做增量估算。
对 CJK 内容自动使用较低的 ratio（CJK 字符通常为 3 字节/1-2 token），
减少低估偏差。
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

from .const import ESTIMATE_CHARS_PER_TOKEN, ESTIMATE_CHARS_PER_TOKEN_CJK

if TYPE_CHECKING:
    from csycode.llm import Message, Usage

# CJK 字符 Unicode 范围（含中文、日文、韩文）
_CJK_RANGES = (
    ("一", "鿿"),   # CJK Unified
    ("㐀", "䶿"),   # CJK Unified Ext-A
    ("豈", "﫿"),   # CJK Compat
    ("぀", "ゟ"),   # Hiragana
    ("゠", "ヿ"),   # Katakana
    ("가", "힯"),   # Hangul
    ("　", "〿"),   # CJK Symbols
    ("＀", "￯"),   # Halfwidth/Fullwidth
)

_CJK_RANGE_PATTERN = re.compile(
    "|".join(f"{lo}-{hi}" for lo, hi in _CJK_RANGES)
)


def _cjk_ratio(text: str) -> float:
    """返回文本中 CJK 字符占比（0~1）。"""
    if not text:
        return 0.0
    cjk_count = len(_CJK_RANGE_PATTERN.findall(text))
    return cjk_count / len(text)


def _chars_per_token(text: str) -> float:
    """根据 CJK 内容占比自适应选择 chars-per-token 比率。

    CJK 字符在 UTF-8 中占 3 字节，在 BPE tokenizer 中通常为 1-2 token，
    所以字节/token 比更低（约 1.5-2.5）。混合内容按 CJK 比例线性插值。
    """
    cjk = _cjk_ratio(text)
    # 纯 ASCII: 3.5, 纯 CJK: 2.0, 中间线性插值
    return ESTIMATE_CHARS_PER_TOKEN - cjk * (ESTIMATE_CHARS_PER_TOKEN - ESTIMATE_CHARS_PER_TOKEN_CJK)


def usage_anchor(u: Usage) -> int:
    """把 stream 尾事件中的 usage 合并成单一锚点值。

    等价于 input_tokens + output_tokens + cache_write。
    cache_read 已包含在 input_tokens 中（Anthropic 和 OpenAI 均如此），
    不应重复计入。
    """
    return u.input_tokens + u.output_tokens + u.cache_write


def message_chars(msgs: list[Message]) -> int:
    """计算单段消息列表的字符总量（UTF-8 字节数）。

    累加每条消息的 content、tool_calls 的 arguments、tool_results 的 content。
    """
    import json

    total = 0
    for m in msgs:
        if m.content:
            total += len(m.content.encode("utf-8"))
        if m.tool_calls:
            for tc in m.tool_calls:
                total += len(tc.name.encode("utf-8"))
                if tc.arguments:
                    try:
                        total += len(
                            json.dumps(tc.arguments, ensure_ascii=False).encode("utf-8")
                        )
                    except (TypeError, ValueError):
                        total += len(str(tc.arguments).encode("utf-8"))
    return total


def message_text(msgs: list[Message]) -> str:
    """拼接消息列表的文本内容（用于 CJK 检测）。"""
    import json

    parts: list[str] = []
    for m in msgs:
        if m.content:
            parts.append(m.content)
        if m.tool_calls:
            for tc in m.tool_calls:
                parts.append(tc.name)
                if tc.arguments:
                    try:
                        parts.append(json.dumps(tc.arguments, ensure_ascii=False))
                    except (TypeError, ValueError):
                        parts.append(str(tc.arguments))
    return "".join(parts)


def estimate_tokens(anchor: int, all_msgs: list[Message], anchor_msg_len: int) -> int:
    """锚定最近一次 provider usage + 之后新增消息的字符增量。

    入参语义:
      - anchor: 上一次主对话路径 stream 真实 usage 之和（int）。
      - all_msgs: 当前 conv.messages() 完整列表。
      - anchor_msg_len: 当 anchor 被记录时 conv.length() 的值，
        表示锚点之前已被这份 usage 算进的消息条数。
      - 函数只把 all_msgs[anchor_msg_len:] 这部分的字符累加，
        避免把已含在 anchor 里的历史重复计算。
      - 返回 anchor + math.ceil(sum(chars(tail)) / ratio)。

    锚点为 0、anchor_msg_len 为 0（首轮 / 摘要后）时退化为纯字符估算。
    """
    safe_start = max(0, anchor_msg_len)
    tail = all_msgs[safe_start:] if safe_start < len(all_msgs) else []
    if not tail:
        return max(anchor, 0)
    chars = message_chars(tail)
    ratio = _chars_per_token(message_text(tail))
    increment = math.ceil(chars / ratio)
    return max(anchor, 0) + increment
