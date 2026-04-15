"""
测试 OpenClaw WebSocket 服务 + LLM 二次处理完整流程

功能：
1. 启动 WebSocket 服务
2. 提交任务（包含二次处理上下文）
3. 等待任务完成
4. 验证最终结果
"""

import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
from loguru import logger

from app.services.openclaw.task_manager import get_openclaw_manager
from app.services.openclaw.models import TaskStatus


async def test_complete_workflow():
    """测试完整的异步处理流程"""

    # 获取任务管理器
    manager = get_openclaw_manager()

    try:
        # 启动服务
        logger.info("=" * 60)
        logger.info("启动 OpenClaw WebSocket 服务...")
        logger.info("=" * 60)
        await manager.start()

        # 提交任务（包含二次处理上下文）
        tool_prompt = "panel模式:查询今天北京的天气"
        user_input = "今天北京天气怎么样？"
        memory_context = "用户位于北京"
        conversation_history = "用户: 今天北京天气怎么样？"
        inner_monologue = "用户想知道北京今天的天气情况"
        emotion_delta = {"P": 0.1, "A": 0.2, "D": -0.1}

        logger.info("=" * 60)
        logger.info("提交任务...")
        logger.info(f"Tool Prompt: {tool_prompt}")
        logger.info(f"User Input: {user_input}")
        logger.info("=" * 60)

        task_id = await manager.submit_task(
            tool_prompt=tool_prompt,
            user_input=user_input,
            memory_context=memory_context,
            conversation_history=conversation_history,
            inner_monologue=inner_monologue,
            emotion_delta=emotion_delta,
        )

        logger.info(f"任务已提交: {task_id}")

        # 等待任务完成
        logger.info("=" * 60)
        logger.info("等待任务完成（包含 LLM 二次处理）...")
        logger.info("=" * 60)

        result = await manager.wait_for_result(task_id, timeout=120.0)

        logger.info("=" * 60)
        logger.info("任务完成！")
        logger.info("=" * 60)
        logger.info(f"OpenClaw 原始结果: {json.dumps(result.get('openclaw_result'), ensure_ascii=False, indent=2)}")
        logger.info(f"LLM 二次处理结果: {json.dumps(result.get('final_result'), ensure_ascii=False, indent=2)}")
        logger.info("=" * 60)

        # 验证结果结构
        final_result = result.get("final_result")
        if final_result:
            assert "content" in final_result, "最终结果必须包含 content"
            logger.info("✅ 验证通过：最终结果包含 content 字段")
        else:
            logger.warning("⚠️  警告：最终结果为空，可能二次处理失败")

        # 打印统计信息
        stats = await manager.get_stats()
        logger.info(f"\n服务统计: {json.dumps(stats, ensure_ascii=False, indent=2)}")

    except Exception as e:
        logger.error(f"测试失败: {e}")
        raise
    finally:
        # 停止服务
        logger.info("\n停止服务...")
        await manager.stop()
        logger.info("测试完成")


async def test_workflow_with_real_openclaw():
    """测试真实的 OpenClaw 调用（需要 OpenClaw 服务运行）"""

    logger.info("=" * 60)
    logger.info("真实环境测试 - OpenClaw WebSocket 服务")
    logger.info("=" * 60)
    logger.info("前置条件:")
    logger.info("1. OpenClaw Gateway 正在运行 (ws://localhost:18789/gateway)")
    logger.info("2. Redis 正在运行 (redis://localhost:6379/1)")
    logger.info("3. LiteLLM 代理正在运行 (http://localhost:4000)")
    logger.info("=" * 60)

    await test_complete_workflow()


if __name__ == "__main__":
    # 配置日志
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO",
    )

    # 运行测试
    asyncio.run(test_workflow_with_real_openclaw())
