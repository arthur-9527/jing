#!/usr/bin/env python3
"""
测试询问机制和打断机制
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.openclaw import get_openclaw_manager
from loguru import logger


async def test_heartbeat_and_cancel():
    """测试询问机制和打断机制"""
    print("=" * 60)
    print("OpenClaw 询问机制和打断机制测试")
    print("=" * 60)

    manager = get_openclaw_manager()

    # 配置日志
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"
    )

    try:
        print("\n启动服务...")
        await manager.start()
        print("✓ 服务已启动")

        # 显示配置
        config = manager._config.timeout
        print(f"\n询问机制配置:")
        print(f"  - 启用询问: {config.enable_heartbeat}")
        print(f"  - 询问阈值: {config.heartbeat_threshold}秒 ({config.heartbeat_threshold/60:.1f}分钟)")
        print(f"  - 询问间隔: {config.heartbeat_interval}秒 ({config.heartbeat_interval/60:.1f}分钟)")
        print(f"  - 询问消息: {config.heartbeat_query_message}")

        # 提交一个任务
        print("\n提交任务...")
        task_id = await manager.submit_task("请讲一个很长的故事")
        print(f"✓ 任务已提交: {task_id[:8]}...")

        # 等待一会儿
        print("\n等待5秒...")
        await asyncio.sleep(5.0)

        # 查看任务状态
        status = await manager.get_task_status(task_id)
        print(f"\n任务状态: {status['status']}")
        if status.get('session_key'):
            print(f"  - Session: {status['session_key']}")
        if status.get('run_id'):
            print(f"  - RunId: {status['run_id'][:8]}...")

        # 测试打断机制
        print("\n测试打断机制...")
        print(f"  取消任务: {task_id[:8]}...")
        cancelled = await manager.cancel_task(task_id)
        if cancelled:
            print(f"  ✓ 任务已取消")

        # 等待一会儿让状态更新
        await asyncio.sleep(2.0)

        # 查看最终状态
        final_status = await manager.get_task_status(task_id)
        print(f"\n最终状态: {final_status['status']}")

        # 显示统计信息
        stats = await manager.get_stats()
        print(f"\n统计信息:")
        print(f"  - 总任务数: {stats['tasks']['total_tasks']}")
        print(f"  - 已取消: {stats['tasks']['by_status'].get('cancelled', 0)}")

        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)

    finally:
        print("\n停止服务...")
        await manager.stop()
        print("✓ 服务已停止")


if __name__ == "__main__":
    asyncio.run(test_heartbeat_and_cancel())
