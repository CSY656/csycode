"""Team 核心数据类型 —— 对齐 mewcode teams/models.py。

定义 BackendType、Team、TeammateInfo 及异常类。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ── BackendType ───────────────────────────────────────────────────

class BackendType(str, Enum):
    """执行后端类型。"""
    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"


# ── 异常类 ────────────────────────────────────────────────────────

class TeamError(Exception):
    """Team 模块所有异常的基类。"""
    pass


class TeamNotFoundError(TeamError):
    """Team 不存在。"""
    pass


class TeamHasActiveMembersError(TeamError):
    """Team 还有活跃成员，无法删除。"""
    pass


class MemberExistsError(TeamError):
    """成员名在 Team 内已存在。"""
    pass


class MemberNotFoundError(TeamError):
    """成员不存在。"""
    pass


class InProcessTeammateNoSpawnError(TeamError):
    """in-process 队员不允许再 spawn 子队员。"""
    pass


# ── TeammateInfo ──────────────────────────────────────────────────

@dataclass
class TeammateInfo:
    """队员信息。

    Attributes:
        name: Lead 分配的队员名，Team 内唯一。
        agent_id: 对应 task.BackgroundTask.id。
        agent_type: 使用的 subagent 定义名；Fork 路径下为空字符串。
        model: 模型覆盖，空字符串表继承。
        worktree_path: Worktree 绝对路径。
        branch: 对应 worktree 分支名。
        backend_type: 可 per-member 不同的后端类型。
        pane_id: tmux pane / iterm2 split id，in-process 为空。
        is_active: None 或 True 表活跃，False 表空闲；从 members 移除视为终止。
        plan_mode_required: 是否强制 plan 模式起步。
        session_dir: 队员独立 session 目录绝对路径。
    """
    name: str
    agent_id: str
    agent_type: str = ""
    model: str = ""
    worktree_path: str = ""
    branch: str = ""
    backend_type: BackendType = BackendType.IN_PROCESS
    pane_id: str = ""
    is_active: bool | None = None
    plan_mode_required: bool = False
    session_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "model": self.model,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "backend_type": str(self.backend_type),
            "pane_id": self.pane_id,
            "is_active": self.is_active,
            "plan_mode_required": self.plan_mode_required,
            "session_dir": self.session_dir,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TeammateInfo:
        backend_raw = data.get("backend_type", "in-process")
        try:
            backend_type = BackendType(backend_raw)
        except ValueError:
            backend_type = BackendType.IN_PROCESS
        return cls(
            name=data.get("name", ""),
            agent_id=data.get("agent_id", ""),
            agent_type=data.get("agent_type", ""),
            model=data.get("model", ""),
            worktree_path=data.get("worktree_path", ""),
            branch=data.get("branch", ""),
            backend_type=backend_type,
            pane_id=data.get("pane_id", ""),
            is_active=data.get("is_active", None),
            plan_mode_required=data.get("plan_mode_required", False),
            session_dir=data.get("session_dir", ""),
        )


# ── Team ──────────────────────────────────────────────────────────

@dataclass
class Team:
    """一个 Agent Team。

    Attributes:
        name: 用户给的原始名。
        sanitized_name: 经 sanitize 后用于路径，Team 主键。
        lead_agent_id: 固定 "lead"（本期 Lead = 主 Agent）。
        backend: 全 team 默认后端；可被 member 覆盖。
        description: 团队描述。
        created_at: 创建时间。
        members: 队员列表。
        config_dir: 配置目录（派生，不持久化）。
        config_path: config.json 路径（派生）。
        tasks_path: tasks.json 路径（派生）。
        mailbox_dir: mailbox 目录路径（派生）。
        _lock: 异步锁，保护 Team 状态。
    """
    name: str
    sanitized_name: str = ""
    lead_agent_id: str = "lead"
    backend: BackendType = BackendType.IN_PROCESS
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    members: list[TeammateInfo] = field(default_factory=list)

    # 派生路径（不持久化）
    config_dir: str = ""
    config_path: str = ""
    tasks_path: str = ""
    mailbox_dir: str = ""

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    # ── 成员查询 ──────────────────────────────────────────────────

    def member_by_name(self, name: str) -> TeammateInfo | None:
        """按 name 查找成员。"""
        for m in self.members:
            if m.name == name:
                return m
        return None

    def member_by_agent_id(self, agent_id: str) -> TeammateInfo | None:
        """按 agent_id 查找成员。"""
        for m in self.members:
            if m.agent_id == agent_id:
                return m
        return None

    def active_members(self) -> list[TeammateInfo]:
        """返回所有活跃成员（is_active 不为 False）。"""
        return [m for m in self.members if m.is_active is not False]

    def all_idle(self) -> bool:
        """是否所有非 lead 成员都空闲。"""
        return all(
            m.is_active is False
            for m in self.members
            if m.name != "lead"
        )

    # ── 序列化 ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "sanitized_name": self.sanitized_name,
            "lead_agent_id": self.lead_agent_id,
            "backend": str(self.backend),
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Team:
        members = [TeammateInfo.from_dict(m) for m in data.get("members", [])]
        backend_raw = data.get("backend", "in-process")
        try:
            backend = BackendType(backend_raw)
        except ValueError:
            backend = BackendType.IN_PROCESS

        created_at = datetime.now()
        try:
            created_at = datetime.fromisoformat(data.get("created_at", ""))
        except (ValueError, TypeError):
            pass

        return cls(
            name=data.get("name", ""),
            sanitized_name=data.get("sanitized_name", ""),
            lead_agent_id=data.get("lead_agent_id", "lead"),
            backend=backend,
            description=data.get("description", ""),
            created_at=created_at,
            members=members,
        )
