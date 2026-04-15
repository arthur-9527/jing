#!/usr/bin/env python3
"""
测试状态同步功能

测试场景：
1. 项目重启：清空所有任务
2. OpenClaw重启：标记所有RUNNING任务为失败
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.openclaw import get_openclaw_manager
from loguru import logger


async def test_restart_scenarios():
    """测试重启场景"""
    print("=" * 60)
    print("OpenClaw 状态同步测试")
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
        print("\n=== 场景1：项目重启检测 ===")

        print("\n第一次启动服务...")
        await manager.start()
        print("✓ 服务已启动")

        # 提交一些任务
        print("\n提交3个任务...")
        task1 = await manager.submit_task("任务1")
        task2 = await manager.submit_task("任务2")
        task3 = await manager.submit_task("任务3")
        print(f"✓ 已提交3个任务")

        # 等待任务分配
        await asyncio.sleep(2.0)

        # 查看状态
        stats = await manager.get_stats()
        print(f"\n当前状态:")
        print(f"  - 总任务数: {stats['tasks']['total_tasks']}")
        print(f"  - Pending: {stats['tasks']['by_status'].get('pending', 0)}")
        print(f"  - Running: {stats['tasks']['by_status'].get('running', 0)}")

        print("\n停止服务...")
        await manager.stop()
        print("✓ 服务已停止")

        print("\n第二次启动服务（模拟重启）...")
        await manager.start()
        print("✓ 服务已启动")

        # 查看状态（应该被清空）
        stats = await manager.get_stats()
        print(f"\n重启后状态:")
        print(f"  - 总任务数: {stats['tasks']['total_tasks']}")
        print(f"  - Pending: {stats['tasks']['by_status'].get('pending', 0)}")
        print(f"  - Running: {stats['tasks']['by_status'].get('running', 0)}")

        if stats['tasks']['total_tasks'] == 0:
            print("✅ 项目重启清空成功！")
        else:
            print("❌ 项目重启清空失败！")

        print("\n停止服务...")
        await manager.stop()

        print("\n=== 场景2：OpenClaw重启模拟 ===")
        print("（注：实际需要手动重启OpenClaw来测试）")
        print("预期行为：所有RUNNING任务标记为FAILED")
        print("错误信息：'OpenClaw服务中断或重启，任务已终止'")

        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)

    finally:
        try:
            print("\n清理服务...")
            await manager.stop()
            print("✓ 服务已停止")
        except:
            pass


if __name__ == "__main__":
    asyncio.run(test_restart_scenarios())
