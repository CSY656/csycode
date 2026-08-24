"""工具注册中心模块。

ToolRegistry 集中管理所有工具实例，提供按名查找和协议格式导出。
对齐 mewcode：支持 enable/disable、deferred tools 和协议感知的 schema 导出。
"""

from __future__ import annotations

from typing import Any

from .base import Tool


class ToolRegistry:
    """工具注册中心。

    负责工具实例的注册、查找、启用/禁用、延迟工具发现，
    以及将工具列表导出为各 LLM 协议的原生格式。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()

    def register(self, tool: Tool) -> None:
        """注册一个工具实例。

        Args:
            tool: 工具实例，以其 name 属性作为唯一标识。
                  同名工具会被后注册的覆盖。
        """
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名称查找工具。

        Args:
            name: 工具名称。

        Returns:
            匹配的 Tool 实例，未找到返回 None。
        """
        return self._tools.get(name)

    def is_enabled(self, name: str) -> bool:
        """检查工具是否已启用（已注册且未被禁用）。"""
        return name in self._tools and name not in self._disabled

    def enable(self, name: str) -> None:
        """启用指定工具（从禁用列表中移除）。"""
        self._disabled.discard(name)

    def disable(self, name: str) -> None:
        """禁用指定工具（加入禁用列表）。"""
        if name in self._tools:
            self._disabled.add(name)

    def enable_all(self) -> None:
        """启用所有工具（清空禁用列表）。"""
        self._disabled.clear()

    def mark_discovered(self, name: str) -> None:
        """标记 deferred 工具已被发现（纳入 schema 导出）。"""
        self._discovered.add(name)

    def is_discovered(self, name: str) -> bool:
        """检查 deferred 工具是否已被发现。"""
        return name in self._discovered

    def get_deferred_tool_names(self) -> list[str]:
        """获取尚未被发现且未被禁用的 deferred 工具名称列表。"""
        return [
            name
            for name, tool in self._tools.items()
            if getattr(tool, "should_defer", False)
            and name not in self._discovered
            and name not in self._disabled
        ]

    def count(self) -> int:
        """返回当前已注册工具数量（O(1) 实现）。"""
        return len(self._tools)

    def list_all(self) -> list[Tool]:
        """返回所有已注册工具的列表。"""
        return list(self._tools.values())

    def list_enabled(self) -> list[Tool]:
        """返回所有已启用的工具列表。"""
        return [
            t for name, t in self._tools.items()
            if name not in self._disabled
        ]

    def get_all_schemas(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
        """获取所有已启用且已发现（非 deferred）工具的 schema。

        对齐 mewcode：deferred 工具必须通过 ToolSearch 发现后才会导出。

        Args:
            protocol: 目标协议 ("anthropic", "openai", "openai-compat")

        Returns:
            工具 schema 列表，格式匹配目标协议。
        """
        schemas: list[dict[str, Any]] = []
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            if getattr(tool, "should_defer", False) and name not in self._discovered:
                continue
            base = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            if protocol in ("openai", "openai-compat"):
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": base["name"],
                        "description": base["description"],
                        "parameters": base["input_schema"],
                    },
                })
            else:
                schemas.append(base)
        return schemas

    def to_anthropic_tools(self) -> list[dict]:
        """将已注册工具转为 Anthropic API 的 tools 参数格式。

        每项格式:
        {
            "name": "...",
            "description": "...",
            "input_schema": { ... }  # JSON Schema
        }
        """
        return self.get_all_schemas("anthropic")

    def to_openai_tools(self) -> list[dict]:
        """将已注册工具转为 OpenAI API 的 tools 参数格式。

        每项格式:
        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": { ... }  # JSON Schema
            }
        }
        """
        return self.get_all_schemas("openai")
