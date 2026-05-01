"""情绪定时回归任务

⭐ 每 5 分钟执行一次，对所有活跃角色的情绪状态进行物理模拟回归：
- 加速度快速衰减
- 速度慢速衰减
- 状态向基线回归

与好感度衰减任务类似，确保长时间未交流时情绪主动回归基线。
"""

import asyncio
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 定时任务配置
# ---------------------------------------------------------------------------

# 回归间隔（秒）
EMOTION_DECAY_INTERVAL = 300  # 5 分钟

# 每次回归步数（相当于 5 分钟活跃对话的回归量）
EMOTION_DECAY_STEPS = 30  # 5分钟 × 6步/分钟 = 30步


# ---------------------------------------------------------------------------
# 定时回归任务
# ---------------------------------------------------------------------------

_scheduler: Optional[AsyncIOScheduler] = None
_emotion_decay_job_id = "emotion_decay_tick"


async def emotion_decay_tick():
    """定时情绪回归任务
    
    对所有活跃角色的情绪状态执行物理模拟回归：
    1. 从全局实例管理器获取所有 EmotionService
    2. 执行 decay_steps 步 _tick()
    3. 更新 Redis 存储
    """
    from .service import _emotion_services
    
    if not _emotion_services:
        logger.debug("[emotion_decay_tick] 无活跃角色，跳过")
        return
    
    logger.info(f"[emotion_decay_tick] 开始执行，活跃角色数: {len(_emotion_services)}")
    
    for character_id, emotion_service in _emotion_services.items():
        try:
            # 执行回归步
            emotion_service.tick(steps=EMOTION_DECAY_STEPS)
            
            # 更新 Redis 存储
            await emotion_service.save_state()
            
            state = emotion_service.get_state()
            logger.info(
                f"[emotion_decay_tick] 角色 {character_id} 回归完成: "
                f"P={state.p:.3f}, A={state.a:.3f}, D={state.d:.3f}"
            )
            
        except Exception as e:
            logger.warning(f"[emotion_decay_tick] 角色 {character_id} 回归失败: {e}")
    
    logger.info("[emotion_decay_tick] 执行完成")


async def start_emotion_scheduler():
    """启动情绪定时回归任务"""
    global _scheduler
    
    if _scheduler is not None:
        logger.warning("[emotion_scheduler] 已启动，跳过")
        return
    
    _scheduler = AsyncIOScheduler()
    
    # 添加定时回归任务
    _scheduler.add_job(
        emotion_decay_tick,
        trigger=IntervalTrigger(seconds=EMOTION_DECAY_INTERVAL),
        id=_emotion_decay_job_id,
        name="情绪定时回归任务",
        max_instances=1,  # 确保单实例运行
    )
    
    _scheduler.start()
    logger.info(
        f"[emotion_scheduler] 已启动，间隔: {EMOTION_DECAY_INTERVAL}s，"
        f"步数: {EMOTION_DECAY_STEPS}"
    )


async def stop_emotion_scheduler():
    """停止情绪定时回归任务"""
    global _scheduler
    
    if _scheduler is None:
        return
    
    _scheduler.remove_job(_emotion_decay_job_id)
    _scheduler.shutdown(wait=False)
    _scheduler = None
    
    logger.info("[emotion_scheduler] 已停止")


def get_emotion_scheduler() -> Optional[AsyncIOScheduler]:
    """获取调度器实例"""
    return _scheduler