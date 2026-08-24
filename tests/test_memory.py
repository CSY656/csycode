"""记忆子系统测试 —— Store CRUD、Manager 索引加载、操作分发。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from csycode.memory.store import Store
from csycode.memory.types import Note, NoteType, UpdateAction
from csycode.memory.manager import Manager


class TestStore:
    @pytest.fixture
    def store_dir(self) -> str:
        with tempfile.TemporaryDirectory() as td:
            yield td

    def test_create_note(self, store_dir: str):
        """创建笔记 → 文件存在、MEMORY.md 有索引行。"""
        store = Store(store_dir)
        action = UpdateAction(
            action="create",
            level="project",
            note=Note(
                name="test-memory",
                description="测试记忆",
                type=NoteType.PROJECT,
                content="这是一条测试记忆内容。",
            ),
        )
        store.apply([action])

        # 检查文件
        fname = "project_test-memory.md"
        fpath = os.path.join(store_dir, fname)
        assert os.path.isfile(fpath)

        content = Path(fpath).read_text("utf-8")
        assert "name: test-memory" in content
        assert "type: project" in content
        assert "这是一条测试记忆内容" in content

        # 检查 MEMORY.md
        idx_content = store.load_index()
        assert "test-memory" in idx_content
        assert fname in idx_content

    def test_update_note(self, store_dir: str):
        """更新已有笔记：内容变化但 created 时间不变。"""
        store = Store(store_dir)
        store.apply([
            UpdateAction(
                action="create",
                level="project",
                note=Note(
                    name="update-test",
                    description="旧描述",
                    type=NoteType.PROJECT,
                    content="旧内容",
                ),
            )
        ])
        store.apply([
            UpdateAction(
                action="update",
                level="project",
                note=Note(
                    name="update-test",
                    description="新描述",
                    type=NoteType.PROJECT,
                    content="新内容",
                ),
            )
        ])

        fname = "project_update-test.md"
        fpath = os.path.join(store_dir, fname)
        content = Path(fpath).read_text("utf-8")
        assert "新内容" in content
        assert "新描述" in content

    def test_delete_note(self, store_dir: str):
        """删除笔记 → 文件不存在、MEMORY.md 对应行消失。"""
        store = Store(store_dir)
        store.apply([
            UpdateAction(
                action="create",
                level="project",
                note=Note(
                    name="delete-test",
                    description="测试删除",
                    type=NoteType.PROJECT,
                    content="要删除的内容",
                ),
            )
        ])
        store.apply([
            UpdateAction(
                action="delete",
                level="project",
                note=Note(
                    name="delete-test",
                    description="",
                    type=NoteType.PROJECT,
                    content="",
                ),
            )
        ])

        fname = "project_delete-test.md"
        fpath = os.path.join(store_dir, fname)
        assert not os.path.exists(fpath)

        idx_content = store.load_index()
        assert fname not in idx_content

    def test_load_index_empty(self, store_dir: str):
        """空目录返回空字符串。"""
        store = Store(store_dir)
        assert store.load_index() == ""


class TestManager:
    @pytest.fixture
    def manager_dirs(self) -> tuple[str, str]:
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as user:
            yield proj, user

    def test_load_index_empty(self, manager_dirs: tuple[str, str]):
        """两个目录都空时返回空。"""
        proj, user = manager_dirs
        mgr = Manager(project_dir=proj, user_dir=user)
        assert mgr.load_index() == ""

    def test_load_index_merged(self, manager_dirs: tuple[str, str]):
        """两级索引合并。"""
        proj, user = manager_dirs
        proj_idx = os.path.join(proj, "MEMORY.md")
        user_idx = os.path.join(user, "MEMORY.md")
        Path(proj_idx).write_text("- [P1](p1.md) — project memory", encoding="utf-8")
        Path(user_idx).write_text("- [U1](u1.md) — user memory", encoding="utf-8")

        mgr = Manager(project_dir=proj, user_dir=user)
        result = mgr.load_index()
        assert "project memory" in result
        assert "user memory" in result
        # 项目级在前
        assert result.index("project memory") < result.index("user memory")

    def test_load_index_truncate(self, manager_dirs: tuple[str, str]):
        """超 25KB 截断。"""
        proj, user = manager_dirs
        proj_idx = os.path.join(proj, "MEMORY.md")
        # 写入超过 25KB 的内容
        big_line = "- [M](m.md) — " + "x" * 200 + "\n"
        Path(proj_idx).write_text(big_line * 150, encoding="utf-8")  # ~30KB

        mgr = Manager(project_dir=proj, user_dir=user)
        result = mgr.load_index()
        # load_index 现在返回纯索引合并（含 "## 项目级记忆" 标题），
        # 不再做 truncation——truncation 由 build_memory_prompt 的 _truncate_entrypoint 完成
        assert len(result.encode("utf-8")) > 25000
        assert "项目级记忆" in result
