"""共享任务列表 —— 带依赖追踪的持久化任务存储。

对齐 mewcode teams/shared_task.py 的 SharedTask / SharedTaskStore。
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ── 类型 ──────────────────────────────────────────────────────────

class Status(str, Enum):
    """任务状态。"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class Task:
    """一条共享任务。

    Attributes:
        id: 任务 ID（格式 ``task_<6 位 hex>``）。
        title: 标题。
        description: 描述。
        status: 当前状态。
        assignee: 负责人（队员名）。
        blocked_by: 依赖的任务 ID 列表。
        blocks: 被哪些任务依赖。
        created_at: 创建时间戳。
        updated_at: 更新时间戳。
    """
    id: str
    title: str
    description: str = ""
    status: Status = Status.PENDING
    assignee: str = ""
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": str(self.status),
            "assignee": self.assignee,
            "blocked_by": self.blocked_by,
            "blocks": self.blocks,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        status = Status.PENDING
        try:
            status = Status(data.get("status", "pending"))
        except ValueError:
            pass
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            status=status,
            assignee=data.get("assignee", ""),
            blocked_by=data.get("blocked_by", []),
            blocks=data.get("blocks", []),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
        )

    @property
    def is_ready(self) -> bool:
        """该任务是否就绪（没有未完成的 blocker）。

        注意：此属性依赖调用方在 list_ 时计算，
        因为需要访问其他任务的状态。
        """
        return True  # 默认 True，由 Store.list_ 覆盖


@dataclass
class Filter:
    """任务列表过滤条件。"""
    status: Status | None = None
    assignee: str | None = None


@dataclass
class Patch:
    """任务更新补丁。"""
    title: str | None = None
    description: str | None = None
    status: Status | None = None
    assignee: str | None = None
    add_blocks: list[str] = field(default_factory=list)
    add_blocked_by: list[str] = field(default_factory=list)
    remove_blocks: list[str] = field(default_factory=list)
    remove_blocked_by: list[str] = field(default_factory=list)


# ── Store ─────────────────────────────────────────────────────────

class Store:
    """共享任务持久化存储。

    任务数据存储在 ``<path>``（通常是 ``<team_config_dir>/tasks.json``）。
    读操作 re-read 磁盘以获取跨进程更新，
    写操作原子替换。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()
        # 确保文件存在
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._save_raw({"tasks": []})

    # ── 底层 I/O ──────────────────────────────────────────────────

    def _load_raw(self) -> dict:
        """从磁盘读取原始数据。"""
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"tasks": []}

    def _save_raw(self, data: dict) -> None:
        """原子写入磁盘（先写 .tmp 再 os.replace）。"""
        import os as _os
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        j = json.dumps(data, indent=2, ensure_ascii=False)
        tmp.write_text(j, encoding="utf-8")
        _os.replace(str(tmp), str(self._path))

    def _load_tasks(self) -> dict[str, Task]:
        """从磁盘加载所有任务，返回 {id: Task} dict。"""
        data = self._load_raw()
        tasks: dict[str, Task] = {}
        for item in data.get("tasks", []):
            t = Task.from_dict(item)
            tasks[t.id] = t
        return tasks

    def _save_tasks(self, tasks: dict[str, Task]) -> None:
        """将所有任务写回磁盘。"""
        data = {
            "tasks": [t.to_dict() for t in tasks.values()],
        }
        self._save_raw(data)

    # ── 公开 API ──────────────────────────────────────────────────

    async def create(
        self,
        title: str,
        description: str = "",
        assignee: str = "",
        blocked_by: list[str] | None = None,
        created_by: str = "",
    ) -> Task:
        """创建新任务。

        Args:
            title: 任务标题。
            description: 任务描述。
            assignee: 负责人。
            blocked_by: 依赖的任务 ID 列表。
            created_by: 创建者。

        Returns:
            新创建的 Task。
        """
        async with self._lock:
            tasks = self._load_tasks()
            task_id = f"task_{secrets.token_hex(3)}"
            now = time.time()

            t = Task(
                id=task_id,
                title=title,
                description=description,
                assignee=assignee,
                blocked_by=list(blocked_by or []),
                created_at=now,
                updated_at=now,
            )
            tasks[task_id] = t

            # 双向维护 blocked_by / blocks
            for bid in t.blocked_by:
                if bid in tasks:
                    if task_id not in tasks[bid].blocks:
                        tasks[bid].blocks.append(task_id)

            self._save_tasks(tasks)
            return t

    async def get(self, task_id: str) -> Task | None:
        """获取任务详情。

        Args:
            task_id: 任务 ID。

        Returns:
            Task 或 None。
        """
        tasks = self._load_tasks()
        return tasks.get(task_id)

    async def list_(self, filter_: Filter | None = None) -> list[dict]:
        """列出任务。

        Args:
            filter_: 过滤条件。

        Returns:
            任务 dict 列表，每个含 is_ready 字段。
        """
        tasks = self._load_tasks()
        result: list[Task] = []

        for t in tasks.values():
            if filter_ is not None:
                if filter_.status is not None and t.status != filter_.status:
                    continue
                if filter_.assignee is not None and t.assignee != filter_.assignee:
                    continue
            result.append(t)

        # 计算 is_ready
        output: list[dict] = []
        for t in result:
            d = t.to_dict()
            # 检查所有 blocked_by 是否已完成
            all_ready = True
            for bid in t.blocked_by:
                bt = tasks.get(bid)
                if bt is None or bt.status != Status.COMPLETED:
                    all_ready = False
                    break
            d["is_ready"] = all_ready
            output.append(d)

        return output

    async def update(self, task_id: str, patch: Patch) -> bool:
        """更新任务。

        Args:
            task_id: 任务 ID。
            patch: 更新补丁。

        Returns:
            True 如果更新成功，False 如果任务不存在。
        """
        async with self._lock:
            tasks = self._load_tasks()
            t = tasks.get(task_id)
            if t is None:
                return False

            now = time.time()
            if patch.title is not None:
                t.title = patch.title
            if patch.description is not None:
                t.description = patch.description
            if patch.status is not None:
                t.status = patch.status
            if patch.assignee is not None:
                t.assignee = patch.assignee

            # add_blocks：当前任务 block 其他任务
            for bid in patch.add_blocks:
                if bid not in t.blocks:
                    t.blocks.append(bid)
                if bid in tasks and task_id not in tasks[bid].blocked_by:
                    tasks[bid].blocked_by.append(task_id)

            # add_blocked_by：其他任务 block 当前任务
            for bid in patch.add_blocked_by:
                if bid not in t.blocked_by:
                    t.blocked_by.append(bid)
                if bid in tasks and task_id not in tasks[bid].blocks:
                    tasks[bid].blocks.append(task_id)

            # 移除 blocks
            for bid in patch.remove_blocks:
                if bid in t.blocks:
                    t.blocks.remove(bid)
                if bid in tasks and task_id in tasks[bid].blocked_by:
                    tasks[bid].blocked_by.remove(task_id)

            # 移除 blocked_by
            for bid in patch.remove_blocked_by:
                if bid in t.blocked_by:
                    t.blocked_by.remove(bid)
                if bid in tasks and task_id in tasks[bid].blocks:
                    tasks[bid].blocks.remove(task_id)

            t.updated_at = now
            self._save_tasks(tasks)
            return True

    def path(self) -> str:
        """返回存储文件路径。"""
        return str(self._path)
