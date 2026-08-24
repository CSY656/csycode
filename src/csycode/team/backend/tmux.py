"""tmux 后端 —— 在 tmux 中 spawn 队员 pane。

对齐 mewcode teams/spawn_tmux.py。
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
from typing import TYPE_CHECKING

from csycode.team.types import BackendType

if TYPE_CHECKING:
    from csycode.team.backend import SpawnRequest

log = logging.getLogger(__name__)


# ── TmuxBackend ───────────────────────────────────────────────────

class TmuxBackend:
    """tmux 后端实现 Backend Protocol。

    通过 tmux split-window 创建新 pane 并启动队员子进程。
    """

    def type(self) -> BackendType:
        return BackendType.TMUX

    # ── spawn ────────────────────────────────────────────────────

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """在 tmux 中 split 新 pane 启动队员。

        Returns:
            (pane_id, agent_id)。
        """
        cmd = _build_member_cmd(req)
        log.info("tmux spawn: team=%s member=%s cmd=%s", req.team_name, req.member_name, cmd)

        import os as _os
        if _os.environ.get("TMUX"):
            # 在 tmux 会话内：split-window
            proc = await asyncio.create_subprocess_exec(
                "tmux", "split-window", "-h", "-P", "-F", "#{pane_id}",
                "--", "bash", "-c", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            # 不在 tmux 内但有 tmux 二进制：new-session detached
            session_name = f"csycode-team-{req.team_name}-{req.member_name}"
            proc = await asyncio.create_subprocess_exec(
                "tmux", "new-session", "-d", "-s", session_name,
                "-P", "-F", "#{pane_id}",
                "bash", "-c", cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        stdout, stderr = await proc.communicate()
        pane_id = stdout.decode().strip() if stdout else ""

        if proc.returncode != 0 or not pane_id:
            err = stderr.decode() if stderr else "unknown"
            raise RuntimeError(f"tmux spawn 失败: {err}")

        log.info("tmux spawn ok: pane_id=%s agent_id=%s", pane_id, req.agent_id)
        return (pane_id, req.agent_id)

    # ── wake ──────────────────────────────────────────────────────

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """通过 send-keys 唤醒目标 pane。

        发送空回车触发子进程 stdin reader 去 mailbox 轮询。
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "send-keys", "-t", pane_id, "C-m",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            log.warning("tmux wake 失败 (pane=%s): %s", pane_id, e)

    # ── kill ──────────────────────────────────────────────────────

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """杀掉 tmux pane。"""
        if not pane_id:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "kill-pane", "-t", pane_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            log.warning("tmux kill 失败 (pane=%s): %s", pane_id, e)


# ── 命令构造 ──────────────────────────────────────────────────────

def _build_member_cmd(req: SpawnRequest) -> str:
    """构造队员子进程的启动命令。

    格式: python -m csycode --team-member --team <name> --member <name>
           --agent-id <id> --session-dir <dir> --worktree <path>
           [--agent-type <type>] [--model <model>] [--plan-mode]

    initial_prompt 不通过命令行传递（由 spawn_teammate 预写入 mailbox）。
    """
    parts = [
        sys.executable, "-m", "csycode",
        "--team-member",
        "--team", shlex.quote(req.team_name),
        "--member", shlex.quote(req.member_name),
        "--agent-id", shlex.quote(req.agent_id),
        "--session-dir", shlex.quote(req.session_dir),
        "--worktree", shlex.quote(req.worktree_path),
    ]
    if req.agent_type:
        parts.extend(["--agent-type", shlex.quote(req.agent_type)])
    if req.model:
        parts.extend(["--model", shlex.quote(req.model)])
    if req.plan_mode_required:
        parts.append("--plan-mode")

    return " ".join(parts)
