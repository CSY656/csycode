"""会话过期清理 —— 删除超过指定天数的会话目录。"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from csycode.compact.state import parse_session_time

_logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_DAYS = 30


def clean_expired(sessions_dir: str, max_age: timedelta | None = None) -> int:
    """清理过期会话目录。

    通过 session ID 中的时间戳判断年龄。旧格式 ID 的目录保留不动。

    Args:
        sessions_dir: .csycode/sessions/ 目录路径。
        max_age: 最大保留时长，默认 30 天。

    Returns:
        删除的会话数量。
    """
    if max_age is None:
        max_age = timedelta(days=DEFAULT_MAX_AGE_DAYS)

    base = Path(sessions_dir)
    if not base.is_dir():
        return 0

    cutoff = datetime.now(timezone.utc) - max_age
    removed = 0

    for entry in base.iterdir():
        if not entry.is_dir():
            continue

        parsed = parse_session_time(entry.name)
        if parsed is None:
            # 旧格式 ID，保留不动
            continue

        # 将 naive datetime 视为 UTC
        if parsed.replace(tzinfo=timezone.utc) < cutoff:
            try:
                shutil.rmtree(entry, ignore_errors=False)
                removed += 1
                _logger.info("清理过期会话: %s", entry.name)
            except OSError as e:
                _logger.warning("清理会话 %s 失败: %s", entry.name, e)

    return removed
