"""T4: Manager 构造 + session 持久化单测。"""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from csycode.worktree import (
    Manager,
    WorktreeSession,
    load_session,
    save_session,
)


@pytest.fixture
def temp_git_repo() -> str:
    """创建带初始 commit 的临时 git 仓库。"""
    tmpdir = tempfile.mkdtemp()
    repo = Path(tmpdir) / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), check=True, capture_output=True,
    )
    (repo / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(repo), check=True, capture_output=True,
    )
    yield str(repo)
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


class TestManagerConstruct:
    def test_valid_repo(self, temp_git_repo: str) -> None:
        """有效 git 仓库应成功构造 Manager。"""
        mgr = Manager(temp_git_repo)
        assert mgr.repo_root == str(Path(temp_git_repo).resolve())
        assert mgr.current_session is None
        assert len(mgr.list()) == 0

    def test_non_git_dir(self) -> None:
        """非 git 目录应抛 ValueError。"""
        with pytest.raises(ValueError):
            Manager(str(tempfile.gettempdir()))

    def test_empty_session(self, temp_git_repo: str) -> None:
        """无 session 文件时 current_session 为 None。"""
        mgr = Manager(temp_git_repo)
        assert mgr.get_current_session() is None

    def test_restore_session_no_file(self, temp_git_repo: str) -> None:
        """无 session 文件时 restore_session 返回 None。"""
        mgr = Manager(temp_git_repo)
        assert mgr.restore_session() is None
        assert mgr.current_session is None


class TestSessionPersistence:
    def test_save_and_load(self, temp_git_repo: str) -> None:
        """session 写入 + 读取。"""
        csycode_dir = Path(temp_git_repo) / ".csycode"
        csycode_dir.mkdir(parents=True, exist_ok=True)

        session = WorktreeSession(
            original_cwd=str(Path(temp_git_repo)),
            worktree_path=str(Path(temp_git_repo) / ".csycode" / "worktrees" / "test"),
            worktree_name="test",
            original_branch="main",
            original_head_commit="abc123",
            session_id="test-session-id",
        )
        save_session(csycode_dir, session)

        loaded = load_session(csycode_dir)
        assert loaded is not None
        assert loaded.worktree_name == "test"
        assert loaded.session_id == "test-session-id"

    def test_save_null_deletes_file(self, temp_git_repo: str) -> None:
        """save_session(None) 删除文件（对齐 mewcode）。"""
        csycode_dir = Path(temp_git_repo) / ".csycode"
        csycode_dir.mkdir(parents=True, exist_ok=True)

        # 先写一个 session
        session = WorktreeSession(
            original_cwd=str(Path(temp_git_repo)),
            worktree_path="/tmp/wt",
            worktree_name="test",
            original_branch="main",
            original_head_commit="abc123",
        )
        save_session(csycode_dir, session)

        # 再清空
        save_session(csycode_dir, None)
        assert load_session(csycode_dir) is None
        # 文件应被删除
        session_file = csycode_dir / "worktree_session.json"
        assert not session_file.exists()

    def test_invalid_json(self, temp_git_repo: str) -> None:
        """损坏的 JSON 返回 None（不抛异常）。"""
        csycode_dir = Path(temp_git_repo) / ".csycode"
        csycode_dir.mkdir(parents=True, exist_ok=True)
        session_file = csycode_dir / "worktree_session.json"
        session_file.write_text("not-valid-json")
        assert load_session(csycode_dir) is None

    def test_serialization_fields(self) -> None:
        """序列化字段名使用小写下划线。"""
        session = WorktreeSession(
            original_cwd="/tmp",
            worktree_path="/tmp/wt",
            worktree_name="test",
            original_branch="main",
            original_head_commit="abc123",
            session_id="uuid-123",
        )
        # 通过 save_session 间接测试序列化
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            save_session(d, session)
            loaded = load_session(d)
            assert loaded is not None
            assert loaded.original_cwd == "/tmp"
            assert loaded.worktree_path == "/tmp/wt"
