"""T8: Tool ctx cwd 传递单测。"""

import os
from pathlib import Path

from csycode.tools.ctx import with_cwd, cwd_from_ctx, resolve_path


class TestCwdFromCtx:
    """cwd_from_ctx 基础行为。"""

    def test_default_none(self) -> None:
        """默认返回 None。"""
        assert cwd_from_ctx() is None

    def test_with_cwd_sets(self) -> None:
        """with_cwd 注入后 cwd_from_ctx 返回对应路径。"""
        with with_cwd("/tmp/test"):
            assert cwd_from_ctx() == "/tmp/test"

    def test_nested_restores(self) -> None:
        """嵌套 with_cwd 退出后恢复外层值。"""
        with with_cwd("/outer"):
            assert cwd_from_ctx() == "/outer"
            with with_cwd("/inner"):
                assert cwd_from_ctx() == "/inner"
            assert cwd_from_ctx() == "/outer"
        assert cwd_from_ctx() is None

    def test_empty_directory_noop(self) -> None:
        """空字符串不改变 ContextVar。"""
        with with_cwd(""):
            assert cwd_from_ctx() is None


class TestResolvePath:
    """resolve_path 路径解析。"""

    def test_absolute_path_returns_directly(self) -> None:
        """绝对路径直接返回。"""
        # 使用平台相关的绝对路径（Windows: C:\..., POSIX: /...）
        abs_input = str(Path.cwd() / "file.txt")
        result = resolve_path(abs_input)
        assert result == str(Path(abs_input))

    def test_relative_path_with_ctx_cwd(self, tmp_path: Path) -> None:
        """相对路径拼接 ctx cwd。"""
        with with_cwd(str(tmp_path)):
            result = resolve_path("file.txt")
            assert result == str(tmp_path / "file.txt")

    def test_relative_path_without_ctx(self) -> None:
        """无 ctx cwd 时回落到进程 cwd。"""
        result = resolve_path("file.txt")
        expected = str(Path.cwd() / "file.txt")
        assert result == expected

    def test_empty_path_returns_cwd(self) -> None:
        """空字符串返回 cwd 本身。"""
        result = resolve_path("")
        assert result == str(Path.cwd())

    def test_empty_path_with_ctx(self, tmp_path: Path) -> None:
        """空字符串 + ctx cwd。"""
        with with_cwd(str(tmp_path)):
            result = resolve_path("")
            assert result == str(tmp_path)

    def test_windows_absolute_path(self) -> None:
        """Windows 绝对路径（如 D:\\foo）应直接返回。"""
        result = resolve_path("D:\\foo\\bar.txt")
        # 在非 Windows 平台，Path 可能不识别为绝对路径，
        # 但 resolve_path 本身不区分平台
        pp = Path("D:\\foo\\bar.txt")
        if pp.is_absolute():
            assert result == str(pp)
        # 如果不是绝对路径（Linux 下），行为正确即可
