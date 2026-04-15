"""
任务系统抽象接口 - Provider 抽象基类 + 同步接口

核心设计：
1. TaskSyncInterface: Provider 状态同步接口（双向）
2. TaskProvider: Provider 抽象基类
   - 同步模式 Provider: 有内部队列，通过 sync_interface 同步
   - 非同步模式 Provider: 无队列，TaskSystem 直接 await execute()
"""

from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING
from dataclasses import dataclass

if TYPE_CHECKING:
    from .models import ProviderResult


class TaskSyncInterface(ABC):
    """Provider 状态同步接口（双向）
    
    TaskSystem → Provider (下发):
    - on_task_submitted(): 通知 Provider 有新任务（同步模式）
    - on_task_cancel(): 通知 Provider 取消任务
    
    Provider → TaskSystem (上报):
    - on_task_running(): Provider 上报任务开始执行
    - on_task_result(): Provider 上报任务完成
    - on_task_error(): Provider 上报任务失败
    """
    
    # ===== TaskSystem → Provider (下发) =====
    
    @abstractmethod
    async def on_task_submitted(self, task_id: str, tool_prompt: str, context: dict) -> None:
        """通知 Provider 有新任务
        
        Args:
            task_id: 任务 ID（与主队列一致）
            tool_prompt: LLM 工具调用提示
            context: 任务上下文（可选）
        
        Note:
            同步模式 Provider 需要实现此方法，接收任务入内部队列。
            非同步模式 Provider 不需要实现。
        """
        pass
    
    @abstractmethod
    async def on_task_cancel(self, task_id: str) -> bool:
        """通知 Provider 取消任务
        
        Args:
            task_id: 任务 ID
        
        Returns:
            是否取消成功
        """
        pass
    
    # ===== Provider → TaskSystem (上报) =====
    
    @abstractmethod
    async def on_task_running(self, task_id: str) -> None:
        """Provider 上报：任务开始执行
        
        Note:
            Provider 调用此方法通知 TaskSystem 更新主队列状态。
        """
        pass
    
    @abstractmethod
    async def on_task_result(self, task_id: str, result: dict) -> None:
        """Provider 上报：任务完成
        
        Args:
            task_id: 任务 ID
            result: 原始结果（ProviderResult.to_dict()）
        
        Note:
            Provider 调用此方法通知 TaskSystem 处理结果（二次改写）。
        """
        pass
    
    @abstractmethod
    async def on_task_error(self, task_id: str, error: str) -> None:
        """Provider 上报：任务失败
        
        Args:
            task_id: 任务 ID
            error: 错误信息
        """
        pass


class TaskProvider(ABC):
    """Provider 抽象基类
    
    支持两种模式：
    1. 同步模式 (use_sync_mode=True): 有内部队列，通过 TaskSyncInterface 同步
       - 实现 on_task_submitted() 接收任务
       - 实现 on_task_cancel() 取消任务
       - 通过 _sync_interface 上报状态
    
    2. 非同步模式 (use_sync_mode=False): 无队列，TaskSystem 直接 await execute()
       - 实现 execute() 直接执行任务
       - 返回 ProviderResult
    """
    
    def __init__(self):
        self._sync_interface: Optional[TaskSyncInterface] = None
    
    # ===== 基础属性 =====
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称（用于路由和日志）"""
        pass
    
    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """根据配置判断是否启用"""
        pass
    
    @property
    def use_sync_mode(self) -> bool:
        """是否使用同步模式
        
        - True: Provider 有内部队列，通过 TaskSyncInterface 同步
        - False: Provider 无队列，TaskSystem 直接 await execute()
        
        默认 False，同步模式 Provider 需要覆盖为 True。
        """
        return False
    
    # ===== 同步接口注入 =====
    
    def set_sync_interface(self, interface: TaskSyncInterface):
        """注入同步接口（TaskSystem 调用）
        
        Args:
            interface: TaskSyncInterface 实例
        """
        self._sync_interface = interface
    
    # ===== 生命周期 =====
    
    @abstractmethod
    async def start(self, clear_queue: bool = True) -> None:
        """启动 Provider
        
        Args:
            clear_queue: 是否清空内部队列（由配置决定）
        
        Raises:
            ProviderInitError: 初始化失败（会阻塞应用启动）
        
        Note:
            - 阻塞式初始化，失败会阻塞应用启动
            - WebSocket Provider: 连接 WS + 清空队列 + 初始化 Session
            - HTTP Provider: 初始化 HTTP 客户端（可选）
        """
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """停止 Provider"""
        pass
    
    @abstractmethod
    async def clear_all_tasks(self) -> int:
        """清空所有任务
        
        Returns:
            清空的任务数量
        """
        pass
    
    # ===== 同步模式 Provider 需要实现 =====
    
    async def on_task_submitted(self, task_id: str, tool_prompt: str, context: dict) -> None:
        """接收任务（同步模式 Provider 实现）
        
        Args:
            task_id: 任务 ID
            tool_prompt: 工具调用提示
            context: 任务上下文
        
        Note:
            同步模式 Provider (use_sync_mode=True) 必须实现此方法。
            非同步模式 Provider 不需要实现。
        """
        if self.use_sync_mode:
            raise NotImplementedError(
                f"同步模式 Provider '{self.name}' 必须实现 on_task_submitted()"
            )
    
    async def on_task_cancel(self, task_id: str) -> bool:
        """取消任务（同步模式 Provider 实现）
        
        Args:
            task_id: 任务 ID
        
        Returns:
            是否取消成功
        
        Note:
            同步模式 Provider (use_sync_mode=True) 必须实现此方法。
        """
        if self.use_sync_mode:
            raise NotImplementedError(
                f"同步模式 Provider '{self.name}' 必须实现 on_task_cancel()"
            )
        return False
    
    # ===== 非同步模式 Provider 需要实现 =====
    
    async def execute(
        self, 
        task_id: str, 
        tool_prompt: str, 
        context: dict,
        timeout: float = 60.0
    ) -> "ProviderResult":
        """直接执行任务（非同步模式 Provider 实现）
        
        Args:
            task_id: 任务 ID
            tool_prompt: 工具调用提示
            context: 任务上下文
            timeout: 超时时间
        
        Returns:
            ProviderResult: 原始结果
        
        Raises:
            TimeoutError: 超时
            ProviderError: 执行失败
        
        Note:
            非同步模式 Provider (use_sync_mode=False) 必须实现此方法。
            同步模式 Provider 不需要实现。
        """
        if not self.use_sync_mode:
            raise NotImplementedError(
                f"非同步模式 Provider '{self.name}' 必须实现 execute()"
            )
        # 默认实现（避免类型检查错误）
        from .models import ProviderResult
        return ProviderResult(
            task_id=task_id,
            success=False,
            content="",
            panel_html=None,
            error="Provider 未实现 execute()",
        )
    
    # ===== 状态上报（同步模式 Provider 使用）=====
    
    async def _report_running(self, task_id: str) -> None:
        """上报任务开始执行"""
        if self._sync_interface:
            await self._sync_interface.on_task_running(task_id)
    
    async def _report_result(self, task_id: str, result: dict) -> None:
        """上报任务完成"""
        if self._sync_interface:
            await self._sync_interface.on_task_result(task_id, result)
    
    async def _report_error(self, task_id: str, error: str) -> None:
        """上报任务失败"""
        if self._sync_interface:
            await self._sync_interface.on_task_error(task_id, error)


class ProviderInitError(Exception):
    """Provider 初始化失败"""
    pass


class ProviderError(Exception):
    """Provider 执行失败"""
    pass