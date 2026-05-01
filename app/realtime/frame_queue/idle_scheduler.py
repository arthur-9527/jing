"""
待机动作调度器

三种动作加载场景：
1. 低水位填充 → 只加载 default 动作（mei_wait）循环
2. 空闲调度 → 20s 无输入后启动，每 30-60s 随机插入一个 idle 动作（由 APScheduler 驱动）
3. LLM/API 触发 → 由外部直接调用 load_motion（不在此模块）

注意：idle 动作插入只在系统处于 IDLE 状态时才会执行

⭐ 迁移到 APScheduler：
- 空闲检测循环由 AgentService 通过 APScheduler 任务驱动
- IdleScheduler 提供被动回调接口：on_idle_trigger()
- 只有在 INITING → IDLE 转换后才启动 APScheduler 任务
"""

import random
import time
from typing import Optional, Callable, TYPE_CHECKING, Any
from uuid import UUID
from loguru import logger

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.stone.models.motion import motions, keyframes, motion_tags, motion_tag_map
from .types import VPDFrame, BoneFrame
from .interpolator import interpolate_transition
from app.config import settings

if TYPE_CHECKING:
    from .frame_queue import FrameQueueManager

# 空闲检测：多久没输入后开始随机 idle（秒）
IDLE_DETECT_DELAY = 20.0
# 随机 idle 插入间隔范围（秒）
IDLE_MIN_INTERVAL = 30.0
IDLE_MAX_INTERVAL = 60.0


def keyframe_to_vpd(kf: Any) -> VPDFrame:
    """将 DB Keyframe 转换为 VPDFrame"""
    bones = []
    bone_data = kf.bone_data
    if isinstance(bone_data, list):
        for b in bone_data:
            bones.append(BoneFrame(
                name=b["name"],
                translation=b.get("translation") or b.get("trans", [0, 0, 0]),
                quaternion=b.get("quaternion") or b.get("quat", [0, 0, 0, 1]),
            ))
    elif isinstance(bone_data, dict):
        for name, data in bone_data.items():
            bones.append(BoneFrame(
                name=name,
                translation=data.get("translation") or data.get("trans", [0, 0, 0]),
                quaternion=data.get("quaternion") or data.get("quat", [0, 0, 0, 1]),
            ))
    return VPDFrame(bones=bones, fi=kf.frame_index)


class IdleScheduler:
    """待机动作调度器
    
    ⭐ APScheduler 驱动模式：
    - 空闲检测由外部 APScheduler 任务定时调用 on_idle_trigger()
    - 只有在系统处于 IDLE 状态且空闲时间超过阈值时才插入 idle 动作
    - APScheduler 任务由 AgentService 在 INITING → IDLE 转换后注册
    """

    def __init__(
        self,
        frame_queue: "FrameQueueManager",
        db_session_factory,
        min_interval: float = IDLE_MIN_INTERVAL,
        max_interval: float = IDLE_MAX_INTERVAL,
    ):
        self._frame_queue = frame_queue
        self._db_session_factory = db_session_factory
        self._min_interval = min_interval
        self._max_interval = max_interval

        # 缓存
        self._idle_motion_ids: list[UUID] = []
        self._default_motion_id: Optional[UUID] = None
        self._thinking_motion_ids: list[UUID] = []  # ⭐ thinking 动作缓存
        self._cache_loaded = False

        # 空闲调度状态（不再使用 asyncio.Task）
        self._last_active_time: float = time.monotonic()
        self._running = False
        self._idle_job_id: Optional[str] = None  # ⭐ APScheduler 任务 ID

        # 状态检查回调：返回 True 表示系统处于 IDLE 状态
        self._is_idle_callback: Optional[Callable[[], bool]] = None

    async def start(self) -> None:
        """启动调度器（只做初始化，不启动循环）
        
        ⭐ APScheduler 任务由 AgentService 在 INITING → IDLE 转换后注册
        """
        self._running = True
        self._last_active_time = time.monotonic()
        self._frame_queue.set_idle_scheduler(self)
        # ⭐ 不再启动 asyncio.sleep 循环，由外部 APScheduler 驱动
        logger.info("[IdleScheduler] 启动（等待 APScheduler 任务注册）")

    async def stop(self) -> None:
        """停止调度器（移除 APScheduler 任务）"""
        self._running = False
        # ⭐ 移除 APScheduler 任务（如果已注册）
        if self._idle_job_id:
            from app.scheduler.scheduler import get_scheduler
            scheduler = get_scheduler()
            scheduler.remove_job(self._idle_job_id)
            self._idle_job_id = None
            logger.info(f"[IdleScheduler] APScheduler 任务已移除: {self._idle_job_id}")
        self._frame_queue.set_idle_scheduler(None)
        logger.info("[IdleScheduler] 停止")

    def pause(self) -> None:
        """有输入/对话，重置空闲计时"""
        self._last_active_time = time.monotonic()
        logger.debug("[IdleScheduler] 活跃，重置空闲计时")

    def resume(self) -> None:
        """对话结束，重新开始空闲倒计时"""
        self._last_active_time = time.monotonic()
        logger.debug("[IdleScheduler] 恢复，开始空闲倒计时")

    def set_is_idle_callback(self, callback: Callable[[], bool]) -> None:
        """
        设置状态检查回调。

        Args:
            callback: 回调函数，返回 True 表示系统处于 IDLE 状态，
                      返回 False 表示系统处于其他状态（LISTENING/THINKING/SPEAKING）
        """
        self._is_idle_callback = callback
        logger.info("[IdleScheduler] 状态检查回调已设置")

    # === APScheduler 驱动接口 ===

    def set_idle_job_id(self, job_id: str) -> None:
        """设置 APScheduler 任务 ID（由 AgentService 调用）"""
        self._idle_job_id = job_id
        logger.info(f"[IdleScheduler] APScheduler 任务 ID 已设置: {job_id}")

    async def on_idle_trigger(self) -> Optional[float]:
        """空闲检测触发回调（由 APScheduler 定时调用）
        
        检查是否满足 idle 动作插入条件：
        1. 系统处于 IDLE 状态
        2. 空闲时间超过 IDLE_DETECT_DELAY (20s)
        
        Returns:
            下次执行的随机间隔秒数（30-60s），或 None 表示跳过
        """
        if not self._running:
            return None

        elapsed = time.monotonic() - self._last_active_time
        
        # 检查空闲时间是否超过阈值
        if elapsed < IDLE_DETECT_DELAY:
            logger.debug(f"[IdleScheduler] 空闲时间不足: {elapsed:.0f}s < {IDLE_DETECT_DELAY}s")
            return None

        # 检查系统是否处于 IDLE 状态
        if self._is_idle_callback is not None:
            if not self._is_idle_callback():
                logger.debug(
                    f"[IdleScheduler] 非 IDLE 状态，跳过 idle 插入 "
                    f"(空闲时间: {elapsed:.0f}s)"
                )
                return None

        # 满足条件，插入随机 idle 动作
        await self._insert_random_idle()

        # 返回随机间隔供 APScheduler 修改下次执行时间
        next_interval = random.uniform(self._min_interval, self._max_interval)
        logger.info(f"[IdleScheduler] idle 动作已插入，下次间隔: {next_interval:.0f}s")
        return next_interval

    # === 1. 低水位填充：只加载 default ===

    async def load_default(self, transition_from: Optional[VPDFrame] = None) -> None:
        """
        低水位时由 FrameQueueManager 调用。
        只加载 default 动作（mei_wait）循环填充。
        """
        try:
            async with self._db_session_factory() as db:
                if not self._cache_loaded:
                    await self._load_cache(db)

                if not self._default_motion_id:
                    return

                await self._load_motion_by_id(
                    db, self._default_motion_id, transition_from, "default"
                )
        except Exception as e:
            logger.error(f"[IdleScheduler] 加载 default 动作失败: {e}")

    # 兼容 FrameQueueManager 调用名
    async def load_random_idle(self, transition_from: Optional[VPDFrame] = None) -> None:
        """低水位回调（兼容接口），实际只加载 default"""
        await self.load_default(transition_from)

    # === 2. 空闲动作插入（由 APScheduler 驱动）===

    async def _insert_random_idle(self) -> None:
        """随机选取一个 idle 动作插入帧队列"""
        try:
            async with self._db_session_factory() as db:
                if not self._cache_loaded:
                    await self._load_cache(db)

                if not self._idle_motion_ids:
                    logger.debug("[IdleScheduler] 无 idle 动作可用")
                    return

                motion_id = random.choice(self._idle_motion_ids)
                last_frame = self._frame_queue._buffer.peek_last()

                await self._load_motion_by_id(
                    db, motion_id, last_frame, "idle"
                )
        except Exception as e:
            logger.error(f"[IdleScheduler] 插入 idle 动作失败: {e}")

    # === 内部方法 ===

    async def _load_cache(self, db: AsyncSession) -> None:
        """首次加载缓存 idle + default + thinking 动作 ID"""
        await self._load_idle_motion_ids(db)
        await self._load_default_motion_id(db)
        await self._load_thinking_motion_ids(db)
        self._cache_loaded = True

    async def _load_idle_motion_ids(self, db: AsyncSession) -> None:
        """从 DB 加载所有 idle 标签的动作 ID"""
        try:
            # ⭐ 使用 SQLAlchemy Core Table 查询
            stmt = (
                select(motions.c.id)
                .select_from(motions)
                .join(motion_tag_map, motions.c.id == motion_tag_map.c.motion_id)
                .join(motion_tags, motion_tag_map.c.tag_id == motion_tags.c.id)
                .where(
                    and_(
                        motion_tags.c.tag_type == "system",
                        motion_tags.c.tag_name == "idle",
                        motions.c.status == "active",
                    )
                )
            )
            result = await db.execute(stmt)
            self._idle_motion_ids = [row[0] for row in result.all()]
            logger.info(
                f"[IdleScheduler] 缓存 {len(self._idle_motion_ids)} 个 idle 动作"
            )
        except Exception as e:
            logger.error(f"[IdleScheduler] 加载 idle 动作列表失败: {e}")
            self._idle_motion_ids = []

    async def _load_default_motion_id(self, db: AsyncSession) -> None:
        """从 DB 加载 default 标签的动作 ID"""
        try:
            # ⭐ 使用 SQLAlchemy Core Table 查询
            stmt = (
                select(motions.c.id)
                .select_from(motions)
                .join(motion_tag_map, motions.c.id == motion_tag_map.c.motion_id)
                .join(motion_tags, motion_tag_map.c.tag_id == motion_tags.c.id)
                .where(
                    and_(
                        motion_tags.c.tag_type == "system",
                        motion_tags.c.tag_name == "default",
                        motions.c.status == "active",
                    )
                )
                .limit(1)
            )
            result = await db.execute(stmt)
            row = result.first()
            self._default_motion_id = row[0] if row else None
            logger.info(
                f"[IdleScheduler] 缓存 default 动作: {self._default_motion_id}"
            )
        except Exception as e:
            logger.error(f"[IdleScheduler] 加载 default 动作失败: {e}")
            self._default_motion_id = None

    async def _load_thinking_motion_ids(self, db: AsyncSession) -> None:
        """从 DB 加载所有 thinking 标签的动作 ID"""
        try:
            # ⭐ 使用 SQLAlchemy Core Table 查询
            stmt = (
                select(motions.c.id)
                .select_from(motions)
                .join(motion_tag_map, motions.c.id == motion_tag_map.c.motion_id)
                .join(motion_tags, motion_tag_map.c.tag_id == motion_tags.c.id)
                .where(
                    and_(
                        motion_tags.c.tag_type == "system",
                        motion_tags.c.tag_name == "thinking",
                        motions.c.status == "active",
                    )
                )
            )
            result = await db.execute(stmt)
            self._thinking_motion_ids = [row[0] for row in result.all()]
            logger.info(
                f"[IdleScheduler] 缓存 {len(self._thinking_motion_ids)} 个 thinking 动作"
            )
        except Exception as e:
            logger.error(f"[IdleScheduler] 加载 thinking 动作列表失败: {e}")
            self._thinking_motion_ids = []

    # === 3. Thinking 动作：IDLE → LISTENING 时队首插入 ===

    async def load_random_thinking(self) -> None:
        """
        随机选取一个 thinking 动作，队首插入（高优先级）。
        
        由 StateManager 在 IDLE → LISTENING 状态转换时调用。
        使用 insert_motion_head 立即切换动作，展示"倾听"姿态。
        """
        try:
            async with self._db_session_factory() as db:
                if not self._cache_loaded:
                    await self._load_cache(db)

                if not self._thinking_motion_ids:
                    logger.debug("[IdleScheduler] 无 thinking 动作可用")
                    return

                motion_id = random.choice(self._thinking_motion_ids)
                await self._load_motion_head(db, motion_id, "thinking")
        except Exception as e:
            logger.error(f"[IdleScheduler] 插入 thinking 动作失败: {e}")

    async def _load_motion_head(
        self,
        db: AsyncSession,
        motion_id: UUID,
        label: str,
    ) -> None:
        """按 motion_id 查帧，队首插入（高优先级动作）"""
        # ⭐ 使用 SQLAlchemy Core Table 查询
        motion_stmt = select(motions).where(motions.c.id == motion_id)
        motion_result = await db.execute(motion_stmt)
        motion_row = motion_result.first()
        if not motion_row:
            return

        # 查询关键帧
        kf_stmt = (
            select(keyframes)
            .where(keyframes.c.motion_id == motion_id)
            .order_by(keyframes.c.frame_index)
        )
        kf_result = await db.execute(kf_stmt)
        keyframes_db = list(kf_result.all())

        if not keyframes_db:
            logger.warning(f"[IdleScheduler] 动作 {motion_row.name} 无关键帧")
            return

        vpd_frames = [keyframe_to_vpd(kf) for kf in keyframes_db]

        # ⭐ 队首插入，保留前 5 帧做平滑过渡
        count = await self._frame_queue.insert_motion_head(
            motion_id=str(motion_row.id),
            frames=vpd_frames,
        )

        logger.info(
            f"[IdleScheduler] 队首插入 {label} 动作: {motion_row.display_name or motion_row.name} "
            f"({count} 帧)"
        )

    async def _load_motion_by_id(
        self,
        db: AsyncSession,
        motion_id: UUID,
        transition_from: Optional[VPDFrame],
        label: str,
    ) -> None:
        """通用：按 motion_id 查帧并写入缓冲区"""
        # ⭐ 使用 SQLAlchemy Core Table 查询
        motion_stmt = select(motions).where(motions.c.id == motion_id)
        motion_result = await db.execute(motion_stmt)
        motion_row = motion_result.first()
        if not motion_row:
            return

        # 查询关键帧
        kf_stmt = (
            select(keyframes)
            .where(keyframes.c.motion_id == motion_id)
            .order_by(keyframes.c.frame_index)
        )
        kf_result = await db.execute(kf_stmt)
        keyframes_db = list(kf_result.all())

        if not keyframes_db:
            logger.warning(f"[IdleScheduler] 动作 {motion_row.name} 无关键帧")
            return

        vpd_frames = [keyframe_to_vpd(kf) for kf in keyframes_db]

        # 过渡帧
        if transition_from is not None and vpd_frames:
            transition_frames = interpolate_transition(
                from_frame=transition_from,
                to_frame=vpd_frames[0],
                steps=settings.IDLE_TRANSITION_FRAMES,
            )
            self._frame_queue._buffer.write_batch(transition_frames)

        count = await self._frame_queue.load_motion(
            motion_id=str(motion_row.id),
            frames=vpd_frames,
            append=True,
        )

        logger.info(
            f"[IdleScheduler] 加载 {label} 动作: {motion_row.display_name or motion_row.name} "
            f"({count} 帧)"
        )
