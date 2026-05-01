"""好感度系统 - 三维社交关系模型（Trust/Intimacy/Respect）

⭐ Stone 迁移：使用 Stone AffectionRepository 替代直接 Redis 操作
"""

import logging
from typing import Optional

from app.services.affection.service import AffectionService
from app.services.affection.context_manager import AffectionContextManager
from app.services.affection.models import (
    AffectionDimension,
    DimensionState,
    AffectionState,
    AffectionAssessment,
    AffectionSnapshot,
    AffectionLevel,
    AffectionLevelResult,
    LevelTransition,
    classify_affection_levels,
    get_affection_level,
    DIMENSION_DESCRIPTIONS,
    DIMENSION_LEVEL_LABELS_ZH,
    DIMENSION_LEVEL_LABELS_EN,
    LEVEL_COUNT,
)
from app.services.affection.scheduler import (
    start_affection_scheduler,
    stop_affection_scheduler,
    get_affection_scheduler,
    affection_context_refresh_tick,
    affection_decay_tick,
    set_context_manager,
    set_affection_repo,
    register_active_user,
    unregister_active_user,
    CONTEXT_REFRESH_INTERVAL,
    AFFECTION_DECAY_INTERVAL,
    EMOTIONAL_DECAY_RATIO,
)

logger = logging.getLogger(__name__)

# 全局单例（用于日记定时任务等场景）
_affection_service_instance: Optional[AffectionService] = None


async def get_affection_service() -> AffectionService:
    """获取全局 AffectionService 实例（用于日记定时任务等场景）

    ⭐ Stone 迁移：使用 Stone AffectionRepository 替代直接 Redis 客户端。
    """
    global _affection_service_instance
    if _affection_service_instance is None:
        import asyncpg
        from app.config import settings
        from app.stone.repositories.affection_redis import get_affection_repo

        url = settings.DATABASE_URL.replace("+asyncpg", "")
        pool = await asyncpg.create_pool(url, min_size=1, max_size=3)

        affection_repo = get_affection_repo()
        _affection_service_instance = AffectionService(
            affection_repo=affection_repo,
            db_conn=pool,
        )
        logger.info("[Affection] 全局 AffectionService 已初始化(Stone)")
    return _affection_service_instance


__all__ = [
    "AffectionService",
    "AffectionContextManager",
    "AffectionDimension",
    "DimensionState",
    "AffectionState",
    "AffectionAssessment",
    "AffectionSnapshot",
    "AffectionLevel",
    "AffectionLevelResult",
    "LevelTransition",
    "classify_affection_levels",
    "get_affection_level",
    "DIMENSION_DESCRIPTIONS",
    "DIMENSION_LEVEL_LABELS_ZH",
    "DIMENSION_LEVEL_LABELS_EN",
    "LEVEL_COUNT",
    "get_affection_service",
    # Scheduler (unified)
    "start_affection_scheduler",
    "stop_affection_scheduler",
    "get_affection_scheduler",
    "affection_context_refresh_tick",
    "affection_decay_tick",
    "register_active_user",
    "unregister_active_user",
    "set_context_manager",
    "set_affection_repo",
    "CONTEXT_REFRESH_INTERVAL",
    "AFFECTION_DECAY_INTERVAL",
    "EMOTIONAL_DECAY_RATIO",
]
