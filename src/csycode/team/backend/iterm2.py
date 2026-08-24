"""iTerm2 后端 —— 通过 it2 CLI 在 iTerm2 中 spawn 队员。

对齐 mewcode teams/spawn_iterm2.py。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from csycode.team.types import BackendType

if TYPE_CHECKING:
    from csycode.team.backend import SpawnRequest

log = logging.getLogger(__name__)


class Iterm2Backend:
    """iTerm2 后端实现 Backend Protocol。

    通过 it2 CLI split-pane 创建新 pane 并启动队员子进程。
    """

    def type(self) -> BackendType:
        return BackendType.ITERM2

    # ── spawn ────────────────────────────────────────────────────

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """在 iTerm2 中 split 新 pane 启动队员。

        Returns:
            (pane_id, agent_id)。
        """
        from csycode.team.backend.tmux import _build_member_cmd

        cmd = _build_member_cmd(req)
        log.info("iterm2 spawn: team=%s member=%s", req.team_name, req.member_name)

        proc = await asyncio.create_subprocess_exec(
            "it2", "split", "--new-pane", "--command", f"/bin/zsh -c {cmd}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        pane_id = stdout.decode().strip() if stdout else ""

        if proc.returncode != 0 or not pane_id:
            err = stderr.decode() if stderr else "unknown"
            raise RuntimeError(f"iterm2 spawn 失败: {err}")

        log.info("iterm2 spawn ok: pane_id=%s agent_id=%s", pane_id, req.agent_id)
        return (pane_id, req.agent_id)

    # ── wake ──────────────────────────────────────────────────────

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """通过 it2 send-text 唤醒目标 pane。"""
        if not pane_id:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "it2", "send-text", "--pane", pane_id, "",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            log.warning("iterm2 wake 失败 (pane=%s): %s", pane_id, e)

    # ── kill ──────────────────────────────────────────────────────

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """关闭 iTerm2 pane。"""
        if not pane_id:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "it2", "close-pane", "--pane", pane_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            log.warning("iterm2 kill 失败 (pane=%s): %s", pane_id, e)
