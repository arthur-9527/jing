"""
OpenClaw WebSocket 服务模块（内部组件）

此模块提供 OpenClaw Provider 的内部组件：
- WebSocket 客户端（ws_client.py）
- Redis 仓库（redis_repo.py）
- 配置管理（config.py）
- 数据模型（models.py）

⭐ 新任务系统架构：
- TaskSystem（app/task_system/）统一管理任务
- OpenClawProvider 使用此模块的内部组件
- 不再导出 OpenClawTaskManager（旧系统已废弃）

使用示例（新系统）:
    from app.task_system import get_task_system
    
    # 提交任务
    task_id = await task_system.submit(
        tool_prompt="帮我查询天气",
        provider_name="openclaw",
    )
    
    # 等待播报
    broadcast = await task_system.wait_for_broadcast(task_id)
"""

# ⭐ 不再导出旧系统的 TaskManager
# OpenClawTaskManager 和 get_openclaw_manager 已废弃
# 新系统使用 app.task_system.TaskSystem

# 导出配置和数据模型（供 OpenClawProvider 内部使用）
from .config import (
    OpenClawServiceConfig,
    OpenClawWSConfig,
    OpenClawSessionConfig,
    OpenClawRedisConfig,
    OpenClawTimeoutConfig,
    get_openclaw_config,
    set_openclaw_config,
)

from .models import (
    TaskStatus,
    SessionStatus,
    Task,
    SessionState,
    ChatMessage,
)

__all__ = [
    # ⭐ 已废弃：不再导出旧系统
    # "OpenClawTaskManager",
    # "get_openclaw_manager",

    # 配置（内部使用）
    "OpenClawServiceConfig",
    "OpenClawWSConfig",
    "OpenClawSessionConfig",
    "OpenClawRedisConfig",
    "OpenClawTimeoutConfig",
    "get_openclaw_config",
    "set_openclaw_config",

    # 数据模型（内部使用）
    "TaskStatus",
    "SessionStatus",
    "Task",
    "SessionState",
    "ChatMessage",
]

# 版本信息
__version__ = "1.0.0"
__author__ = "Jing Team"

# 模块级别文档
__doc__ += """

配置说明:
    配置支持三种方式（按优先级排序）:
    1. 手动设置: set_openclaw_config(config)
    2. 主配置文件: 从 app.config.settings 读取
    3. 环境变量: OPENCLAW_WS_URL, OPENCLAW_WS_TOKEN, REDIS_URL 等
    4. 默认配置: 代码中的默认值

架构说明:
    - 单个WebSocket连接到 OpenClaw Gateway
    - 3个session并发执行任务（通过sessionKey区分）
    - Redis持久化任务状态，支持服务重启恢复
    - 调度器自动分配任务给空闲session

任务状态:
    PENDING -> ASSIGNED -> RUNNING -> COMPLETED
              |_________|_________|
                        |
                    FAILED/TIMEOUT/CANCELLED
"""
