"""Worktree 生命周期管理: enter / exit / remove / auto_cleanup。

对齐 mewcode manager.py 的 enter/exit/auto_cleanup 逻辑:
- 变更保护使用 count_worktree_changes 提供具体计数
- WorktreeHasChangesError 携带 uncommitted / new_commits 详情
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from dataclasses import dataclass
from enum import Enum
from .manager import Worktree
from .session import WorktreeSession, save_session
from .git import (
    _run_git,
    has_worktree_changes,
    count_worktree_changes,
    CleanupResult,
)

log = logging.getLogger(__name__)


# ── 枚举与数据类型 ────────────────────────────────────────────


class ExitAction(str, Enum):
    KEEP = "keep"
    REMOVE = "remove"


@dataclass
class ExitOptions:
    discard_changes: bool = False


@dataclass
class ExitReport:
    removed: bool
    path: str
    branch: str


class WorktreeHasChangesError(Exception):
    """Worktree 有未提交修改或本地多于 base 的 commit。

    对齐 mewcode WorktreeError: 携带具体变更计数。
    """

    def __init__(
        self,
        name: str,
        path: str,
        uncommitted: int = 0,
        new_commits: int = 0,
    ) -> None:
        self.name = name
        self.path = path
        self.uncommitted = uncommitted
        self.new_commits = new_commits

        detail_parts = []
        if uncommitted:
            detail_parts.append(f"{uncommitted} 个未提交修改")
        if new_commits:
            detail_parts.append(f"{new_commits} 个新 commit")
        detail = "，".join(detail_parts) if detail_parts else "有未提交变更"

        super().__init__(
            f"Worktree '{name}' {detail}。"
            f"请先提交或使用 --discard 强制删除"
        )


# ── 生命周期方法 ──────────────────────────────────────────────


async def _enter_worktree(manager, name: str) -> WorktreeSession:
    """进入 Worktree（不改变进程 cwd）。"""
    async with manager._lock:
        wt = manager.active.get(name)
        if wt is None:
            raise manager.WorktreeError(f"Worktree 不存在: {name}")

        original_cwd = os.getcwd()
        original_branch = manager._get_current_branch()
        original_head = manager._get_head_commit()

        session = WorktreeSession(
            original_cwd=original_cwd,
            worktree_path=wt.path,
            worktree_name=name,
            original_branch=original_branch,
            original_head_commit=original_head,
            session_id=secrets.token_hex(8),
        )
        manager.current_session = session
        save_session(manager._csycode_dir, session)
        return session


async def _exit_worktree(
    manager, name: str, action: ExitAction, opts: ExitOptions
) -> ExitReport:
    """退出 Worktree 会话。

    变更保护使用 count_worktree_changes 提供具体计数（对齐 mewcode）。
    """
    async with manager._lock:
        session = manager.current_session
        if session is None:
            raise manager.WorktreeError("当前没有活跃的 Worktree 会话")
        if session.worktree_name != name:
            raise manager.WorktreeError(
                f"只能退出当前会话的 Worktree '{session.worktree_name}'，"
                f"不能退出 '{name}'"
            )

        wt = manager.active.get(name)
        if wt is None:
            raise manager.WorktreeError(f"Worktree 不在 active 中: {name}")

        # 变更保护（带计数）
        if action == ExitAction.REMOVE and not opts.discard_changes:
            changes = await count_worktree_changes(wt.path, wt.head_commit)
            if changes.uncommitted > 0 or changes.new_commits > 0:
                raise WorktreeHasChangesError(
                    name,
                    wt.path,
                    uncommitted=changes.uncommitted,
                    new_commits=changes.new_commits,
                )

        # os.chdir 兜底
        with contextlib.suppress(OSError):
            os.chdir(session.original_cwd)

        # 清空 session
        manager.current_session = None
        save_session(manager._csycode_dir, None)

        # 删除
        if action == ExitAction.REMOVE:
            await _do_remove_worktree(manager, wt)
            return ExitReport(removed=True, path=wt.path, branch=wt.branch)
        else:
            return ExitReport(removed=False, path=wt.path, branch=wt.branch)


async def _remove_worktree(manager, name: str, opts: ExitOptions) -> None:
    """独立 remove 入口（允许删除非当前 session 的 Worktree）。"""
    async with manager._lock:
        wt = manager.active.get(name)
        if wt is None:
            raise manager.WorktreeError(f"Worktree 不存在: {name}")

        # 变更保护（带计数）
        if not opts.discard_changes:
            changes = await count_worktree_changes(wt.path, wt.head_commit)
            if changes.uncommitted > 0 or changes.new_commits > 0:
                raise WorktreeHasChangesError(
                    name,
                    wt.path,
                    uncommitted=changes.uncommitted,
                    new_commits=changes.new_commits,
                )

        # 若要删除的是当前 session，先清空 session
        if (
            manager.current_session is not None
            and manager.current_session.worktree_name == name
        ):
            with contextlib.suppress(OSError):
                os.chdir(manager.current_session.original_cwd)
            manager.current_session = None
            save_session(manager._csycode_dir, None)

        await _do_remove_worktree(manager, wt)


async def _do_remove_worktree(manager, wt: Worktree) -> None:
    """执行实际的 git worktree remove + 分支删除。

    对齐 mewcode _remove_worktree:
    - git worktree remove --force
    - await asyncio.sleep(0.1) 等 lockfile 释放
    - git branch -D <branch>
    """
    try:
        await _run_git(
            manager.repo_root, "worktree", "remove", "--force", wt.path
        )
    except RuntimeError:
        log.warning("git worktree remove 失败，尝试 rmtree + prune")
        import shutil

        shutil.rmtree(wt.path, ignore_errors=True)
        try:
            await _run_git(manager.repo_root, "worktree", "prune")
        except RuntimeError:
            pass

    await asyncio.sleep(0.1)
    try:
        await _run_git(manager.repo_root, "branch", "-D", wt.branch)
    except RuntimeError:
        pass

    manager.active.pop(wt.name, None)


async def _auto_cleanup_worktree(manager, name: str) -> CleanupResult:
    """SubAgent 退出时自动清理 Worktree。

    对齐 mewcode auto_cleanup:
    - manual=True: 直接 kept（csycode 特有）
    - 有变更: kept 并返回路径信息
    - 无变更: remove
    """
    wt = manager.active.get(name)
    if wt is None:
        return CleanupResult(kept=False)

    if wt.manual:
        return CleanupResult(kept=True, path=wt.path, branch=wt.branch)

    if await has_worktree_changes(wt.path, wt.head_commit):
        return CleanupResult(kept=True, path=wt.path, branch=wt.branch)

    await _do_remove_worktree(manager, wt)
    return CleanupResult(kept=False)
