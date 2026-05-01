"""好感度服务 - 三维Redis读写 + PostgreSQL持久化"""

import logging
import random
from datetime import datetime
from typing import Optional, Dict, Any

from app.services.affection.models import (
    AffectionState,
    AffectionAssessment,
    DimensionState,
    AffectionDimension,
    DELTA_MIN,
    DELTA_MAX,
    AFFECTION_MIN,
    AFFECTION_MAX,
    INIT_MIN,
    INIT_MAX,
    RETAINED_RATIO,
    DIMENSION_DESCRIPTIONS,
    LEVEL_DESCRIPTIONS,
    LevelTransition,
    get_affection_level,
)

logger = logging.getLogger(__name__)


class AffectionService:
    """好感度服务（三维）
    
    负责：
    - Redis读写（三维感性事件实时存储）
    - PostgreSQL持久化（三维基础好感度）
    - 衰减任务管理（按维度独立衰减）
    
    ⭐ Stone 迁移：使用 AffectionRepository
    """
    
    def __init__(self, affection_repo=None, llm_client=None, db_conn=None):
        """初始化服务

        ⭐ Stone 迁移：使用 AffectionRepository
        
        Args:
            affection_repo: AffectionRepository 实例（Stone 数据层）
            llm_client: LLM客户端（可选，用于评估好感度增量）
            db_conn: 数据库连接（asyncpg 风格接口: fetchrow/execute）
        """
        self._affection_repo = affection_repo
        self._llm = llm_client
        self._db = db_conn

        # Redis key 前缀（用于 fallback）
        self._key_prefix = "affection"
    
    # ============ Redis Key 辅助 ============
    
    def _redis_key(self, character_id: str, user_id: str) -> str:
        """好感度状态 Redis key"""
        return f"{self._key_prefix}:{character_id}:{user_id}"
    
    def set_affection_repo(self, affection_repo: Any) -> None:
        """设置 AffectionRepository（延迟注入）- ⭐ Stone 数据层"""
        self._affection_repo = affection_repo
        logger.info("[AffectionService] AffectionRepo 已设置")

    # ============ Redis 读写 ============
    
    async def get_state(self, character_id: str, user_id: str) -> AffectionState:
        """获取三维好感度状态
        
        ⭐ Stone 迁移：使用 AffectionRepository
        """
        if not self._affection_repo:
            raise RuntimeError("[AffectionService] AffectionRepo 未配置")
        
        # 从 Stone Repository 获取状态
        state_dict = await self._affection_repo.get_state_3d(character_id, user_id)
        
        dimensions: Dict[AffectionDimension, DimensionState] = {}

        if state_dict:
            # Redis 命中：直接从缓存读取
            # state_dict 格式: {"trust_base": float, "trust_emotional_retained": float, ...}
            for dim in AffectionDimension:
                dim_key = dim.value
                base = float(state_dict.get(f"{dim_key}_base", 0.0) or 0.0)
                emotional_retained = float(state_dict.get(f"{dim_key}_emotional_retained", 0.0) or 0.0)

                dimensions[dim] = DimensionState(
                    dimension=dim,
                    base=base,
                    emotional_retained=emotional_retained,
                )
        else:
            # Redis 未命中：一次性从 PG 加载/初始化三维 base 值
            bases = await self._load_or_init_all_bases(character_id, user_id)
            for dim in AffectionDimension:
                dimensions[dim] = DimensionState(
                    dimension=dim,
                    base=bases[dim],
                    emotional_retained=0.0,
                )
            await self._save_state_to_redis(character_id, user_id, dimensions)
        
        return AffectionState(
            character_id=character_id,
            user_id=user_id,
            dimensions=dimensions,
            updated_at=datetime.now(),
        )
    
    async def _save_state_to_redis(
        self, 
        character_id: str, 
        user_id: str, 
        dimensions: Dict[AffectionDimension, DimensionState]
    ) -> None:
        """保存三维状态到 Redis
        
        ⭐ Stone 迁移：优先使用 AffectionRepository
        """
        # ⭐ 优先使用 Stone Repository
        if self._affection_repo:
            from app.stone.repositories.affection_redis import AffectionDimension as StoneDimension
            dims = {}
            for dim in AffectionDimension:
                dim_state = dimensions.get(dim, DimensionState(dimension=dim))
                stone_dim = StoneDimension(dim.value)
                dims[stone_dim] = {
                    "base": dim_state.base,
                    "emotional_retained": dim_state.emotional_retained,
                }
            await self._affection_repo.set_state_3d(character_id, user_id, dims)
            return
        
        # 兼容旧接口
        key = self._redis_key(character_id, user_id)
        mapping = {}
        for dim in AffectionDimension:
            dim_state = dimensions.get(dim, DimensionState(dimension=dim))
            mapping[f"{dim.value}_base"] = str(dim_state.base)
            mapping[f"{dim.value}_emotional_retained"] = str(dim_state.emotional_retained)
        await self._redis.hset(key, mapping=mapping)
    
    async def _load_or_init_all_bases(
        self,
        character_id: str,
        user_id: str,
    ) -> Dict[AffectionDimension, float]:
        """一次性加载或初始化三维基础好感度（避免逐维度初始化的并发问题）"""
        if self._db is None:
            return {
                dim: random.uniform(INIT_MIN, INIT_MAX)
                for dim in AffectionDimension
            }

        try:
            row = await self._db.fetchrow(
                """
                SELECT trust_base, intimacy_base, respect_base
                FROM affection_state
                WHERE character_id = $1 AND user_id = $2
                """,
                character_id, user_id
            )

            if row:
                return {
                    AffectionDimension.TRUST: float(row["trust_base"] or 0.0),
                    AffectionDimension.INTIMACY: float(row["intimacy_base"] or 0.0),
                    AffectionDimension.RESPECT: float(row["respect_base"] or 0.0),
                }

            # 无记录，一次性初始化三个维度
            init_values = {
                AffectionDimension.TRUST: random.uniform(INIT_MIN, INIT_MAX),
                AffectionDimension.INTIMACY: random.uniform(INIT_MIN, INIT_MAX),
                AffectionDimension.RESPECT: random.uniform(INIT_MIN, INIT_MAX),
            }

            await self._db.execute(
                """
                INSERT INTO affection_state (character_id, user_id, trust_base, intimacy_base, respect_base)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (character_id, user_id) DO NOTHING
                """,
                character_id, user_id,
                init_values[AffectionDimension.TRUST],
                init_values[AffectionDimension.INTIMACY],
                init_values[AffectionDimension.RESPECT],
            )

            logger.info(
                "[Service] 初始化PG好感度: character=%s, user=%s, trust=%.2f, intimacy=%.2f, respect=%.2f",
                character_id, user_id,
                init_values[AffectionDimension.TRUST],
                init_values[AffectionDimension.INTIMACY],
                init_values[AffectionDimension.RESPECT],
            )

            return init_values

        except Exception as e:
            logger.warning("[Service] PG查询失败，使用随机初始值: %s", e)
            return {
                dim: random.uniform(INIT_MIN, INIT_MAX)
                for dim in AffectionDimension
            }
    
    async def _add_retained_in_redis(
        self, character_id: str, user_id: str, dimension: AffectionDimension, value: float
    ) -> None:
        """添加保留值到对应维度（原子操作）
        
        ⭐ Stone 迁移：优先使用 AffectionRepository
        """
        # ⭐ 优先使用 Stone Repository
        if self._affection_repo:
            from app.stone.repositories.affection_redis import AffectionDimension as StoneDimension
            stone_dim = StoneDimension(dimension.value)
            new_value = await self._affection_repo.incr_retained(
                character_id, user_id, stone_dim, value
            )
            logger.info(
                "[Service] %s emotional_retained 原子更新(Stone): +%.2f -> %.2f",
                dimension.value, value, float(new_value)
            )
            return
        
        # 兼容旧接口
        key = self._redis_key(character_id, user_id)
        retained_key = f"{dimension.value}_emotional_retained"
        # 使用 HINCRBYFLOAT 原子操作，避免并发竞态
        new_value = await self._redis.hincrbyfloat(key, retained_key, value)
        logger.info(
            "[Service] %s emotional_retained 原子更新: +%.2f -> %.2f",
            dimension.value, value, float(new_value)
        )
    
    # ============ 感性好感度操作 ============
    
    async def add_emotional_delta(
        self,
        character_id: str,
        user_id: str,
        dimension: AffectionDimension,
        delta: float,
        context: Optional[dict] = None,
    ) -> None:
        """添加感性好感度增量（直接累加到 emotional_retained）"""
        delta = max(DELTA_MIN, min(DELTA_MAX, delta))
        await self._add_retained_in_redis(character_id, user_id, dimension, delta)
        
        logger.info(
            "[Service] 添加感性增量: character=%s, user=%s, dimension=%s, delta=%.2f",
            character_id, user_id, dimension.value, delta
        )
    
    async def add_emotional_deltas_batch(
        self,
        character_id: str,
        user_id: str,
        assessment: AffectionAssessment,
        context: Optional[dict] = None,
    ) -> None:
        """批量添加三维感性增量"""
        for dim in AffectionDimension:
            delta = assessment.get_delta(dim)
            if abs(delta) > 0.01:
                await self.add_emotional_delta(character_id, user_id, dim, delta, context)

    # ============ 感性总结计算 ============

    @staticmethod
    def compute_emotional_summaries(state: AffectionState) -> Dict[AffectionDimension, float]:
        """计算各维度感性总结值 = emotional_retained

        供 scheduler 和 settle_on_diary 共用，避免重复计算。
        感性值已由定时器衰减，直接使用即可。
        """
        summaries = {}
        for dim in AffectionDimension:
            dim_state = state.get_dimension(dim)
            summaries[dim] = dim_state.emotional_retained
        return summaries

    # ============ 日记结算 ============

    # ============ 阶段检测与关系事件 ============

    def _get_level(self, dim: AffectionDimension, value: float) -> int:
        """获取指定维度和好感度值对应的级别编号（1-9）"""
        return get_affection_level(dim, value)

    def _detect_level_transitions(
        self,
        state: AffectionState,
        new_bases: Dict[AffectionDimension, float],
    ) -> list[LevelTransition]:
        """检测各维度的级别变化

        对比结算前的总好感度（base + emotional）和结算后的新base，
        判断是否跨越了级别阈值（9级制）。

        Args:
            state: 结算前的好感度状态
            new_bases: 结算后的新base值

        Returns:
            级别变化事件列表
        """
        from app.services.affection.models import DIMENSION_LEVEL_LABELS_ZH

        transitions = []
        for dim in AffectionDimension:
            dim_state = state.get_dimension(dim)
            old_total = dim_state.total  # 结算前的总好感度
            new_total = new_bases[dim]   # 结算后的新base（感性已归零）

            old_level = self._get_level(dim, old_total)
            new_level = self._get_level(dim, new_total)

            if old_level != new_level:
                transitions.append(LevelTransition(
                    dimension=dim,
                    from_level=old_level,
                    to_level=new_level,
                    from_label=DIMENSION_LEVEL_LABELS_ZH[dim][old_level],
                    to_label=DIMENSION_LEVEL_LABELS_ZH[dim][new_level],
                    old_value=old_total,
                    new_value=new_total,
                ))

        return transitions

    async def _create_relationship_event(
        self,
        character_id: str,
        user_id: str,
        transition: LevelTransition,
    ) -> None:
        """根据级别变化创建关系事件，写入 PostgreSQL heartbeat_events 表"""
        try:
            from app.stone import get_heartbeat_repo
            from app.agent.memory.models import HeartbeatNode

            trigger_text = transition.to_trigger_text()
            inner_monologue = (
                f"{character_id} 对 {user_id} 的{DIMENSION_DESCRIPTIONS[transition.dimension].split(' - ')[0]} "
                f"从 {transition.from_label}（L{transition.from_level}）"
                f" 变为 {transition.to_label}（L{transition.to_level}）"
                f"（{transition.old_value:.1f} → {transition.new_value:.1f}）"
            )

            intensity = min(1.0, abs(transition.new_value - transition.old_value) / 50.0)
            intensity = max(0.5, min(1.0, intensity))  # 关系事件强度在 [0.5, 1.0]

            # 使用 Stone Repository
            heartbeat_repo = get_heartbeat_repo()
            await heartbeat_repo.insert(
                character_id=character_id,
                user_id=user_id,
                event_node=HeartbeatNode.RELATIONSHIP.value,
                event_subtype=transition.to_subtype(),
                trigger_text=trigger_text,
                emotion_state={},  # 关系事件不需要情绪状态快照
                intensity=intensity,
                inner_monologue=inner_monologue,
            )

            logger.info(
                "[Service] ❤️ 关系事件已创建: character=%s, user=%s, dim=%s, "
                "%s -> %s (intensity=%.2f)",
                character_id, user_id,
                transition.dimension.value,
                transition.from_stage,
                transition.to_stage,
                intensity,
            )

        except Exception as e:
            logger.warning("[Service] 关系事件创建失败: %s", e)

    # ============ 日记结算 ============

    async def settle_on_diary(
        self,
        character_id: str,
        user_id: str,
        diary_rational_delta: Optional[AffectionAssessment] = None,
    ) -> dict:
        """日记生成时一次性清算三维好感度

        新base = 原base + 感性总结 + 理性增量

        结算后检测阶段变化，如跨越阈值则创建关系事件。
        """
        # 1. 加载当前状态（结算前快照）
        state_before = await self.get_state(character_id, user_id)

        # 2. 计算各维度感性总结（使用共享方法）
        emotional_summaries = self.compute_emotional_summaries(state_before)

        # 3. 使用传入的理性增量，或默认零增量
        rational_assessment = diary_rational_delta or AffectionAssessment()

        # 4. 各维度结算
        new_bases = {}
        total_deltas = {}

        for dim in AffectionDimension:
            dim_state = state_before.get_dimension(dim)
            emotional_sum = emotional_summaries[dim]
            rational_delta = rational_assessment.get_delta(dim)
            total_delta = emotional_sum + rational_delta

            new_base = dim_state.base + total_delta
            new_base = max(AFFECTION_MIN, min(AFFECTION_MAX, new_base))

            new_bases[dim] = new_base
            total_deltas[dim] = total_delta

        # 5. 更新 Redis：base 更新，emotional_retained 归零
        await self._save_state_to_redis(character_id, user_id, {
            dim: DimensionState(dimension=dim, base=new_bases[dim], emotional_retained=0.0)
            for dim in AffectionDimension
        })

        # 6. 持久化到 PG
        await self._persist_to_db(character_id, user_id, new_bases)

        # 7. 检测级别变化，创建关系事件
        level_transitions = self._detect_level_transitions(state_before, new_bases)
        relationship_events = []
        for transition in level_transitions:
            await self._create_relationship_event(character_id, user_id, transition)
            relationship_events.append(transition.to_trigger_text())

        logger.info(
            "[Service] 日记结算完成: trust=%.2f, intimacy=%.2f, respect=%.2f",
            total_deltas[AffectionDimension.TRUST],
            total_deltas[AffectionDimension.INTIMACY],
            total_deltas[AffectionDimension.RESPECT],
        )

        if level_transitions:
            logger.info(
                "[Service] 级别变化检测到: %d 个维度, 事件=%s",
                len(level_transitions),
                relationship_events,
            )

        return {
            "emotional_summaries": {
                dim.value: emotional_summaries[dim] for dim in AffectionDimension
            },
            "rational_deltas": rational_assessment.to_dict(),
            "total_deltas": {
                dim.value: total_deltas[dim] for dim in AffectionDimension
            },
            "new_bases": {
                dim.value: new_bases[dim] for dim in AffectionDimension
            },
            # 级别变化相关
            "level_transitions": [
                {
                    "dimension": t.dimension.value,
                    "from_level": t.from_level,
                    "to_level": t.to_level,
                    "from_label": t.from_label,
                    "to_label": t.to_label,
                    "old_value": t.old_value,
                    "new_value": t.new_value,
                    "is_upgrade": t.is_upgrade,
                    "trigger_text": t.to_trigger_text(),
                    "subtype": t.to_subtype(),
                }
                for t in level_transitions
            ],
            "relationship_events": relationship_events,
        }
    
    async def _persist_to_db(
        self,
        character_id: str,
        user_id: str,
        new_bases: Dict[AffectionDimension, float],
    ) -> None:
        """持久化三维数值到 PostgreSQL"""
        if self._db is None:
            return
        
        try:
            await self._db.execute(
                """
                INSERT INTO affection_state (character_id, user_id, trust_base, intimacy_base, respect_base)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (character_id, user_id) DO UPDATE SET
                    trust_base = $3,
                    intimacy_base = $4,
                    respect_base = $5
                """,
                character_id, user_id,
                new_bases[AffectionDimension.TRUST],
                new_bases[AffectionDimension.INTIMACY],
                new_bases[AffectionDimension.RESPECT],
            )
            
            logger.info(
                "[Service] PG持久化完成: character=%s, user=%s, trust=%.2f, intimacy=%.2f, respect=%.2f",
                character_id, user_id,
                new_bases[AffectionDimension.TRUST],
                new_bases[AffectionDimension.INTIMACY],
                new_bases[AffectionDimension.RESPECT],
            )
            
        except Exception as e:
            logger.error("[Service] PG持久化失败: %s", e)
    
    # ============ 查询接口 ============
    
    async def get_affection_summary(self, character_id: str, user_id: str) -> dict:
        """获取三维好感度摘要"""
        state = await self.get_state(character_id, user_id)
        return state.to_dict()
    
    async def get_affection_context_string(self, character_id: str, user_id: str) -> str:
        """获取用于LLM上下文的好感度描述字符串"""
        state = await self.get_state(character_id, user_id)
        return state.to_context_string()