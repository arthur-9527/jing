"""
任务系统 Providers - Provider 实现模块

包含：
- base_provider: Provider 抽象基类（从 base.py 导入）
- openclaw_provider: OpenClaw WebSocket Provider 实现
"""

from ..base import TaskProvider, TaskSyncInterface
from .openclaw_provider import OpenClawProvider

__all__ = [
    "TaskProvider",
    "TaskSyncInterface",
    "OpenClawProvider",
]