"""好感度上下文管理器

核心组件：缓存 LLM 生成的动态好感度语境。

架构设计：
- Key: agent:affection:ctx:{character_id}:{user_id}
- TTL: 3600秒（1小时）
- 刷新策略: 每10分钟由 affection_context_refresh_tick 调度器检测级别变化并刷新
- 每回合只读：get_affection_context() 仅读取缓存，不重新生成

设计原则：
1. 避免每轮对话都调用 LLM 生成好感度描述，节省开销
2. 只在好感度维度级别变化（9级）时重新生成
3. 生成内容包含两部分：关系描述 + 动态提示词
4. 结合当前好感度级别 + PAD 情绪状态 + 角色人设 → 结构化输出
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, Any

from app.services.affection.models import (
    AffectionState,
    AffectionDimension,
    AffectionLevelResult,
    get_affection_level,
    classify_affection_levels,
)
from app.services.affection.prompts import build_affection_context_prompt

logger = logging.getLogger(__name__)


# Redis TTL：1小时
CONTEXT_TTL = 3600  # seconds

class AffectionContextManager:
    """好感度上下文管理器

    职责：
    - 缓存 LLM 生成的动态好感度语境（Redis，TTL=1h）
    - 每回合提供只读访问（get_affection_context）
    - 定时检测好感度级别变化（check_and_regenerate，由调度器驱动）
    - 级别变化时用 LLM 重新生成语境（结合角色人设 + PAD 情绪）

    触发重新生成条件：
    - 任一维度好感度级别（1-9）发生变化
    - 级别定义见 models.py（9级均分，每级约 22.2 分）
    """

    def __init__(
        self,
        affection_service: Any,  # AffectionService
        llm_client: Any,  # LLMClient
        character_config: Any,  # CharacterConfig
        affection_repo: Any = None,  # ⭐ Stone AffectionRepository
        personality_text: str = "",  # 角色人设描述
        emotion_traits_text: str = "",  # 角色情绪特点
    ):
        """初始化

        Args:
            affection_service: AffectionService 实例
            llm_client: LLM 客户端
            character_config: 角色配置（包含人设信息）
            affection_repo: Stone AffectionRepository 实例（用于 Redis 缓存）
            personality_text: 角色人设描述文本
            emotion_traits_text: 角色情绪特点文本
        """
        self._affection_service = affection_service
        self._llm = llm_client
        self._character = character_config
        self._affection_repo = affection_repo
        self._personality_text = personality_text
        self._emotion_traits_text = emotion_traits_text

        logger.info("[AffectionContextManager] 初始化完成，AffectionRepo: %s", affection_repo is not None)

    # === 缓存读写（通过 Stone AffectionRepository） ===

    async def _load_from_cache(self, character_id: str, user_id: str) -> Optional[dict]:
        """从 Stone AffectionRepository 加载缓存数据"""
        if not self._affection_repo:
            return None

        try:
            data = await self._affection_repo.get_context(character_id, user_id)
            if data:
                logger.debug("[AffectionContextManager] 从缓存加载: %s:%s", character_id, user_id)
            return data
        except Exception as e:
            logger.warning("[AffectionContextManager] 缓存加载失败: %s", e)
            return None

    async def _save_to_cache(self, character_id: str, user_id: str, cache_data: dict) -> bool:
        """保存缓存数据到 Stone AffectionRepository（1小时 TTL）"""
        if not self._affection_repo:
            return False

        try:
            await self._affection_repo.set_context(character_id, user_id, cache_data, ttl=CONTEXT_TTL)
            logger.debug("[AffectionContextManager] 保存到缓存: %s:%s, TTL=%ds", character_id, user_id, CONTEXT_TTL)
            return True
        except Exception as e:
            logger.warning("[AffectionContextManager] 缓存保存失败: %s", e)
            return False

    async def _refresh_ttl(self, character_id: str, user_id: str) -> bool:
        """刷新缓存 TTL"""
        if not self._affection_repo:
            return False

        try:
            await self._affection_repo.refresh_context_ttl(character_id, user_id, CONTEXT_TTL)
            logger.debug("[AffectionContextManager] 刷新 TTL: %s:%s", character_id, user_id)
            return True
        except Exception as e:
            logger.warning("[AffectionContextManager] TTL 刷新失败: %s", e)
            return False

    # === 公开接口 ===

    async def get_affection_context(
        self,
        character_id: str,
        user_id: str,
        emotion_service: Any,  # EmotionService
    ) -> str:
        """获取好感度动态语境（只读缓存，返回拼接后的文本）

        设计原则：
        - 每回合仅读取 Redis 缓存，不检测变化、不重新生成
        - 冷启动（无缓存）时生成一次并写入缓存
        - 定时刷新由 affection_context_refresh_tick 调度器负责

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            emotion_service: EmotionService 实例（冷启动时用于生成）

        Returns:
            好感度语境文本（关系描述 + 动态提示词拼接，供直接注入 User Prompt）
        """
        # 1. 读缓存
        cached_data = await self._load_from_cache(character_id, user_id)
        if cached_data:
            await self._refresh_ttl(character_id, user_id)
            logger.debug(
                "[AffectionContextManager] 使用缓存: character=%s, user=%s",
                character_id, user_id,
            )
            return self._format_context_text(cached_data)

        # 2. 冷启动：无缓存时生成一次
        logger.info(
            "[AffectionContextManager] 冷启动生成好感度语境: character=%s, user=%s",
            character_id, user_id,
        )
        return await self._generate_and_cache(character_id, user_id, emotion_service)

    async def get_affection_context_structured(
        self,
        character_id: str,
        user_id: str,
        emotion_service: Any,
    ) -> dict:
        """获取好感度结构化语境（供需要单独使用关系描述和动态提示词的场景）

        Returns:
            dict with keys: levels, relationship_description, dynamic_prompt_hints, generated_at
        """
        cached_data = await self._load_from_cache(character_id, user_id)
        if cached_data:
            await self._refresh_ttl(character_id, user_id)
            return cached_data

        await self._generate_and_cache(character_id, user_id, emotion_service)
        return await self._load_from_cache(character_id, user_id) or {}

    async def check_and_regenerate(
        self,
        character_id: str,
        user_id: str,
        emotion_service: Any,
    ) -> bool:
        """定时检查：如果任何维度级别发生变化则重新生成语境

        由 affection_context_refresh_tick 调度器每 10 分钟调用。
        基于 9 级级别变化触发（任意维度 level 数字变化即触发）。

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            emotion_service: EmotionService 实例

        Returns:
            是否重新生成了语境
        """
        # 1. 获取当前各维度级别编号
        affection_state = await self._affection_service.get_state(character_id, user_id)
        current_levels = self._compute_current_levels(affection_state)

        # 2. 读取缓存中的级别
        cached_data = await self._load_from_cache(character_id, user_id)

        if not cached_data:
            # 无缓存，首次生成
            await self._generate_and_cache(character_id, user_id, emotion_service)
            return True

        # 3. 对比级别是否变化（从缓存中提取 level 数字）
        cached_levels = self._extract_level_numbers(cached_data.get("levels", {}))
        if current_levels != cached_levels:
            logger.info(
                "[AffectionContextManager] 级别变化检测到，重新生成: %s -> %s",
                cached_levels, current_levels,
            )
            await self._generate_and_cache(character_id, user_id, emotion_service)
            return True

        # 4. 无变化，仅刷新 TTL
        logger.debug(
            "[AffectionContextManager] 级别未变化，刷新 TTL: %s",
            current_levels,
        )
        await self._refresh_ttl(character_id, user_id)
        return False

    # === 清除缓存 ===

    async def clear_cache_async(self, character_id: str, user_id: str) -> None:
        """异步清除指定用户的缓存"""
        if self._affection_repo:
            try:
                await self._affection_repo.delete_context(character_id, user_id)
                logger.info("[AffectionContextManager] 清除缓存: %s:%s", character_id, user_id)
            except Exception as e:
                logger.warning("[AffectionContextManager] 缓存删除失败: %s", e)

    # === 辅助方法 ===

    @staticmethod
    def _extract_level_numbers(levels_cache: dict) -> dict[str, int]:
        """从缓存的 levels 数据中提取纯 level 编号用于对比

        Args:
            levels_cache: {"trust": {"level": 7, ...}, "intimacy": {...}, ...}

        Returns:
            {"trust": 7, "intimacy": 8, "respect": 5}
        """
        result = {}
        for dim in ["trust", "intimacy", "respect"]:
            dim_data = levels_cache.get(dim, {})
            if isinstance(dim_data, dict):
                result[dim] = dim_data.get("level", 0)
            else:
                result[dim] = dim_data  # 兼容旧格式（纯数字）
        return result

    @staticmethod
    def _compute_current_levels(state: AffectionState) -> dict[str, int]:
        """根据当前好感度状态计算各维度级别编号

        Args:
            state: 当前好感度状态

        Returns:
            {"trust": 7, "intimacy": 8, "respect": 5}
        """
        levels = {}
        for dim in AffectionDimension:
            dim_state = state.get_dimension(dim)
            levels[dim.value] = get_affection_level(dim, dim_state.total)
        return levels

    @staticmethod
    def _format_context_text(cached_data: dict) -> str:
        """将缓存数据格式化为可注入 LLM prompt 的文本

        Args:
            cached_data: 缓存数据，包含 levels / relationship_description / dynamic_prompt_hints

        Returns:
            拼接后的语境文本
        """
        parts = []

        # 级别概览
        levels_data = cached_data.get("levels", {})
        if levels_data:
            parts.append("## 好感度状态")
            dim_names = {"trust": "信任", "intimacy": "亲密", "respect": "尊重"}
            for key, dim_cn in dim_names.items():
                lv = levels_data.get(key, {})
                label = lv.get("label_zh", "") if isinstance(lv, dict) else ""
                level_num = lv.get("level", "?") if isinstance(lv, dict) else ""
                if label:
                    parts.append(f"- {dim_cn}：{label}（L{level_num}/9）")

        # 关系描述
        rel_desc = cached_data.get("relationship_description", "")
        if rel_desc:
            parts.append(f"\n## 关系描述\n{rel_desc}")

        # 动态提示词
        hints = cached_data.get("dynamic_prompt_hints", "")
        if hints:
            parts.append(f"\n## 对话提示\n{hints}")

        return "\n".join(parts)

    async def _generate_and_cache(
        self,
        character_id: str,
        user_id: str,
        emotion_service: Any,
    ) -> str:
        """生成好感度语境并写入缓存

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            emotion_service: EmotionService 实例

        Returns:
            格式化后的语境文本
        """
        generated = await self._generate_context(
            character_id, user_id, emotion_service
        )

        # 获取完整的结构化级别信息（含中文标签）
        affection_state = await self._affection_service.get_state(character_id, user_id)
        level_result = affection_state.get_levels()

        cache_data = {
            "levels": level_result.to_dict(),
            "relationship_description": generated.get("relationship_description", ""),
            "dynamic_prompt_hints": generated.get("dynamic_prompt_hints", ""),
            "generated_at": datetime.now().isoformat(),
        }

        await self._save_to_cache(character_id, user_id, cache_data)

        return self._format_context_text(cache_data)

    async def _generate_context(
        self,
        character_id: str,
        user_id: str,
        emotion_service: Any,
    ) -> dict:
        """用 LLM 生成好感度语境（关系描述 + 动态提示词）

        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            emotion_service: EmotionService 实例

        Returns:
            {"relationship_description": str, "dynamic_prompt_hints": str}
        """
        try:
            # 获取好感度状态和级别
            affection_state = await self._affection_service.get_state(character_id, user_id)
            level_result = affection_state.get_levels()

            # 获取 PAD 状态和动力学
            pad_state = emotion_service.get_state()
            pad_dict = pad_state.to_dict() if hasattr(pad_state, 'to_dict') else {"P": 0.0, "A": 0.0, "D": 0.0}

            dynamics = emotion_service.get_dynamics()
            dynamics_dict = dynamics.to_dict() if hasattr(dynamics, 'to_dict') else {"intensity": 0.0}

            # 获取角色人设
            personality_text = self._personality_text or getattr(self._character, 'personality_text', '') or ''
            emotion_traits_text = self._emotion_traits_text or getattr(self._character, 'emotion_traits_text', '') or ''

            # 构建 prompt
            prompt = build_affection_context_prompt(
                affection_state=affection_state,
                level_result=level_result,
                pad_state=pad_dict,
                pad_dynamics=dynamics_dict,
                personality_text=personality_text,
                emotion_traits_text=emotion_traits_text,
            )

            # 调用 LLM
            response = await self._llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.7,
            )

            # 解析 JSON 响应
            result = self._parse_llm_response(response)
            logger.info(
                "[AffectionContextManager] 生成好感度语境: rel=%s..., hints=%s...",
                result.get("relationship_description", "")[:30],
                result.get("dynamic_prompt_hints", "")[:30],
            )
            return result

        except Exception as e:
            logger.warning("[AffectionContextManager] LLM生成失败，使用默认语境: %s", e)
            return await self._build_default_context_dict(character_id, user_id, emotion_service)

    @staticmethod
    def _parse_llm_response(response: str) -> dict:
        """解析 LLM 的 JSON 响应"""
        import re
        # 尝试提取 JSON
        json_match = re.search(r'\{.*\}', response.strip(), re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        # 降级：整段作为关系描述
        return {
            "relationship_description": response.strip()[:200],
            "dynamic_prompt_hints": "",
        }

    async def _build_default_context_dict(
        self,
        character_id: str,
        user_id: str,
        emotion_service: Any,
    ) -> dict:
        """构建默认好感度语境字典（LLM 调用失败时使用）"""
        try:
            affection_state = await self._affection_service.get_state(character_id, user_id)
            level_result = affection_state.get_levels()

            trust_lv = level_result.trust
            intimacy_lv = level_result.intimacy

            if trust_lv.level >= 7 and intimacy_lv.level >= 7:
                rel = "对用户非常信任和亲近，愿意分享内心想法。"
                hints = "可表现出亲密、放松的对话风格，适当分享角色的内心感受。"
            elif trust_lv.level >= 6 and intimacy_lv.level >= 6:
                rel = "对用户有一定信任，愿意正常交流。"
                hints = "保持友善但不过分亲密的对话风格。"
            elif trust_lv.level >= 5:
                rel = "对用户初步信任，但还比较谨慎。"
                hints = "礼貌但有一定距离感的对话风格。"
            else:
                rel = "对用户还不太熟悉，保持谨慎态度。"
                hints = "保持礼貌的距离，对话风格偏向正式和保守。"

            return {
                "relationship_description": rel,
                "dynamic_prompt_hints": hints,
            }
        except Exception:
            return {
                "relationship_description": "与用户的关系状态正常。",
                "dynamic_prompt_hints": "",
            }

    async def _build_default_context(
        self,
        character_id: str,
        user_id: str,
        emotion_service: Any,
    ) -> str:
        """构建默认好感度语境文本（LLM 调用失败时使用，兼容旧接口）"""
        result = await self._build_default_context_dict(character_id, user_id, emotion_service)
        return result.get("relationship_description", "")
