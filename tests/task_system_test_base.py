#!/usr/bin/env python3
"""
任务系统测试辅助模块 - 共享测试逻辑

提供：
1. setup_task_system: 创建并启动 TaskSystem（支持环境变量覆盖）
2. teardown_task_system: 停止并清理 TaskSystem
3. reset_all_globals: 重置所有全局实例
"""

import asyncio
import sys
import os

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


def reset_all_globals():
    """重置所有全局实例
    
    用于测试之间隔离，避免全局实例污染
    """
    # 重置 TaskRepository
    from app.task_system.redis_repo import reset_task_repository
    reset_task_repository()
    
    # 重置 TaskSystem
    from app.task_system import set_task_system
    set_task_system(None)
    
    # 重置配置
    from app.task_system.config import reload_task_system_settings
    reload_task_system_settings()
    
    # 重置 OpenClaw Provider 相关
    from app.task_system.providers.openclaw_provider import reset_openclaw_provider
    reset_openclaw_provider()
    
    logger.debug("[TestBase] 所有全局实例已重置")


async def setup_task_system(env_overrides: dict = None) -> "TaskSystem":
    """创建并启动 TaskSystem
    
    Args:
        env_overrides: 环境变量覆盖，如 {"OPENCLAW_ENABLED": "false"}
    
    Returns:
        TaskSystem 实例
    """
    # 应用环境变量覆盖
    original_values = {}
    if env_overrides:
        for key, value in env_overrides.items():
            original_values[key] = os.environ.get(key)
            os.environ[key] = value
            logger.info(f"[TestBase] 设置环境变量: {key}={value}")
    
    # 重置所有全局实例
    reset_all_globals()
    
    # 创建 TaskSystem
    from app.task_system import TaskSystem, set_task_system
    task_system = TaskSystem()
    set_task_system(task_system)
    
    # 启动
    await task_system.start()
    logger.info("[TestBase] TaskSystem 已启动")
    
    # 保存原始环境变量以便恢复
    task_system._test_original_env = original_values
    
    return task_system


async def teardown_task_system(task_system: "TaskSystem"):
    """停止并清理 TaskSystem
    
    Args:
        task_system: TaskSystem 实例
    """
    # 停止 TaskSystem
    if task_system:
        try:
            await task_system.stop()
            logger.info("[TestBase] TaskSystem 已停止")
        except Exception as e:
            logger.warning(f"[TestBase] TaskSystem 停止异常（可忽略）: {e}")
    
    # 恢复环境变量
    if hasattr(task_system, '_test_original_env'):
        for key, original_value in task_system._test_original_env.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value
            logger.debug(f"[TestBase] 恢复环境变量: {key}={original_value}")
    
    # 重置全局实例
    reset_all_globals()


def check_identity_file() -> bool:
    """检查 OpenClaw 身份文件是否存在
    
    Returns:
        True 如果存在，False 否则
    """
    from pathlib import Path
    identity_file = Path.home() / ".openclaw" / "pipecat_identity.json"
    if identity_file.exists():
        logger.info(f"[TestBase] 身份文件存在: {identity_file}")
        return True
    else:
        logger.warning(f"[TestBase] 身份文件不存在: {identity_file}")
        return False