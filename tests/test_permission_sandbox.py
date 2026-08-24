"""路径沙箱单元测试（T3）。"""

import os
import platform
from pathlib import Path

import pytest

from csycode.permission.sandbox import (
    eval_symlinks_or_ancestor,
    resolve_root,
    sandbox_ok,
)

# Windows 上创建符号链接需要管理员权限，相关测试跳过
_WIN_SKIP_SYMLINK = platform.system() == "Windows"


class _FakeEngine:
    """用于 sandbox_ok 测试的轻量引擎替身。"""

    def __init__(self, root: str) -> None:
        self.root = root


class TestResolveRoot:
    """项目根解析测试。"""

    def test_resolve_existing_dir(self, tmp_path):
        """解析已存在的目录。"""
        result = resolve_root(str(tmp_path))
        assert os.path.isabs(result)
        assert result == str(tmp_path.resolve())

    def test_resolve_expands_user(self):
        """expanduser 生效。"""
        result = resolve_root("~/Documents")
        assert "~" not in result
        assert os.path.isabs(result)

    def test_resolve_missing_raises(self, tmp_path):
        """不存在的目录应抛 FileNotFoundError。"""
        missing = str(tmp_path / "nonexistent")
        with pytest.raises(FileNotFoundError):
            resolve_root(missing)


class TestSandboxOk:
    """沙箱围栏测试。"""

    def test_file_inside_root(self, tmp_path):
        """项目内的文件应通过。"""
        root = str(tmp_path)
        (tmp_path / "test.py").write_text("hello")
        engine = _FakeEngine(root)
        assert sandbox_ok(engine, "test.py") is True

    def test_file_outside_root(self, tmp_path):
        """项目外的文件应被拒。"""
        engine = _FakeEngine(str(tmp_path))
        assert sandbox_ok(engine, "/etc/passwd") is False

    def test_parent_dir_escape(self, tmp_path):
        """../ 逃逸应被拒。"""
        engine = _FakeEngine(str(tmp_path))
        assert sandbox_ok(engine, "../outside") is False

    def test_empty_path_means_root(self, tmp_path):
        """空路径视为项目根。"""
        engine = _FakeEngine(str(tmp_path))
        assert sandbox_ok(engine, "") is True
        assert sandbox_ok(engine, "   ") is True

    def test_relative_path_resolved(self, tmp_path):
        """相对路径相对 root 解析。"""
        root = str(tmp_path)
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "f.txt").write_text("x")
        engine = _FakeEngine(root)
        assert sandbox_ok(engine, "subdir/f.txt") is True

    @pytest.mark.skipif(_WIN_SKIP_SYMLINK, reason="Windows 建符号链接需管理员权限")
    def test_symlink_escape(self, tmp_path):
        """项目内的软链接指向项目外应被拒。"""
        root = str(tmp_path)
        (tmp_path / "inside_link").symlink_to("/etc/passwd")
        engine = _FakeEngine(root)
        assert sandbox_ok(engine, "inside_link") is False

    @pytest.mark.skipif(_WIN_SKIP_SYMLINK, reason="Windows 建符号链接需管理员权限")
    def test_symlink_to_inside(self, tmp_path):
        """项目内的软链接指向项目内应通过。"""
        root = str(tmp_path)
        (tmp_path / "target.txt").write_text("hello")
        (tmp_path / "good_link").symlink_to(str(tmp_path / "target.txt"))
        engine = _FakeEngine(root)
        assert sandbox_ok(engine, "good_link") is True

    def test_new_file_ancestor_fallback(self, tmp_path):
        """新建文件（含未创建中间目录）应通过祖先回退（专测 T3 分支）。"""
        root = str(tmp_path)
        engine = _FakeEngine(root)
        # 中间目录 new_dir 尚未创建，文件本身也不存在
        assert sandbox_ok(engine, "new_dir/sub_dir/new_file.txt") is True

    def test_absolute_path_inside(self, tmp_path):
        """绝对路径在项目内应通过。"""
        root = str(tmp_path)
        abs_inside = str(tmp_path / "inside.txt")
        Path(abs_inside).write_text("x")
        engine = _FakeEngine(root)
        assert sandbox_ok(engine, abs_inside) is True


class TestEvalSymlinksOrAncestor:
    """符号链接安全解析测试。"""

    def test_existing_file(self, tmp_path):
        """已存在文件直接 resolve。"""
        f = tmp_path / "real.txt"
        f.write_text("hello")
        result = eval_symlinks_or_ancestor(str(f))
        assert result == str(f.resolve())

    def test_nonexistent_file_with_existing_parent(self, tmp_path):
        """不存在的文件逐级回退到已存在祖先。"""
        f = tmp_path / "new.txt"  # 不存在
        result = eval_symlinks_or_ancestor(str(f))
        # 应等于父目录 resolve 后 + 文件名
        expected = str(tmp_path.resolve() / "new.txt")
        assert result == expected

    def test_nonexistent_deep_path(self, tmp_path):
        """深层不存在的路径逐级回退。"""
        deep = tmp_path / "a" / "b" / "c" / "d.txt"
        result = eval_symlinks_or_ancestor(str(deep))
        # 应回退到 tmp_path.resolve() + a/b/c/d.txt
        expected = str(tmp_path.resolve() / "a" / "b" / "c" / "d.txt")
        assert result == expected
