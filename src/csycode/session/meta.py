"""会话元数据 —— SessionMeta 数据类与读写。

对齐 mewcode 的 SessionMeta：将标题、模型、消息数、token 数等
摘要信息保存在独立的 .meta JSON 文件中，使会话列表无需扫描完整 JSONL。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

META_FILENAME = "session.meta"


@dataclass
class SessionMeta:
    """会话元数据 —— 用于快速列表展示，无需扫描 JSONL。"""

    id: str = ""
    title: str = ""
    model: str = ""
    message_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    created_at: float = 0.0
    last_active: float = 0.0


def read_meta(session_dir: str) -> SessionMeta | None:
    """从 session_dir 读取 session.meta，不存在或损坏时返回 None。"""
    meta_path = Path(session_dir) / META_FILENAME
    if not meta_path.is_file():
        return None

    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return SessionMeta(
            id=data.get("id", ""),
            title=data.get("title", ""),
            model=data.get("model", ""),
            message_count=data.get("message_count", 0),
            total_input_tokens=data.get("total_input_tokens", 0),
            total_output_tokens=data.get("total_output_tokens", 0),
            created_at=data.get("created_at", 0.0),
            last_active=data.get("last_active", 0.0),
        )
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("读取 session.meta 失败 %s: %s", meta_path, e)
        return None


def write_meta(session_dir: str, meta: SessionMeta) -> None:
    """写入 session.meta 文件。"""
    meta_path = Path(session_dir) / META_FILENAME
    try:
        meta_path.write_text(
            json.dumps(asdict(meta), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("写入 session.meta 失败 %s: %s", meta_path, e)


def update_meta(
    session_dir: str,
    *,
    title: str | None = None,
    model: str | None = None,
    message_count: int | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    created_at: float = 0.0,
    session_id: str = "",
) -> SessionMeta | None:
    """更新或创建会话元数据（增量更新）。

    如果 session.meta 已存在则合并更新；否则创建新文件。
    返回更新后的 SessionMeta 或 None（失败时）。
    """
    meta = read_meta(session_dir)
    if meta is None:
        meta = SessionMeta(
            id=session_id,
            created_at=created_at or 0.0,
        )

    if title is not None and not meta.title:
        meta.title = title
    if model is not None and not meta.model:
        meta.model = model
    if message_count is not None:
        meta.message_count = max(meta.message_count, message_count)
    if not meta.id and session_id:
        meta.id = session_id
    if created_at and not meta.created_at:
        meta.created_at = created_at

    meta.total_input_tokens += input_tokens
    meta.total_output_tokens += output_tokens
    meta.last_active = max(meta.last_active, created_at or 0.0)

    write_meta(session_dir, meta)
    return meta
