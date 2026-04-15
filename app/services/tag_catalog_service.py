"""标签目录服务 - 预加载所有 action 和 emotion 标签"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, and_

from app.agent.memory.embedding import get_embedding
from app.database import get_db_session
from app.models.tag import MotionTag, MotionTagMap
from app.models.motion import Motion

if TYPE_CHECKING:
    from app.agent.character.loader import CharacterConfig

logger = logging.getLogger(__name__)

# 标签匹配阈值
MATCH_THRESHOLD = 0.5
MAX_CANDIDATES = 5
# emotion 匹配加分
EMOTION_BONUS = 0.2
# 筛选差距容限（低于最高分超过此值则被排除）
SCORE_GAP_THRESHOLD = 0.1


def _cosine_similarity(vec1: list[float] | None, vec2: list[float] | None) -> float:
    """计算余弦相似度"""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot_product / (norm1 * norm2)


class TagCatalogService:
    """预加载所有 action 和 emotion 标签，用于动作匹配加速"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._initialized = False
        self._action_tags: set[str] = set()
        self._emotion_tags: set[str] = set()
        self._all_tags: dict[str, tuple[str, list[float] | None]] = {}  # tag_name -> (type, embedding)

    async def initialize(self):
        """启动时从数据库加载所有标签"""
        async with self._lock:
            if self._initialized:
                return

            async with get_db_session() as session:
                # 加载所有 action 标签
                action_result = await session.execute(
                    select(MotionTag.tag_name, MotionTag.embedding)
                    .where(MotionTag.tag_type == "action")
                )
                for name, emb in action_result:
                    if name:
                        self._action_tags.add(name)
                        emb_list = list(emb) if emb is not None else None
                        self._all_tags[name] = ("action", emb_list)

                # 加载所有 emotion 标签
                emotion_result = await session.execute(
                    select(MotionTag.tag_name, MotionTag.embedding)
                    .where(MotionTag.tag_type == "emotion")
                )
                for name, emb in emotion_result:
                    if name:
                        self._emotion_tags.add(name)
                        emb_list = list(emb) if emb is not None else None
                        self._all_tags[name] = ("emotion", emb_list)

            self._initialized = True
            logger.info(
                f"[TagCatalog] 已加载 {len(self._action_tags)} 个 action 标签, "
                f"{len(self._emotion_tags)} 个 emotion 标签"
            )

    def validate_action(self, action: str) -> bool:
        """验证 action 标签是否有效"""
        return action in self._action_tags

    def validate_emotion(self, emotion: str) -> bool:
        """验证 emotion 标签是否有效"""
        return emotion in self._emotion_tags

    def get_actions(self) -> list[str]:
        """获取所有 action 标签"""
        return sorted(self._action_tags)

    def get_emotions(self) -> list[str]:
        """获取所有 emotion 标签"""
        return sorted(self._emotion_tags)

    def get_embedding(self, tag: str) -> list[float] | None:
        """获取标签的 embedding"""
        data = self._all_tags.get(tag)
        return data[1] if data else None

    async def match_motion_by_tags(
        self,
        action: str,
        emotion: str,
        desp: str,
    ) -> dict | None:
        """
        根据标签匹配最佳动作
        
        新评分体系：
        1. 用 action 标签筛选候选动作（最多5个）
        2. 用 desp 描述计算 embedding 相似度作为基础分数
        3. emotion 匹配则加 EMOTION_BONUS (0.2) 分
        4. 筛选低于最高分超过 SCORE_GAP_THRESHOLD (0.1) 的候选
        5. 返回最终分数最高的动作
        
        Args:
            action: 动作标签
            emotion: 情绪标签（用于加分，不做筛选）
            desp: 动作描述文本
            
        Returns:
            最佳匹配动作 dict 或 None
        """
        if not action and not emotion:
            return None

        # 计算描述的 embedding
        try:
            desp_embedding = await get_embedding(desp)
        except Exception as e:
            logger.warning(f"[TagCatalog] 描述 embedding 计算失败: {e}")
            return None

        # 查询候选动作（只按 action 筛选，不按 emotion 筛选）
        async with get_db_session() as session:
            # 构建查询：必须匹配 action 标签，且 system 标签必须是 "others"
            stmt = (
                select(
                    Motion.id,
                    Motion.display_name,
                    Motion.description,
                    Motion.original_duration,
                    Motion.embedding,
                )
                .where(Motion.status == "active")
                .join(MotionTagMap, MotionTagMap.motion_id == Motion.id)
                .join(MotionTag, MotionTagMap.tag_id == MotionTag.id)
                .where(
                    and_(
                        MotionTag.tag_type == "action",
                        MotionTag.tag_name == action,
                    )
                )
            )

            # 必须有 system="others" 标签（排除 idle 等系统动作）
            system_subq = (
                select(MotionTagMap.motion_id)
                .join(MotionTag, MotionTagMap.tag_id == MotionTag.id)
                .where(
                    and_(
                        MotionTagMap.motion_id == Motion.id,
                        MotionTag.tag_type == "system",
                        MotionTag.tag_name == "others",
                    )
                )
            )
            stmt = stmt.where(system_subq.exists())

            stmt = stmt.limit(MAX_CANDIDATES)
            result = await session.execute(stmt)
            candidates = result.mappings().all()

        if not candidates:
            logger.debug(f"[TagCatalog] 无候选动作: action={action}, emotion={emotion}")
            return None

        # 计算每个候选的基础分数和 emotion 加分
        candidate_scores: list[dict] = []
        emotion_match = bool(emotion)

        for candidate in candidates:
            mid = str(candidate["id"])
            emb = candidate.get("embedding")
            if emb is None:
                continue

            # 基础分数：desp embedding 相似度
            base_score = _cosine_similarity(desp_embedding, list(emb))

            # 检查该动作是否有匹配的 emotion 标签
            has_emotion_match = False
            if emotion_match:
                async with get_db_session() as session:
                    emotion_check = await session.execute(
                        select(MotionTagMap.id)
                        .join(MotionTag, MotionTagMap.tag_id == MotionTag.id)
                        .where(
                            and_(
                                MotionTagMap.motion_id == mid,
                                MotionTag.tag_type == "emotion",
                                MotionTag.tag_name == emotion,
                            )
                        )
                    )
                    has_emotion_match = emotion_check.scalar() is not None

            # 最终分数 = 基础分数 + emotion 加分
            final_score = base_score + (EMOTION_BONUS if has_emotion_match else 0.0)

            candidate_scores.append({
                "id": mid,
                "display_name": candidate["display_name"],
                "description": candidate.get("description", ""),
                "original_duration": candidate.get("original_duration", 0.0),
                "base_score": base_score,
                "has_emotion_match": has_emotion_match,
                "score": final_score,
            })

        if not candidate_scores:
            logger.debug(f"[TagCatalog] 无有效候选: action={action}")
            return None

        # 找出最高分
        best_score = max(cs["score"] for cs in candidate_scores)

        # 筛选：排除低于最高分超过阈值的候选
        filtered = [
            cs for cs in candidate_scores
            if best_score - cs["score"] <= SCORE_GAP_THRESHOLD
        ]

        # 按最终分数排序，取最高分
        filtered.sort(key=lambda x: x["score"], reverse=True)
        best = filtered[0]

        logger.info(
            f"[TagCatalog] 动作匹配: {best['display_name']} "
            f"(score={best['score']:.3f}, base={best['base_score']:.3f}, "
            f"emotion_match={best['has_emotion_match']}, action={action})"
        )
        logger.debug(f"[TagCatalog] 候选详情: {[{'name': c['display_name'], 'score': c['score'], 'base': c['base_score'], 'emotion': c['has_emotion_match']} for c in candidate_scores]}")

        # 阈值过滤：最终分数需要 >= MATCH_THRESHOLD
        if best["score"] >= MATCH_THRESHOLD:
            return {
                "id": best["id"],
                "display_name": best["display_name"],
                "description": best["description"],
                "duration": best.get("original_duration", 0.0),
                "score": round(best["score"], 4),
                "base_score": round(best["base_score"], 4),
                "emotion_bonus": EMOTION_BONUS if best["has_emotion_match"] else 0.0,
            }

        logger.debug(
            f"[TagCatalog] 无匹配动作: best_score={best['score']:.3f} < {MATCH_THRESHOLD}"
        )
        return None


# 全局单例
_tag_catalog_service: TagCatalogService | None = None


def get_tag_catalog_service() -> TagCatalogService:
    """获取标签目录服务单例"""
    global _tag_catalog_service
    if _tag_catalog_service is None:
        _tag_catalog_service = TagCatalogService()
    return _tag_catalog_service