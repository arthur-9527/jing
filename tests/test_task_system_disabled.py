#!/usr/bin/env python3
"""
测试场景 2: OpenClaw 未启用（通过环境变量模拟）

流程：
1. 设置 OPENCLAW_ENABLED=false
2. 启动任务系统（跳过 OpenClaw Provider）
3. 尝试提交任务，验证错误

运行方式：
    python tests/test_task_system_disabled.py
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


async def test_openclaw_disabled():
    """测试 OpenClaw 未启用场景
    
    ⭐ 预期行为：即使 Provider 未启用，也应该产生播报（LLM 改写错误）
    """
    
    print("\n" + "=" * 60)
    print("测试场景 2: OpenClaw 未启用（环境变量模拟）")
    print("=" * 60)
    
    # 创建并启动 TaskSystem（禁用 OpenClaw）
    task_system = await setup_task_system(env_overrides={
        "OPENCLAW_ENABLED": "false",
    })
    
    try:
        # Step 1: 检查配置
        print("\n[Step 1] 检查配置...")
        stats = await task_system.get_stats()
        print(f"✓ 配置: openclaw_enabled={stats['settings']['openclaw_enabled']}")
        print(f"✓ Provider 数量: {len(stats.get('providers', {}))}")
        
        # Step 2: 提交任务（不再抛异常，而是产生错误播报）
        print("\n[Step 2] 提交任务...")
        task_id = await task_system.submit(
            tool_prompt="帮我查询天气",
            provider_name="openclaw",
            context={"user_input": "帮我查一下天气"},
        )
        print(f"✓ 任务已提交: {task_id}")
        
        # Step 3: 等待播报结果
        print("\n[Step 3] 等待播报结果...")
        try:
            broadcast = await task_system.wait_for_broadcast(task_id, timeout=30.0)
            print("✓ 收到播报内容")
            
            # Step 4: 打印改写后的台词
            print("\n[Step 4] 检查播报内容:")
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
            
            print("\n✅ 测试通过：Provider 未启用也能产生播报")
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
    
    result = await test_openclaw_disabled()
    
    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)
    
    if result:
        print("✅ OpenClaw 未启用测试通过！")
    else:
        print("❌ OpenClaw 未启用测试失败！")
    
    print("=" * 60)
    print()


if __name__ == "__main__":
    asyncio.run(main())