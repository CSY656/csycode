"""Configuration loading and validation.

Reads .csycode/config.yaml and produces a validated Config with providers,
tools, agent, and MCP server settings.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""


# ── 环境变量工具 ────────────────────────────────────────────────────────

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def resolve_env_vars(value: str) -> str:
    """展开字符串中的 ${VAR}，未定义变量保留原样。"""
    return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def build_child_env(declared_env: dict[str, str] | None) -> dict[str, str]:
    """构建子进程环境变量：仅传递 PATH + 声明的变量（安全，不泄露 host 环境）。"""
    env: dict[str, str] = {}
    path = os.environ.get("PATH", "")
    if path:
        env["PATH"] = path
    for key, value in (declared_env or {}).items():
        env[key] = resolve_env_vars(value)
    return env


# ── 数据模型 ────────────────────────────────────────────────────────────


@dataclass
class ProviderConfig:
    name: str
    protocol: Literal["anthropic", "openai", "openai-compat"]
    api_key: str
    model: str
    base_url: str | None = None
    thinking: bool = False
    context_window: int = 0  # 单位 token，0 表示走协议默认

    def resolve_api_key(self) -> str:
        """解析 API key，支持 ${VAR} 环境变量替换。"""
        if self.api_key:
            return resolve_env_vars(self.api_key)
        # 尝试从环境变量获取
        env_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openai-compat": "OPENAI_API_KEY",
        }
        env_var = env_map.get(self.protocol, "")
        if env_var:
            from_env = os.environ.get(env_var, "")
            if from_env:
                return from_env
        return self.api_key


def effective_context_window(p: ProviderConfig) -> int:
    """获取 provider 的有效上下文窗口。

    配置 > 0 返回配置值；否则按 protocol 给默认值。
    """
    # 协议默认值（内联避免额外模块依赖）
    _DEFAULT_ANTHROPIC = 200000
    _DEFAULT_OPENAI = 128000

    if p.context_window > 0:
        return p.context_window
    if p.protocol == "anthropic":
        return _DEFAULT_ANTHROPIC
    if p.protocol in ("openai", "openai-compat"):
        return _DEFAULT_OPENAI
    return _DEFAULT_ANTHROPIC


@dataclass
class ToolConfig:
    """工具系统配置，全部有默认值。"""

    timeouts: dict[str, int] = field(
        default_factory=lambda: {
            "read_file": 10,
            "write_file": 10,
            "edit_file": 10,
            "run_command": 120,
            "glob": 30,
            "grep": 60,
        }
    )
    allow_paths: list[str] = field(default_factory=list)


@dataclass
class AgentConfig:
    """Agent Loop 配置，全部有默认值。"""

    max_iterations: int = 50
    max_consecutive_unknown_tools: int = 2


@dataclass
class MCPServerConfig:
    """单个 MCP server 配置（对齐 mewcode）。"""

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)

    @property
    def is_stdio(self) -> bool:
        return self.command is not None


@dataclass
class FeaturesConfig:
    """Feature flag 配置（ch15）。"""

    coordinator_mode: bool = False
    fork_teammate: bool = False


@dataclass
class Config:
    providers: list[ProviderConfig] = field(default_factory=list)
    tools: ToolConfig = field(default_factory=ToolConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    # ch13: SubAgent 后台开关
    enable_subagent_background: bool = True
    # ch15: Feature flags
    features: FeaturesConfig = field(default_factory=FeaturesConfig)

    def effective_enable_subagent_background(self) -> bool:
        """返回 SubAgent 后台模式是否生效。默认 True。"""
        return self.enable_subagent_background


def load(path: str) -> Config:
    """Load and validate configuration from a YAML file.

    Args:
        path: Path to the config YAML file.

    Returns:
        A validated Config object.

    Raises:
        ConfigError: If the file is missing, YAML is malformed, or validation fails.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"配置文件不存在: {path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML 解析失败: {e}") from e

    if raw is None or "providers" not in raw:
        raise ConfigError("配置文件缺少 'providers' 字段")

    return _parse_raw(raw)


def _parse_raw(raw: dict) -> Config:
    """从已解析的 YAML dict 构造 Config（provider 强制校验）。"""
    providers_raw = raw["providers"]
    if not isinstance(providers_raw, list) or len(providers_raw) == 0:
        raise ConfigError("'providers' 必须是非空列表")

    providers = []
    for i, entry in enumerate(providers_raw):
        providers.append(_parse_provider(entry, i))

    # 解析可选的 tools 配置段
    tools = ToolConfig()
    if "tools" in raw and isinstance(raw["tools"], dict):
        tools_raw = raw["tools"]
        # timeouts
        if "timeouts" in tools_raw and isinstance(tools_raw["timeouts"], dict):
            for key, value in tools_raw["timeouts"].items():
                if key in tools.timeouts and isinstance(value, int):
                    tools.timeouts[key] = value
        # allow_paths
        if "allow_paths" in tools_raw and isinstance(tools_raw["allow_paths"], list):
            tools.allow_paths = [
                str(p) for p in tools_raw["allow_paths"] if isinstance(p, str)
            ]

    # 解析可选的 agent 配置段
    agent = AgentConfig()
    if "agent" in raw and isinstance(raw["agent"], dict):
        agent_raw = raw["agent"]
        if "max_iterations" in agent_raw and isinstance(
            agent_raw["max_iterations"], int
        ):
            agent.max_iterations = agent_raw["max_iterations"]
        if "max_consecutive_unknown_tools" in agent_raw and isinstance(
            agent_raw["max_consecutive_unknown_tools"], int
        ):
            agent.max_consecutive_unknown_tools = agent_raw[
                "max_consecutive_unknown_tools"
            ]

    # 解析可选的 mcp_servers 配置段
    mcp_servers: list[MCPServerConfig] = []
    if "mcp_servers" in raw and isinstance(raw["mcp_servers"], list):
        for i, srv in enumerate(raw["mcp_servers"]):
            if not isinstance(srv, dict):
                continue
            mcp_servers.append(_parse_mcp_server(srv, i))

    # ch13: 解析 enable_subagent_background
    enable_subagent_background = True
    if "enable_subagent_background" in raw:
        enable_subagent_background = bool(raw["enable_subagent_background"])

    # ch15: 解析 features 配置段
    features = FeaturesConfig()
    if "features" in raw and isinstance(raw["features"], dict):
        feat_raw = raw["features"]
        if "coordinator_mode" in feat_raw:
            features.coordinator_mode = bool(feat_raw["coordinator_mode"])
        if "fork_teammate" in feat_raw:
            features.fork_teammate = bool(feat_raw["fork_teammate"])

    return Config(
        providers=providers, tools=tools, agent=agent, mcp_servers=mcp_servers,
        enable_subagent_background=enable_subagent_background,
        features=features,
    )


# ── 三层配置合并（对齐 mewcode）─────────────────────────────────────────


def load_merged(project_root: str) -> Config:
    """加载三层配置并合并：用户级 → 项目级 → 本地级。

    合并规则（对齐 mewcode _merge_config）：
    - providers: 后层完全覆盖前层
    - tools: dict 浅合并（后层覆盖同 key）
    - agent: 后层覆盖前层非默认字段
    - mcp_servers: 按 name 合并，后层同 name 覆盖前层
    - permission_mode: 后层覆盖前层（若存在）

    Args:
        project_root: 项目根目录路径。

    Returns:
        合并后的 Config。

    Raises:
        ConfigError: 所有层级都没有有效 providers 时抛出。
    """
    home = Path.home()
    user_path = home / ".csycode" / "config.yaml"
    project_path = Path(project_root) / ".csycode" / "config.yaml"
    local_path = Path(project_root) / ".csycode" / "config.local.yaml"

    configs: list[Config] = []
    for path in (user_path, project_path, local_path):
        try:
            if path.exists():
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                if raw and "providers" in raw:
                    configs.append(_parse_raw(raw))
        except (yaml.YAMLError, ConfigError) as e:
            # 降级：单层失败不阻塞启动，仅记录警告
            import logging
            logging.getLogger(__name__).warning(
                "跳过配置层 %s: %s", path, e
            )

    if not configs:
        raise ConfigError(
            "未找到有效配置。请在 ~/.csycode/config.yaml 或 "
            ".csycode/config.yaml 中配置 providers。"
        )

    merged = configs[0]
    for override in configs[1:]:
        merged = _merge_config(merged, override)

    return merged


def _merge_config(base: Config, override: Config) -> Config:
    """将 override 合并到 base，返回新的 Config。

    合并规则：
    - providers: override 完全替换 base
    - tools: timeouts 浅合并，allow_paths 替换
    - agent: 非默认字段覆盖
    - mcp_servers: 按 name 合并（override 优先）
    """
    # Providers: 完全覆盖
    providers = override.providers if override.providers else base.providers

    # Tools: timeouts dict 合并，allow_paths 覆盖
    tools = ToolConfig()
    tools.timeouts = {**base.tools.timeouts, **override.tools.timeouts}
    tools.allow_paths = override.tools.allow_paths if override.tools.allow_paths else base.tools.allow_paths

    # Agent: 非默认字段覆盖
    agent = AgentConfig()
    agent.max_iterations = (
        override.agent.max_iterations
        if override.agent.max_iterations != AgentConfig().max_iterations
        else base.agent.max_iterations
    )
    agent.max_consecutive_unknown_tools = (
        override.agent.max_consecutive_unknown_tools
        if override.agent.max_consecutive_unknown_tools != AgentConfig().max_consecutive_unknown_tools
        else base.agent.max_consecutive_unknown_tools
    )

    # MCP servers: 按 name 合并
    merged_servers: dict[str, MCPServerConfig] = {}
    for s in base.mcp_servers:
        merged_servers[s.name] = s
    for s in override.mcp_servers:
        merged_servers[s.name] = s  # override 覆盖同名

    # ch16 fix: 合并 enable_subagent_background（override 非默认时覆盖 base）
    _default_cfg = Config()
    enable_subagent_bg = (
        override.enable_subagent_background
        if override.enable_subagent_background != _default_cfg.enable_subagent_background
        else base.enable_subagent_background
    )
    # ch16 fix: 合并 features（逐字段：非默认值覆盖）
    features = FeaturesConfig()
    _default_features = FeaturesConfig()
    features.coordinator_mode = (
        override.features.coordinator_mode
        if override.features.coordinator_mode != _default_features.coordinator_mode
        else base.features.coordinator_mode
    )
    features.fork_teammate = (
        override.features.fork_teammate
        if override.features.fork_teammate != _default_features.fork_teammate
        else base.features.fork_teammate
    )

    return Config(
        providers=providers,
        tools=tools,
        agent=agent,
        mcp_servers=list(merged_servers.values()),
        enable_subagent_background=enable_subagent_bg,
        features=features,
    )


def _parse_provider(entry: dict, index: int) -> ProviderConfig:
    """Parse and validate a single provider entry."""
    prefix = f"providers[{index}]"

    name = _require_str(entry, "name", prefix)
    protocol = _require_str(entry, "protocol", prefix)
    api_key = _require_str(entry, "api_key", prefix)
    model = _require_str(entry, "model", prefix)

    if protocol not in ("anthropic", "openai", "openai-compat"):
        raise ConfigError(
            f"{prefix}.protocol 必须是 'anthropic'、'openai' 或 'openai-compat'，当前值: '{protocol}'"
        )

    base_url = entry.get("base_url") or None
    thinking = bool(entry.get("thinking", False))
    context_window = int(entry.get("context_window", 0) or 0)

    return ProviderConfig(
        name=name,
        protocol=protocol,  # type: ignore[arg-type]
        api_key=api_key,
        model=model,
        base_url=base_url,
        thinking=thinking,
        context_window=context_window,
    )


def _require_str(entry: dict, key: str, prefix: str) -> str:
    """Extract a required string field, raising ConfigError on failure."""
    value = entry.get(key)
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise ConfigError(f"{prefix}.{key} 不能为空")
    if not isinstance(value, str):
        raise ConfigError(f"{prefix}.{key} 必须是字符串")
    return value.strip()


def _parse_mcp_server(entry: dict, index: int) -> MCPServerConfig:
    """Parse and validate a single mcp_server entry."""
    prefix = f"mcp_servers[{index}]"

    name = _require_str(entry, "name", prefix)
    command = entry.get("command")
    url = entry.get("url")

    # command 和 url 不能同时存在
    if command is not None and url is not None:
        raise ConfigError(f"{prefix} '{name}': cannot have both 'command' and 'url'")
    # 至少要有一个
    if command is None and url is None:
        raise ConfigError(
            f"{prefix} '{name}': must have either 'command' (stdio) or 'url' (http)"
        )

    args = entry.get("args", [])
    if not isinstance(args, list):
        args = []
    args = [str(a) for a in args]

    headers = entry.get("headers", {})
    if not isinstance(headers, dict):
        headers = {}
    headers = {str(k): str(v) for k, v in headers.items()}

    env = entry.get("env", {})
    if not isinstance(env, dict):
        env = {}
    env = {str(k): str(v) for k, v in env.items()}

    return MCPServerConfig(
        name=str(name),
        command=str(command).strip() if command else None,
        args=args,
        url=str(url).strip() if url else None,
        headers=headers,
        env=env,
    )
