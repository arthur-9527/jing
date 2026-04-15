"""
任务系统管理器 - 统一的任务管理与分发

核心设计：
1. 主队列管理（TaskRepository）
2. Provider 协调（同步/非同步模式）
3. 二次改写（PostProcessor）
4. 阻塞初始化 + 门控集成
5. 播报入队

使用方式：
    task_system = get_task_system()
    await task_system.start()  # 阻塞初始化
    
    task_id = await task_system.submit(tool_prompt, provider_name="openclaw")
    broadcast = await task_system.wait_for_broadcast(task_id)
"""

import asyncio
from typing import Optional, Dict, Any, List

from loguru import logger

from .base import TaskProvider, TaskSyncInterface
from .models import Task, TaskStatus, ProviderResult, BroadcastContent
from .redis_repo import TaskRepository, get_task_repository
from .post_processor import PostProcessor, get_post_processor
from .config import get_task_system_settings
from .providers.openclaw_provider import OpenClawProvider


class InternalSyncInterface(TaskSyncInterface):
    """内部同步接口实现
    
    Provider → TaskSystem 的状态上报
    """
    
    def __init__(self, task_system: "TaskSystem"):
        self._task_system = task_system
    
    async def on_task_submitted(
        self, 
        task_id: str, 
        tool_prompt: str, 
        context: dict
    ) -> None:
        """TaskSystem → Provider（下发）"""
        # 由 Provider 接收，此处不需要实现
        pass
    
    async def on_task_cancel(self, task_id: str) -> bool:
        """TaskSystem → Provider（下发）"""
        # 由 Provider 处理，此处不需要实现
        return False
    
    async def on_task_running(self, task_id: str) -> None:
        """Provider → TaskSystem（上报）：任务开始执行"""
        await self._task_system._repository.update_status(task_id, TaskStatus.RUNNING)
        logger.debug(f"[TaskSystem] Provider 上报 RUNNING: {task_id[:8]}...")
    
    async def on_task_result(self, task_id: str, result: dict) -> None:
        """Provider → TaskSystem（上报）：任务完成
        
        流程：
        1. 更新状态为 PROVIDER_DONE
        2. 存储原始结果
        3. 触发二次改写
        4. 入播报队列
        """
        await self._task_system._handle_provider_result(task_id, result)
    
    async def on_task_error(self, task_id: str, error: str) -> None:
        """Provider → TaskSystem（上报）：任务失败
        
        ⭐ 失败时也产生播报（通过 LLM 二次改写错误信息）
        """
        await self._task_system._repository.update_status(
            task_id, 
            TaskStatus.FAILED, 
            error=error
        )
        
        # ⭐ 将错误作为 ProviderResult 传给二次改写流程
        error_result = ProviderResult(
            task_id=task_id,
            success=False,
            content="",  # 失败时无正常内容
            panel_html=None,
            error=error,
        )
        
        # 调用现有的处理流程（二次改写）
        await self._task_system._handle_provider_result(task_id, error_result.to_dict())
        
        logger.warning(f"[TaskSystem] Provider 上报 FAILED: {task_id[:8]}..., error={error}")


class TaskSystem:
    """任务系统管理器
    
    核心职责：
    1. 主队列管理（创建任务、状态追踪）
    2. Provider 协调（分发任务、同步状态）
    3. 二次改写（处理 Provider 结果）
    4. 播报入队（输出到 PlaybackScheduler）
    
    初始化流程（阻塞）：
    1. 清空主队列
    2. 初始化 Provider（WS 连接 + 清空内部队列）
    3. 初始化 PostProcessor
    4. 注册门控就绪
    """
    
    def __init__(self):
        self._settings = get_task_system_settings()
        
        # 核心组件
        self._repository: Optional[TaskRepository] = None
        self._post_processor: Optional[PostProcessor] = None
        self._sync_interface: Optional[InternalSyncInterface] = None
        
        # Provider 注册表
        self._providers: Dict[str, TaskProvider] = {}
        
        # 播报队列（PlaybackScheduler 的 Redis repo）
        self._playback_repo = None
        
        # 运行状态
        self._running = False
        
        # 结果等待器：task_id → Future
        self._result_waiters: Dict[str, asyncio.Future] = {}
    
    # ===== 生命周期 =====
    
    async def start(self) -> None:
        """启动任务系统
        
        ⭐ 阻塞式初始化：
        1. 清空主队列
        2. 初始化所有启用的 Provider
        3. 初始化 PostProcessor
        4. 注册门控就绪
        
        Raises:
            RuntimeError: 初始化失败（阻塞应用启动）
        """
        if self._running:
            logger.warning("[TaskSystem] 已在运行中，跳过启动")
            return
        
        # 检查总开关
        if not self._settings.TASK_SYSTEM_ENABLED:
            logger.info("[TaskSystem] 任务系统已禁用，跳过初始化")
            # 即使禁用也要标记门控就绪
            self._mark_init_gate_ready()
            return
        
        logger.info("[TaskSystem] 启动中...")
        
        try:
            # Step 1: 初始化主队列 Redis
            await self._init_repository()
            
            # Step 2: 清空主队列
            cleared_count = await self._repository.clear_all_on_start()
            logger.warning(
                f"[TaskSystem] 启动清理：清空主队列 {cleared_count} 个遗留任务"
            )
            
            # Step 3: 初始化同步接口
            self._sync_interface = InternalSyncInterface(self)
            
            # Step 4: 注册并初始化 Provider
            await self._init_providers()
            
            # Step 5: 初始化 PostProcessor
            await self._init_post_processor()
            
            # Step 6: 初始化播报队列连接
            await self._init_playback_repo()
            
            # Step 7: 标记门控就绪
            self._mark_init_gate_ready()
            
            self._running = True
            logger.info("[TaskSystem] 启动完成")
            
        except Exception as e:
            logger.error(f"[TaskSystem] 启动失败: {e}")
            # 标记门控就绪（避免阻塞其他组件）
            self._mark_init_gate_ready()
            raise RuntimeError(f"任务系统初始化失败: {e}")
    
    async def _init_repository(self) -> None:
        """初始化主队列 Redis"""
        self._repository = await get_task_repository()
        logger.info("[TaskSystem] 主队列 Redis 已连接")
    
    async def _init_providers(self) -> None:
        """注册并初始化 Provider
        
        ⭐ Provider 初始化失败不阻止系统启动，只是标记 Provider 为不可用
        """
        # 注册 OpenClaw Provider（同步模式）
        if self._settings.OPENCLAW_ENABLED:
            openclaw_provider = OpenClawProvider()
            openclaw_provider.set_sync_interface(self._sync_interface)
            self._providers["openclaw"] = openclaw_provider
            
            try:
                clear_queue = self._settings.OPENCLAW_CLEAR_QUEUE_ON_START
                await openclaw_provider.start(clear_queue=clear_queue)
                logger.info("[TaskSystem] OpenClaw Provider 已启动")
            except Exception as e:
                # ⭐ 不抛异常，只记录错误，Provider 标记为 disabled
                logger.error(f"[TaskSystem] OpenClaw Provider 启动失败: {e}")
                openclaw_provider._enabled = False
                openclaw_provider._start_error = str(e)
                logger.warning("[TaskSystem] OpenClaw Provider 标记为不可用")
        
        # 未来可添加其他 Provider
        # if self._settings.HTTP_PROVIDER_ENABLED:
        #     http_provider = HTTPProvider()
        #     self._providers["http"] = http_provider
        #     await http_provider.start()
    
    async def _init_post_processor(self) -> None:
        """初始化二次改写模块"""
        if self._settings.POST_PROCESS_ENABLED:
            self._post_processor = get_post_processor()
            await self._post_processor.initialize()
            logger.info("[TaskSystem] PostProcessor 已初始化")
        else:
            logger.info("[TaskSystem] 二次改写已禁用")
    
    async def _init_playback_repo(self) -> None:
        """初始化播报队列连接"""
        try:
            from app.services.playback.redis_repo import get_playback_repository
            self._playback_repo = await get_playback_repository()
            logger.info("[TaskSystem] 播报队列 Redis 已连接")
        except Exception as e:
            logger.warning(f"[TaskSystem] 播报队列连接失败: {e}")
            # 不阻塞启动，播报队列可选
    
    def _mark_init_gate_ready(self) -> None:
        """标记初始化门控就绪"""
        try:
            from app.services.init_gate import get_init_gate
            get_init_gate().mark_ready("task_system")
            logger.info("[TaskSystem] 门控已标记就绪")
        except Exception as e:
            logger.warning(f"[TaskSystem] 门控标记失败: {e}")
    
    async def stop(self) -> None:
        """停止任务系统"""
        if not self._running:
            return
        
        logger.info("[TaskSystem] 停止中...")
        
        self._running = False
        
        # 停止所有 Provider
        for provider in self._providers.values():
            try:
                await provider.stop()
                logger.info(f"[TaskSystem] Provider '{provider.name}' 已停止")
            except Exception as e:
                logger.warning(f"[TaskSystem] Provider '{provider.name}' 停止失败: {e}")
        
        # 关闭 PostProcessor
        if self._post_processor:
            await self._post_processor.close()
        
        # 断开 Redis
        if self._repository:
            await self._repository.disconnect()
        
        # 清空等待器
        for future in self._result_waiters.values():
            if not future.done():
                future.set_exception(RuntimeError("任务系统已停止"))
        self._result_waiters.clear()
        
        logger.info("[TaskSystem] 已停止")
    
    # ===== 任务提交 =====
    
    async def submit(
        self,
        tool_prompt: str,
        provider_name: str = "openclaw",
        context: Dict[str, Any] = None,
    ) -> str:
        """提交任务
        
        ⭐ Provider 不存在/未启用时也产生播报（LLM 改写错误）
        
        Args:
            tool_prompt: LLM 工具调用提示
            provider_name: Provider 名称（默认 openclaw）
            context: 任务上下文（用于二次改写）
        
        Returns:
            任务 ID（即使失败也返回，会自动产生错误播报）
        """
        if not self._running:
            raise RuntimeError("任务系统未启动")
        
        # 检查 Provider
        provider = self._providers.get(provider_name)
        if not provider or not provider.is_enabled:
            # ⭐ Provider 不存在/未启用，创建任务并产生错误播报
            error_msg = f"Provider '{provider_name}' 不存在或未启用"
            
            # 创建任务
            task_id = await self._repository.create_task(
                tool_prompt=tool_prompt,
                provider_name=provider_name,
                context=context or {},
            )
            
            # 直接标记失败并产生播报
            await self._repository.update_status(task_id, TaskStatus.FAILED, error=error_msg)
            
            # 创建错误结果，走二次改写流程
            error_result = ProviderResult(
                task_id=task_id,
                success=False,
                content="",
                panel_html=None,
                error=error_msg,
            )
            
            # 调用二次改写流程
            await self._handle_provider_result(task_id, error_result.to_dict())
            
            logger.warning(
                f"[TaskSystem] Provider 不存在，产生错误播报: "
                f"{task_id[:8]}..., provider={provider_name}"
            )
            
            return task_id
        
        # 创建主队列任务
        task_id = await self._repository.create_task(
            tool_prompt=tool_prompt,
            provider_name=provider_name,
            context=context or {},
        )
        
        # 更新状态为 SUBMITTED
        await self._repository.update_status(task_id, TaskStatus.SUBMITTED)
        
        # 创建结果等待器
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._result_waiters[task_id] = future
        
        # 分发任务
        if provider.use_sync_mode:
            # ⭐ 同步模式：通知 Provider 接收任务
            await provider.on_task_submitted(task_id, tool_prompt, context or {})
        else:
            # ⭐ 非同步模式：直接 await 执行
            await self._repository.update_status(task_id, TaskStatus.RUNNING)
            result = await provider.execute(
                task_id, 
                tool_prompt, 
                context or {},
                timeout=self._settings.OPENCLAW_TIMEOUT
            )
            await self._handle_provider_result(task_id, result.to_dict())
        
        logger.info(
            f"[TaskSystem] 任务已提交: {task_id[:8]}... → "
            f"provider={provider_name}"
        )
        
        return task_id
    
    async def wait_for_broadcast(
        self,
        task_id: str,
        timeout: float = 60.0,
    ) -> BroadcastContent:
        """等待任务完成，返回播报内容
        
        Args:
            task_id: 任务 ID
            timeout: 超时时间
        
        Returns:
            BroadcastContent: 播报内容
        
        Raises:
            TimeoutError: 超时
            RuntimeError: 任务失败
        """
        future = self._result_waiters.get(task_id)
        if not future:
            # 检查任务是否已完成
            task = await self._repository.get_task(task_id)
            if task and task.status == TaskStatus.COMPLETED:
                return BroadcastContent.from_dict(task.broadcast_content)
            raise ValueError(f"任务不存在: {task_id}")
        
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return BroadcastContent.from_dict(result)
        except asyncio.TimeoutError:
            logger.warning(f"[TaskSystem] 等待超时: {task_id[:8]}...")
            raise TimeoutError(f"任务等待超时: {task_id}")
        finally:
            self._result_waiters.pop(task_id, None)
    
    async def cancel(self, task_id: str) -> bool:
        """取消任务
        
        Args:
            task_id: 任务 ID
        
        Returns:
            是否取消成功
        """
        task = await self._repository.get_task(task_id)
        if not task:
            return False
        
        # 更新状态
        await self._repository.update_status(task_id, TaskStatus.CANCELLED)
        
        # 通知 Provider
        provider = self._providers.get(task.provider_name)
        if provider and provider.use_sync_mode:
            await provider.on_task_cancel(task_id)
        
        # 清理等待器
        future = self._result_waiters.pop(task_id, None)
        if future and not future.done():
            future.set_exception(RuntimeError("任务已取消"))
        
        logger.info(f"[TaskSystem] 任务已取消: {task_id[:8]}...")
        return True
    
    # ===== 结果处理 =====
    
    async def _handle_provider_result(
        self,
        task_id: str,
        result: dict,
    ) -> None:
        """处理 Provider 返回的结果
        
        流程：
        1. 更新状态为 PROVIDER_DONE
        2. 存储原始结果
        3. 二次改写
        4. 入播报队列
        5. 完成 Future
        """
        # Step 1: 更新状态
        await self._repository.update_status(task_id, TaskStatus.PROVIDER_DONE)
        await self._repository.update_provider_result(task_id, result)
        
        # Step 2: 获取任务上下文
        task = await self._repository.get_task(task_id)
        if not task:
            logger.warning(f"[TaskSystem] 任务不存在: {task_id[:8]}...")
            return
        
        # Step 3: 二次改写
        await self._repository.update_status(task_id, TaskStatus.POST_PROCESSING)
        
        provider_result = ProviderResult.from_dict(result)
        provider_result.task_id = task_id
        
        if self._post_processor:
            broadcast = await self._post_processor.process(provider_result, task)
        else:
            # 二次改写禁用，直接使用原始结果
            broadcast = BroadcastContent(
                task_id=task_id,
                content=provider_result.content,
                panel_html=provider_result.panel_html,
                action=None,
            )
        
        # Step 4: 存储播报内容
        await self._repository.update_broadcast_content(task_id, broadcast.to_dict())
        
        # Step 5: 入播报队列
        await self._enqueue_broadcast(broadcast)
        
        # Step 6: 完成 Future
        future = self._result_waiters.get(task_id)
        if future and not future.done():
            future.set_result(broadcast.to_dict())
        
        logger.info(
            f"[TaskSystem] 任务完成: {task_id[:8]}..., "
            f"content={broadcast.content[:40]}..."
        )
    
    async def _enqueue_broadcast(self, broadcast: BroadcastContent) -> None:
        """入播报队列
        
        将播报内容推送到 PlaybackScheduler 的 Redis 队列
        """
        if self._playback_repo:
            try:
                from app.services.playback.models import PlaybackTask
                
                # ⭐ 创建 PlaybackTask 对象
                task = PlaybackTask(
                    id=broadcast.task_id,
                    content=broadcast.content,
                    panel_html=broadcast.panel_html,
                    action=broadcast.action,
                )
                
                await self._playback_repo.enqueue(task)
                logger.info(
                    f"[TaskSystem] 播报已入队: task_id={broadcast.task_id[:8]}, "
                    f"content={broadcast.content[:30]}..."
                )
            except Exception as e:
                logger.error(f"[TaskSystem] 播报入队失败: {e}")
        else:
            logger.warning("[TaskSystem] 播报队列未初始化，无法入队")
    
    # ===== Provider 注册 =====
    
    def register_provider(self, provider: TaskProvider) -> None:
        """注册 Provider
        
        Args:
            provider: Provider 实例
        """
        provider.set_sync_interface(self._sync_interface)
        self._providers[provider.name] = provider
        logger.info(f"[TaskSystem] Provider 已注册: {provider.name}")
    
    def get_provider(self, name: str) -> Optional[TaskProvider]:
        """获取 Provider"""
        return self._providers.get(name)
    
    # ===== 状态查询 =====
    
    async def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        return await self._repository.get_task(task_id)
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = {
            "running": self._running,
            "settings": {
                "task_system_enabled": self._settings.TASK_SYSTEM_ENABLED,
                "openclaw_enabled": self._settings.OPENCLAW_ENABLED,
                "post_process_enabled": self._settings.POST_PROCESS_ENABLED,
            },
            "repository": await self._repository.get_stats() if self._repository else {},
            "providers": {},
            "result_waiters": len(self._result_waiters),
        }
        
        # Provider 统计
        for name, provider in self._providers.items():
            if hasattr(provider, "get_stats"):
                stats["providers"][name] = await provider.get_stats()
            else:
                stats["providers"][name] = {
                    "enabled": provider.is_enabled,
                    "sync_mode": provider.use_sync_mode,
                }
        
        return stats


# ===== 全局实例（懒加载）=====
_task_system: Optional[TaskSystem] = None


def get_task_system() -> TaskSystem:
    """获取任务系统实例"""
    global _task_system
    if _task_system is None:
        _task_system = TaskSystem()
    return _task_system


def set_task_system(task_system: TaskSystem) -> None:
    """手动设置任务系统实例（用于测试）"""
    global _task_system
    _task_system = task_system