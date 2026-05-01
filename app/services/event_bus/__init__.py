"""Event Bus - 发布-订阅模式的事件总线系统

用于 Agent 核心与各子系统（记忆、情绪、好感度、动作）之间的解耦通信。
"""

from .bus import EventBus, event_bus
from .events import Event, EventType

__all__ = ["EventBus", "event_bus", "Event", "EventType"]