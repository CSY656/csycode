"""in-process 后端 —— 在同一进程中跑队员 asyncio task。

对齐 mewcode teams/spawn_inprocess.py。
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from csycode.team.types import BackendType

if TYPE_CHECKING:
    from csycode.team.backend import SpawnRequest

log = logging.getLogger(__name__)


class InProcessBackend:
    """in-process 后端实现 Backend Protocol。

    队员与主 Agent 在同一进程内运行，共享 task.Manager。
    """

    def __init__(self, task_mgr: Any = None) -> None:
        self._task_mgr = task_mgr

    def type(self) -> BackendType:
        return BackendType.IN_PROCESS

    # ── spawn ────────────────────────────────────────────────────

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """在同进程中启动队员 asyncio task。

        复用 task.Manager.launch 起后台子 Agent。

        Returns:
            ("", agent_id)。in-process 无 pane_id。
        """
        if req.sub_agent is None or req.conv is None or self._task_mgr is None:
            raise RuntimeError("in-process spawn 需要 sub_agent, conv, task_mgr")

        task_id = await self._task_mgr.launch(
            agent=req.sub_agent,
            conv=req.conv,
            name=req.member_name,
            task_text=req.initial_prompt,
        )

        log.info("in-process spawn ok: agent_id=%s task_id=%s", req.agent_id, task_id)
        return ("", task_id)

    # ── wake ──────────────────────────────────────────────────────

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """no-op: 同进程下一轮 Loop 自动读邮箱。"""
        pass

    # ── kill ──────────────────────────────────────────────────────

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """取消 in-process 队员的 asyncio task。"""
        if self._task_mgr is None:
            return
        try:
            await self._task_mgr.stop(agent_id)
        except Exception as e:
            log.warning("in-process kill 失败 (agent=%s): %s", agent_id, e)
