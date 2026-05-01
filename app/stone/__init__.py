"""Stone 数据层 - 统一数据访问层

提供：
- PostgreSQL 连接池管理
- Redis 连接池管理
- 统一的 Redis Key 命名规范
- Repository 层数据访问抽象

使用示例：
    # 初始化（在 main.py 中）
    from app.stone import init_database, init_redis_pool
    await init_database()
    await init_redis_pool()

    # 使用 Repository
    from app.stone import get_chat_repo
    chat_repo = get_chat_repo()
    messages = await chat_repo.get_recent(character_id, user_id)
"""

from .database import Database, get_database, init_database, close_database, get_db_session
from .redis_pool import RedisPool, get_redis_pool, init_redis_pool, close_redis_pool
from .key_builder import RedisKeyBuilder, build_key

# Repository 导出
from .repositories import (
    BaseRepository,
    RedisRepository,
    ChatMessageRepository,
    get_chat_repo,
    KeyEventRepository,
    get_key_event_repo,
    HeartbeatEventRepository,
    get_heartbeat_repo,
    DiaryRepository,
    WeeklyIndexRepository,
    MonthlyIndexRepository,
    AnnualIndexRepository,
    get_diary_repo,
    get_weekly_repo,
    get_monthly_repo,
    get_annual_repo,
    EmotionStateRepository,
    get_emotion_repo,
    AffectionRepository,
    get_affection_repo,
    AFFECTION_LEVELS,
    DailyLifeEventRepository,
    get_daily_life_repo,
    BackgroundRepository,
    get_background_repo,
    AgentStateRepository,
    get_agent_state_repo,
    TaskQueueRepository,
    get_task_queue_repo,
    PlaybackQueueRepository,
    get_playback_queue_repo,
    ConversationBufferRepository,
    get_conversation_buffer_repo,
    OpenClawTaskRepository,
    get_openclaw_task_repo,
    # Motion (PostgreSQL)
    MotionRepository,
    TagRepository,
    get_motion_repo,
    get_tag_repo,
)

__all__ = [
    # Database
    "Database",
    "get_database",
    "init_database",
    "close_database",
    "get_db_session",
    # Redis
    "RedisPool",
    "get_redis_pool",
    "init_redis_pool",
    "close_redis_pool",
    # Key Builder
    "RedisKeyBuilder",
    "build_key",
    # Repositories - PostgreSQL
    "BaseRepository",
    "ChatMessageRepository",
    "get_chat_repo",
    "KeyEventRepository",
    "get_key_event_repo",
    "HeartbeatEventRepository",
    "get_heartbeat_repo",
    "DiaryRepository",
    "get_diary_repo",
    "WeeklyIndexRepository",
    "get_weekly_repo",
    "MonthlyIndexRepository",
    "get_monthly_repo",
    "AnnualIndexRepository",
    "get_annual_repo",
    # Repositories - Redis
    "RedisRepository",
    "EmotionStateRepository",
    "get_emotion_repo",
    "AffectionRepository",
    "get_affection_repo",
    "AFFECTION_LEVELS",
    # Repositories - Other
    "DailyLifeEventRepository",
    "get_daily_life_repo",
    "BackgroundRepository",
    "get_background_repo",
    "AgentStateRepository",
    "get_agent_state_repo",
    # TaskQueue (Redis)
    "TaskQueueRepository",
    "get_task_queue_repo",
    # PlaybackQueue (Redis)
    "PlaybackQueueRepository",
    "get_playback_queue_repo",
    # ConversationBuffer (Redis)
    "ConversationBufferRepository",
    "get_conversation_buffer_repo",
    # OpenClawTask (Redis)
    "OpenClawTaskRepository",
    "get_openclaw_task_repo",
    # Motion (PostgreSQL)
    "MotionRepository",
    "TagRepository",
    "get_motion_repo",
    "get_tag_repo",
]
