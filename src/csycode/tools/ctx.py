"""工具 ctx cwd 传递机制。

通过 contextvars.ContextVar 在异步调用链中传递 Worktree cwd，
实现显式工作目录注入而不依赖 os.chdir。
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

_ctx_cwd: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cwd", default=None
)


@contextmanager
def with_cwd(directory: str) -> Generator[None, None, None]:
    """上下文管理器：将 directory 注入当前 ContextVar。

    嵌套调用时内层会恢复外层值（reset 而非直接覆盖）。
    directory 为空字符串时不改变 ContextVar——等效于无隔离。

    用法:
        with with_cwd("/path/to/worktree"):
            # 此范围内 cwd_from_ctx() 返回 "/path/to/worktree"
            ...
    """
    if not directory:
        yield
        return
    token = _ctx_cwd.set(directory)
    try:
        yield
    finally:
        _ctx_cwd.reset(token)


def cwd_from_ctx() -> str | None:
    """获取当前 ContextVar 中的 cwd（可能为 None 表示未注入）。"""
    return _ctx_cwd.get()


def resolve_path(p: str) -> str:
    """解析工具路径参数。

    解析规则:
    - p 为空字符串: 返回 ctx cwd 或进程 cwd
    - p 为绝对路径: 直接返回
    - p 为相对路径: 拼接 ctx cwd（优先）或进程 cwd

    Returns:
        绝对路径字符串。
    """
    base = _ctx_cwd.get() or str(Path.cwd())
    if not p:
        return base
    pp = Path(p)
    if pp.is_absolute():
        return str(pp)
    return str(Path(base) / pp)
