"""日常事务场景生成器

功能：
1. 调用 LLM 生成日常场景
2. 写入 daily_life_events 表
3. 同时写入 heartbeat_events 表
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from loguru import logger

from app.agent.llm.client import LLMClient
from app.agent.character.loader import load_character, CharacterConfig
from app.agent.memory.retriever import retrieve_short_term_memories
# emotion service 导入可选（测试环境可能不存在）
try:
    from app.services.emotion import get_emotion_service
    _emotion_service_available = True
except ImportError:
    _emotion_service_available = False

from .models import DailyLifeEvent
from .config import get_daily_life_settings
from .prompts import build_daily_life_prompt, format_recent_daily_events, format_candidate_users


class DailyLifeGenerator:
    """日常事务场景生成器
    
    负责：
    1. 加载角色人设
    2. 加载短期记忆
    3. 调用 LLM 生成场景
    4. 写入数据库（daily_life_events + heartbeat_events）
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        """
        Args:
            llm_client: LLM 客户端实例（可选，默认创建新实例）
        """
        self._llm = llm_client
        self._character_config: Optional[CharacterConfig] = None
        self._settings = get_daily_life_settings()
    
    @property
    def llm(self) -> LLMClient:
        """懒加载 LLM 客户端"""
        if self._llm is None:
            self._llm = LLMClient()
            logger.info("[DailyLifeGenerator] LLM Client 已初始化")
        return self._llm
    
    def _load_character(self) -> CharacterConfig:
        """加载角色配置"""
        if self._character_config is None:
            self._character_config = load_character()
            logger.info(f"[DailyLifeGenerator] 角色配置已加载: {self._character_config.name}")
        return self._character_config
    
    async def generate(
        self,
        character_id: str = "daji",
        user_id: str = "default_user",
        candidate_users: list[dict] | None = None,
        reference_image_path: str | None = None,
    ) -> Optional[DailyLifeEvent]:
        """生成一个日常事务事件

        流程：
        1. 检查当前时间是否在活跃时段
        2. 加载角色人设
        3. 加载短期记忆
        4. 获取最近的日常事件（避免重复）
        5. 获取当前情绪状态
        6. 构建 Prompt（含候选用户列表）
        7. 调用 LLM
        8. 解析响应（含分享决策）
        9. 写入数据库
        10. 执行分享（如果 AI 决定分享）

        Args:
            character_id: 角色 ID
            user_id: 默认用户 ID
            candidate_users: 候选用户列表 [{"user_id": ..., "trust": ..., "intimacy": ..., "respect": ...}, ...]
            reference_image_path: 角色参考图路径（用于图片/视频生成）
        """
        if candidate_users is None:
            candidate_users = []
        t0 = datetime.now()
        
        # 1. 时间检查
        now = datetime.now(timezone(timedelta(hours=8)))  # 上海时间
        hour = now.hour
        
        if hour < self._settings.DAILY_LIFE_ACTIVE_START_HOUR or hour >= self._settings.DAILY_LIFE_ACTIVE_END_HOUR:
            logger.info(f"[DailyLifeGenerator] 当前时间 {hour}:00 不在活跃时段，跳过")
            return None
        
        logger.info("[DailyLifeGenerator] 开始生成日常事务事件...")
        
        # 2. 加载角色人设
        config = self._load_character()
        personality_text = config.personality_text or ""
        if config.emotion_traits_text:
            personality_text += f"\n情绪特点：{config.emotion_traits_text}"
        
        # 3. 加载短期记忆
        try:
            memory_result = await retrieve_short_term_memories(
                character_id=character_id,
                user_id=user_id,
                user_input="",  # 空输入，走固定加载模式
            )
            short_term_memory = memory_result.get("combined", "")
            logger.debug(f"[DailyLifeGenerator] 短期记忆长度: {len(short_term_memory)} chars")
        except Exception as e:
            logger.warning(f"[DailyLifeGenerator] 加载短期记忆失败: {e}")
            short_term_memory = "无"
        
        # 4. 获取最近日常事件（避免重复）
        try:
            from app.stone import get_daily_life_repo
            daily_life_repo = get_daily_life_repo()
            recent_events = await daily_life_repo.get_recent(
                character_id=character_id,
                user_id=user_id,
                limit=5,
                days=7,
            )
            recent_events_text = format_recent_daily_events(recent_events)
            logger.debug(f"[DailyLifeGenerator] 最近事件: {len(recent_events)} 条")
        except Exception as e:
            logger.warning(f"[DailyLifeGenerator] 获取最近事件失败: {e}")
            recent_events_text = "无"
        
        # 5. 获取当前情绪状态（可选）
        emotion_state_text = "愉悦度=0.30, 激活度=0.50, 支配度=0.60"
        if _emotion_service_available:
            try:
                emotion_service = await get_emotion_service()
                state = emotion_service.get_state()
                emotion_state_text = f"愉悦度={state.p:.2f}, 激活度={state.a:.2f}, 支配度={state.d:.2f}"
            except Exception as e:
                logger.warning(f"[DailyLifeGenerator] 获取情绪状态失败: {e}")
        
        # 6. 构建 Prompt
        candidate_users_text = format_candidate_users(candidate_users)
        prompt = build_daily_life_prompt(
            character_name=config.name,
            personality_text=personality_text,
            short_term_memory=short_term_memory,
            current_time=now,
            recent_daily_events=recent_events_text,
            emotion_state=emotion_state_text,
            candidate_users_text=candidate_users_text,
        )
        
        # 7. 调用 LLM
        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9,  # 较高温度增加多样性
                use_fast=False,
            )
            logger.debug(f"[DailyLifeGenerator] LLM 响应: {response[:100]}...")
        except Exception as e:
            logger.error(f"[DailyLifeGenerator] LLM 调用失败: {e}")
            return None
        
        # 8. 解析响应
        event_data = self._parse_llm_response(response)
        if event_data is None:
            logger.warning("[DailyLifeGenerator] LLM 响应解析失败")
            return None
        
        # 9. 解析分享决策
        share_data = event_data.get("share", {}) or {}
        target_user_id = share_data.get("target_user_id")
        share_text = share_data.get("share_text")
        share_image_prompt = share_data.get("share_image_prompt")
        share_video_prompt = share_data.get("share_video_prompt")

        # 10. 构建 DailyLifeEvent
        event = DailyLifeEvent(
            character_id=character_id,
            user_id=user_id,
            event_time=now,
            scenario=event_data.get("scenario", ""),
            scenario_detail=event_data.get("scenario_detail"),
            dialogue=event_data.get("dialogue"),
            inner_monologue=event_data.get("inner_monologue"),
            emotion_delta=event_data.get("emotion_delta", {"P": 0.0, "A": 0.0, "D": 0.0}),
            intensity=event_data.get("intensity", 0.3),
            target_user_id=target_user_id,
            share_text=share_text,
            share_image_prompt=share_image_prompt,
            share_video_prompt=share_video_prompt,
        )

        # 11. 写入数据库
        try:
            await self._save_event(event)
            elapsed = (datetime.now() - t0).total_seconds() * 1000
            logger.info(f"[DailyLifeGenerator] 事件生成完成: {event.to_summary()}, 耗时 {elapsed:.0f}ms")
        except Exception as e:
            logger.error(f"[DailyLifeGenerator] 写入数据库失败: {e}")
            return None

        # 12. 执行分享
        if target_user_id and share_text:
            logger.info(
                f"[DailyLifeGenerator] AI 决定分享给 {target_user_id}: "
                f"text={bool(share_text)}, image={bool(share_image_prompt)}, video={bool(share_video_prompt)}"
            )
            try:
                from app.channel.im_sender import IMSender
                sender = IMSender(
                    character_id=character_id,
                    reference_image_path=reference_image_path,
                )
                success = await sender.send_share(
                    user_id=target_user_id,
                    text=share_text,
                    image_prompt=share_image_prompt,
                    video_prompt=share_video_prompt,
                )
                if success:
                    event.share_executed = True
                    logger.info(f"[DailyLifeGenerator] 分享执行成功: user={target_user_id}")
                else:
                    logger.info(f"[DailyLifeGenerator] 分享条件不满足，已跳过: user={target_user_id}")
            except Exception as e:
                logger.warning(f"[DailyLifeGenerator] 分享执行异常: {e}")

        return event
    
    def _parse_llm_response(self, response: str) -> Optional[Dict[str, Any]]:
        """解析 LLM 响应
        
        支持：
        1. 直接 JSON 解析
        2. 从 Markdown 代码块提取
        3. raw_decode 部分解析
        """
        response = response.strip()
        
        # 尝试直接解析
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        
        # 尝试从 Markdown 代码块提取
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
        if json_match:
            try:
                return json.loads(json_match.group(1).strip())
            except json.JSONDecodeError:
                pass
        
        # 尝试 raw_decode
        try:
            decoder = json.JSONDecoder()
            result, _ = decoder.raw_decode(response)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass
        
        # 尝试提取 JSON 对象
        try:
            json_match = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', response)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
        
        logger.warning(f"[DailyLifeGenerator] 无法解析响应: {response[:200]}")
        return None
    
    async def _save_event(self, event: DailyLifeEvent) -> None:
        """保存事件到数据库（原子性事务）
        
        使用 DailyLifeEventRepository.insert_with_heartbeat 在同一个事务中完成：
        1. 写入 daily_life_events 表
        2. 写入 heartbeat_events 表
        3. 更新关联 ID
        """
        from app.stone import get_daily_life_repo
        
        daily_life_repo = get_daily_life_repo()
        
        # 构建 event_data 字典
        event_data = {
            "character_id": event.character_id,
            "user_id": event.user_id,
            "event_time": event.event_time,
            "scenario": event.scenario,
            "scenario_detail": event.scenario_detail,
            "dialogue": event.dialogue,
            "inner_monologue": event.inner_monologue,
            "emotion_delta": event.emotion_delta,
            "intensity": event.intensity,
            "target_user_id": event.target_user_id,
            "share_text": event.share_text,
            "share_image_prompt": event.share_image_prompt,
            "share_video_prompt": event.share_video_prompt,
            "share_executed": event.share_executed,
        }
        
        event_id, heartbeat_id = await daily_life_repo.insert_with_heartbeat(
            event_data=event_data,
            event_node="special_moment",
            event_subtype="surprise_event",
        )
        
        event.id = event_id
        event.heartbeat_event_id = heartbeat_id
        
        logger.info(f"[DailyLifeGenerator] 数据写入完成: event_id={event_id}, heartbeat_id={heartbeat_id}")


# ===== 全局实例 =====
_generator: Optional[DailyLifeGenerator] = None


def get_daily_life_generator(llm_client: Optional[LLMClient] = None) -> DailyLifeGenerator:
    """获取日常事务生成器实例"""
    global _generator
    if _generator is None:
        _generator = DailyLifeGenerator(llm_client)
    return _generator


def reset_daily_life_generator() -> None:
    """重置生成器（用于测试）"""
    global _generator
    _generator = None