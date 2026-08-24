"""Worktree 后台过期清理 (sweep_stale) + 周期性清理任务。

对齐 mewcode cleanup.py:
- EPHEMERAL_PATTERNS: 5 个正则覆盖多种临时 Worktree 命名模式
- cleanup_stale_worktrees: 三层过滤 + 变更检查
- start_stale_cleanup_task: 周期性后台清理任务
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from .git import (
    read_worktree_head_sha,
    has_worktree_changes,
    has_unpushed_commits,
    _run_git,
)

if TYPE_CHECKING:
    from .manager import Manager

log = logging.getLogger(__name__)

# ── 匹配临时 Worktree 的正则（对齐 mewcode） ───────────────────

EPHEMERAL_PATTERNS = [
    re.compile(r"^agent-a[0-9a-f]{7}$"),  # SubAgent 临时 Worktree
    re.compile(r"^wf_[0-9a-f]{8}-[0-9a-f]{3}-\d+$"),  # Workflow
    re.compile(r"^wf-\d+$"),  # Workflow 备用
    re.compile(
        r"^bridge-[A-Za-z0-9_]+(-[A-Za-z0-9_]+)*$"
    ),  # Bridge
    re.compile(
        r"^job-[a-zA-Z0-9._-]{1,55}-[0-9a-f]{8}$"
    ),  # Job
]

EPHEMERAL_PATTERN = EPHEMERAL_PATTERNS[0]  # 向后兼容: 第一个模式


def _is_ephemeral(name: str) -> bool:
    """判断名称是否匹配任一临时 Worktree 命名模式。"""
    return any(p.match(name) for p in EPHEMERAL_PATTERNS)


def random_agent_name() -> str:
    """生成 SubAgent 临时 worktree 名称。

    Returns:
        形如 "agent-a" + 7 位 hex 的字符串（如 "agent-a1b2c3d"）。
    """
    return "agent-a" + secrets.token_hex(4)[:7]


# ── 清理逻辑 ─────────────────────────────────────────────────


async def cleanup_stale_worktrees(
    manager: "Manager", cutoff_hours: int
) -> int:
    """清理过期的临时 Worktree。

    对齐 mewcode cleanup_stale_worktrees:
    1. 名字只匹配临时模式
    2. 跳过当前 session + 未过期（mtime 在 cutoff 内）
    3. 有变更 / 未推送 commit 跳过（fail-closed）
    4. 在 active 中走标准 remove，否则直接 git rmtree

    Args:
        manager: Worktree 管理器。
        cutoff_hours: 过期小时数（mtime 在此之前的才考虑清理）。

    Returns:
        已删除的 Worktree 数量。
    """
    cutoff = datetime.now() - timedelta(hours=cutoff_hours)
    removed = 0
    worktree_dir = Path(manager.worktree_dir)

    if not worktree_dir.exists():
        return 0

    for entry in sorted(worktree_dir.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name

        # 第一层：名字匹配
        if not _is_ephemeral(name):
            continue

        # 第二层：跳过当前 session
        if manager.current_session is not None and (
            manager.current_session.worktree_path == str(entry)
            or manager.current_session.worktree_name == name
        ):
            continue

        # 第三层：时间过滤
        try:
            mtime = datetime.fromtimestamp(entry.stat().st_mtime)
            if mtime > cutoff:
                continue
        except OSError:
            continue

        # 读 HEAD SHA
        head_sha = read_worktree_head_sha(str(entry))
        if head_sha is None:
            continue

        # 第四层：变更检查（fail-closed）
        try:
            if await has_worktree_changes(str(entry), head_sha):
                continue
        except Exception:
            continue

        # 第五层：未推送 commit 检查（fail-closed）
        try:
            if await has_unpushed_commits(str(entry)):
                continue
        except Exception:
            continue

        # 通过全部检查 → 删除
        try:
            if name in manager.active:
                # 在 active 中：走标准 remove
                from .lifecycle import _do_remove_worktree

                await _do_remove_worktree(manager, manager.active[name])
            else:
                # 不在 active 中：直接 git rmtree
                await _run_git(
                    manager.repo_root,
                    "worktree",
                    "remove",
                    "--force",
                    str(entry),
                )
            removed += 1
            log.info("已清理过期 Worktree: %s", name)
        except Exception as e:
            log.warning("清理过期 Worktree %s 失败: %s", name, e)

    return removed


async def start_stale_cleanup_task(
    manager: "Manager",
    interval: int = 3600,
    cutoff_hours: int = 24,
) -> None:
    """启动周期性后台清理任务。

    对齐 mewcode start_stale_cleanup_task:
    - 每 interval 秒运行一次 cleanup_stale_worktrees
    - 永不退出（由 asyncio.CancelledError 终止）

    Args:
        manager: Worktree 管理器。
        interval: 清理间隔（秒），默认 3600（1 小时）。
        cutoff_hours: 过期截止小时数，默认 24。
    """
    while True:
        await asyncio.sleep(interval)
        try:
            count = await cleanup_stale_worktrees(manager, cutoff_hours)
            if count:
                log.info("过期 Worktree 清理完成，删除了 %d 个", count)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("过期 Worktree 清理异常: %s", e)


# ── 向后兼容: sweep_stale ─────────────────────────────────────


async def sweep_stale(manager: "Manager", cutoff: datetime) -> list[str]:
    """清理过期的临时 Worktree（向后兼容接口）。

    委托给 cleanup_stale_worktrees，返回名称列表。
    """
    hours = max(0, (datetime.now() - cutoff).total_seconds() / 3600)
    await cleanup_stale_worktrees(manager, int(hours) or 1)
    return []  # 无法精确恢复名称列表，向后兼容返回空


def patch_manager_methods(manager: "Manager") -> None:
    """将模块级函数绑定到 Manager 实例上作为方法。

    避免 manager.py 与 create.py / lifecycle.py / sweep.py 之间的循环导入。
    在 cli.py 中构造 Manager 后调用。
    """
    from .create import _create_worktree
    from .lifecycle import (
        _enter_worktree,
        _exit_worktree,
        _remove_worktree,
        _auto_cleanup_worktree,
    )

    async def create(name: str, base_ref: str, manual: bool):
        return await _create_worktree(manager, name, base_ref, manual)

    async def enter(name: str):
        return await _enter_worktree(manager, name)

    async def exit_worktree(name: str, action, opts):
        return await _exit_worktree(manager, name, action, opts)

    async def remove(name: str, opts):
        return await _remove_worktree(manager, name, opts)

    async def auto_cleanup(name: str):
        return await _auto_cleanup_worktree(manager, name)

    async def sweep(cutoff: datetime) -> list[str]:
        return await sweep_stale(manager, cutoff)

    manager.create = create  # type: ignore[method-assign]
    manager.enter = enter  # type: ignore[method-assign]
    manager.exit = exit_worktree  # type: ignore[method-assign]
    manager.remove = remove  # type: ignore[method-assign]
    manager.auto_cleanup = auto_cleanup  # type: ignore[method-assign]
    manager.sweep_stale = sweep  # type: ignore[method-assign]

    # 设置错误类型引用（供 lifecycle 等使用）
    manager.WorktreeError = WorktreeError  # type: ignore[attr-defined]


class WorktreeError(Exception):
    """Worktree 操作错误（供 sweep 包内部使用）。"""
    pass
