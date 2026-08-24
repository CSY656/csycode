r"""Worktree 底层 git 操作与变更检测。

- _run_git: 统一异步 git 子进程调用（csycode 保持 async，不用 mewcode 的同步 subprocess）
- Changes: 变更计数 dataclass（对齐 mewcode changes.py）
- read_worktree_head_sha: 纯文件系统读还原 HEAD SHA（支持 commondir / packed-refs）
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path


# ── 统一 git 调用 ───────────────────────────────────────────────


async def _run_git(work_dir: str, *args: str) -> str:
    """统一异步 git 子进程调用。

    环境变量注入 GIT_TERMINAL_PROMPT=0 + GIT_ASKPASS=""，
    stdin=DEVNULL 防止子进程等待输入。

    Raises:
        RuntimeError: git 退出码非零。
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""

    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=work_dir,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace").rstrip("\n")
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败 (exit={proc.returncode}): {stderr}"
        )
    return stdout


# ── 变更检测 ─────────────────────────────────────────────────────


@dataclass
class Changes:
    """Worktree 变更计数（对齐 mewcode changes.py）。

    fail-closed: git 命令出错时对应字段设为 1（宁可保守报告有变更）。
    """

    uncommitted: int = 0
    new_commits: int = 0


@dataclass
class CleanupResult:
    """自动清理结果。"""

    kept: bool
    path: str = ""
    branch: str = ""


async def count_worktree_changes(
    wt_path: str, head_commit: str
) -> Changes:
    """统计 Worktree 的未提交修改行数和本地新增 commit 数。

    fail-closed: 任一 git 命令出错时对应字段设为 1。
    """
    changes = Changes()

    # 1. git status --porcelain → 未提交修改行数
    try:
        status = await _run_git(wt_path, "status", "--porcelain")
        changes.uncommitted = len(
            [line for line in status.splitlines() if line.strip()]
        )
    except RuntimeError:
        changes.uncommitted = 1  # fail-closed

    # 2. git rev-list --count <base>..HEAD → 新增 commit 数
    try:
        count_str = await _run_git(
            wt_path, "rev-list", "--count", f"{head_commit}..HEAD"
        )
        changes.new_commits = int(count_str.strip())
    except (RuntimeError, ValueError):
        changes.new_commits = 1  # fail-closed

    return changes


async def has_worktree_changes(wt_path: str, head_commit: str) -> bool:
    """检测 Worktree 是否有未提交修改或本地多于 base 的 commit。"""
    c = await count_worktree_changes(wt_path, head_commit)
    return c.uncommitted > 0 or c.new_commits > 0


async def has_unpushed_commits(wt_path: str) -> bool:
    """检测 Worktree 是否有未推送的 commit。

    Fail-closed: 出错返回 True（宁可保留）。
    """
    try:
        stdout = await _run_git(
            wt_path, "rev-list", "--max-count=1", "HEAD", "--not", "--remotes"
        )
        return bool(stdout.strip())
    except RuntimeError:
        return True  # fail-closed


# ── 快速恢复：文件系统读 HEAD SHA ──────────────────────────────────


def read_worktree_head_sha(wt_path: str) -> str | None:
    """纯文件系统读还原 Worktree 的 HEAD commit SHA。

    对齐 mewcode WorktreeManager.read_worktree_head_sha:
    - 读 .git 文件（gitdir 指针）
    - 支持 commondir（共享仓库）
    - 支持 packed-refs 回退
    - 支持 detached HEAD
    - 不调 git 子进程，毫秒级返回

    Returns:
        commit SHA 或 None（任何步骤失败）。
    """
    wt = Path(wt_path)
    git_file = wt / ".git"
    if not git_file.exists():
        return None

    try:
        content = git_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not content.startswith("gitdir:"):
        return None

    gitdir = Path(content.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (wt / gitdir).resolve()

    # 支持 commondir（worktree 共享 objects/refs 的仓库）
    commondir_file = gitdir / "commondir"
    if commondir_file.exists():
        try:
            commondir_rel = commondir_file.read_text(encoding="utf-8").strip()
            commondir = (gitdir / commondir_rel).resolve()
        except OSError:
            commondir = gitdir
    else:
        commondir = gitdir

    # 读 HEAD
    head_file = gitdir / "HEAD"
    if not head_file.exists():
        return None

    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if head_content.startswith("ref:"):
        ref_path = head_content.split(":", 1)[1].strip()
        ref_file = gitdir / ref_path
        if not ref_file.exists():
            ref_file = commondir / ref_path
        if ref_file.exists():
            try:
                return ref_file.read_text(encoding="utf-8").strip()
            except OSError:
                pass

        # packed-refs 回退
        packed_refs = commondir / "packed-refs"
        if packed_refs.exists():
            try:
                for line in packed_refs.read_text(
                    encoding="utf-8"
                ).splitlines():
                    if line.strip() and not line.startswith("#"):
                        parts = line.split()
                        if len(parts) == 2 and parts[1] == ref_path:
                            return parts[0]
            except OSError:
                pass
        return None

    # detached HEAD: 内容就是 SHA
    return head_content
