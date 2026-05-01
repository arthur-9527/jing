"""记忆系统模块

主要接口：
- get_memory_extractor: 获取记忆提取器
- get_memory_scheduler: 获取定时任务调度器
- retrieve_memories: 检索记忆
- retrieve_long_term_memories_deep: 长期记忆深度检索（Deep Path）
"""

from app.agent.memory.models import (
    EventType,
    HeartbeatNode,
    HeartbeatSubtype,
    EVENT_TYPE_DESCRIPTIONS,
    HEARTBEAT_NODE_DESCRIPTIONS,
)
from app.agent.memory.extractor import (
    MemoryExtractor,
    get_memory_extractor,
    reset_memory_extractor,
)
from app.agent.memory.scheduler import (
    MemoryScheduler,
    get_memory_scheduler,
    start_memory_scheduler,
    stop_memory_scheduler,
)
from app.agent.memory.retriever import retrieve_memories
from app.agent.memory.long_term_deep import (
    retrieve_long_term_memories_deep,
    trim_by_score_and_chars,
    LongTermMemoryResult,
    MIN_CHARS,
    MAX_CHARS,
    CONFIDENCE_THRESHOLD,
)

__all__ = [
    # 事件类型
    "EventType",
    "HeartbeatNode",
    "HeartbeatSubtype",
    "EVENT_TYPE_DESCRIPTIONS",
    "HEARTBEAT_NODE_DESCRIPTIONS",
    # 提取器
    "MemoryExtractor",
    "get_memory_extractor",
    "reset_memory_extractor",
    # 调度器
    "MemoryScheduler",
    "get_memory_scheduler",
    "start_memory_scheduler",
    "stop_memory_scheduler",
    # 检索
    "retrieve_memories",
    # 长期记忆深度检索（Deep Path）
    "retrieve_long_term_memories_deep",
    "trim_by_score_and_chars",
    "LongTermMemoryResult",
    "MIN_CHARS",
    "MAX_CHARS",
    "CONFIDENCE_THRESHOLD",
]
