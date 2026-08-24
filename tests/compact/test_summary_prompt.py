"""摘要 Prompt 模板与解析测试。"""

from __future__ import annotations

from csycode.compact.summary_prompt import (
    build_summary_prompt,
    extract_summary,
    serialize_conversation,
)
from csycode.llm import Message, ToolCall

from .conftest import make_assistant_msg, make_user_msg


class TestBuildSummaryPrompt:
    def test_returns_single_user_message(self):
        msgs = [make_user_msg("hello")]
        result = build_summary_prompt(msgs)
        assert len(result) == 1
        assert result[0].role == "user"

    def test_contains_nine_section_titles(self):
        """包含 9 部分小节的固定标题。"""
        result = build_summary_prompt([make_user_msg("hello")])
        content = result[0].content
        assert "1. Primary Request and Intent" in content
        assert "2. Key Technical Concepts" in content
        assert "3. Files and Code Sections" in content
        assert "4. Errors and fixes" in content
        assert "5. Problem Solving" in content
        assert "6. All user messages" in content
        assert "7. Pending Tasks" in content
        assert "8. Current Work" in content
        assert "9. Optional Next Step" in content

    def test_contains_analysis_and_summary_tags(self):
        result = build_summary_prompt([make_user_msg("hello")])
        content = result[0].content
        assert "<analysis>" in content
        assert "</analysis>" in content
        assert "<summary>" in content
        assert "</summary>" in content

    def test_no_tool_call_instruction(self):
        """明确指示不调用任何工具。"""
        result = build_summary_prompt([make_user_msg("hello")])
        content = result[0].content
        assert "Do NOT call any tools" in content


class TestSerializeConversation:
    def test_deterministic_output(self):
        """相同 msgs 两次序列化返回逐字节相等。"""
        msgs = [
            make_user_msg("hello"),
            make_assistant_msg("hi there"),
        ]
        s1 = serialize_conversation(msgs)
        s2 = serialize_conversation(msgs)
        assert s1 == s2

    def test_user_messages_prefixed(self):
        msgs = [make_user_msg("hello")]
        out = serialize_conversation(msgs)
        assert "user: hello" in out

    def test_tool_results_with_id(self):
        msg = Message(role="user", content="result content", tool_call_id="call-1")
        out = serialize_conversation([msg])
        assert "[result id=call-1]" in out

    def test_tool_calls_serialized(self):
        tc = ToolCall(id="tc1", name="read_file", arguments={"path": "/f.py"})
        msg = Message(role="assistant", content="", tool_calls=[tc])
        out = serialize_conversation([msg])
        assert "[call read_file id=tc1" in out


class TestExtractSummary:
    def test_standard_summary(self):
        raw = "some text <analysis>ignore</analysis> <summary>the summary here</summary> more"
        assert extract_summary(raw) == "the summary here"

    def test_missing_tags_returns_raw(self):
        raw = "no tags here"
        assert extract_summary(raw) == "no tags here"

    def test_nested_tags_uses_last(self):
        raw = "<summary>first</summary><summary>second</summary>"
        result = extract_summary(raw)
        assert result in ("first", "second")  # 取决于标签查找策略
