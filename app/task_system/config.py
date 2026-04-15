"""
任务系统配置 - 环境变量 + 开关

配置项：
- TASK_SYSTEM_ENABLED: 任务系统总开关
- OPENCLAW_ENABLED: OpenClaw Provider 开关
- OPENCLAW_*: OpenClaw 连接配置
- POST_PROCESS_*: 二次改写配置
"""

from pydantic_settings import BaseSettings
from typing import Optional


class TaskSystemSettings(BaseSettings):
    """任务系统配置"""
    
    # ===== 任务系统总开关 =====
    TASK_SYSTEM_ENABLED: bool = True
    
    # ===== OpenClaw Provider 配置 =====
    OPENCLAW_ENABLED: bool = True
    OPENCLAW_WS_URL: str = "ws://localhost:8080/ws"
    OPENCLAW_SESSION_KEYS: str = "agent:main:chat1,agent:main:chat2,agent:main:chat3"
    OPENCLAW_TIMEOUT: float = 60.0
    OPENCLAW_CLEAR_QUEUE_ON_START: bool = True
    
    # ===== 二次改写配置 =====
    POST_PROCESS_ENABLED: bool = True
    POST_PROCESS_PROVIDER: str = "cerebras"  # cerebras / litellm
    POST_PROCESS_MODEL: str = "llama-4-scout-17b-16e-instruct"
    POST_PROCESS_BASE_URL: str = ""
    POST_PROCESS_API_KEY: str = ""
    POST_PROCESS_TIMEOUT: float = 30.0
    
    # ===== HTTP Provider 配置（示例，未来扩展）=====
    HTTP_PROVIDER_ENABLED: bool = False
    HTTP_PROVIDER_URL: str = ""
    HTTP_PROVIDER_API_KEY: str = ""
    HTTP_PROVIDER_TIMEOUT: float = 60.0
    
    @property
    def openclaw_session_list(self) -> list[str]:
        """解析 session keys 为列表"""
        return [k.strip() for k in self.OPENCLAW_SESSION_KEYS.split(",") if k.strip()]
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # ⭐ 忽略 .env 中不相关的字段
    }


# ===== 全局配置实例（懒加载）=====
_settings: Optional[TaskSystemSettings] = None


def get_task_system_settings() -> TaskSystemSettings:
    """获取任务系统配置实例"""
    global _settings
    if _settings is None:
        _settings = TaskSystemSettings()
    return _settings


def reload_task_system_settings() -> TaskSystemSettings:
    """重新加载配置（用于测试）"""
    global _settings
    _settings = TaskSystemSettings()
    return _settings