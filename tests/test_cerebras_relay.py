"""测试 Cerebras SDK 通过 LiteLLM 中转"""

import asyncio
import os
import sys

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.llm.providers.cerebras import CerebrasProvider


async def test_cerebras_relay():
    """测试通过中转服务器调用"""

    # 从 .env 读取配置
    api_key = os.getenv("CEREBRAS_API_KEY", "sk-dummy")
    model = os.getenv("CEREBRAS_MODEL", "qwen3-chat")
    base_url = os.getenv("CEREBRAS_API_BASE_URL", "http://43.153.150.28:4000")

    print(f"测试配置:")
    print(f"  API Key: {api_key}")
    print(f"  Model: {model}")
    print(f"  Base URL: {base_url}")
    print()

    provider = CerebrasProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
    )

    # 测试非流式对话
    print("=" * 50)
    print("测试非流式对话...")
    print("=" * 50)

    messages = [
        {"role": "system", "content": "你是一个简洁的助手，请用一句话回复。"},
        {"role": "user", "content": "你好，请介绍一下你自己。"},
    ]

    try:
        response = await provider.chat(messages, temperature=0.7)
        print(f"响应: {response}")
        print("✅ 非流式对话测试成功!")
    except Exception as e:
        print(f"❌ 非流式对话测试失败: {e}")
        return False

    print()

    # 测试流式对话
    print("=" * 50)
    print("测试流式对话...")
    print("=" * 50)

    messages = [
        {"role": "system", "content": "你是一个简洁的助手。"},
        {"role": "user", "content": "请说一句话。"},
    ]

    try:
        full_response = ""
        async for chunk in provider.chat_stream(messages, temperature=0.7):
            full_response += chunk
            print(chunk, end="", flush=True)
        print()
        print(f"完整响应: {full_response}")
        print("✅ 流式对话测试成功!")
    except Exception as e:
        print(f"❌ 流式对话测试失败: {e}")
        return False

    print()
    print("=" * 50)
    print("所有测试通过! ✅")
    print("=" * 50)
    return True


if __name__ == "__main__":
    # 加载 .env
    from dotenv import load_dotenv
    load_dotenv()

    success = asyncio.run(test_cerebras_relay())
    sys.exit(0 if success else 1)