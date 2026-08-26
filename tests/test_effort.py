"""思考强度领域模块测试。"""

from __future__ import annotations

import pytest

from csycode.effort import (
    DEFAULT_REASONING_EFFORT,
    REASONING_EFFORTS,
    parse_reasoning_effort,
    reasoning_effort_help,
)


def test_effort_levels_and_default():
    assert REASONING_EFFORTS == ("low", "medium", "high", "xhigh")
    assert DEFAULT_REASONING_EFFORT == "high"


@pytest.mark.parametrize("value, expected", [
    ("low", "low"),
    (" MEDIUM ", "medium"),
    ("High", "high"),
    ("XHIGH", "xhigh"),
])
def test_parse_effort_normalizes(value: str, expected: str):
    assert parse_reasoning_effort(value) == expected


@pytest.mark.parametrize("value", ["", "middle", "high extra"])
def test_parse_effort_rejects_invalid(value: str):
    assert parse_reasoning_effort(value) is None


def test_effort_help_lists_all_levels():
    assert reasoning_effort_help() == "用法: /effort <low|medium|high|xhigh>"
