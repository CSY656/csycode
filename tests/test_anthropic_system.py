"""Anthropic provider 系统提示序列化测试。

守护回归：
- 稳定 system 块序列化后带 cache_control 断点
- 环境 system 块序列化后不带 cache_control
"""

from __future__ import annotations

from csycode.llm import Message, Request, System
from csycode.llm.anthropic_provider import AnthropicProvider


# 不实际调用 API 的纯序列化测试
class TestAnthropicSystemSerialization:
    """验证 _build_messages 等内部逻辑对 system 块的处理。"""

    def test_stable_block_has_cache_control(self):
        """稳定 system 块应携带 cache_control: ephemeral。"""
        provider = _make_dummy_provider()
        req = Request(
            messages=[Message(role="user", content="hello")],
            system=System(stable="You are a helpful assistant.", environment=""),
        )

        system_blocks = provider._build_system_blocks(req)
        assert len(system_blocks) == 1
        block = system_blocks[0]
        assert block["type"] == "text"
        assert block["text"] == "You are a helpful assistant."
        assert "cache_control" in block
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_environment_block_no_cache_control(self):
        """环境 system 块不应携带 cache_control。"""
        provider = _make_dummy_provider()
        req = Request(
            messages=[Message(role="user", content="hello")],
            system=System(stable="", environment="Platform: linux\nDate: 2025-01-15"),
        )

        system_blocks = provider._build_system_blocks(req)
        assert len(system_blocks) == 1
        block = system_blocks[0]
        assert block["type"] == "text"
        assert "Platform: linux" in block["text"]
        assert "cache_control" not in block

    def test_both_blocks_stable_first_with_cache_control(self):
        """同时有 stable 和 environment 时：stable 在前带 cache_control，env 在后不带。"""
        provider = _make_dummy_provider()
        req = Request(
            messages=[Message(role="user", content="hello")],
            system=System(
                stable="You are a helpful assistant.",
                environment="Platform: linux",
            ),
        )

        system_blocks = provider._build_system_blocks(req)
        assert len(system_blocks) == 2

        # 第一块：stable → 带 cache_control
        assert system_blocks[0]["text"] == "You are a helpful assistant."
        assert "cache_control" in system_blocks[0]
        assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}

        # 第二块：environment → 不带 cache_control
        assert "Platform: linux" in system_blocks[1]["text"]
        assert "cache_control" not in system_blocks[1]

    def test_empty_system_blocks(self):
        """stable 和 environment 均为空时，system_blocks 为空列表。"""
        provider = _make_dummy_provider()
        req = Request(
            messages=[Message(role="user", content="hello")],
            system=System(stable="", environment=""),
        )

        system_blocks = provider._build_system_blocks(req)
        assert system_blocks == []

    def test_reminder_appended_to_last_user(self):
        """reminder 应追加到最后一条 user 消息的 content 块中。"""
        provider = _make_dummy_provider()
        messages = [
            {"role": "user", "content": "hello"},
        ]
        provider._append_reminder(messages, "<system-reminder>Plan Mode</system-reminder>")

        assert len(messages) == 1
        last = messages[-1]
        assert isinstance(last["content"], list)
        # 第一块是原文，第二块是 reminder
        assert len(last["content"]) == 2
        assert last["content"][0] == {"type": "text", "text": "hello"}
        assert last["content"][1] == {"type": "text", "text": "<system-reminder>Plan Mode</system-reminder>"}

    def test_reminder_new_user_when_last_is_assistant(self):
        """末条非 user 时，reminder 应新起一条 user 消息。"""
        provider = _make_dummy_provider()
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        provider._append_reminder(messages, "<system-reminder>Reminder</system-reminder>")

        assert len(messages) == 3
        assert messages[-1] == {"role": "user", "content": "<system-reminder>Reminder</system-reminder>"}


# ── helpers ──────────────────────────────────────────────────────────────

def _make_dummy_provider() -> AnthropicProvider:
    """创建一个不连 API 的 AnthropicProvider（仅用于测试内部方法）。"""
    from csycode.config import ProviderConfig

    cfg = ProviderConfig(
        name="test",
        protocol="anthropic",
        api_key="sk-test",
        model="claude-test",
    )
    return AnthropicProvider(cfg)
