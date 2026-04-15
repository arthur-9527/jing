#!/usr/bin/env python3
"""
3个天气查询任务并发执行示例

展示完整的流程：
1. 提交3个任务
2. 3个session并发执行
3. 独立协程等待响应
4. 收集所有结果
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.openclaw import get_openclaw_manager


async def main():
    """3个天气查询并发示例"""
    print("=" * 60)
    print("3个城市天气查询并发测试")
    print("=" * 60)

    manager = get_openclaw_manager()
    await manager.start()

    try:
        # 步骤1：提交3个任务
        print("\n[步骤1] 提交3个任务")
        task_ids = []
        cities = ["上海", "北京", "南京"]

        for city in cities:
            task_id = await manager.submit_task(
                f"请查询{city}明天的天气，包括温度、天气状况、风力"
            )
            task_ids.append(task_id)
            print(f"  ✓ 任务已提交: {city} → {task_id[:8]}...")

        # 步骤2：查看任务状态
        print("\n[步骤2] 任务分配情况")
        await asyncio.sleep(1.0)  # 等待调度器分配

        stats = await manager.get_stats()
        for session_key, session_state in stats["sessions"].items():
            status = "BUSY" if session_state["current_task_id"] else "IDLE"
            print(f"  {session_key}: {status}")
            if session_state["current_task_id"]:
                print(f"    → 任务: {session_state['current_task_id'][:8]}...")

        print(f"  Pending队列: {stats['tasks']['pending_queue_length']}")

        # 步骤3：并发等待所有结果
        print("\n[步骤3] 等待所有任务完成...")

        results = []
        for i, (task_id, city) in enumerate(zip(task_ids, cities), 1):
            try:
                print(f"  [{i}/3] 等待{city}天气查询...", end="", flush=True)
                result = await manager.wait_for_result(task_id, timeout=60.0)
                results.append((city, result))
                print(f" ✓")

                # 显示结果
                if result.get("ok"):
                    content = result.get("result", {}).get("content", "")
                    print(f"      {content[:100]}...")
                else:
                    print(f"      失败: {result.get('error')}")

            except TimeoutError:
                print(f" ✗ 超时")
                results.append((city, {"ok": False, "error": "超时"}))

        # 步骤4：总结
        print("\n[步骤4] 执行总结")
        success_count = sum(1 for _, r in results if r.get("ok"))
        print(f"  成功: {success_count}/3")
        print(f"  失败: {3-success_count}/3")

        # 详细结果
        print("\n详细结果:")
        for city, result in results:
            status = "✓" if result.get("ok") else "✗"
            print(f"  {status} {city}: {result.get('result', {}).get('content', 'N/A')[:80]}...")

    finally:
        await manager.stop()
        print("\n服务已停止")


if __name__ == "__main__":
    asyncio.run(main())
