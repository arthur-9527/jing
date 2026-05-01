"""RealtimeManager - 实时语音流模块的生命周期管理器

对标 `app.channel.manager.ChannelManager` 的设计。

职责：
- 管理 Pipecat Pipeline 的启动/停止
- 管理实时流相关组件（FrameQueue, LipSync, Playback, Idle, StateManager 等）
- 提供统一的生命周期接口给 main.py

使用方式:
    from app.realtime import get_realtime_manager

    realtime_manager = get_realtime_manager(character_config)
    await realtime_manager.start()   # 启动实时语音管线
    await realtime_manager.stop()    # 停止
"""

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class RealtimeManager:
    """实时语音流管理器

    封装 AgentService 的完整生命周期，对外暴露简单的 start/stop 接口。
    """

    def __init__(self, character_config: Optional[dict] = None):
        self._character_config = character_config
        self._agent_service = None
        self._running = False

    async def start(self) -> None:
        """启动实时语音流

        1. 初始化并启动 AgentService（Pipecat Pipeline + 所有组件）
        2. 启动情绪衰减调度器
        3. 启动好感度衰减调度器
        """
        if self._running:
            logger.warning("[RealtimeManager] 已在运行中")
            return

        logger.info("[RealtimeManager] 正在启动实时语音流...")

        # 1. 启动 Agent 服务（Pipecat Pipeline + FrameQueue + LipSync + Playback 等）
        from app.realtime.agent_service import start_agent_service
        self._agent_service = await start_agent_service()
        logger.info("[RealtimeManager] Agent 服务已启动")

        # 2. 启动情绪定时衰减任务
        try:
            from app.services.emotion.scheduler import start_emotion_scheduler
            await start_emotion_scheduler()
            logger.info("[RealtimeManager] 情绪调度器已启动")
        except Exception as e:
            logger.warning(f"[RealtimeManager] 情绪调度器启动失败: {e}")

        # 3. 启动好感度统一调度器（语境刷新 + 感性衰减）
        try:
            from app.services.affection.scheduler import (
                start_affection_scheduler,
                set_affection_repo,
            )
            from app.stone.repositories.affection_redis import get_affection_repo
            set_affection_repo(get_affection_repo())
            await start_affection_scheduler()
            logger.info("[RealtimeManager] 好感度统一调度器已启动")
        except Exception as e:
            logger.warning(f"[RealtimeManager] 好感度调度器启动失败: {e}")

        self._running = True
        logger.info("[RealtimeManager] 实时语音流已启动")

    async def stop(self) -> None:
        """停止实时语音流"""
        import asyncio

        shutdown_timeout = settings.GRACEFUL_SHUTDOWN_TIMEOUT

        async def _shutdown_with_timeout(coro, name: str, timeout: float):
            try:
                await asyncio.wait_for(coro, timeout=timeout)
                logger.info(f"[RealtimeManager] {name} 已关闭")
            except asyncio.TimeoutError:
                logger.warning(f"[RealtimeManager] {name} 关闭超时 ({timeout}s)")
            except Exception as e:
                logger.error(f"[RealtimeManager] 关闭 {name} 失败: {e}")

        # 1. 停止情绪调度器
        try:
            from app.services.emotion.scheduler import stop_emotion_scheduler
            await _shutdown_with_timeout(
                stop_emotion_scheduler(), "情绪调度器", shutdown_timeout
            )
        except Exception:
            pass

        # 2. 停止好感度统一调度器
        try:
            from app.services.affection.scheduler import stop_affection_scheduler
            await _shutdown_with_timeout(
                stop_affection_scheduler(), "好感度调度器", shutdown_timeout
            )
        except Exception:
            pass

        # 3. 停止 Agent 服务
        from app.realtime.agent_service import stop_agent_service
        await _shutdown_with_timeout(stop_agent_service(), "Agent 服务", shutdown_timeout)

        self._running = False
        logger.info("[RealtimeManager] 实时语音流已停止")

    @property
    def agent_service(self):
        return self._agent_service

    def is_running(self) -> bool:
        return self._running


# ---------------------------------------------------------------------------
# 全局实例
# ---------------------------------------------------------------------------

_realtime_manager: Optional[RealtimeManager] = None


def get_realtime_manager(
    character_config: Optional[dict] = None,
) -> RealtimeManager:
    """获取 RealtimeManager 单例"""
    global _realtime_manager
    if _realtime_manager is None:
        _realtime_manager = RealtimeManager(character_config=character_config)
    return _realtime_manager


def reset_realtime_manager() -> None:
    """重置 RealtimeManager（用于测试）"""
    global _realtime_manager
    _realtime_manager = None
