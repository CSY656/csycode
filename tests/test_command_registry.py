"""命令注册中心测试 —— 注册、冲突检测、前缀匹配、visible 排序。"""

from __future__ import annotations

import pytest

from csycode.command.command import Command, Kind
from csycode.command.registry import Registry
from csycode.command.ui import NopUI


# ── helpers ───────────────────────────────────────────────────────────

async def _nop_handler(ui) -> None:
    pass


def _make_cmd(name: str, **kwargs) -> Command:
    defaults = {
        "name": name,
        "description": f"{name} 描述",
        "kind": Kind.LOCAL,
        "handler": _nop_handler,
    }
    defaults.update(kwargs)
    return Command(**defaults)


# ── 注册测试 ──────────────────────────────────────────────────────────


class TestRegister:
    def test_register_ok(self):
        """正常注册不抛异常，lookup 能查到。"""
        reg = Registry()
        cmd = _make_cmd("help")
        reg.register(cmd)
        assert reg.lookup("help") is cmd

    def test_register_multiple_ok(self):
        """多条注册成功。"""
        reg = Registry()
        reg.register(_make_cmd("help"))
        reg.register(_make_cmd("status"))
        reg.register(_make_cmd("exit", kind=Kind.UI))
        assert len(reg.visible()) == 3

    def test_register_duplicate_name_raises(self):
        """同名二次注册 raise RuntimeError。"""
        reg = Registry()
        reg.register(_make_cmd("help"))
        with pytest.raises(RuntimeError, match="command conflict"):
            reg.register(_make_cmd("help"))

    def test_register_duplicate_alias_raises(self):
        """别名冲突 raise RuntimeError。"""
        reg = Registry()
        reg.register(_make_cmd("help", aliases=["h"]))
        with pytest.raises(RuntimeError, match="command conflict"):
            reg.register(_make_cmd("hello", aliases=["h"]))

    def test_register_alias_collides_with_name(self):
        """别名与已有命令名冲突。"""
        reg = Registry()
        reg.register(_make_cmd("help"))
        with pytest.raises(RuntimeError, match="command conflict"):
            reg.register(_make_cmd("hello", aliases=["help"]))

    def test_register_empty_name_raises(self):
        """空名字 raise RuntimeError。"""
        reg = Registry()
        with pytest.raises(RuntimeError):
            reg.register(Command("", "", Kind.LOCAL, _nop_handler))

    def test_register_uppercase_name_raises(self):
        """大写名字 raise RuntimeError。"""
        reg = Registry()
        with pytest.raises(RuntimeError):
            reg.register(Command("Help", "", Kind.LOCAL, _nop_handler))


# ── lookup 测试 ──────────────────────────────────────────────────────


class TestLookup:
    def test_lookup_case_insensitive(self):
        """lookup 大小写不敏感。"""
        reg = Registry()
        reg.register(_make_cmd("help"))
        assert reg.lookup("HELP") is not None
        assert reg.lookup("Help") is not None
        assert reg.lookup("help") is not None

    def test_lookup_alias(self):
        """别名可以 lookup。"""
        reg = Registry()
        reg.register(_make_cmd("help", aliases=["h", "?"]))
        assert reg.lookup("h") is reg.lookup("help")

    def test_lookup_missing(self):
        """未注册名返回 None。"""
        reg = Registry()
        assert reg.lookup("foobar") is None


# ── visible 测试 ─────────────────────────────────────────────────────


class TestVisible:
    def test_visible_sorted(self):
        """visible() 返回按 name 字典序排序的列表。"""
        reg = Registry()
        reg.register(_make_cmd("status"))
        reg.register(_make_cmd("help"))
        reg.register(_make_cmd("exit"))
        names = [c.name for c in reg.visible()]
        assert names == ["exit", "help", "status"]

    def test_visible_excludes_hidden(self):
        """hidden=True 的命令不出现在 visible 中。"""
        reg = Registry()
        reg.register(_make_cmd("help"))
        reg.register(_make_cmd("secret", hidden=True))
        assert len(reg.visible()) == 1
        assert reg.visible()[0].name == "help"

    def test_visible_returns_copy(self):
        """visible() 返回副本，外部修改不影响内部。"""
        reg = Registry()
        reg.register(_make_cmd("help"))
        v = reg.visible()
        v.clear()
        assert len(reg.visible()) == 1


# ── prefix_match 测试 ────────────────────────────────────────────────


class TestPrefixMatch:
    def test_prefix_match(self):
        """prefix_match 只返回主名匹配的命令。"""
        reg = Registry()
        reg.register(_make_cmd("session"))
        reg.register(_make_cmd("status"))
        reg.register(_make_cmd("exit"))
        matches = reg.prefix_match("/s")
        names = [c.name for c in matches]
        assert names == ["session", "status"]

    def test_prefix_match_empty(self):
        """空前缀返回全部 visible。"""
        reg = Registry()
        reg.register(_make_cmd("help"))
        reg.register(_make_cmd("status"))
        assert len(reg.prefix_match("")) == 2

    def test_prefix_match_no_result(self):
        """无匹配返回空列表。"""
        reg = Registry()
        reg.register(_make_cmd("help"))
        matches = reg.prefix_match("/x")
        assert matches == []

    def test_prefix_match_ignores_alias(self):
        """prefix_match 不按别名匹配 —— 别名 'x' 不应匹配名为 'status' 的命令。"""
        reg = Registry()
        # status 本身以 's' 开头，别名 'x' 不应参与前缀匹配
        reg.register(_make_cmd("status", aliases=["xstatus"]))
        matches = reg.prefix_match("/x")
        # 前缀 /x 不匹配任何主名，应返回空
        assert len(matches) == 0
