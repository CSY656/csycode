"""WorktreeManager —— 核心管理器。

对齐 mewcode WorktreeManager:
- _run_git 为同步实例方法（封装 subprocess）但 csycode 保留 async
- read_worktree_head_sha 为静态方法（纯文件系统读）
- restore_session() 从磁盘恢复会话（解耦于 __init__）
- _get_current_branch / _get_head_commit 为可复用辅助方法
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .slug import flatten_slug
from .session import (
    WorktreeSession,
    load_session,
    save_session,
)
from .git import read_worktree_head_sha

log = logging.getLogger(__name__)

# 默认软链目录列表（可通过 WorktreeConfig 覆盖）
DEFAULT_SYMLINK_DIRS = ["node_modules", ".venv", "vendor"]


@dataclass
class Worktree:
    """单个 Worktree 的元信息。"""

    name: str  # 原始 slug（可能含 /）
    path: str  # 绝对路径
    branch: str  # worktree-<flat_slug>
    based_on: str  # 创建时的 base 引用
    head_commit: str  # 创建时的 commit SHA
    created: datetime  # no default — 由工厂方法填入
    manual: bool = False  # csycode 特有：是否用户手动创建


class WorktreeError(Exception):
    """Worktree 操作错误。"""

    pass


class Manager:
    """Git Worktree 管理器。

    所有状态变更受 asyncio.Lock 保护。
    Git 子进程操作不持锁，避免长锁阻塞。
    """

    def __init__(
        self,
        repo_root: str,
        symlink_directories: list[str] | None = None,
        worktree_dir: str | None = None,
    ) -> None:
        """初始化 Worktree 管理器。

        Args:
            repo_root: Git 仓库根目录的绝对路径。
            symlink_directories: 创建后设置 C 中要软链的目录列表。
            worktree_dir: Worktree 存放目录（默认 <repo_root>/.csycode/worktrees）。

        Raises:
            ValueError: repo_root 不是有效的 git 仓库根目录。
        """
        self.repo_root = str(Path(repo_root).resolve())
        self.symlink_directories = (
            symlink_directories
            if symlink_directories is not None
            else list(DEFAULT_SYMLINK_DIRS)
        )
        self.worktree_dir = worktree_dir or str(
            Path(self.repo_root) / ".csycode" / "worktrees"
        )
        self._csycode_dir = Path(self.repo_root) / ".csycode"
        self._lock = asyncio.Lock()
        self.active: dict[str, Worktree] = {}
        self.current_session: WorktreeSession | None = None

        # 校验 repo_root 是 git 仓库根目录
        result = subprocess.run(
            ["git", "-C", self.repo_root, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        top_level = result.stdout.strip()
        if result.returncode != 0 or not top_level:
            raise ValueError(f"不是有效的 Git 仓库: {self.repo_root}")
        if str(Path(top_level).resolve()) != self.repo_root:
            raise ValueError(
                f"路径不是 Git 仓库根目录: {self.repo_root}"
            )

        # 创建 worktree_dir（如不存在）
        Path(self.worktree_dir).mkdir(parents=True, exist_ok=True)

    # ── 辅助 git 方法 ─────────────────────────────────────────

    def _get_current_branch(self) -> str:
        """获取当前分支名（失败返回 "HEAD"）。"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else "HEAD"
        except (subprocess.SubprocessError, OSError):
            return "HEAD"

    def _get_head_commit(self) -> str:
        """获取当前 HEAD commit SHA（失败返回 ""）。"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.SubprocessError, OSError):
            return ""

    # ── 从持久化的 session 中恢复 ──────────────────────────────

    def restore_session(self) -> WorktreeSession | None:
        """从磁盘读取并恢复之前持久化的 WorktreeSession。

        对齐 mewcode: 在 __init__ 后调用（由 cli.py / app.py 触发）。
        - 若 session 文件不存在或损坏 → 返回 None
        - 若 session 指向的 worktree 目录已不存在 → 清理 session 文件，返回 None
        - 否则还原 active 映射与 current_session

        Returns:
            恢复的 WorktreeSession 或 None。
        """
        session = load_session(self._csycode_dir)
        if session is None:
            return None

        wt_path = session.worktree_path
        head_sha = read_worktree_head_sha(wt_path)
        if head_sha is None:
            # worktree 目录已不存在 → 清理
            save_session(self._csycode_dir, None)
            log.warning(
                "session worktree 目录已不存在，清空 session: %s", wt_path
            )
            return None

        wt = Worktree(
            name=session.worktree_name,
            path=wt_path,
            branch=f"worktree-{flatten_slug(session.worktree_name)}",
            based_on="unknown",
            head_commit=head_sha,
            created=datetime.now(),
            manual=True,
        )
        self.active[session.worktree_name] = wt
        self.current_session = session
        return session

    # ── 查询方法 ─────────────────────────────────────────────

    def list(self) -> list[Worktree]:
        """列出所有活跃的 Worktree（按 name 排序）。"""
        return sorted(self.active.values(), key=lambda w: w.name)

    def get(self, name: str) -> Worktree | None:
        """按名称获取 Worktree。"""
        return self.active.get(name)

    def get_current_session(self) -> WorktreeSession | None:
        """获取当前活跃的 Worktree 会话。"""
        return self.current_session
