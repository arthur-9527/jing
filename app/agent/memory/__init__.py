"""记忆系统模块

主要接口：
- get_memory_extractor: 获取记忆提取器
- get_memory_scheduler: 获取定时任务调度器
- retrieve_memories: 检索记忆
- retrieve_long_term_memories: 长期记忆检索（简化版）
- should_trigger_long_term_recall: 判断是否需要长期记忆检索
- extract_time_anchor: 提取时间锚点
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
from app.agent.memory.long_term_retrieval import (
    retrieve_long_term_memories,
    should_trigger_long_term_recall,
    extract_time_anchor,
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
    # 长期记忆检索
    "retrieve_long_term_memories",
    "should_trigger_long_term_recall",
    "extract_time_anchor",
]
