#!/usr/bin/env python3
"""
测试2个session并发发送"你好"
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.openclaw import get_openclaw_manager
from loguru import logger


async def main():
    """测试2个并发任务"""
    print("=" * 60)
    print("OpenClaw 2个Session并发测试")
    print("=" * 60)

    manager = get_openclaw_manager()

    # 配置日志：只显示关键信息
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")

    try:
        print("\n启动服务...")
        await manager.start()
        print("✓ 服务已启动")

        # 显示session配置
        stats = await manager.get_stats()
        print(f"\nSession配置: {len(stats['sessions'])}个session")
        for key, state in stats["sessions"].items():
            print(f"  - {key}")

        # 同时提交2个任务
        print("\n提交2个任务...")
        task1 = await manager.submit_task("你好")
        task2 = await manager.submit_task("你好")

        print(f"  ✓ 任务1: {task1[:8]}...")
        print(f"  ✓ 任务2: {task2[:8]}...")

        # 等待2-3秒后查看状态
        await asyncio.sleep(3.0)
        stats = await manager.get_stats()
        print("\n任务分配状态:")
        for key, state in stats["sessions"].items():
            status = "BUSY" if state["current_task_id"] else "IDLE"
            print(f"  {key}: {status}")
            if state["current_task_id"]:
                print(f"    → 任务: {state['current_task_id'][:8]}...")
                print(f"    → runId: {state['run_id'][:8]}...")

        # 等待结果（设置较短的超时）
        print("\n等待结果（最多30秒）...")

        # 等待任务1
        print(f"  [1/2] 等待任务1...", end="", flush=True)
        try:
            result1 = await manager.wait_for_result(task1, timeout=30.0)
            if result1.get("ok"):
                content = result1.get("result", {}).get("content", "")
                print(f" ✓")
                print(f"      回复: {content[:100]}")
            else:
                print(f" ✗ {result1.get('error', 'Unknown error')}")
        except TimeoutError:
            print(f" ✗ 超时")

        # 等待任务2
        print(f"  [2/2] 等待任务2...", end="", flush=True)
        try:
            result2 = await manager.wait_for_result(task2, timeout=30.0)
            if result2.get("ok"):
                content = result2.get("result", {}).get("content", "")
                print(f" ✓")
                print(f"      回复: {content[:100]}")
            else:
                print(f" ✗ {result2.get('error', 'Unknown error')}")
        except TimeoutError:
            print(f" ✗ 超时")

        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)

    finally:
        print("\n停止服务...")
        await manager.stop()
        print("✓ 服务已停止")


if __name__ == "__main__":
    asyncio.run(main())