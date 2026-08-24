"""恢复段渲染模块单元测试。"""

from __future__ import annotations

from datetime import datetime, timezone

from csycode.compact.recovery import (
    BOUNDARY_NOTICE,
    build_recovery_attachment,
    render_file_block,
    render_tools_block,
)
from csycode.compact.state import FileReadRecord


# ── render_file_block ────────────────────────────────────────────────


class TestRenderFileBlock:
    def test_basic_rendering(self):
        rec = FileReadRecord(
            path="/test/file.py",
            content="def hello(): pass",
            timestamp=datetime(2026, 7, 11, tzinfo=timezone.utc),
        )
        out = render_file_block(rec)
        assert "### /test/file.py" in out
        assert "def hello(): pass" in out
        assert "read at" in out

    def test_truncate_long_content(self):
        """超长内容保留头部，尾部出现 (content truncated)。"""
        from csycode.compact.const import ESTIMATE_CHARS_PER_TOKEN, RECOVERY_TOKENS_PER_FILE

        char_limit = int(RECOVERY_TOKENS_PER_FILE * ESTIMATE_CHARS_PER_TOKEN)
        long_content = "x" * (char_limit + 1000)
        rec = FileReadRecord(
            path="/long.py",
            content=long_content,
            timestamp=datetime.now(timezone.utc),
        )
        out = render_file_block(rec)
        assert "(content truncated)" in out
        assert len(out) < len(long_content) + 200  # 截断后明显更短


# ── render_tools_block ───────────────────────────────────────────────


class TestRenderToolsBlock:
    def test_renders_tool_names(self):
        from csycode.compact.const import ESTIMATE_CHARS_PER_TOKEN

        defs = [
            {"name": "read_file", "description": "读取文件", "input_schema": {"type": "object"}},
            {"name": "bash", "description": "执行命令", "input_schema": {"type": "object"}},
        ]
        out = render_tools_block(defs)
        assert "read_file" in out
        assert "bash" in out
        assert "读取文件" in out
        assert "执行命令" in out

    def test_handles_openai_format(self):
        defs = [
            {"type": "function", "function": {"name": "read", "description": "read file", "parameters": {}}},
        ]
        out = render_tools_block(defs)
        assert "read" in out


# ── build_recovery_attachment ────────────────────────────────────────


class TestBedBuildRecoveryAttachment:
    def test_three_sections_present(self):
        out = build_recovery_attachment([], [])
        assert "最近读过的文件" in out
        assert "当前可用工具" in out
        assert "边界提示" in out

    def test_empty_files_shows_none(self):
        out = build_recovery_attachment([], [])
        assert "(无)" in out

    def test_file_limit_respected(self):
        """最多保留 RECOVERY_FILE_LIMIT 个文件，按时间戳倒序。"""
        records = []
        for i in range(7):
            records.append(
                FileReadRecord(
                    path=f"/file{i}.py",
                    content=f"content {i}",
                    timestamp=datetime(2026, 7, 11, hour=i, tzinfo=timezone.utc),
                )
            )
        # 按时间戳倒序排序（build_recovery_attachment 期望已排序的快照）
        records.sort(key=lambda r: r.timestamp, reverse=True)
        out = build_recovery_attachment(records, [])
        # 只有最近 5 个（hours 6,5,4,3,2），早期的 file0, file1 被排除
        for i in range(2):  # file0(hour=0), file1(hour=1)
            assert f"file{i}.py" not in out
        for i in range(2, 7):  # file2~file6
            assert f"file{i}.py" in out

    def test_boundary_notice_stable(self):
        """两次调用输出逐字节相等。"""
        records = [
            FileReadRecord(
                path="/a.py", content="a", timestamp=datetime.now(timezone.utc)
            )
        ]
        o1 = build_recovery_attachment(records, [{"name": "t", "description": "d", "input_schema": {}}])
        o2 = build_recovery_attachment(records, [{"name": "t", "description": "d", "input_schema": {}}])
        assert o1 == o2

    def test_tool_names_match_input(self):
        """输出中工具名集合 == 入参工具名集合。"""
        defs = [
            {"name": "read_file", "description": "d1", "input_schema": {}},
            {"name": "bash", "description": "d2", "input_schema": {}},
        ]
        out = build_recovery_attachment([], defs)
        assert "read_file" in out
        assert "bash" in out


# ── BOUNDARY_NOTICE ──────────────────────────────────────────────────


def test_boundary_notice_immutable():
    """BOUNDARY_NOTICE 是稳定的字符串常量。"""
    assert "文件读取工具" in BOUNDARY_NOTICE
    assert "不要依据摘要内容做猜测" in BOUNDARY_NOTICE
