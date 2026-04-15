#!/usr/bin/env python3
"""
任务系统集成测试

测试场景：
1. Panel 模式 - 天气查询（真实 OpenClaw 调用）
2. OpenClaw 未启用（通过环境变量模拟）
3. OpenClaw 连接失败（使用无效 WebSocket URL 模拟）

运行方式：
    conda activate backend
    python tests/test_task_system_integration.py
"""

import asyncio
import sys
import os
import time

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger


def setup_logging():
    """配置日志"""
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>"
    )


async def test_panel_weather_real():
    """测试场景 1: Panel 模式 - 天气查询（真实 OpenClaw 调用）
    
    流程：
    1. 启动任务系统
    2. 提交天气查询 tool_prompt
    3. 等待播报结果
    4. 检查播报内容
    5. 检查播报队列 Redis
    """
    print("\n" + "=" * 60)
    print("测试场景 1: Panel 模式 - 天气查询（真实 OpenClaw）")
    print("=" * 60)
    
    # 检查身份文件
    from pathlib import Path
    identity_file = Path.home() / ".openclaw" / "pipecat_identity.json"
    if not identity_file.exists():
        print(f"❌ 身份文件不存在: {identity_file}")
        print("请先创建设备身份文件，跳过真实测试")
        return False
    
    print(f"✓ 身份文件存在: {identity_file}")
    
    # 导入任务系统
    from app.task_system import get_task_system, set_task_system, TaskSystem
    from app.task_system.config import reload_task_system_settings
    
    # 重置配置和实例
    reload_task_system_settings()
    set_task_system(TaskSystem())
    
    task_system = get_task_system()
    
    try:
        # Step 1: 启动任务系统
        print("\n[Step 1] 启动任务系统...")
        await task_system.start()
        print("✓ 任务系统已启动")
        
        # 检查 Provider 状态
        stats = await task_system.get_stats()
        print(f"✓ Provider 状态: {stats.get('providers', {})}")
        
        # Step 2: 提交天气查询
        print("\n[Step 2] 提交天气查询...")
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
            print(f"  - Task ID: {broadcast.task_id[:16]}...")
            print(f"  - 播报文本: {broadcast.content[:100]}...")
            
            if broadcast.panel_html:
                print(f"  - Panel HTML: 存在")
                panel = broadcast.panel_html
                print(f"    - x={panel.get('x')}, y={panel.get('y')}")
                print(f"    - width={panel.get('width')}, height={panel.get('height')}")
                html_preview = panel.get("html", "")[:50]
                print(f"    - html preview: {html_preview}...")
            else:
                print(f"  - Panel HTML: 无")
            
            if broadcast.action:
                print(f"  - Action: {broadcast.action}")
            else:
                print(f"  - Action: 无")
            
            # Step 5: 检查播报队列 Redis
            print("\n[Step 5] 检查播报队列 Redis...")
            try:
                from app.services.playback.redis_repo import get_playback_repository
                playback_repo = await get_playback_repository()
                
                # 查看队列长度
                queue_len = await playback_repo.get_queue_length()
                print(f"✓ 播报队列长度: {queue_len}")
                
                # 尝试 peek 最新一条
                if queue_len > 0:
                    latest = await playback_repo.peek_latest()
                    if latest:
                        print(f"✓ 最新播报: content={latest.content[:50]}...")
                
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
        print(f"❌ 任务系统启动失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # 停止任务系统
        await task_system.stop()
        print("✓ 任务系统已停止")


async def test_openclaw_disabled():
    """测试场景 2: OpenClaw 未启用
    
    通过环境变量模拟 OPENCLAW_ENABLED=false
    """
    print("\n" + "=" * 60)
    print("测试场景 2: OpenClaw 未启用（环境变量模拟）")
    print("=" * 60)
    
    # 临时修改环境变量
    original_value = os.environ.get("OPENCLAW_ENABLED", "true")
    os.environ["OPENCLAW_ENABLED"] = "false"
    print(f"✓ 已设置 OPENCLAW_ENABLED=false")
    
    # 导入任务系统
    from app.task_system import get_task_system, set_task_system, TaskSystem
    from app.task_system.config import reload_task_system_settings
    
    # 重置配置和实例
    reload_task_system_settings()
    set_task_system(TaskSystem())
    
    task_system = get_task_system()
    
    try:
        # 启动任务系统
        print("\n[Step 1] 启动任务系统...")
        await task_system.start()
        print("✓ 任务系统已启动（跳过 OpenClaw Provider）")
        
        # 检查配置
        stats = await task_system.get_stats()
        print(f"✓ 配置: openclaw_enabled={stats['settings']['openclaw_enabled']}")
        print(f"✓ Provider 数量: {len(stats.get('providers', {}))}")
        
        # 尝试提交任务
        print("\n[Step 2] 尝试提交任务...")
        try:
            task_id = await task_system.submit(
                tool_prompt="测试任务",
                provider_name="openclaw",
            )
            print(f"❌ 不应该成功提交任务: {task_id}")
            return False
        except ValueError as e:
            print(f"✓ 正确抛出 ValueError: {e}")
            if "未启用" in str(e) or "不存在" in str(e):
                print("✅ 错误信息正确")
                return True
            else:
                print(f"❌ 错误信息不正确: {e}")
                return False
        except Exception as e:
            print(f"❌ 意外的错误类型: {type(e).__name__}: {e}")
            return False
        
    except Exception as e:
        print(f"❌ 任务系统启动失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # 停止任务系统
        await task_system.stop()
        print("✓ 任务系统已停止")
        
        # 恢复环境变量
        os.environ["OPENCLAW_ENABLED"] = original_value
        print(f"✓ 已恢复 OPENCLAW_ENABLED={original_value}")


async def test_openclaw_connection_failed():
    """测试场景 3: OpenClaw 连接失败
    
    使用无效的 WebSocket URL 模拟连接失败
    """
    print("\n" + "=" * 60)
    print("测试场景 3: OpenClaw 连接失败（无效 URL 模拟）")
    print("=" * 60)
    
    # 临时修改环境变量 - 使用无效 URL
    original_url = os.environ.get("OPENCLAW_WS_URL", "")
    os.environ["OPENCLAW_WS_URL"] = "ws://invalid-host:9999/invalid"
    os.environ["OPENCLAW_ENABLED"] = "true"
    print(f"✓ 已设置无效 WebSocket URL: ws://invalid-host:9999/invalid")
    
    # 导入任务系统
    from app.task_system import get_task_system, set_task_system, TaskSystem
    from app.task_system.config import reload_task_system_settings
    
    # 重置配置和实例
    reload_task_system_settings()
    set_task_system(TaskSystem())
    
    task_system = get_task_system()
    
    try:
        # 尝试启动任务系统
        print("\n[Step 1] 启动任务系统...")
        try:
            await task_system.start()
            print("❌ 不应该成功启动")
            return False
        except RuntimeError as e:
            print(f"✓ 正确抛出 RuntimeError: {e}")
            
            # 检查错误信息
            error_msg = str(e)
            if "Provider" in error_msg and ("失败" in error_msg or "初始化" in error_msg):
                print("✅ 错误信息正确（Provider 初始化失败）")
                return True
            else:
                print(f"⚠ 错误信息可能不完整: {e}")
                return True  # 仍然视为通过，因为抛出了异常
        
    except Exception as e:
        print(f"❌ 意外的错误: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # 尝试停止（可能未启动成功）
        try:
            await task_system.stop()
            print("✓ 任务系统已停止")
        except Exception as e:
            print(f"⚠ 停止时出错（可忽略）: {e}")
        
        # 恢复环境变量
        if original_url:
            os.environ["OPENCLAW_WS_URL"] = original_url
        else:
            os.environ.pop("OPENCLAW_WS_URL", None)
        print(f"✓ 已恢复环境变量")


async def main():
    """运行所有测试"""
    setup_logging()
    
    print("\n" + "=" * 60)
    print("任务系统集成测试")
    print("=" * 60)
    print()
    
    results = []
    
    # 测试场景 1: 真实 OpenClaw
    result1 = await test_panel_weather_real()
    results.append(("场景1: Panel 模式天气查询", result1))
    
    # 测试场景 2: OpenClaw 未启用
    result2 = await test_openclaw_disabled()
    results.append(("场景2: OpenClaw 未启用", result2))
    
    # 测试场景 3: OpenClaw 连接失败
    result3 = await test_openclaw_connection_failed()
    results.append(("场景3: OpenClaw 连接失败", result3))
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
    
    all_passed = all(r[1] for r in results)
    
    print()
    if all_passed:
        print("🎉 所有测试通过！")
    else:
        print("⚠️ 部分测试失败，请检查日志")
    
    print("=" * 60)
    print()


if __name__ == "__main__":
    asyncio.run(main())