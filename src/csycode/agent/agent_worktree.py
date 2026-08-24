"""Agent Worktree 隔离执行 —— _execute_with_worktree + notice 构造。

对齐 mewcode AgentTool._execute_with_worktree:
- generate_worktree_name → create worktree → build notice →
- 构造子 Agent（work_dir=wt.path）→ run_to_completion →
- auto_cleanup（传入 head_commit）→ 有变更追加保留信息
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from csycode.tools.ctx import with_cwd

if TYPE_CHECKING:
    from csycode.worktree import Manager


def _random_name() -> str:
    """延迟导入避免 worktree 包不存在时崩溃。"""
    from csycode.worktree import random_agent_name

    return random_agent_name()


def build_worktree_notice(parent_cwd: str, wt_path: str) -> str:
    """构造 Worktree 上下文通知文本。

    对齐 mewcode build_worktree_notice:
    提示子 Agent 在隔离副本中工作，需要翻译父 Agent 的绝对路径到本地路径。
    """
    return f"""<worktree-context>
你当前在一个独立的 Git Worktree 副本中工作，与父 Agent 隔离。
- 父目录: {parent_cwd}
- 你的工作目录: {wt_path}
- 父 Agent 提到的绝对路径基于父目录，你需要翻译成本地路径（替换前缀）再读写
- 编辑文件前，必须先在本地 Worktree 重新 read_file 一次，避免使用过时内容
</worktree-context>"""


async def _execute_with_worktree(
    manager: "Manager",
    definition,
    sub_agent,
    sub_conv,
    prompt: str,
    events: asyncio.Queue,
) -> str:
    """在临时 Worktree 中执行子 Agent 任务。

    对齐 mewcode AgentTool._execute_with_worktree:
    1. 生成临时名称 agent-a<7hex>
    2. 创建 Worktree (base_ref="HEAD")
    3. 构造 worktree_notice 拼到 task 前
    4. 注入 ctx cwd (csycode 特有: via ContextVar)
    5. run_to_completion
    6. auto_cleanup（传入 head_commit）；有变更则追加保留信息
    """
    name = _random_name()
    parent_cwd = str(Path.cwd())

    # 创建临时 Worktree
    wt = await manager.create(name, "HEAD", manual=False)

    # 构造通知文本
    notice = build_worktree_notice(parent_cwd, wt.path)
    task_text = notice + "\n\n" + prompt

    # 注入 ctx cwd 并执行子 Agent
    final_text = ""
    try:
        with with_cwd(wt.path):
            final_text = await sub_agent.run_to_completion(
                sub_conv, task_text, events
            )
    finally:
        # auto_cleanup（无论成功/失败都执行）
        report = await manager.auto_cleanup(name)
        if report.kept:
            final_text = (final_text or "") + (
                f"\n[Worktree 保留: {report.path}，分支 {report.branch}]"
            )

    return final_text or "(子 Agent 未返回输出)"
