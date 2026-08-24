"""Agent Team 协作模块 —— ch15。

提供 Team 数据结构、Manager 生命周期管理、mailbox 消息通信、
AgentNameRegistry 名称注册表、SharedTaskStore 共享任务列表、
三种执行后端（tmux / iterm2 / in-process）、Coordinator Mode 等。
"""

from __future__ import annotations

from csycode.team.types import (
    BackendType,
    Team,
    TeammateInfo,
    TeamError,
    TeamNotFoundError,
    TeamHasActiveMembersError,
    MemberExistsError,
    MemberNotFoundError,
    InProcessTeammateNoSpawnError,
)
from csycode.team.manager import Manager
from csycode.team.persistence import sanitize, atomic_write_json, read_json
from csycode.team.filelock import acquire_lock
from csycode.team.registry import AgentNameRegistry
from csycode.team.mailbox import Box, Message, MessageType, create_message
from csycode.team.tasks import Task, Status as TaskStatus, Store as TaskStore, Filter as TaskFilter, Patch as TaskPatch
from csycode.team.progress import TeammateProgress, ToolActivity, random_verb
from csycode.team.transcript import save_transcript, load_transcript

__all__ = [
    "BackendType",
    "Team",
    "TeammateInfo",
    "TeamError",
    "TeamNotFoundError",
    "TeamHasActiveMembersError",
    "MemberExistsError",
    "MemberNotFoundError",
    "InProcessTeammateNoSpawnError",
    "Manager",
    "sanitize",
    "atomic_write_json",
    "read_json",
    "acquire_lock",
    "AgentNameRegistry",
    "Box",
    "Message",
    "MessageType",
    "create_message",
    "Task",
    "TaskStatus",
    "TaskStore",
    "TaskFilter",
    "TaskPatch",
    "TeammateProgress",
    "ToolActivity",
    "random_verb",
    "save_transcript",
    "load_transcript",
]
