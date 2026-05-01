"""
任务系统配置 - 环境变量 + 开关

配置项：
- TASK_SYSTEM_ENABLED: 任务系统总开关
- OPENCLAW_ENABLED: OpenClaw Provider 开关
- OPENCLAW_*: OpenClaw 连接配置
- POST_PROCESS_*: 二次改写配置（使用 CHAT_PROVIDER）
"""

from pydantic_settings import BaseSettings
from typing import Optional

from app.config import settings as main_settings


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
    # 二次改写使用 CHAT_PROVIDER + CHAT_MODEL（从主配置读取）
    POST_PROCESS_ENABLED: bool = True
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
    
    @property
    def POST_PROCESS_PROVIDER(self) -> str:
        """二次改写使用的 Provider（跟随 CHAT_PROVIDER）"""
        return main_settings.CHAT_PROVIDER
    
    @property
    def POST_PROCESS_MODEL(self) -> str:
        """二次改写使用的模型（跟随 CHAT_MODEL 或 Provider 默认）"""
        if main_settings.CHAT_MODEL:
            return main_settings.CHAT_MODEL
        
        provider = main_settings.CHAT_PROVIDER
        if provider == "cerebras":
            return main_settings.CEREBRAS_MODEL
        else:  # litellm
            return main_settings.LITELLM_MODEL
    
    @property
    def POST_PROCESS_BASE_URL(self) -> str:
        """二次改写使用的 API Base URL（根据 Provider）"""
        provider = main_settings.CHAT_PROVIDER
        if provider == "cerebras":
            return main_settings.CEREBRAS_API_BASE_URL or ""
        else:  # litellm
            return main_settings.LITELLM_API_BASE_URL
    
    @property
    def POST_PROCESS_API_KEY(self) -> str:
        """二次改写使用的 API Key（根据 Provider）"""
        provider = main_settings.CHAT_PROVIDER
        if provider == "cerebras":
            return main_settings.CEREBRAS_API_KEY or ""
        else:  # litellm
            return main_settings.LITELLM_API_KEY or ""
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # 忽略 .env 中不相关的字段
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