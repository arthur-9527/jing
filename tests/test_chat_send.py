#!/usr/bin/env python3
"""
测试OpenClaw chat.send和接收响应
"""

import asyncio
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.openclaw.ws_client import OpenClawWSClient
from loguru import logger

async def test_chat():
    """测试chat.send"""
    client = OpenClawWSClient()

    try:
        print("正在连接...")
        await client.connect()
        print("✓ 连接成功")

        session_key = "agent:main:chat1"
        message = "你好"

        print(f"发送消息到 session={session_key}: {message}")
        run_id = await client.send_chat_message(session_key, message)
        print(f"✓ 消息已发送, runId={run_id}")

        # 等待响应
        print("等待响应（最多30秒）...")
        try:
            result = await client.wait_for_run_id(run_id, timeout=30.0)
            print(f"✓ 收到响应:")
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except asyncio.TimeoutError:
            print("✗ 30秒内没有收到响应")
        except Exception as e:
            print(f"✗ 等待响应失败: {e}")

    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.disconnect()
        print("已断开连接")

if __name__ == "__main__":
    # 配置日志
    logger.remove()
    logger.add(sys.stderr, level="DEBUG", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")

    asyncio.run(test_chat())