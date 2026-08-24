"""项目指令加载器测试 —— @include 展开、深度截断、环路检测、路径逃逸。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from csycode.instructions.loader import Loader, process_includes, _parse_include


class TestParseInclude:
    def test_old_format(self):
        assert _parse_include("@include sub/rules.md") == "sub/rules.md"

    def test_normal_line_not_include(self):
        assert _parse_include("just a normal line") == ""
        assert _parse_include("@username something") == ""

    def test_empty_line(self):
        assert _parse_include("") == ""


class TestProcessIncludes:
    def test_normal_expand(self, tmp_path: Path):
        """基本的 @include 展开。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "rules.md").write_text("hello world", encoding="utf-8")

        content = "@include sub/rules.md"
        result = process_includes(content, tmp_path, tmp_path)
        assert "hello world" in result

    def test_depth_limit(self, tmp_path: Path):
        """超过最大深度时截断并发出警告。

        构造深度链条: root → d1 → d2 → d3 → d4 → d5 → d6(超限)。
        用独立文件避免环路检测先于深度检测触发。
        """
        for i in range(6):
            fname = "level_%d.md" % i
            next_fname = "level_%d.md" % (i + 1)
            (tmp_path / fname).write_text(
                "@include %s" % next_fname, encoding="utf-8"
            )
        # d6 自引用会导致下一层触发深度超限
        (tmp_path / "level_6.md").write_text(
            "@include level_7.md", encoding="utf-8"
        )

        result = process_includes(
            "@include level_0.md", tmp_path, tmp_path
        )
        # 深度超过 MAX_INCLUDE_DEPTH(5) 时应有警告
        assert "超过最大嵌套深度" in result

    def test_cycle_detection(self, tmp_path: Path):
        """环路检测：A include B, B include A。"""
        (tmp_path / "a.md").write_text("@include b.md", encoding="utf-8")
        (tmp_path / "b.md").write_text("@include a.md", encoding="utf-8")
        result = process_includes(
            "@include a.md", tmp_path, tmp_path
        )
        assert "检测到环路" in result

    def test_file_not_found(self, tmp_path: Path):
        """文件不存在时输出 HTML 注释。"""
        result = process_includes(
            "@include missing.md", tmp_path, tmp_path
        )
        assert "跳过" in result

    def test_binary_file_skip(self, tmp_path: Path):
        """二进制文件跳过。"""
        (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")
        result = process_includes(
            "@include data.bin", tmp_path, tmp_path
        )
        assert "跳过" in result

    def test_code_block_skip(self, tmp_path: Path):
        """围栏代码块内的 @include 不展开。"""
        (tmp_path / "rules.md").write_text("expanded", encoding="utf-8")
        content = "```\n@include rules.md\n```"
        result = process_includes(content, tmp_path, tmp_path)
        assert "expanded" not in result

    def test_nested_includes(self, tmp_path: Path):
        """嵌套 include: A include B, B include C。"""
        (tmp_path / "c.md").write_text("deep", encoding="utf-8")
        (tmp_path / "b.md").write_text("@include c.md", encoding="utf-8")
        result = process_includes(
            "@include b.md", tmp_path, tmp_path
        )
        assert "deep" in result

    def test_path_escape(self, tmp_path: Path):
        """路径逃逸：尝试 include 项目根之外的路径。"""
        outside = tmp_path.parent / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        result = process_includes(
            "@include ../outside.md", tmp_path, tmp_path
        )
        assert "跳过" in result or "secret" not in result


class TestLoader:
    def test_load_from_project_root(self, tmp_path: Path):
        """从项目根加载 csyCODE.md。"""
        (tmp_path / "csyCODE.md").write_text("# Project Rules\n\n用中文回复", encoding="utf-8")
        loader = Loader(str(tmp_path))
        result = loader.load()
        assert "用中文回复" in result

    def test_load_from_dot_csycode(self, tmp_path: Path):
        """从 .csycode/csyCODE.md 加载。"""
        dot_dir = tmp_path / ".csycode"
        dot_dir.mkdir()
        (dot_dir / "csyCODE.md").write_text("local rules", encoding="utf-8")
        loader = Loader(str(tmp_path))
        result = loader.load()
        assert "local rules" in result

    def test_no_files_returns_empty(self, tmp_path: Path):
        """没有指令文件时返回空字符串。"""
        loader = Loader(str(tmp_path))
        result = loader.load()
        assert result == ""
