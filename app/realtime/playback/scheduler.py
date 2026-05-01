"""
播报调度器 - APScheduler 驱动

核心设计：
1. APScheduler 每秒检查一次系统状态和播报队列
2. 如果 IDLE 且队列有任务，执行播报
3. 打断时丢弃当前任务，恢复 IDLE 后继续下一个

与 AgentService 解耦：
- 不依赖内存队列
- 不依赖事件触发
- APScheduler 定时轮询机制

⭐ 迁移到 APScheduler：
- 定时检查由 APScheduler 任务驱动（AgentService 注册）
- PlaybackScheduler 提供被动回调接口：on_check_trigger()
- 只有在 INITING → IDLE 转换后才启动 APScheduler 任务
"""

import time
from typing import Optional, TYPE_CHECKING
from loguru import logger

from .redis_repo import PlaybackQueueRepository
from .models import PlaybackTask

if TYPE_CHECKING:
    from app.realtime.state_manager import StateManagerProcessor, AgentState
    from app.realtime.agent_service import AgentService


class PlaybackScheduler:
    """播报调度器
    
    ⭐ APScheduler 驱动模式：
    - 定时检查由外部 APScheduler 任务调用 on_check_trigger()
    - 只有在系统处于 IDLE 状态且队列有任务时才执行播报
    - APScheduler 任务由 AgentService 在 INITING → IDLE 转换后注册
    
    工作流程：
    1. 启动时清空 Redis 队列（重启清理）
    2. APScheduler 每秒调用 on_check_trigger()
    3. 检查：is_idle && queue > 0
    4. 如果满足条件，Pop 任务并执行播报
    5. 打断时丢弃当前任务（不入回队列）
    
    Attributes:
        _state_manager: 状态管理器，用于检查 is_idle
        _agent_service: Agent 服务，用于执行播报
        _redis_repo: Redis 仓库，用于队列操作
        _current_task_id: 当前正在播报的任务 ID
    """
    
    def __init__(
        self,
        state_manager: "StateManagerProcessor",
        agent_service: "AgentService",
        redis_repo: PlaybackQueueRepository,
        check_interval: float = 1.0,  # 检查间隔（秒）- 用于 APScheduler 配置
    ):
        """初始化调度器
        
        Args:
            state_manager: 状态管理器
            agent_service: Agent 服务
            redis_repo: Redis 仓库
            check_interval: 检查间隔（秒），默认 1s（用于 APScheduler interval 配置）
        """
        self._state_manager = state_manager
        self._agent_service = agent_service
        self._redis_repo = redis_repo
        self._check_interval = check_interval
        
        # 运行状态（不再使用 asyncio.Task）
        self._running = False
        self._job_id: Optional[str] = None  # ⭐ APScheduler 任务 ID
        
        # 当前播报任务追踪（用于打断丢弃）
        self._current_task_id: Optional[str] = None
        
        logger.info(
            f"[PlaybackScheduler] 创建完成（APScheduler 模式），"
            f"check_interval={check_interval}s"
        )
    
    # ==================== 生命周期 ====================
    
    async def start(self) -> None:
        """启动调度器（只做初始化，不启动循环）
        
        ⭐ APScheduler 任务由 AgentService 在 INITING → IDLE 转换后注册
        
        流程：
        1. 确保 Redis 已连接
        2. 清空播报队列（重启清理）
        """
        if self._running:
            logger.warning("[PlaybackScheduler] 已在运行中，跳过启动")
            return
        
        logger.info("[PlaybackScheduler] 启动中...")
        
        # Step 1: 确保 Redis 已连接
        if not self._redis_repo.is_connected:
            logger.warning("[PlaybackScheduler] Redis 未连接，尝试连接...")
            # 注意：connect() 由 get_playback_repository() 已调用
        
        # Step 2: 清空播报队列（重启清理）
        cleared_count = await self._redis_repo.clear_all()
        logger.warning(
            f"[PlaybackScheduler] 启动清理：清空 {cleared_count} 个遗留播报任务"
        )
        
        # Step 3: 标记运行状态（不启动 asyncio 循环）
        self._running = True
        
        logger.info("[PlaybackScheduler] 已启动（等待 APScheduler 任务注册）")
    
    async def stop(self) -> None:
        """停止调度器（移除 APScheduler 任务）"""
        if not self._running:
            return
        
        logger.info("[PlaybackScheduler] 停止中...")
        
        self._running = False
        
        # ⭐ 移除 APScheduler 任务（如果已注册）
        if self._job_id:
            from app.scheduler.scheduler import get_scheduler
            scheduler = get_scheduler()
            scheduler.remove_job(self._job_id)
            self._job_id = None
            logger.info(f"[PlaybackScheduler] APScheduler 任务已移除: {self._job_id}")
        
        # 清空当前任务标记
        self._current_task_id = None
        
        logger.info("[PlaybackScheduler] 已停止")
    
    @property
    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running
    
    @property
    def current_task_id(self) -> Optional[str]:
        """获取当前播报任务 ID"""
        return self._current_task_id
    
    # ==================== APScheduler 驱动接口 ====================
    
    def set_job_id(self, job_id: str) -> None:
        """设置 APScheduler 任务 ID（由 AgentService 调用）"""
        self._job_id = job_id
        logger.info(f"[PlaybackScheduler] APScheduler 任务 ID 已设置: {job_id}")
    
    async def on_check_trigger(self) -> None:
        """播报检查触发回调（由 APScheduler 定时调用）
        
        检查是否满足播报条件：
        1. 系统处于 IDLE 状态
        2. 队列有任务
        
        如果满足条件，从队列 Pop 任务并执行播报。
        """
        if not self._running:
            return
        
        # 条件检查：系统是否 IDLE
        if not self._state_manager.is_idle:
            logger.debug(
                f"[PlaybackScheduler] 非 IDLE 状态 ({self._state_manager.current_state.value})，跳过"
            )
            return
        
        # 从队列取出任务
        task = await self._redis_repo.pop()
        if task:
            await self._execute_playback(task)
    
    # ==================== 条件检查 ====================
    
    def _check_conditions(self) -> bool:
        """检查播报条件
        
        Returns:
            True 表示可以播报，False 表示跳过
        """
        # 条件 1: 系统是否 IDLE
        if not self._state_manager.is_idle:
            logger.debug(
                f"[PlaybackScheduler] 非 IDLE 状态 ({self._state_manager.current_state.value})，跳过"
            )
            return False
        
        # 条件 2: 队列是否有任务（异步检查在 _schedule_loop 中）
        return True
    
    async def _execute_playback(self, task: PlaybackTask) -> None:
        """执行播报
        
        Args:
            task: 播报任务
        
        流程：
        1. 记录当前任务 ID（用于打断丢弃）
        2. 处理动作（如果有）
        3. 推送文本 + Panel + TTS
        4. 状态自动转为 SPEAKING（由 StateManager 处理）
        """
        # ⭐ 记录当前播报任务
        self._current_task_id = task.id
        
        logger.info(
            f"[PlaybackScheduler] 开始播报: {task.to_summary()}"
        )
        
        try:
            # 1. 处理动作（如果有）
            # ⭐ task.action 现在是原始 <a>...</a> 字符串
            if task.action:
                from app.agent.action.processor import process_action
                try:
                    await process_action(
                        action_data=task.action,
                        following_text=task.content,
                    )
                    logger.info(
                        f"[PlaybackScheduler] 动作已处理: {task.action[:50]}..."
                    )
                except Exception as e:
                    logger.error(f"[PlaybackScheduler] 动作处理失败: {e}")
            
            # 2. 推送文本 + Panel + TTS
            await self._agent_service.speak_followup_text(
                content=task.content,
                panel_html=task.panel_html,
            )
            
            # ⭐ 将播报台词写入聊天记录
            from app.services.chat_history import get_conversation_buffer
            try:
                conversation_buffer = await get_conversation_buffer(user_id="default_user")
                await conversation_buffer.append_assistant_message(text=task.content)
                logger.info(
                    f"[PlaybackScheduler] 播报台词已写入聊天记录: {task.content[:30]}..."
                )
            except Exception as e:
                logger.warning(f"[PlaybackScheduler] 写入聊天记录失败: {e}")
            
            # 获取队列剩余数量
            queue_len = await self._redis_repo.get_queue_length()
            
            logger.info(
                f"[PlaybackScheduler] 播报已触发: task={task.id[:8]}, "
                f"剩余队列={queue_len}"
            )
            
        except Exception as e:
            logger.error(
                f"[PlaybackScheduler] 播报执行失败: task={task.id[:8]}, error={e}"
            )
            # 失败时清空当前任务标记
            self._current_task_id = None
    
    # ==================== 打断处理 ====================
    
    def discard_current(self) -> Optional[str]:
        """丢弃当前播报任务（打断时调用）
        
        打断后当前任务直接丢弃，不推回队列。
        原因：播报内容可能已过时（用户打断通常意味着想要新信息）。
        
        Returns:
            被丢弃的任务 ID（用于日志）
        """
        discarded_id = self._current_task_id
        self._current_task_id = None
        
        if discarded_id:
            logger.info(
                f"[PlaybackScheduler] 打断丢弃当前播报任务: task={discarded_id[:8]}"
            )
        else:
            logger.debug("[PlaybackScheduler] 打断时无当前播报任务")
        
        return discarded_id
    
    # ==================== 状态查询 ====================
    
    async def get_status(self) -> dict:
        """获取调度器状态（用于监控/调试）
        
        Returns:
            状态字典
        """
        queue_len = await self._redis_repo.get_queue_length()
        
        return {
            "running": self._running,
            "check_interval": self._check_interval,
            "current_task_id": self._current_task_id,
            "state": self._state_manager.current_state.value if self._state_manager else "unknown",
            "is_idle": self._state_manager.is_idle if self._state_manager else False,
            "queue_length": queue_len,
        }