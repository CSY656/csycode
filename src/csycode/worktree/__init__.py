"""Worktree 隔离管理 —— 封装 Git Worktree 的完整生命周期。

对齐 mewcode worktree 包结构:
- slug.py: 名称校验
- models.py ← session.py: WorktreeSession
- manager.py: Worktree (dataclass) + Manager
- git.py: _run_git + Changes + read_worktree_head_sha
- create.py: 创建 + 快速恢复 + post-creation setup
- lifecycle.py: enter / exit / remove / auto_cleanup
- sweep.py: cleanup_stale_worktrees + start_stale_cleanup_task
"""

from .slug import validate_slug, flatten_slug, flat_slug
from .session import (
    WorktreeSession,
    load_session,
    save_session,
    clear_session,
)
from .git import (
    _run_git,
    Changes,
    CleanupResult,
    count_worktree_changes,
    has_worktree_changes,
    has_unpushed_commits,
    read_worktree_head_sha,
)
from .manager import Manager, Worktree, WorktreeError, DEFAULT_SYMLINK_DIRS
from .create import _perform_post_creation_setup
from .lifecycle import (
    ExitAction,
    ExitOptions,
    ExitReport,
    WorktreeHasChangesError,
)
from .sweep import (
    EPHEMERAL_PATTERNS,
    EPHEMERAL_PATTERN,
    random_agent_name,
    cleanup_stale_worktrees,
    start_stale_cleanup_task,
    sweep_stale,
    patch_manager_methods,
)

__all__ = [
    # slug
    "validate_slug",
    "flatten_slug",
    "flat_slug",
    # session
    "WorktreeSession",
    "load_session",
    "save_session",
    "clear_session",
    # git
    "_run_git",
    "Changes",
    "CleanupResult",
    "count_worktree_changes",
    "has_worktree_changes",
    "has_unpushed_commits",
    "read_worktree_head_sha",
    # manager
    "Manager",
    "Worktree",
    "WorktreeError",
    "DEFAULT_SYMLINK_DIRS",
    # create
    "_perform_post_creation_setup",
    # lifecycle
    "ExitAction",
    "ExitOptions",
    "ExitReport",
    "WorktreeHasChangesError",
    # sweep
    "EPHEMERAL_PATTERNS",
    "EPHEMERAL_PATTERN",
    "random_agent_name",
    "cleanup_stale_worktrees",
    "start_stale_cleanup_task",
    "sweep_stale",
    "patch_manager_methods",
]
