#!/usr/bin/env python3
"""
OpenClaw Redis仓库测试脚本

测试场景：
1. 连接Redis
2. 创建任务
3. 查询任务
4. 更新任务状态
5. 队列操作
6. 统计信息
"""

import asyncio
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.openclaw.redis_repo import OpenClawTaskRepository
from app.services.openclaw.models import TaskStatus


async def test_basic_operations():
    """测试基本CRUD操作"""
    print("=" * 60)
    print("测试1: 基本CRUD操作")
    print("=" * 60)

    repo = OpenClawTaskRepository()
    await repo.connect()
    print("✓ Redis连接成功")

    # 清理旧数据
    await repo.clear_all_tasks()
    await repo.clear_pending_queue()
    print("✓ 清理旧数据完成")

    # 创建任务
    task_id = await repo.create_task("帮我查询今天的天气")
    print(f"✓ 任务已创建: {task_id}")

    # 查询任务
    task = await repo.get_task(task_id)
    assert task is not None, "任务查询失败"
    assert task.id == task_id, "任务ID不匹配"
    assert task.status == TaskStatus.PENDING, f"任务状态错误: {task.status}"
    print(f"✓ 任务查询成功: ID={task.id}, Status={task.status.value}")

    # 更新状态
    await repo.update_status(
        task_id,
        TaskStatus.ASSIGNED,
        session_key="agent:main:chat1",
    )
    task = await repo.get_task(task_id)
    assert task.status == TaskStatus.ASSIGNED, "状态更新失败"
    assert task.session_key == "agent:main:chat1", "session_key未更新"
    print(f"✓ 状态更新成功: {task.status.value}, session={task.session_key}")

    # 更新结果
    test_result = {"content": "今天天气晴朗", "panel_html": None}
    await repo.update_result(task_id, test_result)
    task = await repo.get_task(task_id)
    assert task.status == TaskStatus.COMPLETED, "任务未标记完成"
    assert task.result == test_result, "结果不匹配"
    print(f"✓ 结果更新成功: {task.result}")

    # 删除任务
    deleted = await repo.delete_task(task_id)
    assert deleted, "任务删除失败"
    task = await repo.get_task(task_id)
    assert task is None, "任务仍然存在"
    print("✓ 任务删除成功")

    await repo.disconnect()
    print("✓ Redis断开连接")
    print()


async def test_queue_operations():
    """测试队列操作"""
    print("=" * 60)
    print("测试2: 队列操作")
    print("=" * 60)

    repo = OpenClawTaskRepository()
    await repo.connect()

    # 清理
    await repo.clear_all_tasks()
    await repo.clear_pending_queue()

    # 创建多个任务
    task_ids = []
    for i in range(5):
        task_id = await repo.create_task(f"任务{i}: 请帮我计算{i}+{i}")
        task_ids.append(task_id)
        print(f"✓ 任务{i}已创建: {task_id}")

    # 检查队列长度
    queue_len = await repo.get_pending_queue_length()
    assert queue_len == 5, f"队列长度错误: {queue_len}"
    print(f"✓ Pending队列长度: {queue_len}")

    # 从队列取出任务（FIFO）
    task = await repo.pop_pending_task()
    assert task is not None, "取出任务失败"
    assert task.id == task_ids[0], f"任务顺序错误，期望{task_ids[0]}, 实际{task.id}"
    print(f"✓ 从队列取出任务: {task.id} (第1个)")

    # 再取出一个
    task = await repo.pop_pending_task()
    assert task.id == task_ids[1], "任务顺序错误"
    print(f"✓ 从队列取出任务: {task.id} (第2个)")

    # 检查队列长度
    queue_len = await repo.get_pending_queue_length()
    assert queue_len == 3, f"队列长度错误: {queue_len}"
    print(f"✓ Pending队列长度: {queue_len}")

    # 推回队列
    await repo.push_pending_task(task.id)
    queue_len = await repo.get_pending_queue_length()
    assert queue_len == 4, f"队列长度错误: {queue_len}"
    print(f"✓ 任务推回队列: {task.id}")
    print(f"✓ Pending队列长度: {queue_len}")

    await repo.disconnect()
    print()


async def test_status_filter():
    """测试状态过滤"""
    print("=" * 60)
    print("测试3: 状态过滤和统计")
    print("=" * 60)

    repo = OpenClawTaskRepository()
    await repo.connect()

    # 清理
    await repo.clear_all_tasks()
    await repo.clear_pending_queue()

    # 创建任务并设置不同状态
    task_ids = []
    for i in range(3):
        task_id = await repo.create_task(f"任务{i}")
        task_ids.append(task_id)

    # 设置不同状态
    await repo.update_status(task_ids[0], TaskStatus.RUNNING, session_key="agent:main:chat1")
    await repo.update_status(task_ids[1], TaskStatus.COMPLETED, result={"ok": True})
    # task_ids[2] 保持 PENDING

    print(f"✓ 创建3个任务: 1个RUNNING, 1个COMPLETED, 1个PENDING")

    # 查询各状态任务
    pending_tasks = await repo.get_all_tasks(status=TaskStatus.PENDING)
    running_tasks = await repo.get_all_tasks(status=TaskStatus.RUNNING)
    completed_tasks = await repo.get_all_tasks(status=TaskStatus.COMPLETED)

    assert len(pending_tasks) == 1, f"PENDING任务数量错误: {len(pending_tasks)}"
    assert len(running_tasks) == 1, f"RUNNING任务数量错误: {len(running_tasks)}"
    assert len(completed_tasks) == 1, f"COMPLETED任务数量错误: {len(completed_tasks)}"

    print(f"✓ PENDING任务: {len(pending_tasks)}个")
    print(f"✓ RUNNING任务: {len(running_tasks)}个")
    print(f"✓ COMPLETED任务: {len(completed_tasks)}个")

    # 获取统计信息
    stats = await repo.get_stats()
    print(f"✓ 统计信息:")
    print(f"  - 总任务数: {stats['total_tasks']}")
    print(f"  - Pending队列: {stats['pending_queue_length']}")
    print(f"  - 各状态: {stats['by_status']}")

    await repo.disconnect()
    print()


async def test_persistence():
    """测试持久化（断开重连）"""
    print("=" * 60)
    print("测试4: 持久化测试")
    print("=" * 60)

    # 创建任务
    repo1 = OpenClawTaskRepository()
    await repo1.connect()
    await repo1.clear_all_tasks()

    task_id = await repo1.create_task("持久化测试任务")
    print(f"✓ 任务已创建: {task_id}")

    # 断开连接
    await repo1.disconnect()
    print("✓ 断开连接")

    # 重新连接
    repo2 = OpenClawTaskRepository()
    await repo2.connect()
    print("✓ 重新连接")

    # 查询任务
    task = await repo2.get_task(task_id)
    assert task is not None, "任务丢失"
    assert task.id == task_id, "任务ID不匹配"
    assert task.status == TaskStatus.PENDING, "任务状态错误"
    print(f"✓ 任务仍然存在: {task.id}, Status={task.status.value}")

    await repo2.disconnect()
    print("✓ 持久化测试通过")
    print()


async def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("OpenClaw Redis仓库测试")
    print("=" * 60)
    print()

    try:
        # 检查Redis是否运行
        repo = OpenClawTaskRepository()
        await repo.connect()
        await repo.disconnect()
    except Exception as e:
        print(f"❌ Redis连接失败: {e}")
        print("请确保Redis服务正在运行: redis-cli ping")
        return

    try:
        await test_basic_operations()
        await test_queue_operations()
        await test_status_filter()
        await test_persistence()

        print("=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)
        print()

    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())