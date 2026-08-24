"""后端自动检测 —— 按优先级一次性决定 BackendType。

对齐 mewcode teams/backend_detect.py。
"""

from __future__ import annotations

import os
import shutil

from csycode.team.types import BackendType


def detect() -> BackendType:
    """检测当前环境可用的最佳后端。

    优先级（一次性决定，不做运行时回退）：
    1. $TMUX 已设置 → tmux（当前在 tmux 会话内）
    2. $TERM_PROGRAM == "iTerm.app" 且 it2 CLI 可用 → iterm2
    3. tmux 二进制在 PATH 中 → tmux（外部 spawn 新 session）
    4. 否则 → in-process

    Returns:
        检测到的 BackendType。
    """
    # 1. 已在 tmux 会话内
    if os.environ.get("TMUX"):
        return BackendType.TMUX

    # 2. iTerm2
    if os.environ.get("TERM_PROGRAM") == "iTerm.app" and shutil.which("it2"):
        return BackendType.ITERM2

    # 3. tmux 二进制可用（但不在 tmux 会话内）
    if shutil.which("tmux"):
        return BackendType.TMUX

    # 4. 兜底
    return BackendType.IN_PROCESS
