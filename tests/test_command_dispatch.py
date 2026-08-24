"""parse 输入解析测试。"""

from __future__ import annotations

import pytest

from csycode.command.dispatch import parse


# ── 测试样本表 ────────────────────────────────────────────────────────

TEST_CASES = [
    # (input, expected_name, expected_args, expected_is_slash)
    ("", "", "", False),
    ("   ", "", "", False),
    ("hello", "", "", False),
    ("/", "", "", True),
    ("/help", "help", "", True),
    ("  /HELP  ", "help", "", True),
    ("/help xx", "help", "xx", True),  # 尾随参数 → 保留为 args
    ("/help  ", "help", "", True),  # 仅有尾随空白
    ("//double", "/double", "", True),  # inner="/double" → name_part="/double"
    ("/ /help", "", "/help", True),  # inner 以空格开头 → 退化输入
]


class TestParse:
    def test_cases(self):
        """表驱动测试 parse。"""
        for input_text, expected_name, expected_args, expected_is_slash in TEST_CASES:
            name, args, is_slash = parse(input_text)
            assert name == expected_name, (
                f"parse({input_text!r}) name: {name!r} != {expected_name!r}"
            )
            assert args == expected_args, (
                f"parse({input_text!r}) args: {args!r} != {expected_args!r}"
            )
            assert is_slash == expected_is_slash, (
                f"parse({input_text!r}) is_slash: {is_slash} != {expected_is_slash}"
            )

    def test_only_slash(self):
        """输入仅为 / → ("", "", True)。"""
        name, args, is_slash = parse("/")
        assert name == ""
        assert args == ""
        assert is_slash is True

    def test_slash_with_args(self):
        """带参数的斜杠命令 → 现在保留 name 和 args。"""
        name, args, is_slash = parse("/help me please")
        assert name == "help"
        assert args == "me please"
        assert is_slash is True

    def test_non_slash_input(self):
        """普通文本 → 非命令。"""
        name, _, is_slash = parse("hello world")
        assert name == ""
        assert is_slash is False

    def test_case_insensitive(self):
        """大小写不敏感：/HELP → "help"。"""
        name, _, is_slash = parse("/HELP")
        assert name == "help"
        assert is_slash is True
