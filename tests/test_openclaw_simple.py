"""
简化的 OpenClaw WebSocket 服务测试脚本

只测试核心功能，避免依赖问题
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
from loguru import logger


async def test_config():
    """测试配置加载"""
    print("=" * 60)
    print("🔧 测试配置加载")
    print("=" * 60)

    try:
        from app.services.openclaw.config import get_openclaw_config

        config = get_openclaw_config()

        print(f"✅ WebSocket URL: {config.ws.ws_url}")
        print(f"✅ Max Sessions: {config.session.max_sessions}")
        print(f"✅ Redis URL: {config.redis.redis_url}")
        print(f"✅ Post Process Model: {config.llm_post_process_model}")
        print(f"✅ Post Process Base URL: {config.llm_post_process_base_url}")

        return True
    except Exception as e:
        print(f"❌ 配置加载失败: {e}")
        return False


async def test_redis_connection():
    """测试 Redis 连接"""
    print("\n" + "=" * 60)
    print("🔧 测试 Redis 连接")
    print("=" * 60)

    try:
        from app.services.openclaw.redis_repo import OpenClawTaskRepository

        repo = OpenClawTaskRepository()
        await repo.connect()

        stats = await repo.get_stats()
        print(f"✅ Redis 已连接")
        print(f"✅ 统计: {json.dumps(stats, ensure_ascii=False)}")

        await repo.disconnect()
        return True
    except Exception as e:
        print(f"❌ Redis 连接失败: {e}")
        return False


async def test_task_creation():
    """测试任务创建"""
    print("\n" + "=" * 60)
    print("🔧 测试任务创建")
    print("=" * 60)

    try:
        from app.services.openclaw.redis_repo import OpenClawTaskRepository

        repo = OpenClawTaskRepository()
        await repo.connect()

        # 创建测试任务
        task_id = await repo.create_task(
            tool_prompt="测试任务",
            user_input="测试用户输入",
            memory_context="测试记忆",
            conversation_history="测试历史",
            inner_monologue="测试内心独白",
            emotion_delta={"P": 0.1, "A": 0.2, "D": -0.1},
        )

        print(f"✅ 任务已创建: {task_id}")

        # 读取任务
        task = await repo.get_task(task_id)
        if task:
            print(f"✅ 任务状态: {task.status.value}")
            print(f"✅ Tool Prompt: {task.tool_prompt}")
            print(f"✅ User Input: {task.user_input}")
            print(f"✅ Emotion Delta: {task.emotion_delta}")

        # 清理
        await repo.delete_task(task_id)
        print(f"✅ 测试任务已清理")

        await repo.disconnect()
        return True
    except Exception as e:
        print(f"❌ 任务创建失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主测试函数"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "  OpenClaw WebSocket 服务核心功能测试".center(58) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    results = {}

    # 测试配置
    results['config'] = await test_config()

    # 测试 Redis
    results['redis'] = await test_redis_connection()

    # 测试任务创建
    results['task'] = await test_task_creation()

    # 总结
    print("\n" + "=" * 60)
    print("📊 测试总结")
    print("=" * 60)

    for test_name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_name}: {status}")

    all_passed = all(results.values())
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 所有测试通过！")
    else:
        print("⚠️  部分测试失败，请检查错误信息")
    print("=" * 60)

    return all_passed


if __name__ == "__main__":
    # 配置日志
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO",
    )

    # 运行测试
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
