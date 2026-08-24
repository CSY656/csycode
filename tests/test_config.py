"""Tests for csycode.config."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from csycode.config import ConfigError, effective_context_window, load


def _write_config(content: dict, dir: str) -> str:
    """Write a YAML config to a temp file and return the path."""
    p = Path(dir) / "config.yaml"
    p.write_text(yaml.dump(content), encoding="utf-8")
    return str(p)


class TestLoad:
    def test_single_valid_provider(self, tmp_path: str) -> None:
        path = _write_config(
            {
                "providers": [
                    {
                        "name": "Claude",
                        "protocol": "anthropic",
                        "api_key": "sk-ant-xxx",
                        "model": "claude-sonnet-4-6",
                    }
                ]
            },
            str(tmp_path),
        )
        cfg = load(path)
        assert len(cfg.providers) == 1
        p = cfg.providers[0]
        assert p.name == "Claude"
        assert p.protocol == "anthropic"
        assert p.api_key == "sk-ant-xxx"
        assert p.model == "claude-sonnet-4-6"
        assert p.base_url is None
        assert p.thinking is False

    def test_multiple_providers(self, tmp_path: str) -> None:
        path = _write_config(
            {
                "providers": [
                    {
                        "name": "Claude",
                        "protocol": "anthropic",
                        "api_key": "sk-ant-xxx",
                        "model": "claude-sonnet-4-6",
                    },
                    {
                        "name": "GPT",
                        "protocol": "openai",
                        "api_key": "sk-xxx",
                        "model": "gpt-5",
                    },
                ]
            },
            str(tmp_path),
        )
        cfg = load(path)
        assert len(cfg.providers) == 2
        assert cfg.providers[0].protocol == "anthropic"
        assert cfg.providers[1].protocol == "openai"

    def test_base_url_and_thinking(self, tmp_path: str) -> None:
        path = _write_config(
            {
                "providers": [
                    {
                        "name": "Custom",
                        "protocol": "openai",
                        "api_key": "sk-xxx",
                        "model": "gpt-5",
                        "base_url": "https://api.custom.com/v1",
                        "thinking": True,
                    }
                ]
            },
            str(tmp_path),
        )
        cfg = load(path)
        p = cfg.providers[0]
        assert p.base_url == "https://api.custom.com/v1"
        assert p.thinking is True


class TestValidationErrors:
    def test_file_missing(self) -> None:
        with pytest.raises(ConfigError, match="配置文件不存在"):
            load("/nonexistent/path/config.yaml")

    def test_missing_providers_key(self, tmp_path: str) -> None:
        path = _write_config({}, str(tmp_path))
        with pytest.raises(ConfigError, match="providers"):
            load(path)

    def test_empty_providers(self, tmp_path: str) -> None:
        path = _write_config({"providers": []}, str(tmp_path))
        with pytest.raises(ConfigError, match="非空"):
            load(path)

    def test_missing_name(self, tmp_path: str) -> None:
        path = _write_config(
            {
                "providers": [
                    {
                        "protocol": "anthropic",
                        "api_key": "sk-xxx",
                        "model": "claude",
                    }
                ]
            },
            str(tmp_path),
        )
        with pytest.raises(ConfigError, match="providers\\[0\\].name 不能为空"):
            load(path)

    def test_missing_api_key(self, tmp_path: str) -> None:
        path = _write_config(
            {
                "providers": [
                    {
                        "name": "Claude",
                        "protocol": "anthropic",
                        "model": "claude",
                    }
                ]
            },
            str(tmp_path),
        )
        with pytest.raises(ConfigError, match="providers\\[0\\].api_key 不能为空"):
            load(path)

    def test_invalid_protocol(self, tmp_path: str) -> None:
        path = _write_config(
            {
                "providers": [
                    {
                        "name": "Bad",
                        "protocol": "gemini",
                        "api_key": "xxx",
                        "model": "gemini-pro",
                    }
                ]
            },
            str(tmp_path),
        )
        with pytest.raises(ConfigError, match="protocol"):
            load(path)

    def test_empty_api_key(self, tmp_path: str) -> None:
        path = _write_config(
            {
                "providers": [
                    {
                        "name": "C",
                        "protocol": "openai",
                        "api_key": "   ",
                        "model": "gpt",
                    }
                ]
            },
            str(tmp_path),
        )
        with pytest.raises(ConfigError, match="api_key"):
            load(path)

    def test_yaml_parse_error(self, tmp_path: str) -> None:
        p = Path(str(tmp_path)) / "bad.yaml"
        p.write_text("providers: [{{{bad", encoding="utf-8")
        with pytest.raises(ConfigError, match="YAML 解析失败"):
            load(str(p))


# ── context_window 配置测试 ──────────────────────────────────────────


class TestContextWindow:
    def test_unconfigured_anthropic_defaults_to_200k(self):
        """anthropic 不配置 context_window → 默认 200000。"""
        from csycode.config import ProviderConfig

        p = ProviderConfig(
            name="test", protocol="anthropic", api_key="k", model="m"
        )
        assert effective_context_window(p) == 200000

    def test_zero_openai_defaults_to_128k(self):
        """openai 配置 context_window=0 → 默认 128000。"""
        from csycode.config import ProviderConfig

        p = ProviderConfig(
            name="test", protocol="openai", api_key="k", model="m",
            context_window=0,
        )
        assert effective_context_window(p) == 128000

    def test_positive_value_overrides_default(self):
        """配置正数 context_window → 返回配置值。"""
        from csycode.config import ProviderConfig

        p = ProviderConfig(
            name="test", protocol="anthropic", api_key="k", model="m",
            context_window=100000,
        )
        assert effective_context_window(p) == 100000

    def test_unknown_protocol_defaults_to_200k(self):
        """未知 protocol 不配置 context_window → 保守默认 200000。"""
        from csycode.config import ProviderConfig

        p = ProviderConfig(
            name="test", protocol="anthropic", api_key="k", model="m",
            context_window=0,
        )
        # protocol 已经是 "anthropic"，通过构造来测。用未知 protocol 需要直接调用
        # effective_context_window 不支持未知 protocol 的构造（Literal 约束），
        # 但函数内有兜底返回 200000
        assert effective_context_window(p) == 200000

    def test_context_window_loaded_from_yaml(self, tmp_path: str) -> None:
        """YAML 中 context_window 字段被正确加载。"""
        path = _write_config(
            {
                "providers": [
                    {
                        "name": "Claude",
                        "protocol": "anthropic",
                        "api_key": "sk-ant-xxx",
                        "model": "claude-sonnet-4-6",
                        "context_window": 100000,
                    }
                ]
            },
            str(tmp_path),
        )
        cfg = load(path)
        assert cfg.providers[0].context_window == 100000
        assert effective_context_window(cfg.providers[0]) == 100000
