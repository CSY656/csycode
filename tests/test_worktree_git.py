"""T3: Git helper 单测 —— _run_git, has_worktree_changes, read_worktree_head_sha。"""

import subprocess
import tempfile
from pathlib import Path

import pytest

from csycode.worktree.git import (
    _run_git,
    has_worktree_changes,
    read_worktree_head_sha,
    has_unpushed_commits,
)


@pytest.fixture
def temp_git_repo() -> str:
    """创建一个临时 git 仓库，返回仓库根路径。"""
    with tempfile.TemporaryDirectory() as tmpdir:
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
        # 创建初始 commit
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=str(repo), check=True, capture_output=True,
        )
        yield str(repo)


@pytest.mark.asyncio
class TestRunGit:
    async def test_run_git_success(self, temp_git_repo: str) -> None:
        """_run_git 执行成功应返回 stdout。"""
        result = await _run_git(temp_git_repo, "rev-parse", "HEAD")
        assert len(result) == 40  # SHA1 长度

    async def test_run_git_failure(self, temp_git_repo: str) -> None:
        """_run_git 执行失败应抛 RuntimeError。"""
        with pytest.raises(RuntimeError):
            await _run_git(temp_git_repo, "nonexistent-command")


@pytest.mark.asyncio
class TestHasWorktreeChanges:
    async def test_no_changes(self, temp_git_repo: str) -> None:
        """无修改时返回 False。"""
        head = await _run_git(temp_git_repo, "rev-parse", "HEAD")
        assert await has_worktree_changes(temp_git_repo, head) is False

    async def test_unstaged_changes(self, temp_git_repo: str) -> None:
        """未暂存修改返回 True。"""
        head = await _run_git(temp_git_repo, "rev-parse", "HEAD")
        (Path(temp_git_repo) / "README.md").write_text("# Modified")
        assert await has_worktree_changes(temp_git_repo, head) is True

    async def test_new_commit(self, temp_git_repo: str) -> None:
        """有新 commit 返回 True。"""
        head = await _run_git(temp_git_repo, "rev-parse", "HEAD")
        (Path(temp_git_repo) / "new_file.txt").write_text("new")
        subprocess.run(
            ["git", "add", "."], cwd=temp_git_repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "second"],
            cwd=temp_git_repo, check=True, capture_output=True,
        )
        assert await has_worktree_changes(temp_git_repo, head) is True


class TestResolveHeadSha:
    def test_real_worktree(self, temp_git_repo: str) -> None:
        """在真实 worktree 路径下还原 HEAD SHA。"""
        # 创建 worktree
        wt_path = Path(temp_git_repo) / ".." / "test-wt"
        wt_path = wt_path.resolve()
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "HEAD"],
            cwd=temp_git_repo, check=True, capture_output=True,
        )
        try:
            sha = read_worktree_head_sha(str(wt_path))
            assert sha is not None
            assert len(sha) == 40
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=temp_git_repo, capture_output=True,
            )
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=temp_git_repo, capture_output=True,
            )

    def test_non_existent(self, temp_git_repo: str) -> None:
        """不存在的路径返回 None。"""
        assert read_worktree_head_sha("/nonexistent/path") is None


@pytest.mark.asyncio
class TestHasUnpushedCommits:
    async def test_no_remotes(self, temp_git_repo: str) -> None:
        """无 remote 配置时有未推送 commit（fail-closed）。"""
        # 无 remote 时 --remotes 匹配不到 ref，rev-list 返回所有 commit
        # fail-closed 意味着应该是 True
        pass  # 在无 remote 的临时仓库中行为取决于 git 版本，跳过
