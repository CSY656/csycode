"""T6: Worktree 生命周期单测 —— enter / exit / remove / auto_cleanup。"""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from csycode.worktree import (
    Manager,
    ExitAction,
    ExitOptions,
    WorktreeHasChangesError,
    patch_manager_methods,
)


@pytest.fixture
def manager() -> Manager:
    """创建带 Manager 的临时 git 仓库。"""
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
    mgr = Manager(str(repo))
    patch_manager_methods(mgr)
    return mgr


@pytest.mark.asyncio
class TestEnter:
    async def test_enter_does_not_change_cwd(self, manager: Manager) -> None:
        """enter 不改变进程 cwd。"""
        await manager.create("test-enter", "HEAD", manual=True)
        old_cwd = os.getcwd()
        session = await manager.enter("test-enter")
        assert os.getcwd() == old_cwd
        assert session.worktree_name == "test-enter"


@pytest.mark.asyncio
class TestExit:
    async def test_exit_keep(self, manager: Manager) -> None:
        """exit action=KEEP 保留 Worktree。"""
        await manager.create("test-exit", "HEAD", manual=True)
        await manager.enter("test-exit")
        report = await manager.exit("test-exit", ExitAction.KEEP, ExitOptions())
        assert report.removed is False
        assert manager.get("test-exit") is not None

    async def test_exit_remove_without_discard_blocks_on_changes(
        self, manager: Manager
    ) -> None:
        """有未提交修改时 exit REMOVE 抛 WorktreeHasChangesError。"""
        await manager.create("test-exit2", "HEAD", manual=True)
        await manager.enter("test-exit2")
        wt = manager.get("test-exit2")
        (Path(wt.path) / "new.txt").write_text("changed")
        with pytest.raises(WorktreeHasChangesError):
            await manager.exit(
                "test-exit2", ExitAction.REMOVE, ExitOptions(discard_changes=False)
            )

    async def test_exit_remove_with_discard(self, manager: Manager) -> None:
        """显式 discard 时 exit REMOVE 成功删除。"""
        await manager.create("test-exit3", "HEAD", manual=True)
        await manager.enter("test-exit3")
        report = await manager.exit(
            "test-exit3", ExitAction.REMOVE, ExitOptions(discard_changes=True)
        )
        assert report.removed is True
        assert manager.get("test-exit3") is None


@pytest.mark.asyncio
class TestAutoCleanup:
    async def test_manual_worktree_kept(self, manager: Manager) -> None:
        """manual=True 的 Worktree 直接 kept。"""
        await manager.create("test-manual", "HEAD", manual=True)
        report = await manager.auto_cleanup("test-manual")
        assert report.kept is True
        assert manager.get("test-manual") is not None

    async def test_auto_without_changes_removed(self, manager: Manager) -> None:
        """无变更的临时 Worktree 自动删除。"""
        await manager.create("test-auto", "HEAD", manual=False)
        report = await manager.auto_cleanup("test-auto")
        assert report.kept is False
        assert manager.get("test-auto") is None

    async def test_auto_with_changes_kept(self, manager: Manager) -> None:
        """有变更的临时 Worktree 保留。"""
        await manager.create("test-auto2", "HEAD", manual=False)
        wt = manager.get("test-auto2")
        (Path(wt.path) / "changed.txt").write_text("modified")
        report = await manager.auto_cleanup("test-auto2")
        assert report.kept is True
        assert manager.get("test-auto2") is not None
