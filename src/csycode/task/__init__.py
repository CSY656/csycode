"""后台任务管理包 —— 对齐 mewcode agents/task_manager.py。

提供 Manager（后台任务生命周期管理）和 BackgroundTask（任务状态快照）。
以及 4 个内置工具：TaskList / TaskGet / TaskStop / SendMessage。
"""

from __future__ import annotations

from .manager import BackgroundTask, Manager, Status

__all__ = [
    "BackgroundTask",
    "Manager",
    "Status",
]
