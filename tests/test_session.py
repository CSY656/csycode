"""会话持久化测试 —— JSONL 读写、列表、加载、清理。"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from csycode.session.writer import Writer
from csycode.session.list import list_sessions, SessionInfo
from csycode.session.load import load_session
from csycode.session.cleanup import clean_expired
from csycode.llm import Message
from csycode.compact.state import new_session_context


class TestWriter:
    def test_append_user_message(self, tmp_path: Path):
        """写入 user 消息 → JSONL 可读回。"""
        writer = Writer(str(tmp_path))
        msg = Message(role="user", content="hello")
        writer.append(msg, model="test-model", is_first=True)
        writer.close()

        jsonl_path = tmp_path / "conversation.jsonl"
        assert jsonl_path.is_file()

        lines = jsonl_path.read_text("utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["role"] == "user"
        assert data["content"] == "hello"
        assert data["model"] == "test-model"

    def test_append_multiple_messages(self, tmp_path: Path):
        """写入多条消息 → JSONL 行数正确。"""
        writer = Writer(str(tmp_path))
        writer.append(Message(role="user", content="q1"))
        writer.append(Message(role="assistant", content="a1"))
        writer.append(Message(role="user", content="q2"))
        writer.close()

        jsonl_path = tmp_path / "conversation.jsonl"
        lines = jsonl_path.read_text("utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_write_compact_marker(self, tmp_path: Path):
        """compact marker 写入正确的标记行。"""
        writer = Writer(str(tmp_path))
        writer.append(Message(role="user", content="before"))
        writer.write_compact_marker()
        writer.append(Message(role="user", content="after"))
        writer.close()

        jsonl_path = tmp_path / "conversation.jsonl"
        lines = jsonl_path.read_text("utf-8").strip().split("\n")
        assert len(lines) == 3

        # 第二行是 compact marker
        marker = json.loads(lines[1])
        assert marker.get("type") == "compact"

    def test_open_existing(self, tmp_path: Path):
        """open_existing 追加模式不覆盖已有内容。"""
        # 先创建并写入
        w1 = Writer(str(tmp_path))
        w1.append(Message(role="user", content="first"))
        w1.close()

        # 再打开并追加
        w2 = Writer.open_existing(str(tmp_path))
        w2.append(Message(role="assistant", content="second"))
        w2.close()

        jsonl_path = tmp_path / "conversation.jsonl"
        lines = jsonl_path.read_text("utf-8").strip().split("\n")
        assert len(lines) == 2


class TestLoadSession:
    def test_load_basic(self, tmp_path: Path):
        """基本加载：写入 → 读回消息列表正确。"""
        writer = Writer(str(tmp_path))
        writer.append(Message(role="user", content="hello"))
        writer.append(Message(role="assistant", content="world"))
        writer.close()

        msgs = load_session(str(tmp_path))
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "hello"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "world"

    def test_skip_compact_marker(self, tmp_path: Path):
        """从 compact 标记之后加载。"""
        writer = Writer(str(tmp_path))
        writer.append(Message(role="user", content="old1"))
        writer.append(Message(role="assistant", content="old2"))
        writer.write_compact_marker()
        writer.append(Message(role="user", content="new1"))
        writer.append(Message(role="assistant", content="new2"))
        writer.close()

        msgs = load_session(str(tmp_path))
        # 只加载 compact 标记之后的消息
        assert len(msgs) == 2
        assert msgs[0].content == "new1"
        assert msgs[1].content == "new2"

    def test_bad_line_skip(self, tmp_path: Path):
        """坏行跳过，正常行仍然加载。"""
        jsonl_path = tmp_path / "conversation.jsonl"
        lines = [
            json.dumps({"role": "user", "content": "good", "ts": time.time()}),
            "{this is bad json",
            json.dumps({"role": "assistant", "content": "also good", "ts": time.time()}),
        ]
        jsonl_path.write_text("\n".join(lines), encoding="utf-8")

        msgs = load_session(str(tmp_path))
        assert len(msgs) == 2

    def test_orphaned_tool_calls_truncated(self, tmp_path: Path):
        """末尾孤立的 tool_calls 被截断。"""
        jsonl_path = tmp_path / "conversation.jsonl"
        lines = [
            json.dumps({"role": "user", "content": "read a file", "ts": time.time()}),
            json.dumps({
                "role": "assistant",
                "content": "ok",
                "tool_calls": [{"id": "tc1", "name": "read_file", "arguments": {"path": "test.py"}}],
                "ts": time.time(),
            }),
        ]
        jsonl_path.write_text("\n".join(lines), encoding="utf-8")

        msgs = load_session(str(tmp_path))
        # tool_calls 消息后面没有 tool result，应被截断
        assert len(msgs) == 1
        assert msgs[0].role == "user"


class TestListSessions:
    def test_empty_dir(self, tmp_path: Path):
        """空目录返回空列表。"""
        sessions = list_sessions(str(tmp_path))
        assert sessions == []

    def test_list_sessions(self, tmp_path: Path, monkeypatch):
        """创建多个会话 → 列表返回正确数量。"""
        # 创建几个会话目录，用 sleep 保证文件修改时间有先后
        for ts, title in [
            ("20260601-120000", "test1"),
            ("20260602-120000", "test2"),
        ]:
            sid = "%s-dead" % ts
            sdir = tmp_path / sid
            sdir.mkdir()
            jsonl = sdir / "conversation.jsonl"
            jsonl.write_text(
                json.dumps({"role": "user", "content": title, "ts": time.time(), "model": "gpt-4"}),
                encoding="utf-8",
            )
            time.sleep(0.05)  # 确保修改时间有差异

        sessions = list_sessions(str(tmp_path))
        assert len(sessions) == 2
        # 按时间倒序：后创建的(test2)排前面
        assert "test2" in sessions[0].title
        assert "test1" in sessions[1].title

    def test_skips_old_format(self, tmp_path: Path):
        """旧格式 ID 目录被跳过。"""
        sdir = tmp_path / "1717000000-abc12345"  # 旧格式
        sdir.mkdir()
        (sdir / "conversation.jsonl").write_text(
            json.dumps({"role": "user", "content": "old", "ts": time.time()}),
            encoding="utf-8",
        )

        sessions = list_sessions(str(tmp_path))
        assert len(sessions) == 0
