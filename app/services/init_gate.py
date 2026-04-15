#!/usr/bin/env python3
"""
初始化门控 - 等待所有关键组件就绪

核心思路：
1. 单一入口：一个全局的 InitializationGate 管理所有组件的就绪状态
2. 事件驱动：使用 asyncio.Event 原生机制，无需轮询
3. 声明式注册：各组件只需注册自己，初始化完成时通知门控
4. 最小改动：改动集中在几个关键点，不影响现有架构

使用方式：
1. 在 agent_service.initialize() 中注册所有需要等待的组件
2. 在各组件初始化完成时调用 mark_ready()
3. 在 agent_service.start() 中调用 wait_all() 等待所有组件就绪
"""

import asyncio
from typing import Optional

from loguru import logger


class InitializationGate:
    """初始化门控 - 等待所有关键组件就绪
    
    使用 asyncio.Event 实现事件驱动的等待机制，
    避免 polling，真正异步等待。
    """
    
    def __init__(self):
        self._components: dict[str, asyncio.Event] = {}
        self._registered_names: list[str] = []  # 保持注册顺序
        self._lock = asyncio.Lock()
    
    def register(self, name: str) -> asyncio.Event:
        """注册组件，返回其就绪事件
        
        Args:
            name: 组件名称，如 "pipeline", "llm_agent", "openclaw", "tts"
            
        Returns:
            asyncio.Event: 该组件的就绪事件，可用于手动等待
        """
        if name not in self._components:
            self._components[name] = asyncio.Event()
            self._registered_names.append(name)
            logger.debug(f"[InitGate] 组件注册: {name}")
        return self._components[name]
    
    def mark_ready(self, name: str):
        """标记组件就绪
        
        Args:
            name: 组件名称
        """
        if name in self._components:
            self._components[name].set()
            ready_count = sum(1 for e in self._components.values() if e.is_set())
            total_count = len(self._components)
            logger.info(f"[InitGate] 组件就绪: {name} ({ready_count}/{total_count})")
        else:
            logger.warning(f"[InitGate] 未注册的组件尝试标记就绪: {name}")
    
    def is_ready(self, name: str) -> bool:
        """检查组件是否就绪
        
        Args:
            name: 组件名称
            
        Returns:
            bool: 是否就绪
        """
        event = self._components.get(name)
        return event is not None and event.is_set()
    
    async def wait_all(self, timeout: float = 30.0) -> bool:
        """等待所有注册组件就绪
        
        Args:
            timeout: 超时时间（秒）
            
        Returns:
            bool: 是否所有组件都就绪
        """
        if not self._components:
            logger.warning("[InitGate] 没有注册任何组件，直接返回")
            return True
        
        names = list(self._components.keys())
        logger.info(f"[InitGate] 等待组件就绪: {names}")
        
        # 打印当前状态
        for name in names:
            status = "✓" if self._components[name].is_set() else "⏳"
            logger.info(f"[InitGate]   {name}: {status}")
        
        try:
            events = [self._components[n] for n in names]
            await asyncio.wait_for(
                asyncio.gather(*[e.wait() for e in events]),
                timeout=timeout
            )
            logger.info(f"[InitGate] ✓ 所有组件已就绪")
            return True
        except asyncio.TimeoutError:
            not_ready = [n for n in names if not self._components[n].is_set()]
            logger.warning(f"[InitGate] ✗ 超时 ({timeout}s)，未就绪: {not_ready}")
            return False
    
    async def wait_for(self, name: str, timeout: float = 10.0) -> bool:
        """等待单个组件就绪
        
        Args:
            name: 组件名称
            timeout: 超时时间（秒）
            
        Returns:
            bool: 是否就绪
        """
        if name not in self._components:
            logger.warning(f"[InitGate] 未注册的组件: {name}")
            return False
        
        try:
            await asyncio.wait_for(
                self._components[name].wait(),
                timeout=timeout
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(f"[InitGate] 等待 {name} 超时")
            return False
    
    def reset(self):
        """重置所有组件状态（用于测试或重新初始化）"""
        for event in self._components.values():
            event.clear()
        logger.info("[InitGate] 所有组件状态已重置")
    
    def get_status(self) -> dict:
        """获取所有组件的状态
        
        Returns:
            dict: 组件状态字典
        """
        return {
            name: event.is_set()
            for name, event in self._components.items()
        }
    
    def is_all_ready(self) -> bool:
        """检查所有注册组件是否都已就绪
        
        Returns:
            bool: 所有组件是否都就绪
        """
        if not self._components:
            return True
        return all(event.is_set() for event in self._components.values())


# 全局实例
_gate: Optional[InitializationGate] = None


def get_init_gate() -> InitializationGate:
    """获取全局初始化门控实例"""
    global _gate
    if _gate is None:
        _gate = InitializationGate()
        logger.info("[InitGate] 全局实例已创建")
    return _gate


def reset_init_gate():
    """重置全局实例（用于测试）"""
    global _gate
    if _gate is not None:
        _gate.reset()