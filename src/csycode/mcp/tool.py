"""MCP 工具包装器 —— 将 MCP 远端工具适配为 csycode Tool 协议。

对齐 mewcode-python：
  - MCPToolWrapper 包装 mcp.types.Tool + MCPClient 引用
  - 命名格式：mcp_{server}_{tool}
  - _build_params_model: JSON Schema → Pydantic（用于 schema 生成）
  - _extract_text: CallToolResult.content → 文本聚合
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import mcp.types as mcp_types
from pydantic import BaseModel, create_model

from csycode.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


# ── JSON Schema → Pydantic ────────────────────────────────────────────────


def _json_type_to_python(json_type: str) -> type:
    mapping: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    return mapping.get(json_type, str)


def _build_params_model(
    tool_name: str, input_schema: dict[str, Any]
) -> type[BaseModel]:
    """从 JSON Schema 构建 Pydantic 参数模型。"""
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for name, prop in properties.items():
        py_type = _json_type_to_python(prop.get("type", "string"))
        if name in required:
            field_definitions[name] = (py_type, ...)
        else:
            field_definitions[name] = (py_type | None, None)

    return create_model(f"{tool_name}Params", **field_definitions)


# ── 内容提取 ──────────────────────────────────────────────────────────────


def _extract_text(content: list[Any]) -> str:
    """从 CallToolResult.content 中提取文本。"""
    parts: list[str] = []
    for block in content:
        if isinstance(block, mcp_types.TextContent):
            parts.append(block.text)
        elif isinstance(block, mcp_types.ImageContent):
            parts.append(f"[image: {block.mimeType}]")
        elif isinstance(block, mcp_types.EmbeddedResource):
            resource = block.resource
            if hasattr(resource, "text"):
                parts.append(resource.text)
            else:
                parts.append(f"[binary resource: {resource.uri}]")
    return "\n".join(parts) if parts else "(no output)"


# ── MCPToolWrapper ────────────────────────────────────────────────────────


class MCPToolWrapper(Tool):
    """MCP 远端工具的 csycode 适配器。

    包装一个 mcp.types.Tool 和一个 MCPClient 引用，
    实现 csycode Tool 协议，可注册到 ToolRegistry。
    """

    def __init__(
        self,
        server_name: str,
        tool_def: mcp_types.Tool,
        client,  # MCPClient
    ) -> None:
        self._server_name = server_name
        self._tool_def = tool_def
        self._client = client
        self._parameters: dict[str, Any] = tool_def.inputSchema

        # fsvo "name" set via @property below
        self._name = f"mcp_{server_name}_{tool_def.name}"
        self._description = tool_def.description or tool_def.name

        # Tool 协议属性
        self.is_readonly = False
        self.timeout = 30.0
        self.show_result_to_user = True
        self.allowed_in_plan_mode = False

        # Pydantic 模型（用于 schema 生成，agent loop 使用 **kwargs 调用）
        self.params_model = _build_params_model(tool_def.name, tool_def.inputSchema)

    @property
    def name(self) -> str:
        return self._name

    @property
    def mcp_tool_name(self) -> str:
        return self._tool_def.name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self._tool_def.inputSchema,
        }

    async def execute(self, **kwargs) -> ToolResult:
        """执行 MCP 工具调用，带超时和自动重连。

        覆盖基类 execute() 以提供 MCP 专用的错误处理。
        """
        # 自动重连
        if not self._client.is_alive:
            try:
                await self._client.connect()
            except Exception as e:
                return ToolResult(
                    success=False,
                    content="",
                    error=f"MCP server '{self._server_name}' reconnect failed: {e}",
                    error_type="mcp_error",
                )

        try:
            result = await asyncio.wait_for(
                self._client.call_tool(self._tool_def.name, kwargs if kwargs else None),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                content="",
                error=f"MCP tool '{self.name}' timeout ({self.timeout}s)",
                error_type="timeout",
            )
        except Exception as e:
            self._client._alive = False
            return ToolResult(
                success=False,
                content="",
                error=f"MCP tool call failed: {e}",
                error_type="mcp_error",
            )

        text = _extract_text(result.content)

        if result.isError:
            return ToolResult(
                success=False,
                content="",
                error=text if text != "(no output)" else "MCP remote error",
                error_type="mcp_remote_error",
            )

        return ToolResult(success=True, content=text)

    async def _execute(self, **kwargs) -> ToolResult:
        """占位 —— 实际逻辑在 execute() 中。"""
        return await self.execute(**kwargs)


# ── 兼容旧 API ────────────────────────────────────────────────────────────

McpTool = MCPToolWrapper
