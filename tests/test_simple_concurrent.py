#!/usr/bin/env python3
"""
简化的并发测试
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.openclaw import get_openclaw_manager
from loguru import logger


async def main():
    """简化的并发测试"""
    print("=" * 60)
    print("OpenClaw 并发任务测试")
    print("=" * 60)

    manager = get_openclaw_manager()

    # 配置日志
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")

    try:
        print("\n启动服务...")
        await manager.start()
        print("✓ 服务已启动")

        # 提交3个任务
        print("\n提交3个任务...")
        task1 = await manager.submit_task("1+1等于几")
        task2 = await manager.submit_task("2+2等于几")
        task3 = await manager.submit_task("3+3等于几")

        print(f"  ✓ 任务1: {task1[:8]}...")
        print(f"  ✓ 任务2: {task2[:8]}...")
        print(f"  ✓ 任务3: {task3[:8]}...")

        # 等待第一个结果
        print(f"\n等待任务1完成...")
        result1 = await manager.wait_for_result(task1, timeout=30.0)
        print(f"✓ 任务1: {result1.get('result', {}).get('content', 'N/A')}")

        # 等待第二个结果
        print(f"\n等待任务2完成...")
        result2 = await manager.wait_for_result(task2, timeout=30.0)
        print(f"✓ 任务2: {result2.get('result', {}).get('content', 'N/A')}")

        # 等待第三个结果
        print(f"\n等待任务3完成...")
        result3 = await manager.wait_for_result(task3, timeout=30.0)
        print(f"✓ 任务3: {result3.get('result', {}).get('content', 'N/A')}")

        print("\n✅ 所有任务完成！")

    finally:
        print("\n停止服务...")
        await manager.stop()
        print("✓ 服务已停止")


if __name__ == "__main__":
    asyncio.run(main())