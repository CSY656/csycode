"""Tests for csycode.conversation."""

from __future__ import annotations

from csycode.conversation import Conversation


class TestConversation:
    def test_empty_on_init(self) -> None:
        conv = Conversation()
        assert conv.messages() == []

    def test_add_user(self) -> None:
        conv = Conversation()
        conv.add_user("hello")
        msgs = conv.messages()
        assert len(msgs) == 1
        assert msgs[0].role == "user"
        assert msgs[0].content == "hello"

    def test_add_assistant(self) -> None:
        conv = Conversation()
        conv.add_assistant("hi there")
        msgs = conv.messages()
        assert len(msgs) == 1
        assert msgs[0].role == "assistant"
        assert msgs[0].content == "hi there"

    def test_multi_turn_order(self) -> None:
        conv = Conversation()
        conv.add_user("Q1")
        conv.add_assistant("A1")
        conv.add_user("Q2")
        conv.add_assistant("A2")
        msgs = conv.messages()
        assert len(msgs) == 4
        roles = [m.role for m in msgs]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_messages_returns_copy(self) -> None:
        conv = Conversation()
        conv.add_user("hello")
        msgs = conv.messages()
        msgs.append(None)  # type: ignore[arg-type]
        assert len(conv.messages()) == 1
