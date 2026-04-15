"""
OpenClaw WebSocket 服务配置管理

支持两种模式：
1. 集成模式：从主配置 app.config.settings 读取
2. 独立模式：使用默认配置（用于测试）
"""

import os
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field


@dataclass
class OpenClawWSConfig:
    """WebSocket 配置"""
    # Gateway WebSocket URL
    ws_url: str = "ws://localhost:18789/gateway"

    # Gateway Token
    ws_token: str = "sk-075KyXO0ngF2GnBay40KfLikQwfDhR9iEESfEvMwXkQ"

    # Ed25519 设备身份文件路径
    identity_file: str = str(Path.home() / ".openclaw" / "pipecat_identity.json")

    # 连接超时（秒）
    connect_timeout: float = 15.0

    # Ping 间隔（秒）
    ping_interval: float = 20.0

    # Ping 超时（秒）
    ping_timeout: float = 20.0


@dataclass
class OpenClawSessionConfig:
    """Session 配置"""
    # Session 数量（可配置，默认 2 个）
    max_sessions: int = 2

    # Session keys（根据 max_sessions 自动生成）
    session_keys: List[str] = field(init=False)

    def __post_init__(self):
        """根据 max_sessions 自动生成 session keys"""
        self.session_keys = [
            f"agent:main:chat{i+1}"
            for i in range(self.max_sessions)
        ]


@dataclass
class OpenClawRedisConfig:
    """Redis 配置"""
    # Redis 连接 URL
    redis_url: str = "redis://localhost:6379/1"  # 使用 DB1 避免与主服务冲突

    # 任务记录过期时间（秒）
    task_ttl: int = 3600  # 1 小时

    # Redis Key 前缀
    key_prefix: str = "openclaw"


@dataclass
class OpenClawTimeoutConfig:
    """超时配置"""
    # 任务执行超时（秒）
    task_timeout: float = 60.0

    # WebSocket 发送超时（秒）
    send_timeout: float = 10.0

    # 任务状态查询间隔（秒）
    status_check_interval: float = 0.1

    # 调度器检查间隔（秒）
    scheduler_interval: float = 0.1

    # WebSocket 重连延迟（秒）
    reconnect_delay: float = 5.0

    # ⭐ 询问机制配置（已禁用 - 当前机制下问询没有作用）
    # 启用询问机制（对超长任务进行心跳检测）
    enable_heartbeat: bool = False


@dataclass
class OpenClawServiceConfig:
    """OpenClaw 服务完整配置"""
    ws: OpenClawWSConfig = field(default_factory=OpenClawWSConfig)
    session: OpenClawSessionConfig = field(default_factory=OpenClawSessionConfig)
    redis: OpenClawRedisConfig = field(default_factory=OpenClawRedisConfig)
    timeout: OpenClawTimeoutConfig = field(default_factory=OpenClawTimeoutConfig)

    # 日志级别
    log_level: str = "INFO"

    # 是否启用监控
    enable_metrics: bool = True

    # ⭐ LLM 二次处理配置（简化版：复用主 LLM 配置，只配置模型名称）
    llm_post_process_model: str = "openai/gpt-4o-mini"  # 用于二次处理的模型名称
    llm_post_process_provider: str = "litellm"  # litellm 或 cerebras（复用主 LLM_PROVIDER）
    llm_post_process_timeout: float = 30.0  # 二次处理超时时间
    # 从主配置复用的字段（根据 provider 自动获取）
    llm_post_process_base_url: str = "http://localhost:4000"  # 从 LLM_API_BASE_URL 复用
    llm_post_process_api_key: str = ""  # 从 LLM_API_KEY 或 CEREBRAS_API_KEY 复用

    @classmethod
    def from_main_config(cls) -> "OpenClawServiceConfig":
        """从主配置文件读取（集成模式）"""
        try:
            from app.config import settings

            # 从主配置读取相关参数
            ws_url = getattr(settings, "OPENCLAW_WS_URL", "ws://localhost:18789/gateway")
            ws_token = getattr(settings, "OPENCLAW_WS_TOKEN", "")
            identity_file = getattr(settings, "OPENCLAW_IDENTITY_FILE",
                                    str(Path.home() / ".openclaw" / "pipecat_identity.json"))

            redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/1")

            # 支持从主配置读取 session 数量
            max_sessions = getattr(settings, "OPENCLAW_MAX_SESSIONS", 2)

            # ⭐ 读取 LLM 二次处理配置（简化版：只配置模型名称，复用主 LLM 配置）
            llm_post_process_provider = getattr(
                settings, "OPENCLAW_LLM_PROVIDER", "litellm"
            )
            llm_post_process_model = getattr(
                settings, "OPENCLAW_LLM_POST_PROCESS_MODEL", "openai/gpt-4o-mini"
            )
            llm_post_process_timeout = getattr(
                settings, "OPENCLAW_LLM_POST_PROCESS_TIMEOUT", 30.0
            )

            # 根据 provider 复用主 LLM 配置
            if llm_post_process_provider == "cerebras":
                llm_post_process_base_url = ""  # Cerebras 使用 SDK，不需要 base_url
                llm_post_process_api_key = getattr(settings, "CEREBRAS_API_KEY", "")
            else:
                # litellm 或默认，复用主 LLM 配置
                llm_post_process_base_url = getattr(settings, "LLM_API_BASE_URL", "http://localhost:4000")
                llm_post_process_api_key = getattr(settings, "LLM_API_KEY", "")

            return cls(
                ws=OpenClawWSConfig(
                    ws_url=ws_url,
                    ws_token=ws_token,
                    identity_file=identity_file,
                ),
                session=OpenClawSessionConfig(
                    max_sessions=max_sessions,
                ),
                redis=OpenClawRedisConfig(
                    redis_url=redis_url,
                ),
                timeout=OpenClawTimeoutConfig(
                    task_timeout=getattr(settings, "OPENCLAW_TIMEOUT", 60.0),
                ),
                log_level=getattr(settings, "LOG_LEVEL", "INFO"),
                llm_post_process_model=llm_post_process_model,
                llm_post_process_provider=llm_post_process_provider,
                llm_post_process_base_url=llm_post_process_base_url,
                llm_post_process_api_key=llm_post_process_api_key,
                llm_post_process_timeout=llm_post_process_timeout,
            )
        except ImportError:
            # 主配置不存在，使用默认配置
            return cls()

    @classmethod
    def from_env(cls) -> "OpenClawServiceConfig":
        """从环境变量读取"""
        ws_url = os.getenv("OPENCLAW_WS_URL", "ws://localhost:18789/gateway")
        ws_token = os.getenv("OPENCLAW_WS_TOKEN", "")
        identity_file = os.getenv("OPENCLAW_IDENTITY_FILE",
                                  str(Path.home() / ".openclaw" / "pipecat_identity.json"))
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/1")

        # 支持从环境变量读取 session 数量
        max_sessions = int(os.getenv("OPENCLAW_MAX_SESSIONS", "2"))

        # ⭐ 从环境变量读取 LLM 二次处理配置（简化版）
        llm_post_process_provider = os.getenv("OPENCLAW_LLM_PROVIDER", "litellm")
        llm_post_process_model = os.getenv("OPENCLAW_LLM_POST_PROCESS_MODEL", "openai/gpt-4o-mini")
        llm_post_process_timeout = float(os.getenv("OPENCLAW_LLM_POST_PROCESS_TIMEOUT", "30.0"))

        # 根据 provider 复用主 LLM 配置
        if llm_post_process_provider == "cerebras":
            llm_post_process_base_url = ""  # Cerebras 使用 SDK，不需要 base_url
            llm_post_process_api_key = os.getenv("CEREBRAS_API_KEY", "")
        else:
            # litellm 或默认，复用主 LLM 配置
            llm_post_process_base_url = os.getenv("LLM_API_BASE_URL", "http://localhost:4000")
            llm_post_process_api_key = os.getenv("LLM_API_KEY", "")

        return cls(
            ws=OpenClawWSConfig(
                ws_url=ws_url,
                ws_token=ws_token,
                identity_file=identity_file,
            ),
            session=OpenClawSessionConfig(
                max_sessions=max_sessions,
            ),
            redis=OpenClawRedisConfig(
                redis_url=redis_url,
            ),
            timeout=OpenClawTimeoutConfig(
                task_timeout=float(os.getenv("OPENCLAW_TIMEOUT", "60.0")),
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            llm_post_process_model=llm_post_process_model,
            llm_post_process_provider=llm_post_process_provider,
            llm_post_process_base_url=llm_post_process_base_url,
            llm_post_process_api_key=llm_post_process_api_key,
            llm_post_process_timeout=llm_post_process_timeout,
        )


# 全局配置实例（懒加载）
_config: Optional[OpenClawServiceConfig] = None


def get_openclaw_config() -> OpenClawServiceConfig:
    """获取 OpenClaw 配置实例

    优先级：
    1. 如果已手动设置，使用设置的配置
    2. 尝试从主配置文件读取
    3. 尝试从环境变量读取
    4. 使用默认配置
    """
    global _config
    if _config is not None:
        return _config

    # 尝试从主配置读取
    try:
        _config = OpenClawServiceConfig.from_main_config()
        return _config
    except Exception:
        pass

    # 尝试从环境变量读取
    try:
        _config = OpenClawServiceConfig.from_env()
        return _config
    except Exception:
        pass

    # 使用默认配置
    _config = OpenClawServiceConfig()
    return _config


def set_openclaw_config(config: OpenClawServiceConfig) -> None:
    """手动设置配置（用于测试）"""
    global _config
    _config = config