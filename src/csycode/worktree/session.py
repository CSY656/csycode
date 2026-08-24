"""WorktreeSession 数据类与 JSON 持久化。

对齐 mewcode session.py:
- save: session=None 时删除文件（而非写入 "null"）
- load: 字段校验，JSON 解析失败返回 None
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SESSION_FILENAME = "worktree_session.json"


@dataclass
class WorktreeSession:
    """当前活跃的 Worktree 会话状态。

    Attributes:
        original_cwd: 进入 Worktree 前的原始工作目录。
        worktree_path: Worktree 的绝对路径。
        worktree_name: Worktree 的原始 slug 名称。
        original_branch: 进入前的 Git 分支名。
        original_head_commit: 进入前的 HEAD commit SHA。
        session_id: 本次会话的唯一标识（留空时不持久化）。
        hook_based: 是否通过 hook 机制创建的 Worktree（预留）。
    """

    original_cwd: str
    worktree_path: str
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str = ""
    hook_based: bool = False


def _session_path(mewcode_dir: Path) -> Path:
    return mewcode_dir / SESSION_FILENAME


def save_session(mewcode_dir: Path, session: WorktreeSession | None) -> None:
    """持久化当前会话到 session 文件。

    对齐 mewcode: session=None 时直接删除文件（而非写入 "null"），
    避免遗留无意义的空文件。文件不存在时静默忽略。
    """
    path = _session_path(mewcode_dir)
    if session is None:
        path.unlink(missing_ok=True)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "original_cwd": session.original_cwd,
        "worktree_path": session.worktree_path,
        "worktree_name": session.worktree_name,
        "original_branch": session.original_branch,
        "original_head_commit": session.original_head_commit,
        "session_id": session.session_id,
        "hook_based": session.hook_based,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_session(mewcode_dir: Path) -> WorktreeSession | None:
    """从 session 文件加载当前会话。

    对齐 mewcode: 文件不存在返回 None；JSON 解析失败或缺少
    worktree_path 字段返回 None（仅 warning，不阻断启动）。
    """
    path = _session_path(mewcode_dir)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data or "worktree_path" not in data:
            return None
        return WorktreeSession(
            original_cwd=data["original_cwd"],
            worktree_path=data["worktree_path"],
            worktree_name=data["worktree_name"],
            original_branch=data["original_branch"],
            original_head_commit=data["original_head_commit"],
            session_id=data.get("session_id", ""),
            hook_based=data.get("hook_based", False),
        )
    except (json.JSONDecodeError, KeyError) as e:
        log.warning("Worktree session 文件损坏: %s", e)
        return None


def clear_session(mewcode_dir: Path) -> None:
    """清空 session（等同于 save_session(dir, None)）。"""
    save_session(mewcode_dir, None)
