"""Team 执行后端 —— 屏蔽 tmux / iterm2 / in-process spawn 差异。

Backend Protocol 定义统一接口，三种实现各一个子模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from csycode.team.types import BackendType


# ── SpawnRequest ──────────────────────────────────────────────────

@dataclass
class SpawnRequest:
    """后端 spawn 请求参数。

    Attributes:
        team_name: Team 名称。
        member_name: 队员名。
        agent_id: 预分配的 agent_id。
        worktree_path: Worktree 绝对路径。
        session_dir: Session 目录绝对路径。
        agent_type: subagent 定义名（Fork 路径为空）。
        model: 模型覆盖（空表继承）。
        initial_prompt: 初始任务文本。
        plan_mode_required: 是否强制 plan 模式。
        sub_agent: in-process 后端专用 —— 预构造的 Agent 实例。
        conv: in-process 后端专用 —— 预构造的 Conversation。
        task_mgr: in-process 后端专用 —— 后台任务管理器。
    """
    team_name: str
    member_name: str
    agent_id: str
    worktree_path: str
    session_dir: str
    agent_type: str = ""
    model: str = ""
    initial_prompt: str = ""
    plan_mode_required: bool = False

    # in-process 专用
    sub_agent: Any = None
    conv: Any = None
    task_mgr: Any = None


# ── Backend Protocol ──────────────────────────────────────────────

class Backend(Protocol):
    """执行后端统一接口。

    spawn 启动队员，wake 唤醒目标 pane，kill 终止。
    """

    def type(self) -> BackendType:
        """返回后端类型。"""
        ...

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """在后端启动一个新队员。

        Returns:
            (pane_id, agent_id)。Pane 后端返回实际 pane_id，
            in-process 后端 pane_id 为空字符串。
        """
        ...

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """唤醒目标 pane（in-process 后端为 no-op）。"""
        ...

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """终止目标 pane（in-process 后端 cancel asyncio task）。"""
        ...


# ── 工厂函数 ──────────────────────────────────────────────────────

def new_backend(
    t: BackendType,
    task_mgr: Any = None,
) -> Backend:
    """按类型创建 Backend 实例。

    Args:
        t: 后端类型。
        task_mgr: 后台任务管理器（in-process 后端需要）。

    Returns:
        Backend 实例。
    """
    if t == BackendType.TMUX:
        from csycode.team.backend.tmux import TmuxBackend
        return TmuxBackend()
    elif t == BackendType.ITERM2:
        from csycode.team.backend.iterm2 import Iterm2Backend
        return Iterm2Backend()
    else:
        from csycode.team.backend.inprocess import InProcessBackend
        return InProcessBackend(task_mgr=task_mgr)
