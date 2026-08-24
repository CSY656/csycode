"""Coordinator Mode —— Lead 的调度专精模式。

对齐 mewcode teams/coordinator.py。
开启后 Lead 工具集收窄为调度 + 只读 + bash（git merge），
剥夺 write_file / edit_file。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from csycode.config import Config

# ── 工具白名单 ────────────────────────────────────────────────────

COORDINATOR_ALLOWED_TOOLS: list[str] = [
    "Agent",
    "TeamCreate",
    "TeamDelete",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "SendMessage",
    "read_file",
    "glob",
    "grep",
    "bash",
]

# ── 系统提示词附录 ────────────────────────────────────────────────

COORDINATOR_SYSTEM_PROMPT_SUFFIX = """

## Coordinator Mode（协调者模式）

你正在以 **Coordinator（协调者）** 身份工作。你的职责是**调度队员**完成工作，
而不是自己动手写代码。

### 四阶段工作流

1. **Research（调研）**：派队员探索代码库，收集信息
2. **Synthesis（综合）**：阅读队员产出，形成方案
3. **Implementation（实施）**：派队员执行具体修改
4. **Verification（验证）**：派**独立队员**验证实施结果（不要让实施者验证自己）

### 纪律规则

- **派完队员就停手等汇报**：派出 Agent / SendMessage 后，
  **禁止**立刻调 read_file / glob / grep / bash 自己探索。
  **禁止**用 TaskList 轮询凑时间。
- 派完队员后的正确做法：发一行总结「已派 N 名队员探索 X，等待汇报」，让本轮结束。
- 只有以下场景允许自己使用读类工具：
  - Research 阶段的**第一次**目标定位
  - Synthesis 阶段读**队员产出的报告文件**
  - Verification 阶段 git diff / git status 等收敛操作

### 收敛阶段

所有任务完成后，用 bash 执行 git merge 逐个合并队员的 worktree 分支：
```
git merge worktree-team-<team>+<member> --no-ff -m "merge: <member>"
```
遇到冲突用 read_file 查看 + 推理解决，搞不定就 git merge --abort 上报用户。
"""


# ── 辅助函数 ──────────────────────────────────────────────────────

def env_truthy(v: str) -> bool:
    """判断环境变量是否为真值。

    >>> env_truthy("1")
    True
    >>> env_truthy("true")
    True
    >>> env_truthy("0")
    False
    """
    return v.lower() in ("1", "true", "yes")


def is_enabled(cfg: Config) -> bool:
    """判断 Coordinator Mode 是否启用（双锁机制）。

    Args:
        cfg: 应用配置。

    Returns:
        True 仅当 feature flag 和环境变量都开启。
    """
    # 检查 feature flag
    features = getattr(cfg, "features", None)
    if features is None:
        return False
    coordinator_mode = getattr(features, "coordinator_mode", False)
    if not coordinator_mode:
        return False

    # 检查环境变量
    env_val = os.environ.get("csyCODE_COORDINATOR_MODE", "")
    return env_truthy(env_val)


# Coordinator 禁用的工具（在 Lead 的 ToolRegistry 中 disable 掉）
COORDINATOR_DISABLED_TOOLS: list[str] = [
    "write_file",
    "edit_file",
]


def allowed_tools() -> list[str]:
    """返回 Coordinator Mode 允许的工具列表（文档用）。"""
    return list(COORDINATOR_ALLOWED_TOOLS)


def disabled_tools() -> list[str]:
    """返回 Coordinator Mode 需要禁用的工具列表。"""
    return list(COORDINATOR_DISABLED_TOOLS)


def system_prompt_suffix() -> str:
    """返回 Coordinator 系统提示词附录。"""
    return COORDINATOR_SYSTEM_PROMPT_SUFFIX
