"""会话列表扫描 —— 枚举 .csycode/sessions/ 下的所有会话。

对齐 mewcode SessionMeta: 优先读取 session.meta 快速获取元数据，
不存在时回退到 JSONL 扫描。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from csycode.compact.state import parse_session_time
from csycode.session.meta import read_meta

TITLE_MAX_LENGTH = 50


@dataclass
class SessionInfo:
    """会话列表中的一项。"""

    id: str
    title: str = ""
    model: str = ""
    message_count: int = 0
    size_bytes: int = 0
    modified_at: float = 0.0  # Unix timestamp
    dir_path: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0


def list_sessions(sessions_dir: str) -> list[SessionInfo]:
    """扫描 sessions_dir 下的会话子目录，返回按 last_active 倒序排列的列表。

    优先读取 session.meta（快速），不存在时回退到 JSONL 扫描。
    """
    base = Path(sessions_dir)
    if not base.is_dir():
        return []

    infos: list[SessionInfo] = []
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name)
    except OSError:
        return []

    for entry in entries:
        if not entry.is_dir():
            continue

        # 检查 ID 格式
        parsed = parse_session_time(entry.name)
        if parsed is None:
            continue

        jsonl_path = entry / "conversation.jsonl"
        if not jsonl_path.is_file():
            continue

        # ── 优先读取 .meta（快速路径）──
        meta = read_meta(str(entry))
        if meta is not None:
            try:
                stat = jsonl_path.stat()
                size = stat.st_size
                mtime = stat.st_mtime
            except OSError:
                size = 0
                mtime = meta.last_active

            infos.append(
                SessionInfo(
                    id=entry.name,
                    title=meta.title or "",
                    model=meta.model or "",
                    message_count=meta.message_count,
                    size_bytes=size,
                    modified_at=max(mtime, meta.last_active),
                    dir_path=str(entry),
                    total_input_tokens=meta.total_input_tokens,
                    total_output_tokens=meta.total_output_tokens,
                )
            )
            continue

        # ── 回退到 JSONL 扫描（慢速路径）──
        title = ""
        model = ""
        line_count = 0
        try:
            with open(jsonl_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    line_count += 1
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "compact":
                        line_count -= 1
                        continue
                    if not title and data.get("role") == "user":
                        content = data.get("content", "")
                        if isinstance(content, str):
                            title = content[:TITLE_MAX_LENGTH]
                    if not model and data.get("model"):
                        model = data["model"]
        except OSError:
            continue

        try:
            stat = jsonl_path.stat()
        except OSError:
            stat = None

        infos.append(
            SessionInfo(
                id=entry.name,
                title=title,
                model=model,
                message_count=line_count,
                size_bytes=stat.st_size if stat else 0,
                modified_at=stat.st_mtime if stat else 0.0,
                dir_path=str(entry),
            )
        )

    infos.sort(key=lambda i: i.modified_at, reverse=True)
    return infos
