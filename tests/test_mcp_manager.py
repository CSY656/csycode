"""MCP Manager 测试（对齐 mewcode）。

覆盖：MCPManager 连接 / 失败隔离 / shutdown / MCPClient 生命周期。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import mcp.types as mcp_types
import pytest

from csycode.config import MCPServerConfig
from csycode.mcp.manager import MCPClient, MCPManager, ConnectResult


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def empty_manager() -> MCPManager:
    """空 MCPManager。"""
    return MCPManager()


@pytest.fixture
def stdio_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="test-server",
        command="echo",
        args=["hello"],
    )


@pytest.fixture
def http_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="http-server",
        url="https://example.com/mcp",
        headers={"Authorization": "Bearer token"},
    )


# ── MCPManager 测试 ───────────────────────────────────────────────────


class TestMCPManager:
    """MCPManager 基础测试。"""

    @pytest.mark.asyncio
    async def test_empty_manager_connect_all(self, empty_manager):
        """空 Manager connect_all → 空结果。"""
        result = await empty_manager.connect_all()
        assert isinstance(result, ConnectResult)
        assert result.tools == []
        assert result.servers == []
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_empty_manager_shutdown(self, empty_manager):
        """空 Manager shutdown 不抛异常。"""
        await empty_manager.shutdown()

    def test_load_configs(self, empty_manager, stdio_config):
        """load_configs 加载服务器配置。"""
        empty_manager.load_configs([stdio_config])
        assert "test-server" in empty_manager._configs

    @pytest.mark.asyncio
    async def test_single_server_failure_does_not_block_others(self):
        """一个 server 失败不影响其他。"""
        manager = MCPManager()

        good_config = MCPServerConfig(name="good", command="echo", args=["hello"])
        bad_config = MCPServerConfig(name="bad", command="nonexistent_cmd_xyz_123")

        manager.load_configs([bad_config, good_config])

        with patch("csycode.mcp.manager.MCPClient") as MockClient:
            # good client
            good_instance = AsyncMock()
            good_instance.name = "good"
            good_instance.is_alive = True
            good_instance.instructions = ""
            good_instance.list_tools.return_value = [
                mcp_types.Tool(
                    name="test_tool",
                    description="A test",
                    inputSchema={"type": "object", "properties": {}},
                )
            ]

            # bad client
            bad_instance = AsyncMock()
            bad_instance.name = "bad"
            bad_instance.connect.side_effect = RuntimeError("command not found")

            def make_client(config):
                if config.name == "bad":
                    return bad_instance
                return good_instance

            MockClient.side_effect = make_client

            result = await manager.connect_all()

        assert len(result.errors) == 1
        assert "bad" in result.errors[0]
        assert len(result.tools) >= 1
        assert any("good" in t.name for t in result.tools)

    @pytest.mark.asyncio
    async def test_shutdown_closes_all_clients(self):
        """shutdown 关闭所有客户端。"""
        manager = MCPManager()

        with patch("csycode.mcp.manager.MCPClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value = mock_client
            mock_client.name = "test"
            mock_client.is_alive = True
            mock_client.instructions = ""
            mock_client.list_tools.return_value = []

            manager.load_configs([
                MCPServerConfig(name="test", command="echo"),
            ])
            await manager.connect_all()
            await manager.shutdown()

            mock_client.close.assert_called_once()


# ── MCPClient 测试 ────────────────────────────────────────────────────


class TestMCPClient:
    """MCPClient 测试。"""

    def test_client_init(self, stdio_config):
        """MCPClient 初始化。"""
        client = MCPClient(stdio_config)
        assert client.name == "test-server"
        assert client.is_alive is False
        assert client.instructions == ""

    def test_is_stdio(self):
        """is_stdio 判断正确。"""
        stdio = MCPServerConfig(name="s", command="echo")
        http = MCPServerConfig(name="h", url="http://localhost")
        assert stdio.is_stdio is True
        assert http.is_stdio is False
