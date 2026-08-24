"""冒烟测试：验证 Agent Loop + 权限系统在 Mode.BYPASS 下正常运行。

用法:
    python -m smoke

前提: 需要有效的 .csycode/config.yaml 配置。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 将项目 src 目录加到 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from csycode.agent import Agent, LoopEnd, TextDelta, TokenUsage
from csycode.config import Config, load
from csycode.conversation import Conversation
from csycode.llm import new_provider
from csycode.permission import Mode, new_engine
from csycode.tools import create_default_registry


async def main() -> None:
    """运行冒烟测试。"""
    # 加载配置
    config_path = os.path.join(os.path.dirname(__file__), "..", ".csycode", "config.yaml")
    if not os.path.exists(config_path):
        print("❌ 未找到 .csycode/config.yaml，跳过冒烟测试")
        return

    config: Config = load(config_path)
    if not config.providers:
        print("❌ 配置中没有 provider，跳过冒烟测试")
        return

    provider = new_provider(config.providers[0])
    registry = create_default_registry(config.tools)
    conv = Conversation()

    # ── ch06: 构造权限引擎（bypass 模式跳过人在回路） ──────────
    cwd = str(Path.cwd().resolve())
    engine, err = new_engine(cwd)
    if err is not None:
        print(f"⚠️  权限引擎降级: {err}")

    print(f"🧪 冒烟测试: provider={provider.name}, model={provider.model}")
    print(f"   权限模式: bypassPermissions (跳过人在回路)")
    print()

    # 第一条消息
    conv.add_user("用中文回答：1+1 等于几？一句话。")
    print(f"👤 用户: {conv.messages()[-1].content}")

    agent = Agent(
        provider, registry, conv, config.agent,
        version="dev",
        engine=engine,
    )
    print(f"🤖 Agent:")
    async for event in agent.run(Mode.BYPASS):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, TokenUsage):
            print()
            print(f"  📊 tokens: ↗{event.input_tokens} ↑{event.output_tokens}")
        elif isinstance(event, LoopEnd):
            print(f"  🏁 结束: {event.reason}")
    print()

    # 第二条消息：测试工具调用（写文件在 bypass 下应被放行，但仅在项目根内）
    conv.add_user("在当前目录下创建一个文件 test_smoke.txt，内容是 hello smoke test")
    print(f"👤 用户: {conv.messages()[-1].content}")

    agent2 = Agent(
        provider, registry, conv, config.agent,
        version="dev",
        engine=engine,
    )
    print(f"🤖 Agent:")
    async for event in agent2.run(Mode.BYPASS):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, TokenUsage):
            print()
            print(f"  📊 tokens: ↗{event.input_tokens} ↑{event.output_tokens}")
        elif isinstance(event, LoopEnd):
            print(f"  🏁 结束: {event.reason}")
    print()

    # 清理
    smoke_file = Path("test_smoke.txt")
    if smoke_file.exists():
        smoke_file.unlink()
        print("🧹 已清理 test_smoke.txt")

    print("✅ 冒烟测试完成")


if __name__ == "__main__":
    asyncio.run(main())
