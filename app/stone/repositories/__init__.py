"""Stone Repositories - 数据访问层

Repository 层负责：
- 封装数据库 CRUD 操作
- 封装 Redis 操作
- 提供统一的数据访问接口

设计原则：
- 每个 Repository 只负责一个数据域
- 不包含业务逻辑
- 统一的错误处理和日志
"""

from .base import BaseRepository, RedisRepository
from .chat import ChatMessageRepository, get_chat_repo
from .key_event import KeyEventRepository, get_key_event_repo
from .heartbeat import HeartbeatEventRepository, get_heartbeat_repo
from .diary import (
    DiaryRepository,
    WeeklyIndexRepository,
    MonthlyIndexRepository,
    AnnualIndexRepository,
    get_diary_repo,
    get_weekly_repo,
    get_monthly_repo,
    get_annual_repo,
)
from .emotion_redis import EmotionStateRepository, get_emotion_repo
from .affection_redis import AffectionRepository, get_affection_repo, AFFECTION_LEVELS, AffectionDimension
from .daily_life import DailyLifeEventRepository, get_daily_life_repo
from .background import BackgroundRepository, get_background_repo
from .agent_state import AgentStateRepository, get_agent_state_repo
from .task_queue import TaskQueueRepository, get_task_queue_repo
from .playback_queue import PlaybackQueueRepository, get_playback_queue_repo
from .conversation_buffer import ConversationBufferRepository, get_conversation_buffer_repo
from .openclaw_task import OpenClawTaskRepository, get_openclaw_task_repo
from .motion import MotionRepository, TagRepository, get_motion_repo, get_tag_repo

__all__ = [
    # Base
    "BaseRepository",
    "RedisRepository",
    # Chat
    "ChatMessageRepository",
    "get_chat_repo",
    # KeyEvent
    "KeyEventRepository",
    "get_key_event_repo",
    # Heartbeat
    "HeartbeatEventRepository",
    "get_heartbeat_repo",
    # Diary
    "DiaryRepository",
    "WeeklyIndexRepository",
    "MonthlyIndexRepository",
    "AnnualIndexRepository",
    "get_diary_repo",
    "get_weekly_repo",
    "get_monthly_repo",
    "get_annual_repo",
    # Emotion (Redis)
    "EmotionStateRepository",
    "get_emotion_repo",
    # Affection (Redis)
    "AffectionRepository",
    "get_affection_repo",
    "AFFECTION_LEVELS",
    "AffectionDimension",
    # DailyLife
    "DailyLifeEventRepository",
    "get_daily_life_repo",
    # Background
    "BackgroundRepository",
    "get_background_repo",
    # AgentState
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
