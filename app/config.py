"""应用配置管理"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """应用配置"""

    # 数据库配置 (统一数据库)
    # 不要在代码里硬编码账号密码；请通过 .env / 环境变量提供
    DATABASE_URL: str = "postgresql+asyncpg://postgres@localhost:5432/agent_backend"

    # ========== DashScope 配置 ==========
    DASHSCOPE_API_KEY: Optional[str] = None

    # ========== LLM Provider 配置 ==========
    LLM_PROVIDER: str = "litellm"  # litellm 或 cerebras

    # ========== LLM API 配置 (Agent 用 - LiteLLM Provider) ==========
    LLM_API_BASE_URL: str = "http://127.0.0.1:4000/v1"
    LLM_API_KEY: Optional[str] = None
    LLM_MODEL: str = "qwen3.5-35b"
    LLM_FAST_MODEL: Optional[str] = None

    # ========== Cerebras SDK 配置 ==========
    CEREBRAS_API_KEY: Optional[str] = None
    CEREBRAS_API_BASE_URL: Optional[str] = None  # 自定义 API 端点（用于中转）
    CEREBRAS_MODEL: str = "llama3.1-8b"
    CEREBRAS_FAST_MODEL: Optional[str] = None

    # ========== 任务系统配置（新系统）==========
    # 任务系统总开关
    TASK_SYSTEM_ENABLED: bool = True
    
    # ========== OpenClaw Provider 配置 ==========
    # OpenClaw Provider 开关
    OPENCLAW_ENABLED: bool = True
    # WebSocket 网关地址
    OPENCLAW_WS_URL: str = "ws://127.0.0.1:18789/gateway"
    # WebSocket Token（默认使用 API_KEY）
    OPENCLAW_WS_TOKEN: Optional[str] = None
    # Ed25519 设备身份文件路径（用于认证，可选）
    OPENCLAW_IDENTITY_FILE: Optional[str] = None
    # Session Keys（逗号分隔，替代 OPENCLAW_MAX_SESSIONS）
    OPENCLAW_SESSION_KEYS: str = "agent:main:chat1,agent:main:chat2"
    # 任务超时时间（秒）
    OPENCLAW_TIMEOUT: float = 300.0
    # 启动时清空队列
    OPENCLAW_CLEAR_QUEUE_ON_START: bool = True

    # ========== 二次改写配置（新系统）==========
    # 二次改写开关
    POST_PROCESS_ENABLED: bool = True
    # LLM Provider（litellm 使用 OpenAI 兼容 API，cerebras 使用 Cerebras SDK）
    POST_PROCESS_PROVIDER: str = "litellm"
    # 改写模型名称
    POST_PROCESS_MODEL: str = "qwen3-chat"
    # API Base URL（litellm 模式，复用主 LLM 配置）
    POST_PROCESS_BASE_URL: str = ""
    # API Key（可选，默认使用 LLM_API_KEY）
    POST_PROCESS_API_KEY: str = ""
    # 改写超时（秒）
    POST_PROCESS_TIMEOUT: float = 60.0

    # ========== HTTP Provider 配置（未来扩展）==========
    HTTP_PROVIDER_ENABLED: bool = False
    HTTP_PROVIDER_URL: str = ""
    HTTP_PROVIDER_API_KEY: str = ""

    # ========== Redis 配置 ==========
    REDIS_URL: str = "redis://localhost:6379/0"

    # ========== Embedding 配置 (本地模型) ==========
    # 模型路径必须通过环境变量 LOCAL_EMBEDDING_MODEL_PATH 配置
    LOCAL_EMBEDDING_ENABLED: bool = True
    LOCAL_EMBEDDING_MODEL_PATH: str = ""  # 必须在 .env 中配置，如：models/embedding 或 /app/models/embedding
    EMBEDDING_DIM: int = 512  # bge-small-zh-v1.5 输出 512 维
    EMBEDDING_CACHE_TTL: int = 300  # 缓存 TTL（秒）
    EMBEDDING_CACHE_MAX: int = 1000  # 缓存最大条目数


    # ========== Agent 配置 ==========
    CHARACTER_CONFIG_PATH: str = "config/characters/daji"  # 角色配置目录路径（不含扩展名）
    CONVERSATION_WINDOW_SIZE: int = 10
    EMOTION_INTENSITY_THRESHOLD: float = 0.3

    # ========== 动作解析配置 ==========
    ACTION_PARSE_TIMEOUT: float = 8.0
    ACTION_PARSE_USE_FAST: bool = True

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # CORS 配置
    CORS_ORIGINS: list[str] = ["*"]

    # 应用配置
    APP_NAME: str = "MMD Agent Backend"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ========== ASR Provider 配置 ==========
    ASR_PROVIDER: str = "qwen"  # deepgram 或 qwen

    # Deepgram ASR
    DEEPGRAM_API_KEY: Optional[str] = None
    DEEPGRAM_ASR_MODEL: str = "nova-2-general"
    DEEPGRAM_ASR_LANGUAGE: str = "zh-CN"

    # 千问 ASR (Qwen ASR)
    QWEN_ASR_MODEL: str = "qwen3-asr-flash-realtime"
    QWEN_ASR_LANGUAGE: str = "zh"
    QWEN_ASR_ENABLE_VAD: bool = True
    QWEN_ASR_VAD_THRESHOLD: float = 0.0
    QWEN_ASR_VAD_SILENCE_MS: int = 400

    # ========== TTS Provider 配置 ==========
    TTS_PROVIDER: str = "cosyvoice_ws"  # cartesia 或 cosyvoice_ws

    # CosyVoice WebSocket TTS
    COSYVOICE_WS_MODEL: str = "cosyvoice-v3-flash"
    COSYVOICE_WS_CLONE_AUDIO: Optional[str] = None  # 克隆音色音频文件路径（如 "temp/wendi.mp3"）

    # Cartesia TTS
    CARTESIA_API_KEY: Optional[str] = None
    CARTESIA_VOICE_ID: Optional[str] = None
    CARTESIA_MODEL: str = "sonic-3"
    CARTESIA_LANGUAGE: str = "zh"
    CARTESIA_SAMPLE_RATE: int = 22050

    # ========== 音频配置 ==========
    AUDIO_SAMPLE_RATE: int = 16000
    TTS_SAMPLE_RATE: int = 16000  # TTS 输出采样率（统一使用 16kHz）
    
    # 音频设备索引（可选，默认使用系统默认设备）
    # 设置为 None 或 -1 表示使用系统默认
    # 如需指定特定设备（如 ESP32 USB 麦克风），可通过 .env 配置具体索引
    AUDIO_INPUT_DEVICE_INDEX: Optional[int] = None   # 输入设备索引（麦克风）
    AUDIO_OUTPUT_DEVICE_INDEX: Optional[int] = None  # 输出设备索引（扬声器）

    # ========== Panel 屏幕配置 ==========
    SCREEN_WIDTH: int = 1920      # 屏幕/窗口宽度（像素）
    SCREEN_HEIGHT: int = 1080     # 屏幕/窗口高度（像素）

    # ========== 帧推送配置 ==========
    FRAME_TARGET_FPS: int = 30
    FRAME_BATCH_SIZE: int = 1           # 每次推送帧数（1=单帧）
    FRAME_BUFFER_SIZE: int = 600        # 环形缓冲区大小

    # ========== 口型延迟补偿 ==========
    LIPSYNC_DELAY_MS: int = 80          # 口型延迟补偿（ms）

    # ========== idle 管理 ==========
    IDLE_LOW_WATER_MARK: int = 5        # 缓冲区低水位阈值（帧数）
    IDLE_TRANSITION_FRAMES: int = 10    # idle 过渡帧数

    # ========== 动作插帧 ==========
    MOTION_TRANSITION_FRAMES: int = 5   # 动作切换过渡帧数
    MOTION_HEAD_OFFSET_FRAMES: int = 5  # 队首预留帧数（避免与正在推送的帧冲突）

    # ========== 日志配置 ==========
    LOG_LEVEL: str = "INFO"
    
    # ========== 关闭超时配置 ==========
    GRACEFUL_SHUTDOWN_TIMEOUT: float = 10.0  # 优雅关闭超时时间（秒）

    # ========== 定时任务调度配置 ==========
    SCHEDULER_ENABLED: bool = True          # 是否启用定时任务调度器

    # ========== Cerebras 缓存配置 ==========
    CACHE_HEARTBEAT_ENABLED: bool = True   # 是否启用心跳保活
    CACHE_HEARTBEAT_INTERVAL: int = 240    # 心跳间隔（秒），默认 4 分钟

    # ========== 投机采样配置 ==========
    SPECULATIVE_SAMPLING_ENABLED: bool = True  # 是否启用投机采样（基于ASR中间结果的并发LLM请求）

    # ========== 打断配置 ==========
    INTERRUPTION_ENABLED: bool = False  # 是否支持打断功能（默认不支持，TTS播放期间自动静音）

    # ========== VMD Upload 配置 ==========
    VMD_UPLOAD_MAX_SIZE: int = 100 * 1024 * 1024  # 100MB
    VMD_VIDEO_MAX_SIZE: int = 500 * 1024 * 1024   # 500MB
    VMD_UPLOAD_TEMP_DIR: str = "/tmp/vmd_uploads"
    VMD_UPLOAD_TTL_HOURS: int = 24

    # ========== 多模态 LLM 配置 (Vision) ==========
    VISION_LLM_API_BASE_URL: str = "http://127.0.0.1:4000/v1"
    VISION_LLM_API_KEY: Optional[str] = None
    VISION_LLM_MODEL: str = "gpt-4o"

    class Config:
        env_file = ".env"
        case_sensitive = True


# 全局配置实例
settings = Settings()