"""好感度定时任务调度器

⭐ 合并原来两个独立调度器（语境刷新 + 感性衰减），共用一个 AsyncIOScheduler：
- affection_context_refresh_tick: 每 10 分钟检测好感度语境变化
- affection_decay_tick: 每 10 分钟衰减 emotional_retained → 0

⭐ Stone 迁移：使用 AffectionRepository 替代直接 Redis 操作
"""

import logging
from typing import Optional, Set, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 定时任务配置
# ---------------------------------------------------------------------------

CONTEXT_REFRESH_INTERVAL = 600   # 10 分钟（语境刷新）
AFFECTION_DECAY_INTERVAL = 600   # 10 分钟（感性衰减）
EMOTIONAL_DECAY_RATIO = 0.03     # 3%

# 任务 ID
_CONTEXT_REFRESH_JOB_ID = "affection_context_refresh_tick"
_AFFECTION_DECAY_JOB_ID = "affection_decay_tick"


# ---------------------------------------------------------------------------
# 全局引用
# ---------------------------------------------------------------------------

_active_users: Set[str] = set()
_context_manager: Optional[Any] = None
_affection_repo: Optional[Any] = None  # ⭐ Stone AffectionRepository


def register_active_user(character_id: str, user_id: str) -> None:
    key = f"{character_id}:{user_id}"
    _active_users.add(key)
    logger.debug("[AffectionScheduler] 注册活跃用户: %s", key)


def unregister_active_user(character_id: str, user_id: str) -> None:
    key = f"{character_id}:{user_id}"
    _active_users.discard(key)
    logger.debug("[AffectionScheduler] 取消活跃用户: %s", key)


def set_context_manager(context_manager: Any) -> None:
    global _context_manager
    _context_manager = context_manager
    logger.info("[AffectionScheduler] ContextManager 已设置")


def set_affection_repo(affection_repo: Any) -> None:
    """⭐ Stone 迁移：设置 AffectionRepository 替代 set_redis_client"""
    global _affection_repo
    _affection_repo = affection_repo
    logger.info("[AffectionScheduler] AffectionRepository 已设置")


def set_redis_client(redis_client: Any) -> None:
    """已弃用：请使用 set_affection_repo

    保留此方法仅兼容旧代码，实际会尝试从 redis_client 推断 repo。
    """
    logger.warning("[AffectionScheduler] set_redis_client 已弃用，请使用 set_affection_repo")
    # 尝试兼容：如果传入的是 redis 客户端，设置全局引用以便衰减任务使用
    global _affection_repo
    if _affection_repo is None:
        from app.stone.repositories.affection_redis import AffectionRepository
        _affection_repo = AffectionRepository()
        logger.info("[AffectionScheduler] 已自动创建 AffectionRepository")


# ---------------------------------------------------------------------------
# 共享调度器
# ---------------------------------------------------------------------------

_scheduler: Optional[AsyncIOScheduler] = None


# ---------------------------------------------------------------------------
# 任务 1: 语境刷新
# ---------------------------------------------------------------------------

async def affection_context_refresh_tick():
    """定时好感度语境刷新任务"""
    if not _context_manager or not _active_users:
        logger.debug("[AffectionScheduler] 无活跃用户或 ContextManager，跳过语境刷新")
        return

    logger.info("[AffectionScheduler] 语境刷新开始，活跃用户数: %d", len(_active_users))

    from app.services.emotion.service import _emotion_services

    for user_key in list(_active_users):
        try:
            character_id, user_id = user_key.split(":")

            emotion_service = _emotion_services.get(character_id)
            if not emotion_service:
                logger.debug("[AffectionScheduler] 角色 %s 无情绪服务，跳过", character_id)
                continue

            regenerated = await _context_manager.check_and_regenerate(
                character_id=character_id,
                user_id=user_id,
                emotion_service=emotion_service,
            )

            logger.debug("[AffectionScheduler] 用户 %s 语境刷新完成，regenerated=%s", user_key, regenerated)

        except Exception as e:
            logger.warning("[AffectionScheduler] 用户 %s 语境刷新失败: %s", user_key, e)

    logger.info("[AffectionScheduler] 语境刷新完成")


# ---------------------------------------------------------------------------
# 任务 2: 感性衰减
# ---------------------------------------------------------------------------

async def affection_decay_tick():
    """定时好感度衰减任务

    对所有存在好感度数据的用户执行衰减（通过 Stone AffectionRepository）：
    1. emotional_retained *= (1 - DECAY_RATIO) → 向 0 衰减
    2. base 保持不变（永久积累）

    ⭐ Stone 迁移：使用 AffectionRepository.scan_state_keys + get/set
    """
    if not _affection_repo:
        logger.debug("[AffectionScheduler] AffectionRepository 未就绪，跳过衰减")
        return

    try:
        state_keys = await _affection_repo.scan_state_keys()
    except Exception as e:
        logger.warning("[AffectionScheduler] 扫描好感度 key 失败: %s", e)
        return

    if not state_keys:
        logger.debug("[AffectionScheduler] 无好感度数据，跳过衰减")
        return

    logger.info(
        "[AffectionScheduler] 衰减开始，用户数: %d，衰减比例: %.1f%%",
        len(state_keys), EMOTIONAL_DECAY_RATIO * 100
    )

    for key in state_keys:
        try:
            # key 格式: agent:affection:{character_id}:{user_id}
            suffix = key.split(":", 2)[2]  # 去掉 "agent:"
            parts = suffix.split(":", 1)    # ["affection", "char:user"]
            if len(parts) < 2:
                continue
            char_user = parts[1].rsplit(":", 1)  # ["char", "user"]
            if len(char_user) < 2:
                continue
            character_id = char_user[0]
            user_id = char_user[1]

            await _decay_user_affection(character_id, user_id)

        except Exception as e:
            logger.warning("[AffectionScheduler] key %s 衰减失败: %s", key, e)

    logger.info("[AffectionScheduler] 衰减完成")


async def _decay_user_affection(character_id: str, user_id: str) -> None:
    """衰减单个用户的三维感性好感度（向0衰减）

    ⭐ Stone 迁移：使用 AffectionRepository 操作
    """
    if not _affection_repo:
        return

    try:
        retained = await _affection_repo.get_state_retained(character_id, user_id)

        new_retained = {}
        for field, current_val in retained.items():
            if current_val > 0:
                new_retained[field] = current_val * (1 - EMOTIONAL_DECAY_RATIO)
            elif current_val < 0:
                new_retained[field] = current_val * (1 - EMOTIONAL_DECAY_RATIO)
            else:
                new_retained[field] = 0.0

        if new_retained:
            await _affection_repo.set_state_retained(character_id, user_id, new_retained)

    except Exception as e:
        logger.warning("[AffectionScheduler] 用户 %s:%s 衰减失败: %s", character_id, user_id, e)


# ---------------------------------------------------------------------------
# 启动 / 停止
# ---------------------------------------------------------------------------

async def start_affection_scheduler():
    """启动好感度统一调度器（语境刷新 + 感性衰减）"""
    global _scheduler

    if _scheduler is not None:
        logger.warning("[AffectionScheduler] 已启动，跳过")
        return

    _scheduler = AsyncIOScheduler()

    _scheduler.add_job(
        affection_context_refresh_tick,
        trigger=IntervalTrigger(seconds=CONTEXT_REFRESH_INTERVAL),
        id=_CONTEXT_REFRESH_JOB_ID,
        name="好感度语境定时刷新任务",
        max_instances=1,
    )

    _scheduler.add_job(
        affection_decay_tick,
        trigger=IntervalTrigger(seconds=AFFECTION_DECAY_INTERVAL),
        id=_AFFECTION_DECAY_JOB_ID,
        name="好感度定时衰减任务",
        max_instances=1,
    )

    _scheduler.start()
    logger.info(
        "[AffectionScheduler] 已启动，语境刷新间隔: %ds，衰减间隔: %ds，衰减比例: %.1f%%",
        CONTEXT_REFRESH_INTERVAL, AFFECTION_DECAY_INTERVAL, EMOTIONAL_DECAY_RATIO * 100
    )


async def stop_affection_scheduler():
    """停止好感度统一调度器"""
    global _scheduler

    if _scheduler is None:
        return

    _scheduler.remove_job(_CONTEXT_REFRESH_JOB_ID)
    _scheduler.remove_job(_AFFECTION_DECAY_JOB_ID)
    _scheduler.shutdown(wait=False)
    _scheduler = None

    logger.info("[AffectionScheduler] 已停止")


def get_affection_scheduler() -> Optional[AsyncIOScheduler]:
    return _scheduler
