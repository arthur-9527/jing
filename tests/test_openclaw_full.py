#!/usr/bin/env python3
"""
OpenClaw WebSocket服务完整功能测试

测试场景：
1. 单个任务提交和等待
2. 并发任务（超过3个）
3. 任务状态查询
4. 服务重启恢复
"""

import asyncio
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.openclaw import get_openclaw_manager
from loguru import logger


async def test_single_task():
    """测试单个任务"""
    print("=" * 60)
    print("测试1: 单个任务")
    print("=" * 60)

    manager = get_openclaw_manager()
    await manager.start()

    try:
        # 提交任务
        task_id = await manager.submit_task("你好，请做个自我介绍")
        print(f"✓ 任务已提交: {task_id}")

        # 等待结果
        result = await manager.wait_for_result(task_id, timeout=60.0)
        print(f"✓ 任务完成:")
        print(f"  - OK: {result.get('ok')}")
        print(f"  - Result: {result.get('result', {}).get('content', 'N/A')[:100]}")

        # 查询状态
        status = await manager.get_task_status(task_id)
        print(f"✓ 任务状态: {status.get('status')}")

    finally:
        await manager.stop()
        print("✓ 服务已停止")
        print()


async def test_concurrent_tasks():
    """测试并发任务"""
    print("=" * 60)
    print("测试2: 并发任务（5个任务，只有3个session）")
    print("=" * 60)

    manager = get_openclaw_manager()
    await manager.start()

    try:
        # 提交5个任务
        task_ids = []
        for i in range(5):
            task_id = await manager.submit_task(f"任务{i}: 请计算 {i} + {i} 等于多少")
            task_ids.append(task_id)
            print(f"✓ 任务{i}已提交: {task_id}")

        # 查看统计信息
        stats = await manager.get_stats()
        print(f"✓ 队列统计:")
        print(f"  - Pending队列: {stats['tasks']['pending_queue_length']}")
        print(f"  - 各状态: {stats['tasks']['by_status']}")

        # 等待所有任务完成
        results = []
        for i, task_id in enumerate(task_ids):
            try:
                result = await manager.wait_for_result(task_id, timeout=60.0)
                results.append(result)
                print(f"✓ 任务{i}完成: {result.get('ok')}")
            except Exception as e:
                print(f"✗ 任务{i}失败: {e}")

        print(f"✓ 共完成 {len(results)} 个任务")

    finally:
        await manager.stop()
        print("✓ 服务已停止")
        print()


async def test_status_query():
    """测试状态查询"""
    print("=" * 60)
    print("测试3: 任务状态查询")
    print("=" * 60)

    manager = get_openclaw_manager()
    await manager.start()

    try:
        # 提交任务
        task_id = await manager.submit_task("测试任务：今天天气怎么样？")
        print(f"✓ 任务已提交: {task_id}")

        # 多次查询状态
        for i in range(3):
            await asyncio.sleep(1.0)
            status = await manager.get_task_status(task_id)
            print(f"✓ 查询{i+1}: 状态={status.get('status')}, Session={status.get('session_key')}")

        # 等待完成
        result = await manager.wait_for_result(task_id, timeout=60.0)
        print(f"✓ 任务完成: {result.get('ok')}")

    finally:
        await manager.stop()
        print("✓ 服务已停止")
        print()


async def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("OpenClaw WebSocket服务完整功能测试")
    print("=" * 60)
    print()

    # 检查身份文件
    from pathlib import Path
    identity_file = Path.home() / ".openclaw" / "pipecat_identity.json"
    if not identity_file.exists():
        print(f"❌ 身份文件不存在: {identity_file}")
        print("请先创建设备身份文件")
        return

    print(f"✓ 身份文件存在: {identity_file}")

    # 强制重新加载配置
    from app.services.openclaw import config
    config._config = None

    # 显示实际使用的配置
    actual_config = config.get_openclaw_config()
    print(f"✓ WebSocket URL: {actual_config.ws.ws_url}")
    print(f"✓ Token: {actual_config.ws.ws_token[:20]}...")

    try:
        # 运行测试
        await test_single_task()
        await test_concurrent_tasks()
        await test_status_query()

        print("=" * 60)
        print("✅ 所有测试完成！")
        print("=" * 60)
        print()

    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # 配置日志
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")

    asyncio.run(main())