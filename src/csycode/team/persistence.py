"""Team 持久化工具 —— sanitize、原子写、reload_from_disk_locked。

对齐 mewcode models.py 的 _sanitize_name / save / load。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.team.types import Team


# ── sanitize ──────────────────────────────────────────────────────

def sanitize(name: str) -> str:
    """把团队名转为文件路径安全的 slug。

    只保留 [a-zA-Z0-9._-]，其他字符替换为 -，首尾去 -。
    空字符串返回 ""（调用方负责拒绝）。

    >>> sanitize("foo bar/baz")
    'foo-bar-baz'
    >>> sanitize("refactor auth")
    'refactor-auth'
    """
    slug = re.sub(r"[^a-zA-Z0-9._-]", "-", name.strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


# ── 原子读写 ──────────────────────────────────────────────────────

def atomic_write_json(path: str | Path, value: Any) -> None:
    """原子写入 JSON：先写 .tmp 再 os.replace。

    Args:
        path: 目标文件路径。
        value: 要序列化的值。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    data = json.dumps(value, indent=2, ensure_ascii=False)
    tmp.write_text(data, encoding="utf-8")
    os.replace(str(tmp), str(p))


def read_json(path: str | Path) -> Any:
    """读取 JSON 文件。

    Args:
        path: 文件路径。

    Returns:
        解析后的值。

    Raises:
        FileNotFoundError: 文件不存在。
        json.JSONDecodeError: JSON 解析失败。
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── 跨进程 reload ─────────────────────────────────────────────────

async def reload_from_disk_locked(team: Team) -> None:
    """持锁状态下从磁盘重读 members 字段，覆盖内存。

    用于跨进程并发保护：Pane 后端的 Lead 与子进程是不同进程，
    各持一份内存中的 Team。在 add_member / set_member_active 前
    先 reload，避免"子进程看不到自己"导致的静默丢更新。

    Args:
        team: 已持 _lock 的 Team 实例。
    """
    try:
        data = read_json(team.config_path)
        disk_members = [
            TeammateInfo.from_dict(m)
            for m in data.get("members", [])
        ]
        team.members = disk_members
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        # 磁盘数据不可用时保持内存现状
        pass


# 延迟 import 避免循环
from csycode.team.types import TeammateInfo  # noqa: E402
