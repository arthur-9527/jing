"""应用配置管理"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """应用配置"""

    # 数据库配置 (统一数据库)
    # 不要在代码里硬编码账号密码；请通过 .env / 环境变量提供
    DATABASE_URL: str = "postgresql+asyncpg://postgres@localhost:5432/agent_backend"

    # ========== DashScope 配置（ASR/TTS 共用）==========
    DASHSCOPE_API_KEY: Optional[str] = None

    # ========== Provider 配置 ==========
    # LiteLLM Provider（OpenAI 兼容 API）
    LITELLM_API_BASE_URL: str = "http://127.0.0.1:4000/v1"
    LITELLM_API_KEY: Optional[str] = None
    LITELLM_MODEL: str = "qwen3-chat"

    # Cerebras Provider（Cerebras SDK）
    CEREBRAS_API_BASE_URL: Optional[str] = None
    CEREBRAS_API_KEY: Optional[str] = None
    CEREBRAS_MODEL: str = "llama3.1-8b"

    # ========== 服务 Provider 选择器 ==========
    # Chat 服务（纯粹聊天对话）
    CHAT_PROVIDER: str = "litellm"  # litellm / cerebras
    CHAT_MODEL: Optional[str] = None  # 可选覆盖，None 则用 Provider 默认模型

    # Thinking 服务（需要思考/高精准度，预留）
    THINKING_PROVIDER: str = "litellm"
    THINKING_MODEL: Optional[str] = None

    # Vision 服务（多模态视觉处理）
    VISION_PROVIDER: str = "litellm"
    VISION_API_BASE_URL: Optional[str] = None
    VISION_API_KEY: Optional[str] = None
    VISION_MODEL: Optional[str] = None

    # Vision 视觉能力配置
    # none: 不支持视觉
    # image: 支持图片分析
    # video: 支持视频分析（同时支持图片）
    VISION_MODEL_TYPE: str = "none"

    # ========== 二次改写配置 ==========
    # 二次改写使用 CHAT_PROVIDER + CHAT_MODEL，无需额外配置
    POST_PROCESS_ENABLED: bool = True
    POST_PROCESS_TIMEOUT: float = 60.0

    # ========== 任务系统配置 ==========
    TASK_SYSTEM_ENABLED: bool = True
    
    # ========== OpenClaw Provider 配置 ==========
    OPENCLAW_ENABLED: bool = True
    OPENCLAW_WS_URL: str = "ws://127.0.0.1:18789/gateway"
    OPENCLAW_WS_TOKEN: Optional[str] = None
    OPENCLAW_IDENTITY_FILE: Optional[str] = None
    OPENCLAW_SESSION_KEYS: str = "agent:main:chat1,agent:main:chat2"
    OPENCLAW_TIMEOUT: float = 300.0
    OPENCLAW_CLEAR_QUEUE_ON_START: bool = True

    # ========== Redis 配置 ==========
    REDIS_URL: str = "redis://localhost:6379/1"

    # ========== Embedding 配置 (本地模型) ==========
    # 模型路径必须通过环境变量 LOCAL_EMBEDDING_MODEL_PATH 配置
    LOCAL_EMBEDDING_MODEL_PATH: str = ""  # 必须在 .env 中配置，如：models/embedding 或 /app/models/embedding
    EMBEDDING_DIM: int = 512  # bge-small-zh-v1.5 输出 512 维
    EMBEDDING_CACHE_TTL: int = 300  # 缓存 TTL（秒）
    EMBEDDING_CACHE_MAX: int = 1000  # 缓存最大条目数


    # ========== Agent 配置 ==========
    CHARACTER_CONFIG_PATH: str = "config/characters/daji"  # 角色配置目录路径（不含扩展名）
    CONVERSATION_WINDOW_SIZE: int = 10
    EMOTION_INTENSITY_THRESHOLD: float = 0.3

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
    ASR_PROVIDER: str = "qwen"

    # Qwen ASR
    QWEN_ASR_MODEL: str = "qwen3-asr-flash-realtime"
    QWEN_ASR_LANGUAGE: str = "zh"
    QWEN_ASR_ENABLE_VAD: bool = True
    QWEN_ASR_VAD_THRESHOLD: float = 0.0
    QWEN_ASR_VAD_SILENCE_MS: int = 400

    # ========== TTS Provider 配置 ==========
    TTS_PROVIDER: str = "cosyvoice"

    # CosyVoice TTS
    COSYVOICE_MODEL: str = "cosyvoice-v3-flash"
    # 音色配置（二选一，优先 voice_id）
    COSYVOICE_VOICE_ID: Optional[str] = None   # 直接指定音色 ID（如 "cosyvoice-v3-flash-xxx")
    COSYVOICE_CLONE_AUDIO: Optional[str] = None  # 克隆音频路径（自动转换为 voice_id）

    # ========== 音频配置 ==========
    AUDIO_SAMPLE_RATE: int = 16000
    TTS_SAMPLE_RATE: int = 16000  # TTS 输出采样率（统一使用 16kHz）
    
    # 音频设备索引（可选，默认使用系统默认设备）
    # 设置为 None 或 -1 表示使用系统默认
    # 如需指定特定设备（如 ESP32 USB 麦克风），可通过 .env 配置具体索引
    AUDIO_INPUT_DEVICE_INDEX: Optional[int] = None   # 输入设备索引（麦克风）
    AUDIO_OUTPUT_DEVICE_INDEX: Optional[int] = None  # 输出设备索引（扬声器）

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

    # ========== QQ Bot 配置 ==========
    QQ_BOT_APPID: str = ""  # QQ Bot AppID
    QQ_BOT_SECRET: str = ""  # QQ Bot AppSecret

    # ========== Media Gen (图片/视频生成) 配置 ==========
    # 图片/视频生成功能开关
    IMAGE_VIDEO_GEN_ENABLED: bool = False  # 启用后，系统可以生成图片和视频
    
    # 参考图片路径（用于图生图和图生视频）
    # 路径与克隆声音音频路径一致（如：config/characters/daji/youla.jpg）
    IMAGE_VIDEO_GEN_REFERENCE_IMAGE_PATH: Optional[str] = None

    # ========== Image Gen Provider 配置 ==========
    # 图片生成 Provider（仅支持图生图模式）
    IMAGE_GEN_PROVIDER: str = "dashscope"  # dashscope / stability / openai

    # DashScope 图片生成模型
    # 要求：必须支持图生图能力 + base64 格式输入
    # 推荐值: wanx2.1-t2i-plus（需确认服务商是否支持图生图）
    DASHSCOPE_IMAGE_GEN_MODEL: str = "wanx2.1-t2i-plus"
    DASHSCOPE_IMAGE_GEN_DEFAULT_SIZE: str = "1024*1024"  # 默认图片尺寸
    DASHSCOPE_IMAGE_GEN_DEFAULT_NUM: int = 1              # 默认生成数量

    # ========== Video Gen Provider 配置 ==========
    # 视频生成 Provider（仅支持图生视频模式）
    VIDEO_GEN_PROVIDER: str = "dashscope"  # dashscope / runway / pika

    # DashScope 视频生成模型
    # 要求：必须支持图生视频能力 + base64 格式输入
    # 推荐值: wanx2.1-i2v-plus / wanx2.1-i2v-turbo
    DASHSCOPE_VIDEO_GEN_MODEL: str = "wanx2.1-i2v-plus"
    DASHSCOPE_VIDEO_GEN_RESOLUTION: str = "720p"  # 默认分辨率
    DASHSCOPE_VIDEO_GEN_DURATION: float = 5.0     # 默认视频时长（秒）

    # ========== 阿里云 OSS 配置 ==========
    # OSS 存储功能开关
    OSS_ENABLED: bool = False

    # OSS 访问凭证
    OSS_ACCESS_KEY_ID: Optional[str] = None
    OSS_ACCESS_KEY_SECRET: Optional[str] = None

    # OSS Endpoint（如：oss-cn-hangzhou.aliyuncs.com）
    OSS_ENDPOINT: Optional[str] = None

    # OSS Bucket 名称
    OSS_BUCKET_NAME: Optional[str] = None

    # OSS 生命周期天数（文件自动清理）
    OSS_LIFECYCLE_DAYS: int = 7

    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "ignore",  # 忽略 .env 中旧的/未定义的字段
    }


# 全局配置实例
settings = Settings()