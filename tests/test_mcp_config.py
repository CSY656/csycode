"""MCP 配置加载测试（对齐 mewcode）。

覆盖：resolve_env_vars / build_child_env / MCPServerConfig 解析 / 校验。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from csycode.config import (
    ConfigError,
    MCPServerConfig,
    build_child_env,
    load,
    resolve_env_vars,
)


# ── resolve_env_vars ────────────────────────────────────────────────────


class TestResolveEnvVars:
    def test_substitutes_existing_var(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret123")
        assert resolve_env_vars("${MY_TOKEN}") == "secret123"

    def test_preserves_missing_var(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        assert resolve_env_vars("${NONEXISTENT_VAR}") == "${NONEXISTENT_VAR}"

    def test_no_placeholder_passthrough(self):
        assert resolve_env_vars("plain-text") == "plain-text"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("A", "hello")
        monkeypatch.setenv("B", "world")
        assert resolve_env_vars("${A}-${B}") == "hello-world"

    def test_mixed_existing_and_missing(self, monkeypatch):
        monkeypatch.setenv("EXISTS", "yes")
        monkeypatch.delenv("NOPE", raising=False)
        assert resolve_env_vars("${EXISTS}/${NOPE}") == "yes/${NOPE}"


# ── build_child_env ─────────────────────────────────────────────────────


class TestBuildChildEnv:
    def test_includes_path(self):
        env = build_child_env(None)
        assert "PATH" in env

    def test_includes_declared_vars(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "abc")
        env = build_child_env({"TOKEN": "${MY_SECRET}"})
        assert env["TOKEN"] == "abc"
        assert "PATH" in env

    def test_excludes_host_vars(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
        env = build_child_env({"FOO": "bar"})
        assert "ANTHROPIC_API_KEY" not in env
        assert env["FOO"] == "bar"

    def test_empty_declared_env(self):
        env = build_child_env({})
        assert "PATH" in env
        assert len(env) == 1


# ── MCPServerConfig ────────────────────────────────────────────────────


class TestMCPServerConfig:
    def test_is_stdio_true(self):
        cfg = MCPServerConfig(name="s", command="echo")
        assert cfg.is_stdio is True

    def test_is_stdio_false(self):
        cfg = MCPServerConfig(name="h", url="http://localhost")
        assert cfg.is_stdio is False

    def test_is_stdio_none(self):
        cfg = MCPServerConfig(name="x")
        assert cfg.is_stdio is False


# ── load_config 集成测试 ───────────────────────────────────────────────


class TestLoadConfigMCP:
    def _write_config(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "config.yaml"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return p

    def test_no_mcp_servers(self, tmp_path):
        path = self._write_config(tmp_path, """\
            providers:
              - name: test
                protocol: openai
                base_url: http://localhost
                model: gpt-4o
                api_key: sk-test
        """)
        config = load(str(path))
        assert config.mcp_servers == []

    def test_stdio_server(self, tmp_path):
        path = self._write_config(tmp_path, """\
            providers:
              - name: test
                protocol: openai
                base_url: http://localhost
                model: gpt-4o
                api_key: sk-test
            mcp_servers:
              - name: github
                command: npx
                args: ["-y", "@modelcontextprotocol/server-github"]
                env:
                  GITHUB_TOKEN: "${GITHUB_TOKEN}"
        """)
        config = load(str(path))
        assert len(config.mcp_servers) == 1
        srv = config.mcp_servers[0]
        assert srv.name == "github"
        assert srv.command == "npx"
        assert srv.is_stdio is True
        assert srv.args == ["-y", "@modelcontextprotocol/server-github"]

    def test_http_server(self, tmp_path):
        path = self._write_config(tmp_path, """\
            providers:
              - name: test
                protocol: openai
                base_url: http://localhost
                model: gpt-4o
                api_key: sk-test
            mcp_servers:
              - name: remote
                url: "https://api.example.com/mcp"
                headers:
                  Authorization: "Bearer ${TOKEN}"
        """)
        config = load(str(path))
        srv = config.mcp_servers[0]
        assert srv.name == "remote"
        assert srv.url == "https://api.example.com/mcp"
        assert srv.is_stdio is False

    def test_both_command_and_url_errors(self, tmp_path):
        path = self._write_config(tmp_path, """\
            providers:
              - name: test
                protocol: openai
                base_url: http://localhost
                model: gpt-4o
                api_key: sk-test
            mcp_servers:
              - name: bad
                command: npx
                url: "https://example.com"
        """)
        with pytest.raises(ConfigError, match="cannot have both"):
            load(str(path))

    def test_neither_command_nor_url_errors(self, tmp_path):
        path = self._write_config(tmp_path, """\
            providers:
              - name: test
                protocol: openai
                base_url: http://localhost
                model: gpt-4o
                api_key: sk-test
            mcp_servers:
              - name: bad
                env:
                  FOO: bar
        """)
        with pytest.raises(ConfigError, match="must have either"):
            load(str(path))

    def test_missing_name_errors(self, tmp_path):
        path = self._write_config(tmp_path, """\
            providers:
              - name: test
                protocol: openai
                base_url: http://localhost
                model: gpt-4o
                api_key: sk-test
            mcp_servers:
              - command: npx
        """)
        with pytest.raises(ConfigError, match="name"):
            load(str(path))

    def test_empty_name_errors(self, tmp_path):
        path = self._write_config(tmp_path, """\
            providers:
              - name: test
                protocol: openai
                base_url: http://localhost
                model: gpt-4o
                api_key: sk-test
            mcp_servers:
              - name: ""
                command: npx
        """)
        with pytest.raises(ConfigError, match="name"):
            load(str(path))
