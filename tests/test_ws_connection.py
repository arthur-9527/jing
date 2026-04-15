#!/usr/bin/env python3
"""
简单的WebSocket连接测试
"""

import asyncio
import websockets
import json

async def test_websocket_connection():
    """测试WebSocket连接"""
    ws_url = "ws://127.0.0.1:18789/gateway"
    print(f"尝试连接到: {ws_url}")

    try:
        async with websockets.connect(ws_url) as ws:
            print("✓ WebSocket连接成功！")

            # 等待接收消息
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                print(f"✓ 收到消息: {message[:200]}")

                # 尝试解析
                data = json.loads(message)
                print(f"✓ 消息类型: {data.get('type')}")
                print(f"✓ 消息事件: {data.get('event')}")

            except asyncio.TimeoutError:
                print("⚠ 5秒内没有收到消息")

    except Exception as e:
        print(f"✗ 连接失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_websocket_connection())