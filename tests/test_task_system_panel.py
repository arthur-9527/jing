#!/usr/bin/env python3
"""
测试场景 1: Panel 模式 - 天气查询（真实 OpenClaw 调用）

流程：
1. 启动任务系统
2. 提交天气查询 tool_prompt
3. 等待播报结果
4. 检查播报内容（台词 + Panel + Action）
5. 检查播报队列 Redis

运行方式：
    python tests/test_task_system_panel.py
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
    check_identity_file,
)


async def test_panel_weather():
    """测试 Panel 模式：天气查询（真实 OpenClaw）"""
    
    print("\n" + "=" * 60)
    print("测试场景 1: Panel 模式 - 天气查询（真实 OpenClaw）")
    print("=" * 60)
    
    # 检查身份文件
    if not check_identity_file():
        print("❌ 身份文件不存在，跳过真实测试")
        return False
    
    # 创建并启动 TaskSystem
    task_system = await setup_task_system()
    
    try:
        # Step 1: 检查 Provider 状态
        print("\n[Step 1] 检查 Provider 状态...")
        stats = await task_system.get_stats()
        print(f"✓ Provider 状态: {stats.get('providers', {})}")
        
        # Step 2: 提交天气查询
        print("\n[Step 2] 提交天气查询...")
        # ⭐ 不需要添加前缀，Provider 会自动添加 "panel 模式:"
        tool_prompt = "查询明天上海的天气"
        context = {
            "user_input": "帮我查一下明天上海的天气",
        }
        
        task_id = await task_system.submit(
            tool_prompt=tool_prompt,
            provider_name="openclaw",
            context=context,
        )
        print(f"✓ 任务已提交: {task_id[:16]}...")
        
        # 查看任务状态
        task = await task_system.get_task(task_id)
        if task:
            print(f"✓ 任务状态: {task.status.value}")
        
        # Step 3: 等待播报结果
        print("\n[Step 3] 等待播报结果（最长 60s）...")
        try:
            broadcast = await task_system.wait_for_broadcast(task_id, timeout=60.0)
            print("✓ 任务完成，收到播报内容")
            
            # Step 4: 检查播报内容
            print("\n[Step 4] 检查播报内容:")
            print(f"  - Task ID: {broadcast.task_id}")
            print(f"\n  === 播报台词 ===")
            print(f"  {broadcast.content}")
            
            print(f"\n  === Panel JSON ===")
            if broadcast.panel_html:
                import json
                panel_json = json.dumps(broadcast.panel_html, indent=2, ensure_ascii=False)
                print(f"  {panel_json}")
            else:
                print(f"  (无 Panel)")
            
            print(f"\n  === Action ===")
            if broadcast.action:
                import json
                action_json = json.dumps(broadcast.action, indent=2, ensure_ascii=False)
                print(f"  {action_json}")
            else:
                print(f"  (无 Action)")
            
            # ⭐ 验证内容有效性
            if not broadcast.content or not broadcast.content.strip():
                print("\n❌ 播报内容为空！测试失败")
                return False
            
            print("\n✅ 播报内容有效")
            
            # Step 5: 检查播报队列 Redis
            print("\n[Step 5] 检查播报队列 Redis...")
            try:
                from app.services.playback.redis_repo import get_playback_repository
                playback_repo = await get_playback_repository()
                
                # 查看队列长度
                queue_len = await playback_repo.get_queue_length()
                print(f"✓ 播报队列长度: {queue_len}")
                
                # 尝试 pop 最新一条
                if queue_len > 0:
                    latest = await playback_repo.pop()
                    if latest:
                        print(f"✓ 最新播报: content={latest.content[:50]}...")
                        print(f"✓ 最新播报 panel: {latest.panel_html is not None}")
                
            except Exception as e:
                print(f"⚠ 播报队列检查失败: {e}")
            
            print("\n✅ 测试场景 1 通过！")
            return True
            
        except TimeoutError as e:
            print(f"❌ 等待超时: {e}")
            return False
        except Exception as e:
            print(f"❌ 等待失败: {e}")
            import traceback
            traceback.print_exc()
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
    
    result = await test_panel_weather()
    
    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)
    
    if result:
        print("✅ Panel 模式天气查询测试通过！")
    else:
        print("❌ Panel 模式天气查询测试失败！")
    
    print("=" * 60)
    print()


if __name__ == "__main__":
    asyncio.run(main())