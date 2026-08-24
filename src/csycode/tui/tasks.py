"""TUI 任务通知模块 —— ch13 SubAgent 集成。

负责：
- _consume_task_done: 消费 TaskManager 的 done 队列，注入 <task-notification>
- build_task_notification: 格式化 BackgroundTask 为通知文本
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.task.manager import BackgroundTask

log = logging.getLogger(__name__)

MAX_NOTIFICATION_RESULT_LENGTH = 5000


def build_task_notification(task: "BackgroundTask") -> str:
    """格式化任务完成通知为 <task-notification> 文本。

    对齐 mewcode agents/notification.py。
    """
    result = task.result
    if len(result) > MAX_NOTIFICATION_RESULT_LENGTH:
        result = result[:MAX_NOTIFICATION_RESULT_LENGTH] + "\n... (truncated)"

    elapsed = ""
    if task.end_time > 0:
        secs = task.end_time - task.start_time
        if secs >= 60:
            elapsed = f"{secs / 60:.1f}m"
        else:
            elapsed = f"{secs:.1f}s"

    tokens = ""
    if task.input_tokens or task.output_tokens:
        tokens = (
            f"\nTokens: input={task.input_tokens}, "
            f"output={task.output_tokens}"
        )

    status_str = str(task.status)

    return (
        f"<task-notification>\n"
        f"Task {task.id} (name=\"{task.name}\"): {status_str}\n"
        f"Elapsed: {elapsed}\n"
        f"{tokens}\n"
        f"Result:\n{result}\n"
        f"</task-notification>"
    )


async def consume_task_done(app) -> None:
    """消费 TaskManager done 队列，将 <task-notification> 注入 runtime.

    在 csyCodeApp.on_mount() 中启动为后台协程。

    Args:
        app: csyCodeApp 实例。
    """
    task_mgr = getattr(app, "task_mgr", None)
    if task_mgr is None:
        return

    q = task_mgr.subscribe_done()
    while True:
        try:
            task_id = await q.get()
        except asyncio.CancelledError:
            break

        bt = task_mgr.get(task_id)
        if bt is None:
            continue

        notification = build_task_notification(bt)
        log.debug("task-notification: %s", task_id)

        # 注入到 runtime.pending_reminders
        if app.agent is not None and hasattr(app.agent, "runtime"):
            app.agent.runtime.append_reminders([notification])
