"""文件锁机制 —— 跨进程并发安全。

用 os.open(O_CREAT|O_EXCL) 抢占 lock 文件，
失败时 5-100ms 随机抖动重试，最多 10 次。
持锁超过 10 秒视为 stale 直接清掉。

对齐 mewcode teams/mailbox.py 的 _with_lock 实现。
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

# ── 常量 ──────────────────────────────────────────────────────────

LOCK_MAX_RETRIES = 10
LOCK_STALE_AFTER = 10.0  # 秒
LOCK_BACKOFF_MIN = 0.005  # 5ms
LOCK_BACKOFF_MAX = 0.100  # 100ms


# ── acquire_lock ──────────────────────────────────────────────────

@asynccontextmanager
async def acquire_lock(lock_path: str | Path) -> AsyncIterator[None]:
    """获取文件锁的异步上下文管理器。

    用法:
        async with acquire_lock("/path/to/file.lock"):
            # 临界区代码
            pass

    Args:
        lock_path: lock 文件路径。

    Raises:
        OSError: 重试 10 次后仍然无法获取锁。
    """
    lp = Path(lock_path)
    lp.parent.mkdir(parents=True, exist_ok=True)

    lock_fd = None
    last_err: OSError | None = None

    for _ in range(LOCK_MAX_RETRIES):
        try:
            fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            lock_fd = fd
            os.close(fd)
            break
        except FileExistsError:
            # 锁已存在 —— 检查是否 stale（> 10 秒）
            try:
                info = lp.stat()
                if time.time() - info.st_mtime > LOCK_STALE_AFTER:
                    lp.unlink(missing_ok=True)
                    # stale 锁清掉后立即重试一次（不消耗重试次数）
                    try:
                        fd = os.open(
                            str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                        )
                        lock_fd = fd
                        os.close(fd)
                        break
                    except FileExistsError:
                        pass
            except OSError:
                pass
            # 随机抖动后重试
            sleep_ms = LOCK_BACKOFF_MIN + random.random() * (
                LOCK_BACKOFF_MAX - LOCK_BACKOFF_MIN
            )
            await asyncio.sleep(sleep_ms)
        except OSError as e:
            last_err = e
            break

    if lock_fd is None:
        if last_err is not None:
            raise last_err
        raise OSError(f"无法获取文件锁: {lp}（重试 {LOCK_MAX_RETRIES} 次后失败）")

    try:
        yield
    finally:
        lp.unlink(missing_ok=True)
