"""Agent 名称注册表 —— name ↔ agent_id 双向映射。

线程安全，用于 Team 队员的寻址。
对齐 mewcode teams/registry.py。
"""

from __future__ import annotations

import threading


class AgentNameRegistry:
    """Agent name ↔ agent_id 双向映射（线程安全）。

    弱引用语义：后注册的同名覆盖前面的。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_name: dict[str, str] = {}  # name → agent_id
        self._by_id: dict[str, str] = {}  # agent_id → name

    def register(self, name: str, agent_id: str) -> None:
        """注册名称到 agent_id 的映射。

        若 name 已存在，覆盖旧映射。
        若 agent_id 已有其他 name，先反向清理。
        """
        with self._lock:
            # 若 agent_id 已有其他 name，清理旧映射
            old_name = self._by_id.get(agent_id)
            if old_name and old_name != name:
                self._by_name.pop(old_name, None)

            # 若 name 已存在指向不同 agent_id，清理旧 agent_id 的反向映射
            old_id = self._by_name.get(name)
            if old_id and old_id != agent_id:
                self._by_id.pop(old_id, None)

            self._by_name[name] = agent_id
            self._by_id[agent_id] = name

    def unregister(self, name: str) -> None:
        """取消注册名称。"""
        with self._lock:
            agent_id = self._by_name.pop(name, None)
            if agent_id:
                self._by_id.pop(agent_id, None)

    def unregister_by_agent_id(self, agent_id: str) -> None:
        """按 agent_id 取消注册。"""
        with self._lock:
            name = self._by_id.pop(agent_id, None)
            if name:
                self._by_name.pop(name, None)

    def resolve(self, name_or_id: str) -> str | None:
        """按 name 或 agent_id 解析。

        优先按 name 查，失败则按 agent_id 反查。
        返回 agent_id，解析不到返回 None。
        """
        with self._lock:
            # 先按 name 查
            agent_id = self._by_name.get(name_or_id)
            if agent_id:
                return agent_id
            # 再按 agent_id 反查（确认该 id 已注册）
            name = self._by_id.get(name_or_id)
            if name:
                return name_or_id
            return None

    def name_of(self, agent_id: str) -> str | None:
        """按 agent_id 反查 name。"""
        with self._lock:
            return self._by_id.get(agent_id)

    def list_all(self) -> dict[str, str]:
        """返回 name → agent_id 的副本。"""
        with self._lock:
            return dict(self._by_name)
