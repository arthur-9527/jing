"""动作匹配器：新评分体系（描述0.6 + 标签0.3 + 时长0.1）

说明：
- 描述匹配：使用 motion.description 的 embedding 向量相似度 × 0.6
- 标签匹配：(action_sim×0.6 + emotion_sim×0.2 + others_sim×0.2) × 0.3
- 时长匹配：时长得分 × 0.1
- 标签向量预存在 MotionTag.embedding 字段
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select, and_

from app.agent.memory.embedding import get_embedding
from app.stone import get_database
from app.stone.models.motion import motion, motion_tag, motion_tag_map

logger = logging.getLogger(__name__)

# ── 权重配置（总分1.0） ──
W_DESCRIPTION = 0.6    # 描述匹配权重
W_TAG = 0.3            # 标签匹配权重
W_DURATION = 0.1       # 时长匹配权重

# 标签内部权重
W_ACTION = 0.6          # action 在标签内的权重
W_EMOTION = 0.2        # emotion 在标签内的权重
W_OTHERS = 0.2         # others 在标签内的权重

# others 包含的维度
_OTHERS_DIMENSIONS = ["intensity", "style", "scene", "speed", "rhythm", "complexity"]

# 阈值
MIN_SCORE = 0.30           # 最终加权得分低于此值不返回
DESC_EMBEDDING_FLOOR = 0.2 # 描述 embedding 相似度兜底阈值
DURATION_TOLERANCE = 3.0   # 时长容差（秒），超出后线性衰减
CANDIDATE_LIMIT = 20       # 向量检索候选数量


def _duration_score(target: float, actual: float) -> float:
    """时长匹配得分：容差内满分，超出后线性衰减到0"""
    diff = abs(target - actual)
    if diff <= DURATION_TOLERANCE:
        return 1.0
    # 超出容差部分，每多1秒扣0.2，最低0
    return max(0.0, 1.0 - (diff - DURATION_TOLERANCE) * 0.2)


async def _compute_tag_similarity(
    query_tags: dict[str, str],
    motion_id: str,
    motion_tag_embeddings: dict[str, dict[str, list]],
) -> tuple[float, float, float]:
    """
    计算标签相似度
    
    Returns:
        (action_sim, emotion_sim, others_sim)
        - action_sim: action 标签的向量相似度 (0-1)
        - emotion_sim: emotion 标签的向量相似度 (0-1)
        - others_sim: others 维度平均相似度 (0-1)
    """
    action_sim = 0.0
    emotion_sim = 0.0
    
    # others 维度相似度列表
    others_sims = []
    
    query_action = query_tags.get("action", "")
    query_emotion = query_tags.get("emotion", "")
    
    # 获取动作的所有标签和向量
    motion_tags = motion_tag_embeddings.get(motion_id, {})
    
    # action 相似度计算
    if query_action and "action" in motion_tags and motion_tags["action"]:
        action_embedding = await get_embedding(query_action)
        if action_embedding:
            for tag_name, tag_embedding in motion_tags["action"].items():
                if tag_embedding:
                    sim = _cosine_similarity(action_embedding, tag_embedding)
                    action_sim = max(action_sim, sim)
    
    # emotion 相似度计算
    if query_emotion and "emotion" in motion_tags and motion_tags["emotion"]:
        emotion_embedding = await get_embedding(query_emotion)
        if emotion_embedding:
            for tag_name, tag_embedding in motion_tags["emotion"].items():
                if tag_embedding:
                    sim = _cosine_similarity(emotion_embedding, tag_embedding)
                    emotion_sim = max(emotion_sim, sim)
    
    # others 维度相似度计算
    for dim in _OTHERS_DIMENSIONS:
        query_val = query_tags.get(dim, "")
        if query_val and dim in motion_tags and motion_tags[dim]:
            dim_embedding = await get_embedding(query_val)
            if dim_embedding:
                best_sim = 0.0
                for tag_name, tag_embedding in motion_tags[dim].items():
                    if tag_embedding:
                        sim = _cosine_similarity(dim_embedding, tag_embedding)
                        best_sim = max(best_sim, sim)
                if best_sim > 0:
                    others_sims.append(best_sim)
    
    # others 平均相似度
    others_sim = sum(others_sims) / len(others_sims) if others_sims else 0.0
    
    return action_sim, emotion_sim, others_sim


def _cosine_similarity(vec1: list, vec2: list) -> float:
    """计算余弦相似度"""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    
    if norm1 == 0 or norm2 == 0:
        return 0.0
    
    return dot_product / (norm1 * norm2)


async def match_motion(
    action: str,
    emotion: str = "",
    intensity: str = "",
    style: str = "",
    scene: str = "",
    speed: str = "",
    rhythm: str = "",
    complexity: str = "",
    duration: float = 0.0,
    description: str = "",
) -> dict | None:
    """
    动作匹配（新评分体系）。

    Args:
        action:     核心动作类型
        emotion:    情绪标签
        intensity:  动作强度
        style:      动作风格
        scene:      场景标签
        speed:      动作速度
        rhythm:     节奏类型
        complexity: 复杂度
        duration:   估算时长（秒）
        description: 描述文本（用于描述匹配）

    Returns:
        最佳匹配动作 dict 或 None
    """
    # 构建描述查询文本
    query_text = description if description else action
    if emotion and not description:
        query_text = f"{emotion}地{action}"

    # 生成描述 embedding
    try:
        query_embedding = await get_embedding(query_text)
    except Exception:
        logger.warning("描述 embedding 生成失败: %s", query_text)
        return None

    # 构建查询标签字典
    query_tags = {
        "action": action,
        "emotion": emotion,
        "intensity": intensity,
        "style": style,
        "scene": scene,
        "speed": speed,
        "rhythm": rhythm,
        "complexity": complexity,
    }

    async with get_db_session() as session:
        # 第一步：使用描述 embedding 检索候选
        distance = Motion.embedding.cosine_distance(query_embedding)
        similarity = (1 - distance).label("similarity")

        candidates_stmt = (
            select(
                Motion.id,
                Motion.display_name,
                Motion.description,
                Motion.original_duration,
                similarity,
            )
            .where(
                Motion.status == "active",
                Motion.embedding.isnot(None),
                (1 - distance) >= DESC_EMBEDDING_FLOOR,
            )
            .order_by(distance)
            .limit(CANDIDATE_LIMIT)
        )

        candidates = (await session.execute(candidates_stmt)).mappings().all()

        if not candidates:
            logger.info(
                "动作匹配: '%s' -> 无候选（描述相似度均低于 %.2f）",
                query_text,
                DESC_EMBEDDING_FLOOR,
            )
            return None

        # 第二步：获取候选动作的标签及其向量
        candidate_ids = [r["id"] for r in candidates]

        tags_stmt = (
            select(
                MotionTagMap.motion_id,
                MotionTag.tag_type,
                MotionTag.tag_name,
                MotionTag.embedding,
            )
            .join(MotionTag, MotionTagMap.tag_id == MotionTag.id)
            .where(MotionTagMap.motion_id.in_(candidate_ids))
        )
        tag_rows = (await session.execute(tags_stmt)).mappings().all()

        # 检查 system 标签必须为 "others"
        system_stmt = (
            select(MotionTagMap.motion_id)
            .join(MotionTag, MotionTagMap.tag_id == MotionTag.id)
            .where(
                MotionTagMap.motion_id.in_(candidate_ids),
                MotionTag.tag_type == "system",
                MotionTag.tag_name != "others",
            )
        )
        invalid_motion_ids = set(row["motion_id"] for row in (await session.execute(system_stmt)).mappings().all())

    # 组织标签向量数据: {motion_id: {tag_type: {tag_name: embedding}}}
    tags_with_embeddings: dict[str, dict[str, dict[str, list]]] = {}
    for tr in tag_rows:
        mid = str(tr["motion_id"])
        if mid in invalid_motion_ids:
            continue
        tag_type = tr["tag_type"]
        tag_name = tr["tag_name"]
        embedding = tr["embedding"]
        
        if embedding is not None:
            tags_with_embeddings.setdefault(mid, {}).setdefault(tag_type, {})[tag_name] = list(embedding)

    # 第三步：计算各维度得分
    best = None
    best_score = -1.0

    for r in candidates:
        mid = str(r["id"])
        
        # 跳过 system 标签不合规的动作
        if mid in invalid_motion_ids:
            continue
        
        # 描述相似度
        s_desc = float(r["similarity"])
        
        # 标签相似度（使用向量）
        motion_tags_emb = tags_with_embeddings.get(mid, {})
        action_sim, emotion_sim, others_sim = await _compute_tag_similarity(
            query_tags, mid, motion_tags_emb
        )
        
        # 标签总分
        s_tag = action_sim * W_ACTION + emotion_sim * W_EMOTION + others_sim * W_OTHERS
        
        # 时长得分
        s_dur = _duration_score(duration, r["original_duration"]) if duration > 0 else 1.0

        # 总分
        total = W_DESCRIPTION * s_desc + W_TAG * s_tag + W_DURATION * s_dur

        if total > best_score:
            best_score = total
            best = {
                "id": mid,
                "display_name": r["display_name"],
                "description": r["description"],
                "duration": r["original_duration"],
                "score": round(total, 4),
                "_detail": {
                    "description": round(s_desc, 3),
                    "tag": round(s_tag, 3),
                    "action_sim": round(action_sim, 3),
                    "emotion_sim": round(emotion_sim, 3),
                    "others_sim": round(others_sim, 3),
                    "duration": round(s_dur, 3),
                },
            }

    if best and best["score"] >= MIN_SCORE:
        logger.info(
            "动作匹配: '%s' -> %s (score=%.3f, desc=%.3f, tag=%.3f, dur=%.3f)",
            query_text,
            best["display_name"],
            best["score"],
            best["_detail"]["description"],
            best["_detail"]["tag"],
            best["_detail"]["duration"],
        )
        return best

    logger.info(
        "动作匹配: '%s' -> 无匹配（最高分 %.3f < %.2f）",
        query_text,
        best_score,
        MIN_SCORE,
    )
    return None