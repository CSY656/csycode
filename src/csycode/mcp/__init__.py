"""MCP 客户端子包 —— 连接外部 MCP server，将其工具注册入 csycode。

提供:
  - MCPClient: 单个 MCP server 的客户端
  - MCPManager: 管理所有 MCP 客户端连接
  - MCPToolWrapper / McpTool: 适配 csycode Tool 协议的远端工具包装器
"""

from __future__ import annotations

from .manager import (
    ConnectResult,
    MCPClient,
    MCPManager,
    ServerInfo,
)
from .tool import MCPToolWrapper, McpTool, _extract_text

__all__ = [
    "MCPClient",
    "MCPManager",
    "ConnectResult",
    "ServerInfo",
    "MCPToolWrapper",
    "McpTool",
    "_extract_text",
]
