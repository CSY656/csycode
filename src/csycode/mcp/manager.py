"""MCP 客户端管理器 —— 每个 MCP server 对应一个 MCPClient，MCPManager 统一调度。

对齐 mewcode-python 的设计：
  - MCPClient: 管理单个 MCP 连接的完整生命周期（connect / list_tools / call_tool / close）
  - MCPManager: 持有所有 MCPClient，顺序连接，失败隔离，close 时忽略 cancel scope 错误
  - 安全子进程环境：仅传递 PATH + 声明的 env，不传宿主环境
  - stdio stderr 重定向到 os.devnull
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from csycode.config import MCPServerConfig, build_child_env, resolve_env_vars

logger = logging.getLogger(__name__)


# ── MCPClient ──────────────────────────────────────────────────────────────


class MCPClient:
    """单个 MCP server 的客户端，管理连接、工具列表和调用。"""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._alive = False
        self._init_result: types.InitializeResult | None = None

    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def instructions(self) -> str:
        """返回 MCP server 的 instructions（来自 InitializeResult）。"""
        if self._init_result is not None and self._init_result.instructions:
            return self._init_result.instructions
        return ""

    async def connect(self) -> None:
        """连接 MCP server，握手，存储 session。"""
        if self._alive:
            return

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        try:
            if self.config.is_stdio:
                read, write = await self._connect_stdio()
            else:
                read, write = await self._connect_http()

            session = await self._stack.enter_async_context(ClientSession(read, write))
            self._init_result = await session.initialize()
            self._session = session
            self._alive = True
            logger.info("MCP server '%s' connected", self.name)
        except Exception:
            await self._cleanup_stack()
            raise

    async def _connect_stdio(self) -> tuple[Any, Any]:
        """启动 stdio 子进程并建立 transport。"""
        assert self._stack is not None
        assert self.config.command is not None

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=build_child_env(self.config.env),
        )
        # 子进程 stderr 重定向到 /dev/null，避免污染输出
        devnull = open(os.devnull, "w")
        self._stack.callback(devnull.close)
        read, write = await self._stack.enter_async_context(
            stdio_client(params, errlog=devnull)
        )
        return read, write

    async def _connect_http(self) -> tuple[Any, Any]:
        """HTTP 连接并建立 transport。"""
        assert self._stack is not None
        assert self.config.url is not None

        resolved_headers = {
            k: resolve_env_vars(v) for k, v in self.config.headers.items()
        }
        http_client = httpx.AsyncClient(
            headers=resolved_headers,
            follow_redirects=True,
        )
        await self._stack.enter_async_context(http_client)

        result = await self._stack.enter_async_context(
            streamable_http_client(self.config.url, http_client=http_client)
        )
        read, write = result[0], result[1]
        return read, write

    async def list_tools(self) -> list[types.Tool]:
        """列出 MCP server 提供的工具。"""
        assert self._session is not None
        result = await self._session.list_tools()
        return list(result.tools)

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        """调用远端 MCP 工具。"""
        assert self._session is not None
        return await self._session.call_tool(name, arguments)

    async def close(self) -> None:
        """关闭连接，清理资源。"""
        self._alive = False
        self._session = None
        await self._cleanup_stack()

    async def _cleanup_stack(self) -> None:
        """退出 AsyncExitStack，忽略 anyio cancel scope 跨 task 错误。"""
        if self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            except RuntimeError as e:
                if "cancel scope" in str(e):
                    logger.debug(
                        "Cancel scope cleanup (expected during shutdown): %s", e
                    )
                else:
                    raise
            except Exception:
                logger.debug("Error closing stack for '%s'", self.name, exc_info=True)
            self._stack = None


# ── MCPManager ─────────────────────────────────────────────────────────────


@dataclass
class ServerInfo:
    """单个 MCP server 的连接信息。"""

    name: str
    instructions: str = ""


@dataclass
class ConnectResult:
    """connect_all 的返回结果。"""

    tools: list = field(default_factory=list)
    servers: list[ServerInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class MCPManager:
    """管理所有 MCP 客户端连接。"""

    def __init__(self) -> None:
        self._configs: dict[str, MCPServerConfig] = {}
        self._clients: dict[str, MCPClient] = {}

    def load_configs(self, configs: list[MCPServerConfig]) -> None:
        """加载 MCP server 配置列表。"""
        for cfg in configs:
            self._configs[cfg.name] = cfg

    async def connect_all(self) -> ConnectResult:
        """并发连接所有已加载的 MCP server（30s 超时/每 server）。

        单个 server 连接失败仅记录错误，不影响其他 server。
        对齐 mewcode + ch07 spec F9：asyncio.gather 并发 + 失败隔离。
        """
        result = ConnectResult()

        async def _connect_one(name: str, config: MCPServerConfig) -> None:
            """连接单个 server 并收集工具，30s 超时。"""
            client = MCPClient(config)
            try:
                await asyncio.wait_for(client.connect(), timeout=30.0)
            except asyncio.TimeoutError:
                raise RuntimeError(f"连接超时（30s）") from None

            self._clients[name] = client

            info = ServerInfo(name=name, instructions=client.instructions)
            result.servers.append(info)

            tools = await client.list_tools()
            for tool_def in tools:
                from .tool import MCPToolWrapper

                wrapper = MCPToolWrapper(name, tool_def, client)
                result.tools.append(wrapper)
                logger.info("Registered MCP tool: %s", wrapper.name)

        if not self._configs:
            return result

        tasks = [
            _connect_one(name, config) for name, config in self._configs.items()
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        for i, (name,) in enumerate(
            (n,) for n in self._configs.keys()
        ):
            exc = gathered[i]
            if isinstance(exc, Exception):
                msg = f"MCP server '{name}': {exc}"
                logger.warning(msg)
                result.errors.append(msg)

        return result

    async def register_all_tools(self, registry) -> ConnectResult:
        """连接所有 server 并将工具注册到 registry。"""
        from csycode.tools.registry import ToolRegistry

        result = await self.connect_all()
        for tool in result.tools:
            registry.register(tool)
        return result

    async def get_client(self, name: str) -> MCPClient | None:
        """获取指定名称的 MCP 客户端（按需重连）。"""
        client = self._clients.get(name)
        if client is None:
            config = self._configs.get(name)
            if config is None:
                return None
            client = MCPClient(config)
            await client.connect()
            self._clients[name] = client
            return client

        if not client.is_alive:
            logger.info("Reconnecting MCP server '%s'", name)
            await client.close()
            client = MCPClient(self._configs[name])
            await client.connect()
            self._clients[name] = client

        return client

    async def shutdown(self) -> None:
        """关闭所有 MCP 连接。"""
        for name, client in self._clients.items():
            try:
                await client.close()
                logger.info("MCP server '%s' closed", name)
            except Exception:
                logger.debug("Error closing MCP server '%s'", name, exc_info=True)
        self._clients.clear()
