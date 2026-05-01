"""日常事务调度器

功能：
1. 随机间隔触发（2-6小时）
2. 夜间静默（22:00-08:00）
3. 每日上限检查
4. APScheduler 集成
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from .config import get_daily_life_settings
from .generator import get_daily_life_generator, DailyLifeGenerator


class DailyLifeScheduler:
    """日常事务调度器

    负责：
    1. 定时触发场景生成
    2. 随机间隔调整
    3. 活跃时段检查
    4. 每日上限控制
    5. 收集候选用户（好感度三维均≥60）
    """

    def __init__(
        self,
        character_id: str = "daji",
        reference_image_path: str | None = None,
    ):
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._generator: Optional[DailyLifeGenerator] = None
        self._settings = get_daily_life_settings()
        self._daily_count: int = 0
        self._last_reset_date: datetime = datetime.now().date()
        self._is_running: bool = False
        self._character_id = character_id
        self._reference_image_path = reference_image_path
    
    def _reset_daily_count_if_needed(self) -> None:
        """检查并重置每日计数"""
        today = datetime.now().date()
        if today != self._last_reset_date:
            self._daily_count = 0
            self._last_reset_date = today
            logger.info("[DailyLifeScheduler] 每日计数已重置")
    
    def _is_in_active_hours(self) -> bool:
        """检查当前是否在活跃时段"""
        now = datetime.now(timezone(timedelta(hours=8)))
        hour = now.hour
        return hour >= self._settings.DAILY_LIFE_ACTIVE_START_HOUR and hour < self._settings.DAILY_LIFE_ACTIVE_END_HOUR
    
    def _get_random_interval_minutes(self) -> int:
        """获取随机间隔（分钟）"""
        min_interval = self._settings.DAILY_LIFE_MIN_INTERVAL_MINUTES
        max_interval = self._settings.DAILY_LIFE_MAX_INTERVAL_MINUTES
        return random.randint(min_interval, max_interval)
    
    def _reschedule_next(self) -> None:
        """重新调度下一次触发（使用新的随机间隔）"""
        if self._scheduler and self._is_running:
            next_interval = self._get_random_interval_minutes()
            try:
                self._scheduler.reschedule_job(
                    "daily_life_generation",
                    trigger=IntervalTrigger(minutes=next_interval),
                )
                logger.debug(f"[DailyLifeScheduler] 下次触发间隔: {next_interval} 分钟")
            except Exception as e:
                logger.warning(f"[DailyLifeScheduler] 重新调度失败: {e}")
    
    async def _trigger_generation(self) -> None:
        """触发场景生成"""
        self._reset_daily_count_if_needed()

        if not self._is_in_active_hours():
            logger.debug("[DailyLifeScheduler] 不在活跃时段，跳过")
            self._reschedule_next()
            return

        if self._daily_count >= self._settings.DAILY_LIFE_MAX_DAILY_EVENTS:
            logger.debug(f"[DailyLifeScheduler] 今日已达上限 ({self._daily_count}/{self._settings.DAILY_LIFE_MAX_DAILY_EVENTS})")
            self._reschedule_next()
            return

        try:
            # 收集候选用户（好感度三维均 ≥ 60）
            candidate_users = await self._collect_candidate_users()

            generator = get_daily_life_generator()
            event = await generator.generate(
                character_id=self._character_id,
                candidate_users=candidate_users,
                reference_image_path=self._reference_image_path,
            )

            if event:
                self._daily_count += 1
                share_info = ""
                if event.target_user_id:
                    share_info = f", 分享目标={event.target_user_id}, 已执行={event.share_executed}"
                logger.info(f"[DailyLifeScheduler] 生成成功，今日计数: {self._daily_count}{share_info}")
            else:
                logger.warning("[DailyLifeScheduler] 生成失败或跳过")

        except Exception as e:
            logger.error(f"[DailyLifeScheduler] 触发异常: {e}")

        self._reschedule_next()

    async def _collect_candidate_users(self) -> list[dict]:
        """从 PG affection_state 表收集候选用户

        筛选条件：三维好感度（trust_base + intimacy_base + respect_base）均 ≥ 60

        Returns:
            [{"user_id": "u_0001", "trust": 72.5, "intimacy": 68.3, "respect": 80.1}, ...]
        """
        try:
            from app.stone import get_database
            from sqlalchemy import text
            db = get_database()
            async with db.get_session() as session:
                result = await session.execute(
                    text(
                        """SELECT user_id, trust_base, intimacy_base, respect_base
                        FROM affection_state
                        WHERE character_id = :character_id
                          AND trust_base >= 60
                          AND intimacy_base >= 60
                          AND respect_base >= 60"""
                    ),
                    {"character_id": self._character_id},
                )
                rows = result.mappings().all()

            users = [
                {
                    "user_id": r["user_id"],
                    "trust": float(r["trust_base"] or 0),
                    "intimacy": float(r["intimacy_base"] or 0),
                    "respect": float(r["respect_base"] or 0),
                }
                for r in rows
            ]
            logger.info(f"[DailyLifeScheduler] 候选用户: {len(users)} 人")
            return users
        except Exception as e:
            logger.warning(f"[DailyLifeScheduler] 收集候选用户失败: {e}")
            return []
    
    async def start(self) -> None:
        """启动调度器"""
        if not self._settings.DAILY_LIFE_ENABLED:
            logger.info("[DailyLifeScheduler] 系统未启用，跳过启动")
            return
        
        if self._is_running:
            logger.warning("[DailyLifeScheduler] 已经在运行")
            return
        
        logger.info("[DailyLifeScheduler] 启动调度器...")
        
        self._reset_daily_count_if_needed()
        
        self._scheduler = AsyncIOScheduler(timezone=timezone(timedelta(hours=8)))
        first_interval = self._get_random_interval_minutes()
        
        self._scheduler.add_job(
            self._trigger_generation,
            trigger=IntervalTrigger(minutes=first_interval),
            id="daily_life_generation",
            name="日常事务生成",
            max_instances=1,
            misfire_grace_time=300,
        )
        
        self._scheduler.start()
        self._is_running = True
        
        logger.info(f"[DailyLifeScheduler] 已启动，首次触发间隔: {first_interval} 分钟")
    
    async def stop(self) -> None:
        """停止调度器"""
        if self._scheduler and self._is_running:
            self._scheduler.shutdown(wait=False)
            self._is_running = False
            logger.info("[DailyLifeScheduler] 已停止")
    
    async def trigger_now(self) -> Optional[dict]:
        """立即触发一次（用于测试或手动触发）"""
        logger.info("[DailyLifeScheduler] 手动触发...")

        try:
            candidate_users = await self._collect_candidate_users()
            generator = get_daily_life_generator()
            event = await generator.generate(
                character_id=self._character_id,
                candidate_users=candidate_users,
                reference_image_path=self._reference_image_path,
            )

            if event:
                return event.to_dict()
            return None
        except Exception as e:
            logger.error(f"[DailyLifeScheduler] 手动触发失败: {e}")
            return None
    
    def get_status(self) -> dict:
        """获取调度器状态"""
        return {
            "enabled": self._settings.DAILY_LIFE_ENABLED,
            "is_running": self._is_running,
            "daily_count": self._daily_count,
            "max_daily_events": self._settings.DAILY_LIFE_MAX_DAILY_EVENTS,
            "active_hours": f"{self._settings.DAILY_LIFE_ACTIVE_START_HOUR}:00-{self._settings.DAILY_LIFE_ACTIVE_END_HOUR}:00",
            "is_in_active_hours": self._is_in_active_hours(),
            "next_trigger": None,
        }


_scheduler: Optional[DailyLifeScheduler] = None


def get_daily_life_scheduler(
    character_id: str = "daji",
    reference_image_path: str | None = None,
) -> DailyLifeScheduler:
    """获取全局调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = DailyLifeScheduler(
            character_id=character_id,
            reference_image_path=reference_image_path,
        )
    return _scheduler


def reset_daily_life_scheduler() -> None:
    """重置调度器（用于测试）"""
    global _scheduler
    if _scheduler:
        asyncio.create_task(_scheduler.stop())
    _scheduler = None