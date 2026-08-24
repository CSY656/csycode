"""T9: hook.Loader 单元测试 —— 字段校验、加载错误、合并。"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from csycode.hook.event import Event
from csycode.hook.loader import _compile_hook, _compile_condition, _parse_duration, load
from csycode.hook.rule import ActionType


# ── 时长解析 ──────────────────────────────────────────────────────────────


class TestParseDuration:
    def test_seconds(self):
        assert _parse_duration("30s") == 30.0
        assert _parse_duration("5s") == 5.0
        assert _parse_duration("0s") == 0.0

    def test_minutes(self):
        assert _parse_duration("5m") == 300.0
        assert _parse_duration("1m") == 60.0

    def test_hours(self):
        assert _parse_duration("1h") == 3600.0

    def test_bare_number(self):
        assert _parse_duration("30") == 30.0
        assert _parse_duration("1.5") == 1.5

    def test_int(self):
        assert _parse_duration(30) == 30.0

    def test_invalid(self):
        assert _parse_duration("abc") is None
        assert _parse_duration("") is None
        assert _parse_duration(None) is None


# ── _compile_hook ──────────────────────────────────────────────────────────


class TestCompileHook:
    def test_minimal_valid(self):
        raw = {
            "name": "test-hook",
            "event": "SessionStart",
            "action": {"type": "shell", "command": "echo hi"},
        }
        rule = _compile_hook(raw, "test.yaml", 1)
        assert rule is not None
        assert rule.name == "test-hook"
        assert rule.event == Event.SESSION_START
        assert rule.action.type == ActionType.SHELL
        assert rule.condition is None
        assert rule.only_once is False
        assert rule.asyncio_mode is False

    def test_missing_name(self, capsys):
        raw = {"event": "SessionStart", "action": {"type": "shell", "command": "x"}}
        rule = _compile_hook(raw, "test.yaml", 1)
        assert rule is None
        captured = capsys.readouterr()
        assert "missing or empty 'name'" in captured.err

    def test_unknown_event(self, capsys):
        raw = {
            "name": "bad",
            "event": "UnknownEvent",
            "action": {"type": "shell", "command": "x"},
        }
        rule = _compile_hook(raw, "test.yaml", 1)
        assert rule is None
        captured = capsys.readouterr()
        assert "unknown event" in captured.err.lower()

    def test_async_blocking_event(self, capsys):
        raw = {
            "name": "bad-async",
            "event": "PreToolUse",
            "async": True,
            "action": {"type": "shell", "command": "x"},
        }
        rule = _compile_hook(raw, "test.yaml", 1)
        assert rule is None
        captured = capsys.readouterr()
        assert "async not allowed" in captured.err

    def test_only_once(self):
        raw = {
            "name": "once-hook",
            "event": "PreUserMessage",
            "only_once": True,
            "action": {"type": "prompt", "text": "hello"},
        }
        rule = _compile_hook(raw, "test.yaml", 1)
        assert rule is not None
        assert rule.only_once is True

    def test_timeout_parsing(self):
        raw = {
            "name": "timeout-hook",
            "event": "Stop",
            "timeout": "5s",
            "action": {"type": "shell", "command": "x"},
        }
        rule = _compile_hook(raw, "test.yaml", 1)
        assert rule is not None
        assert rule.timeout_s == 5.0

    def test_empty_name(self, capsys):
        raw = {"name": "  ", "event": "Stop", "action": {"type": "shell", "command": "x"}}
        rule = _compile_hook(raw, "test.yaml", 1)
        assert rule is None


# ── 条件编译 ──────────────────────────────────────────────────────────────


class TestCompileCondition:
    def test_all_of(self):
        raw = {
            "all_of": [
                {"field": "tool_name", "match": {"type": "exact", "value": "write_file"}},
            ]
        }
        cond = _compile_condition(raw, "test", "x.yaml")
        assert cond is not None
        assert cond.mode.value == "all_of"
        assert len(cond.atoms) == 1

    def test_any_of(self):
        raw = {
            "any_of": [
                {"field": "tool_name", "match": {"type": "glob", "value": "*"}},
            ]
        }
        cond = _compile_condition(raw, "test", "x.yaml")
        assert cond is not None
        assert cond.mode.value == "any_of"

    def test_both_all_and_any(self, capsys):
        raw = {"all_of": [], "any_of": []}
        cond = _compile_condition(raw, "test", "x.yaml")
        assert cond is None
        captured = capsys.readouterr()
        assert "cannot have both" in captured.err

    def test_neither_all_nor_any(self, capsys):
        raw = {}
        cond = _compile_condition(raw, "test", "x.yaml")
        assert cond is None
        captured = capsys.readouterr()
        assert "must have" in captured.err

    def test_not_match_type(self):
        """{type: not, inner: {type: exact, value: foo}} 应编译成功。"""
        raw = {
            "all_of": [
                {
                    "field": "tool_name",
                    "match": {"type": "not", "inner": {"type": "exact", "value": "foo"}},
                },
            ]
        }
        cond = _compile_condition(raw, "test", "x.yaml")
        assert cond is not None
        assert len(cond.atoms) == 1


# ── load 集成 ─────────────────────────────────────────────────────────────


class TestLoad:
    def test_load_valid_yaml(self, tmp_path: Path):
        """合法 YAML 加载成功。"""
        hooks_dir = tmp_path / ".csycode"
        hooks_dir.mkdir()
        hooks_file = hooks_dir / "hooks.yaml"
        hooks_file.write_text(
            yaml.safe_dump({
                "hooks": [
                    {
                        "name": "hook1",
                        "event": "SessionStart",
                        "action": {"type": "shell", "command": "echo a"},
                    },
                    {
                        "name": "hook2",
                        "event": "Stop",
                        "action": {"type": "prompt", "text": "done"},
                    },
                ]
            }),
            encoding="utf-8",
        )

        # 使用独立的 fake home 避免与项目级路径冲突
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        with patch.object(Path, "home", return_value=fake_home):
            engine = load(str(tmp_path))
        assert len(engine.rules) == 2
        assert len(engine.sources) == 1

    def test_load_missing_file(self, tmp_path: Path):
        """文件不存在不报错。"""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        with patch.object(Path, "home", return_value=fake_home):
            engine = load(str(tmp_path))
        assert len(engine.rules) == 0

    def test_load_skip_invalid(self, tmp_path: Path, capsys):
        """非法条目跳过但不影响合法条目。"""
        hooks_dir = tmp_path / ".csycode"
        hooks_dir.mkdir()
        hooks_file = hooks_dir / "hooks.yaml"
        hooks_file.write_text(
            yaml.safe_dump({
                "hooks": [
                    {
                        # 缺少 name
                        "event": "Stop",
                        "action": {"type": "shell", "command": "x"},
                    },
                    {
                        "name": "good",
                        "event": "Stop",
                        "action": {"type": "shell", "command": "x"},
                    },
                ]
            }),
            encoding="utf-8",
        )

        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        with patch.object(Path, "home", return_value=fake_home):
            engine = load(str(tmp_path))
        assert len(engine.rules) == 1
        assert engine.rules[0].name == "good"
        captured = capsys.readouterr()
        assert "missing" in captured.err.lower()

    def test_load_name_conflict(self, tmp_path: Path, capsys):
        """跨文件同名冲突跳过后者。"""
        # 项目级
        proj_dir = tmp_path / ".csycode"
        proj_dir.mkdir(parents=True)
        proj_file = proj_dir / "hooks.yaml"
        proj_file.write_text(
            yaml.safe_dump({
                "hooks": [{
                    "name": "dup", "event": "Stop",
                    "action": {"type": "shell", "command": "x"},
                }]
            }),
            encoding="utf-8",
        )

        # 用户级（独立目录，不与项目级重叠）
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        user_dir = fake_home / ".csycode"
        user_dir.mkdir(parents=True)
        user_file = user_dir / "hooks.yaml"
        user_file.write_text(
            yaml.safe_dump({
                "hooks": [{
                    "name": "dup", "event": "SessionStart",
                    "action": {"type": "prompt", "text": "dup"},
                }]
            }),
            encoding="utf-8",
        )

        with patch.object(Path, "home", return_value=fake_home):
            engine = load(str(tmp_path))
        # 项目级保留，用户级同名跳过
        assert len(engine.rules) == 1
        assert engine.rules[0].event == Event.STOP
        captured = capsys.readouterr()
        assert "name conflict" in captured.err.lower()
