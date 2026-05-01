"""事件总线核心实现

实现发布-订阅模式的事件总线，支持：
- 异步处理器
- 多处理器订阅同一事件
- 处理器优先级（按优先级顺序执行）
- 处理器异常隔离（单个 handler 失败不影响其他）
- 弱引用支持（防止内存泄漏）
- 一次性订阅（只触发一次）
- 同步等待所有处理器完成
"""

import asyncio
import logging
from typing import Callable, Dict, List, Optional, Set, Union, Awaitable, Tuple
from collections import defaultdict
from weakref import WeakMethod, ref
from dataclasses import dataclass

from .events import Event, EventType

logger = logging.getLogger(__name__)

# 处理器类型：可以是同步函数或异步函数
Handler = Union[Callable[[Event], None], Callable[[Event], Awaitable[None]]]

# 默认优先级
DEFAULT_PRIORITY = 100


@dataclass
class HandlerEntry:
    """处理器条目：包含处理器和其优先级"""
    handler: Handler
    priority: int = DEFAULT_PRIORITY
    
    def __lt__(self, other: "HandlerEntry") -> bool:
        """按优先级排序（数值小的优先执行）"""
        return self.priority < other.priority


class EventBus:
    """事件总线
    
    实现发布-订阅模式，支持：
    - 异步处理器
    - 多处理器订阅同一事件
    - 处理器优先级排序
    - 处理器异常隔离
    - 弱引用支持
    - 一次性订阅
    - 等待所有处理器完成
    """
    
    def __init__(self):
        """初始化事件总线"""
        # 普通处理器：EventType -> List[HandlerEntry]
        self._handlers: Dict[EventType, List[HandlerEntry]] = defaultdict(list)
        
        # 一次性处理器：EventType -> List[HandlerEntry]
        self._once_handlers: Dict[EventType, List[HandlerEntry]] = defaultdict(list)
        
        # 弱引用处理器：EventType -> List[Tuple[WeakMethod, priority]]
        self._weak_handlers: Dict[EventType, List[Tuple[WeakMethod, int]]] = defaultdict(list)
        
        # 记录订阅数量，用于调试
        self._subscription_count: Dict[EventType, int] = defaultdict(int)
    
    def subscribe(
        self,
        event_type: EventType,
        handler: Handler,
        priority: int = DEFAULT_PRIORITY,
        weak: bool = False,
    ) -> Handler:
        """订阅事件
        
        Args:
            event_type: 要订阅的事件类型
            handler: 事件处理器（同步或异步函数）
            priority: 优先级（数值小的优先执行，默认 100）
            weak: 是否使用弱引用（用于方法绑定，防止内存泄漏）
            
        Returns:
            返回 handler，便于链式调用或取消订阅
            
        Example:
            # 高优先级（最先执行）
            event_bus.subscribe(EventType.USER_MESSAGE, handler, priority=10)
            
            # 默认优先级
            event_bus.subscribe(EventType.USER_MESSAGE, handler)
            
            # 低优先级（最后执行）
            event_bus.subscribe(EventType.USER_MESSAGE, handler, priority=200)
        """
        if weak:
            # 弱引用只支持绑定方法
            if hasattr(handler, '__self__'):
                weak_handler = WeakMethod(handler)
                self._weak_handlers[event_type].append((weak_handler, priority))
            else:
                # 普通函数不支持弱引用，直接存储
                entry = HandlerEntry(handler=handler, priority=priority)
                self._handlers[event_type].append(entry)
        else:
            entry = HandlerEntry(handler=handler, priority=priority)
            self._handlers[event_type].append(entry)
            # 保持列表有序
            self._handlers[event_type].sort()
        
        self._subscription_count[event_type] += 1
        logger.debug(
            f"订阅事件 {event_type.value}, priority={priority}, "
            f"当前订阅数: {self._subscription_count[event_type]}"
        )
        
        return handler
    
    def subscribe_once(
        self,
        event_type: EventType,
        handler: Handler,
        priority: int = DEFAULT_PRIORITY,
    ) -> Handler:
        """一次性订阅事件
        
        处理器只会被调用一次，之后自动取消订阅。
        
        Args:
            event_type: 要订阅的事件类型
            handler: 事件处理器
            priority: 优先级
            
        Returns:
            返回 handler
        """
        entry = HandlerEntry(handler=handler, priority=priority)
        self._once_handlers[event_type].append(entry)
        # 保持列表有序
        self._once_handlers[event_type].sort()
        
        self._subscription_count[event_type] += 1
        logger.debug(f"一次性订阅事件 {event_type.value}, priority={priority}")
        return handler
    
    def unsubscribe(self, event_type: EventType, handler: Handler) -> bool:
        """取消订阅
        
        Args:
            event_type: 事件类型
            handler: 要取消的处理器
            
        Returns:
            是否成功取消
        """
        removed = False
        
        # 从普通处理器列表中移除
        for entry in self._handlers[event_type]:
            if entry.handler == handler:
                self._handlers[event_type].remove(entry)
                removed = True
                break
        
        # 从一次性处理器列表中移除
        for entry in self._once_handlers[event_type]:
            if entry.handler == handler:
                self._once_handlers[event_type].remove(entry)
                removed = True
                break
        
        if removed:
            self._subscription_count[event_type] -= 1
            logger.debug(f"取消订阅事件 {event_type.value}, 当前订阅数: {self._subscription_count[event_type]}")
        
        return removed
    
    def unsubscribe_all(self, event_type: Optional[EventType] = None) -> int:
        """取消所有订阅
        
        Args:
            event_type: 可选，指定事件类型；不指定则取消所有
            
        Returns:
            取消的订阅数量
        """
        if event_type:
            count = len(self._handlers[event_type]) + len(self._once_handlers[event_type])
            self._handlers[event_type].clear()
            self._once_handlers[event_type].clear()
            self._weak_handlers[event_type].clear()
            self._subscription_count[event_type] = 0
            return count
        else:
            total = sum(self._subscription_count.values())
            self._handlers.clear()
            self._once_handlers.clear()
            self._weak_handlers.clear()
            self._subscription_count.clear()
            return total
    
    async def publish(self, event: Event) -> int:
        """发布事件（异步）
        
        将事件分发给所有订阅了该事件类型的处理器。
        处理器按优先级顺序执行。
        处理器异常会被捕获并记录，不会影响其他处理器。
        
        Args:
            event: 要发布的事件
            
        Returns:
            成功处理的处理器数量
        """
        event_type = event.type
        handled_count = 0
        
        # 获取所有处理器（已按优先级排序）
        handlers = self._get_handlers(event_type)
        
        # 处理一次性订阅（处理后移除）
        once_handlers = list(self._once_handlers[event_type])
        self._once_handlers[event_type].clear()
        
        # 合并处理器并按优先级排序
        all_entries = handlers + once_handlers
        all_entries.sort()
        
        if not all_entries:
            logger.debug(f"事件 {event_type.value} 无处理器")
            return 0
        
        logger.debug(f"发布事件 {event_type.value} 到 {len(all_entries)} 个处理器（按优先级顺序）")
        
        # 按优先级顺序执行处理器
        for entry in all_entries:
            try:
                result = entry.handler(event)
                # 如果是协程，等待完成
                if asyncio.iscoroutine(result):
                    await result
                handled_count += 1
            except Exception as e:
                logger.error(
                    f"事件处理器异常 [{event_type.value}] "
                    f"priority={entry.priority} "
                    f"handler={entry.handler.__name__ if hasattr(entry.handler, '__name__') else entry.handler}: {e}",
                    exc_info=True,
                )
        
        return handled_count
    
    async def publish_and_wait(self, event: Event) -> Tuple[int, List[Exception]]:
        """发布事件并等待所有处理器完成
        
        与 publish 不同，此方法：
        1. 并发执行所有处理器（不按顺序）
        2. 等待所有处理器完成（包括失败的）
        3. 返回所有异常
        
        适用于需要确保所有处理器都执行完毕的场景。
        
        Args:
            event: 要发布的事件
            
        Returns:
            (成功处理的数量, 异常列表)
        """
        event_type = event.type
        
        handlers = self._get_handlers(event_type)
        once_handlers = list(self._once_handlers[event_type])
        self._once_handlers[event_type].clear()
        
        all_entries = handlers + once_handlers
        
        if not all_entries:
            return 0, []
        
        # 并发执行所有处理器
        results = await asyncio.gather(
            *[self._handle_event_safe(entry.handler, event) for entry in all_entries],
            return_exceptions=True,
        )
        
        handled_count = 0
        exceptions = []
        
        for result in results:
            if isinstance(result, Exception):
                exceptions.append(result)
            else:
                handled_count += 1
        
        return handled_count, exceptions
    
    def publish_sync(self, event: Event) -> int:
        """发布事件（同步版本）
        
        注意：同步版本会阻塞，且异步处理器不会被正确执行。
        主要用于测试或在同步上下文中使用。
        
        处理器按优先级顺序执行。
        
        Args:
            event: 要发布的事件
            
        Returns:
            成功处理的处理器数量
        """
        event_type = event.type
        handled_count = 0
        
        handlers = self._get_handlers(event_type)
        once_handlers = list(self._once_handlers[event_type])
        self._once_handlers[event_type].clear()
        
        all_entries = handlers + once_handlers
        all_entries.sort()
        
        for entry in all_entries:
            try:
                result = entry.handler(event)
                # 如果是协程，需要检查是否是异步处理器
                if asyncio.iscoroutine(result):
                    logger.warning(f"同步发布遇到异步处理器，跳过: {entry.handler}")
                    continue
                handled_count += 1
            except Exception as e:
                logger.error(f"事件处理器异常 [{event_type.value}]: {e}")
        
        return handled_count
    
    def _get_handlers(self, event_type: EventType) -> List[HandlerEntry]:
        """获取指定事件类型的所有有效处理器
        
        包括普通处理器和弱引用处理器（过滤已失效的弱引用）。
        返回的列表已按优先级排序。
        """
        handlers = list(self._handlers[event_type])
        
        # 处理弱引用处理器
        valid_weak: List[Tuple[WeakMethod, int]] = []
        for weak_handler, priority in self._weak_handlers[event_type]:
            handler = weak_handler()
            if handler is not None:
                handlers.append(HandlerEntry(handler=handler, priority=priority))
                valid_weak.append((weak_handler, priority))
        
        # 更新弱引用列表（过滤失效的）
        self._weak_handlers[event_type] = valid_weak
        
        # 按优先级排序
        handlers.sort()
        
        return handlers
    
    async def _handle_event_safe(self, handler: Handler, event: Event) -> None:
        """执行单个处理器（安全版本，异常会被捕获）
        
        用于 publish_and_wait 的并发执行。
        """
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(
                f"事件处理器异常 [{event.type.value}] "
                f"handler={handler.__name__ if hasattr(handler, '__name__') else handler}: {e}",
                exc_info=True,
            )
            raise
    
    def has_subscribers(self, event_type: EventType) -> bool:
        """检查是否有订阅者
        
        Args:
            event_type: 事件类型
            
        Returns:
            是否有订阅者
        """
        return bool(
            self._handlers[event_type]
            or self._once_handlers[event_type]
            or self._weak_handlers[event_type]
        )
    
    def get_subscription_count(self, event_type: Optional[EventType] = None) -> int:
        """获取订阅数量
        
        Args:
            event_type: 可选，指定事件类型
            
        Returns:
            订阅数量
        """
        if event_type:
            return self._subscription_count[event_type]
        return sum(self._subscription_count.values())
    
    def list_event_types(self) -> Set[EventType]:
        """列出所有有订阅者的事件类型
        
        Returns:
            事件类型集合
        """
        all_types = set()
        all_types.update(self._handlers.keys())
        all_types.update(self._once_handlers.keys())
        all_types.update(self._weak_handlers.keys())
        return all_types
    
    def get_handler_priorities(self, event_type: EventType) -> List[Tuple[Handler, int]]:
        """获取指定事件类型的处理器及其优先级
        
        用于调试和检查订阅顺序。
        
        Args:
            event_type: 事件类型
            
        Returns:
            [(handler, priority)] 列表，按优先级排序
        """
        handlers = self._get_handlers(event_type)
        return [(entry.handler, entry.priority) for entry in handlers]


# 全局单例
event_bus = EventBus()