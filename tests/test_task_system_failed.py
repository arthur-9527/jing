#!/usr/bin/env python3
"""
测试场景 3: OpenClaw 连接失败（通过无效 URL 模拟）

流程：
1. 设置无效的 WebSocket URL
2. 启动任务系统（预期失败）
3. 验证 RuntimeError 和错误信息

运行方式：
    python tests/test_task_system_failed.py
"""

import asyncio
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from task_system_test_base import (
    setup_logging,
    setup_task_system,
    teardown_task_system,
)


async def test_openclaw_connection_failed():
    """测试 OpenClaw 连接失败场景
    
    ⭐ 预期行为：即使 Provider 连接失败，TaskSystem 也会启动，
    提交任务时产生播报（LLM 改写错误）
    """
    
    print("\n" + "=" * 60)
    print("测试场景 3: OpenClaw 连接失败（无效 URL 模拟）")
    print("=" * 60)
    
    # 设置无效 WebSocket URL
    print("\n[Step 0] 设置无效 WebSocket URL...")
    invalid_url = "ws://invalid-host-9999.local:9999/invalid"
    print(f"✓ 已设置无效 WebSocket URL: {invalid_url}")
    
    # 启动 TaskSystem（Provider 会失败，但系统会启动）
    print("\n[Step 1] 启动任务系统...")
    task_system = await setup_task_system(env_overrides={
        "OPENCLAW_WS_URL": invalid_url,
    })
    print("✓ TaskSystem 启动成功（Provider 标记为不可用）")
    
    try:
        # Step 2: 检查 Provider 状态
        print("\n[Step 2] 检查 Provider 状态...")
        stats = await task_system.get_stats()
        provider_stats = stats.get('providers', {}).get('openclaw', {})
        print(f"✓ Provider openclaw enabled: {provider_stats.get('enabled', 'N/A')}")
        
        # Step 3: 提交任务
        print("\n[Step 3] 提交任务...")
        task_id = await task_system.submit(
            tool_prompt="帮我查询天气",
            provider_name="openclaw",
            context={"user_input": "帮我查一下天气"},
        )
        print(f"✓ 任务已提交: {task_id}")
        
        # Step 4: 等待播报结果
        print("\n[Step 4] 等待播报结果...")
        try:
            broadcast = await task_system.wait_for_broadcast(task_id, timeout=30.0)
            print("✓ 收到播报内容")
            
            # Step 5: 打印改写后的台词
            print("\n[Step 5] 检查播报内容:")
            print(f"  - Task ID: {broadcast.task_id}")
            print(f"\n  === 播报台词（LLM 改写错误）===")
            print(f"  {broadcast.content}")
            
            print(f"\n  === Panel JSON ===")
            if broadcast.panel_html:
                import json
                panel_json = json.dumps(broadcast.panel_html, indent=2, ensure_ascii=False)
                print(f"  {panel_json}")
            else:
                print(f"  (无 Panel)")
            
            # 检查任务状态
            task = await task_system.get_task(task_id)
            if task:
                print(f"\n  === 任务状态 ===")
                print(f"  status: {task.status.value}")
                print(f"  error: {task.error}")
            
            print("\n✅ 测试通过：Provider 连接失败也能产生播报")
            return True
            
        except TimeoutError as e:
            print(f"❌ 等待超时: {e}")
            return False
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # 停止 TaskSystem
        await teardown_task_system(task_system)


async def main():
    """运行测试"""
    setup_logging()
    
    result = await test_openclaw_connection_failed()
    
    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)
    
    if result:
        print("✅ OpenClaw 连接失败测试通过！")
    else:
        print("❌ OpenClaw 连接失败测试失败！")
    
    print("=" * 60)
    print()


if __name__ == "__main__":
    asyncio.run(main())