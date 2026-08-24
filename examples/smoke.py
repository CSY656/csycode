"""端到端冒烟测试：验证 Agent Loop + 系统提示缓存。

用法:
    python examples/smoke.py

前提: 需要有效的 .csycode/config.yaml 配置。
"""

from __future__ import annotations

import asyncio
import os
import sys

# 将项目根目录加到 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from csycode.agent import Agent, AgentEvent, LoopEnd, TextDelta, TokenUsage
from csycode.config import Config, load
from csycode.conversation import Conversation
from csycode.llm import new_provider
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

    print(f"🧪 冒烟测试: provider={provider.name}, model={provider.model}")
    print()

    # 第一条消息：触发缓存写入
    conv.add_user("1+1 等于几？用中文回答，一句话。")
    print(f"👤 用户: {conv.messages()[-1].content}")

    agent1 = Agent(provider, registry, conv, config.agent, version="dev")
    print(f"🤖 Agent (第 1 轮, 缓存写入):")
    async for event in agent1.run():
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, TokenUsage):
            print()
            print(f"  📊 tokens: ↗{event.input_tokens} ↑{event.output_tokens}  "
                  f"💾写:{event.cache_write} 读:{event.cache_read}")
        elif isinstance(event, LoopEnd):
            print(f"  🏁 结束: {event.reason}")
    print()

    # 第二条消息：观察缓存命中
    conv.add_user("再问一次：1+1 等于几？")
    print(f"👤 用户: {conv.messages()[-1].content}")

    agent2 = Agent(provider, registry, conv, config.agent, version="dev")
    print(f"🤖 Agent (第 2 轮, 期望缓存命中):")
    async for event in agent2.run():
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, TokenUsage):
            print()
            print(f"  📊 tokens: ↗{event.input_tokens} ↑{event.output_tokens}  "
                  f"💾写:{event.cache_write} 读:{event.cache_read}")
            if event.cache_read > 0:
                print("  ✅ 缓存命中!")
            elif event.cache_write > 0:
                print("  ℹ️  缓存写入（首轮或 TTL 过期）")
        elif isinstance(event, LoopEnd):
            print(f"  🏁 结束: {event.reason}")
    print()
    print("✅ 冒烟测试完成")


if __name__ == "__main__":
    asyncio.run(main())
