#!/usr/bin/env python3
"""
测试OpenClaw WebSocket认证流程
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.openclaw.ws_client import OpenClawWSClient

async def test_connection():
    """测试连接和认证"""
    print("测试OpenClaw WebSocket连接...")

    client = OpenClawWSClient()

    try:
        print("正在连接...")
        await client.connect()
        print(f"✓ 连接成功: authenticated={client.is_authenticated}, connected={client.is_connected}")

        # 等待几秒钟看看是否会收到消息
        print("等待5秒...")
        await asyncio.sleep(5.0)

    except Exception as e:
        print(f"✗ 连接失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.disconnect()
        print("已断开连接")

if __name__ == "__main__":
    asyncio.run(test_connection())