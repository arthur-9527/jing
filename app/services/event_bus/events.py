"""事件类型定义

定义系统中所有的事件类型，用于 Agent 核心与各子系统之间的通信。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
from datetime import datetime


class EventType(str, Enum):
    """事件类型枚举
    
    定义系统中所有可能的事件类型，确保类型安全。
    """
    
    # 用户交互事件
    USER_MESSAGE = "user_message"           # 用户发送消息
    AGENT_REPLY = "agent_reply"             # Agent 回复消息
    
    # Agent 内部事件
    ACTION_DECIDED = "action_decided"       # Agent 决定执行动作
    INTERACTION_COMPLETE = "interaction_complete"  # 一次交互完成
    
    # 子系统更新事件
    MEMORY_STORE = "memory_store"           # 存储记忆
    MEMORY_RETRIEVE = "memory_retrieve"     # 检索记忆
    MEMORY_CONSOLIDATE = "memory_consolidate"  # 记忆整合

    MOTION_EXECUTE = "motion_execute"       # 执行动作
    MOTION_COMPLETE = "motion_complete"     # 动作执行完成
    
    # 系统事件
    HEARTBEAT = "heartbeat"                 # Agent 心跳
    STATE_CHANGE = "state_change"           # 状态变更
    ERROR = "error"                         # 错误事件


@dataclass
class Event:
    """事件数据类
    
    封装事件的所有信息，包括类型、数据、来源和时间戳。
    """
    
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    
    # 可选的事件 ID，用于追踪和关联
    event_id: Optional[str] = None
    
    # 可选的关联事件 ID，用于建立事件链
    parent_event_id: Optional[str] = None
    
    def __post_init__(self):
        """确保 timestamp 是 datetime 对象"""
        if not isinstance(self.timestamp, datetime):
            self.timestamp = datetime.now()
    
    @classmethod
    def create(
        cls,
        event_type: EventType,
        data: Optional[Dict[str, Any]] = None,
        source: str = "",
        event_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
    ) -> "Event":
        """便捷创建事件的方法
        
        Args:
            event_type: 事件类型
            data: 事件数据
            source: 事件来源
            event_id: 事件 ID
            parent_event_id: 关联的父事件 ID
            
        Returns:
            Event 实例
        """
        return cls(
            type=event_type,
            data=data or {},
            source=source,
            event_id=event_id,
            parent_event_id=parent_event_id,
        )
    
    def __repr__(self) -> str:
        return f"Event(type={self.type.value}, source={self.source}, data={self.data})"