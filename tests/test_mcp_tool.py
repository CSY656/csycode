"""MCP 工具包装器测试（对齐 mewcode）。

覆盖：命名拼接 / 参数模型构建 / execute 各分支 / _extract_text。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import mcp.types as mcp_types
import pytest

from csycode.mcp.tool import MCPToolWrapper, _build_params_model, _extract_text
from csycode.tools.base import ToolResult


# ── 辅助函数 ──────────────────────────────────────────────────────────


def make_mcp_tool(
    name: str = "test_tool",
    description: str = "A test tool",
    input_schema: dict | None = None,
) -> mcp_types.Tool:
    """创建一个 mcp.types.Tool 实例。"""
    schema = (
        input_schema
        if input_schema is not None
        else {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
    )
    return mcp_types.Tool(
        name=name,
        description=description,
        inputSchema=schema,
    )


def make_mock_client(is_alive=True, return_content=None, is_error=False, should_raise=None):
    """创建一个 mock MCPClient。"""
    client = MagicMock()
    client.is_alive = is_alive
    client._alive = is_alive
    client.name = "test-server"

    if should_raise:
        client.call_tool = AsyncMock(side_effect=should_raise)
    else:
        async def _call_tool(name, arguments):
            return mcp_types.CallToolResult(
                content=return_content or [],
                isError=is_error,
            )
        client.call_tool = AsyncMock(side_effect=_call_tool)

    client.connect = AsyncMock()
    return client


# ── MCPToolWrapper 构造测试 ────────────────────────────────────────────


class TestMCPToolWrapper:
    """MCPToolWrapper 构造和属性测试。"""

    def test_basic_naming(self):
        """命名格式：mcp_{server}_{tool}。"""
        t = make_mcp_tool("search")
        client = make_mock_client()
        wrapper = MCPToolWrapper("my-server", t, client)
        assert wrapper.name == "mcp_my-server_search"
        assert wrapper.mcp_tool_name == "search"

    def test_description_present(self):
        """description 有内容 → 原样保留。"""
        t = make_mcp_tool("abc", description="Does something useful")
        client = make_mock_client()
        wrapper = MCPToolWrapper("srv", t, client)
        assert wrapper.description == "Does something useful"

    def test_description_fallback(self):
        """description 为空 → 用 tool name。"""
        t = make_mcp_tool("xyz", description="")
        client = make_mock_client()
        wrapper = MCPToolWrapper("srv", t, client)
        assert wrapper.description == "xyz"

    def test_schema_passthrough(self):
        """inputSchema → parameters 透传。"""
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        t = make_mcp_tool("calc", input_schema=schema)
        client = make_mock_client()
        wrapper = MCPToolWrapper("srv", t, client)
        assert wrapper.parameters == schema

    def test_get_schema(self):
        """get_schema() 返回 name + input_schema。"""
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        t = make_mcp_tool("search", input_schema=schema)
        client = make_mock_client()
        wrapper = MCPToolWrapper("srv", t, client)
        s = wrapper.get_schema()
        assert s["name"] == "mcp_srv_search"
        assert s["input_schema"] == schema

    def test_namespace_isolation(self):
        """不同 server 的同名工具不冲突。"""
        t1 = make_mcp_tool("echo")
        t2 = make_mcp_tool("echo")
        client = make_mock_client()
        a = MCPToolWrapper("server-a", t1, client)
        b = MCPToolWrapper("server-b", t2, client)
        assert a.name != b.name
        assert a.name == "mcp_server-a_echo"
        assert b.name == "mcp_server-b_echo"

    def test_params_model_built(self):
        """_build_params_model 生成 Pydantic 模型。"""
        schema = {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["repo"],
        }
        t = make_mcp_tool("search", input_schema=schema)
        client = make_mock_client()
        wrapper = MCPToolWrapper("srv", t, client)
        # params_model 应该有 repo (required) 和 count (optional) 字段
        model = wrapper.params_model
        assert model is not None
        fields = model.model_fields
        assert "repo" in fields
        assert "count" in fields


# ── MCPToolWrapper.execute 测试 ────────────────────────────────────────


class TestMCPToolWrapperExecute:
    """MCPToolWrapper.execute() 各分支测试。"""

    @pytest.mark.asyncio
    async def test_successful_call(self):
        """正常调用 → success=True + content 聚合。"""
        client = make_mock_client(
            return_content=[
                mcp_types.TextContent(type="text", text="Hello"),
                mcp_types.TextContent(type="text", text="World"),
            ]
        )
        t = make_mcp_tool("greet")
        wrapper = MCPToolWrapper("srv", t, client)

        result = await wrapper.execute(query="test")
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.content == "Hello\nWorld"

    @pytest.mark.asyncio
    async def test_remote_is_error(self):
        """远端 isError=True → success=False。"""
        client = make_mock_client(
            return_content=[
                mcp_types.TextContent(type="text", text="Something went wrong"),
            ],
            is_error=True,
        )
        t = make_mcp_tool("failer")
        wrapper = MCPToolWrapper("srv", t, client)

        result = await wrapper.execute()
        assert result.success is False
        assert "Something went wrong" in result.error

    @pytest.mark.asyncio
    async def test_timeout(self):
        """调用超时 → success=False。"""
        async def _slow(name, arguments):
            await asyncio.sleep(10)
        client = make_mock_client()
        client.call_tool = AsyncMock(side_effect=_slow)

        t = make_mcp_tool("slow")
        wrapper = MCPToolWrapper("srv", t, client)
        wrapper.timeout = 0.05  # 50ms

        result = await wrapper.execute()
        assert result.success is False
        assert "timeout" in result.error.lower() or "超时" in result.error

    @pytest.mark.asyncio
    async def test_protocol_error(self):
        """call_tool 抛异常 → success=False。"""
        client = make_mock_client(should_raise=RuntimeError("Connection lost"))
        t = make_mcp_tool("broken")
        wrapper = MCPToolWrapper("srv", t, client)

        result = await wrapper.execute()
        assert result.success is False
        assert "Connection lost" in result.error

    @pytest.mark.asyncio
    async def test_auto_reconnect(self):
        """断开后自动重连。"""
        client = make_mock_client(is_alive=False)
        client.connect = AsyncMock()
        client.call_tool = AsyncMock(return_value=mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text="reconnected")],
            isError=False,
        ))
        t = make_mcp_tool("retry")
        wrapper = MCPToolWrapper("srv", t, client)

        result = await wrapper.execute()
        client.connect.assert_called_once()
        assert result.success is True
        assert result.content == "reconnected"

    @pytest.mark.asyncio
    async def test_reconnect_failed(self):
        """重连失败 → success=False。"""
        client = make_mock_client(is_alive=False)
        client.connect = AsyncMock(side_effect=RuntimeError("No route to host"))
        t = make_mcp_tool("dead")
        wrapper = MCPToolWrapper("srv", t, client)

        result = await wrapper.execute()
        assert result.success is False
        assert "reconnect failed" in result.error

    @pytest.mark.asyncio
    async def test_empty_content(self):
        """远端返回空 content → content="" 且 success=True。"""
        client = make_mock_client(return_content=[], is_error=False)
        t = make_mcp_tool("silent")
        wrapper = MCPToolWrapper("srv", t, client)

        result = await wrapper.execute()
        assert result.success is True
        assert "(no output)" in result.content

    @pytest.mark.asyncio
    async def test_remote_error_empty_content(self):
        """远端 isError=True 但无 content → 兜底错误消息。"""
        client = make_mock_client(return_content=[], is_error=True)
        t = make_mcp_tool("silent_fail")
        wrapper = MCPToolWrapper("srv", t, client)

        result = await wrapper.execute()
        assert result.success is False
        assert "MCP remote error" in result.error


# ── _extract_text 测试 ──────────────────────────────────────────────────


class TestExtractText:
    def test_text_content(self):
        content = [
            mcp_types.TextContent(type="text", text="hello"),
            mcp_types.TextContent(type="text", text="world"),
        ]
        assert _extract_text(content) == "hello\nworld"

    def test_empty_content(self):
        assert _extract_text([]) == "(no output)"

    def test_image_content(self):
        content = [mcp_types.ImageContent(type="image", data="...", mimeType="image/png")]
        assert "[image: image/png]" in _extract_text(content)
