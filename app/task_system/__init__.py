"""
任务系统 - 统一的任务管理与分发

核心设计：
1. 双层队列结构：主队列（TaskSystem）+ Provider 内部队列（可选）
2. 同步接口：Provider 通过 TaskSyncInterface 同步状态
3. 二次改写：统一处理所有 Provider 结果
4. 阻塞初始化：启动时清空队列 + 初始化 Provider + 门控集成

使用方式：
    from app.task_system import get_task_system
    
    task_system = get_task_system()
    await task_system.start()  # 阻塞初始化
    
    task_id = await task_system.submit(tool_prompt, provider_name="openclaw")
    broadcast = await task_system.wait_for_broadcast(task_id)
"""

from .manager import TaskSystem, get_task_system, set_task_system
from .models import TaskStatus, Task, ProviderResult, BroadcastContent
from .base import TaskProvider, TaskSyncInterface

__all__ = [
    "TaskSystem",
    "get_task_system",
    "set_task_system",
    "TaskStatus",
    "Task",
    "ProviderResult",
    "BroadcastContent",
    "TaskProvider",
    "TaskSyncInterface",
]
