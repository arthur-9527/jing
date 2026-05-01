"""记忆系统定时任务调度器

功能：
1. 每小时从 Redis 持久化队列批量写入 PostgreSQL + 事件提取
2. 可扩展：日记生成、周索引、月索引等任务
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from loguru import logger

from app.config import settings
from app.services.chat_history.conversation_buffer import get_conversation_buffer
from app.stone import (
    get_agent_state_repo,
    get_chat_repo,
    get_key_event_repo,
)
from app.agent.memory.extractor import get_memory_extractor
from app.agent.memory.generator import get_memory_generator


class MemoryScheduler:
    """记忆系统定时任务调度器"""

    def __init__(self):
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._running = False

    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            logger.warning("[MemoryScheduler] 已在运行中，跳过启动")
            return

        logger.info("[MemoryScheduler] 启动定时任务调度器...")

        # 创建调度器
        self._scheduler = AsyncIOScheduler()

        # 注册定时任务
        self._register_jobs()

        # 启动调度器
        self._scheduler.start()
        self._running = True

        logger.info("[MemoryScheduler] 定时任务调度器已启动")

    async def stop(self) -> None:
        """停止调度器"""
        if not self._running or not self._scheduler:
            return

        logger.info("[MemoryScheduler] 停止定时任务调度器...")
        self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("[MemoryScheduler] 定时任务调度器已停止")

    def _register_jobs(self) -> None:
        """注册定时任务"""
        # 任务1: 每小时从 Redis 持久化队列写入 PostgreSQL
        self._scheduler.add_job(
            self._job_flush_persistent_queue,
            trigger=CronTrigger(minute=0),  # 每小时整点执行
            id="flush_persistent_queue",
            name="持久化队列写入数据库",
            replace_existing=True,
        )
        logger.info("[MemoryScheduler] 已注册任务: flush_persistent_queue (每小时整点)")

        # 任务2: 每天凌晨2点生成日记
        self._scheduler.add_job(
            self._job_generate_daily_diary,
            trigger=CronTrigger(hour=2, minute=0),
            id="generate_daily_diary",
            name="生成日记",
            replace_existing=True,
        )
        logger.info("[MemoryScheduler] 已注册任务: generate_daily_diary (每天02:00)")

        # 任务3: 每周日凌晨3点生成周索引
        self._scheduler.add_job(
            self._job_generate_weekly_index,
            trigger=CronTrigger(day_of_week=0, hour=3, minute=0),  # 周日3点
            id="generate_weekly_index",
            name="生成周索引",
            replace_existing=True,
        )
        logger.info("[MemoryScheduler] 已注册任务: generate_weekly_index (每周日03:00)")

        # 任务4: 每月最后一天凌晨4点生成月索引
        self._scheduler.add_job(
            self._job_generate_monthly_index,
            trigger=CronTrigger(day=28, hour=4, minute=0),  # 28号4点（避免31号问题）
            id="generate_monthly_index",
            name="生成月索引",
            replace_existing=True,
        )
        logger.info("[MemoryScheduler] 已注册任务: generate_monthly_index (每月28日04:00)")

        # 任务5: 每年12月31日凌晨5点生成年索引
        self._scheduler.add_job(
            self._job_generate_annual_index,
            trigger=CronTrigger(month=12, day=31, hour=5, minute=0),
            id="generate_annual_index",
            name="生成年索引",
            replace_existing=True,
        )
        logger.info("[MemoryScheduler] 已注册任务: generate_annual_index (每年12月31日05:00)")

    async def _job_flush_persistent_queue(self) -> None:
        """定时任务：从 Redis 持久化队列写入 PostgreSQL + 事件提取
        
        流程：
        1. 从 Redis 扫描所有用户的持久化队列（多用户支持）
        2. 遍历每个队列，批量写入 PostgreSQL chat_messages 表
        3. LLM 提取关键事件 + 心动事件
        4. 写入 key_events / heartbeat_events 表
        5. 标记消息已提取
        6. 清空持久化队列
        
        ⭐ 支持多用户：动态扫描所有 memory:buffer:chat:* 队列
        """
        logger.info("[MemoryScheduler] 开始执行持久化队列写入任务...")

        try:
            # ⭐ 动态扫描所有用户的持久化队列
            all_queues = await self._scan_all_persistent_queues()
            
            if not all_queues:
                logger.info("[MemoryScheduler] 没有持久化队列，跳过写入")
                return
            
            logger.info(f"[MemoryScheduler] 发现 {len(all_queues)} 个持久化队列待处理")
            
            total_written = 0
            
            # 遍历每个队列执行写入
            for queue_info in all_queues:
                character_id = queue_info["character_id"]
                user_id = queue_info["user_id"]
                queue_key = queue_info["queue_key"]
                
                try:
                    written_count = await self._flush_single_queue(
                        character_id=character_id,
                        user_id=user_id,
                    )
                    total_written += written_count
                    
                except Exception as e:
                    logger.error(f"[MemoryScheduler] 队列 {queue_key} 写入失败: {e}")
                    continue
            
            logger.info(f"[MemoryScheduler] 持久化队列写入任务完成，总共写入 {total_written} 条消息")

        except Exception as e:
            logger.error(f"[MemoryScheduler] 持久化队列写入任务失败: {e}")
    
    async def _scan_all_persistent_queues(self) -> list[dict]:
        """扫描 Redis 中所有用户的持久化队列
        
        Returns:
            队列信息列表：[{"character_id", "user_id", "queue_key", "length"}, ...]
        """
        try:
            from app.stone import get_redis_pool
            from app.stone.key_builder import RedisKeyBuilder
            redis_client = await get_redis_pool().get_client()

            # 扫描所有持久化队列（使用 Stone Key 模式）
            _kb = RedisKeyBuilder()
            pattern = _kb.build("conversation_persistent", channel="*", user_id="*")
            queues = []
            
            async for key in redis_client.scan_iter(match=pattern):
                # 解析 key: agent:conv:persistent:{character_id}:{user_id}
                # parts: ["agent", "conv", "persistent", "{character_id}", "{user_id}"]
                parts = key.split(":")
                if len(parts) >= 5:
                    character_id = parts[3]
                    user_id = parts[4]
                    
                    # 获取队列长度
                    length = await redis_client.llen(key)
                    
                    if length > 0:
                        queues.append({
                            "character_id": character_id,
                            "user_id": user_id,
                            "queue_key": key,
                            "length": length,
                        })
                        logger.debug(f"[MemoryScheduler] 发现队列: {key}, 消息数: {length}")
            
            
            # 按消息数排序，优先处理大队列
            queues.sort(key=lambda x: x["length"], reverse=True)
            
            return queues
            
        except Exception as e:
            logger.error(f"[MemoryScheduler] 扫描持久化队列失败: {e}")
            return []
    
    async def _flush_single_queue(
        self,
        character_id: str,
        user_id: str,
    ) -> int:
        """处理单个用户的持久化队列
        
        Args:
            character_id: 角色ID
            user_id: 用户ID
        
        Returns:
            写入的消息条数
        """
        buffer = await get_conversation_buffer(
            user_id=user_id,
            character_id=character_id,
        )

        # 获取持久化队列中的消息
        messages = await buffer.get_all_persistent_messages()

        if not messages:
            logger.info(f"[MemoryScheduler] 队列 {character_id}:{user_id} 为空，跳过")
            return 0

        logger.info(f"[MemoryScheduler] 队列 {character_id}:{user_id} 有 {len(messages)} 条消息待写入")

        # 转换消息格式为数据库格式（使用消息本身的 character_id/user_id）
        db_messages = []
        for msg in messages:
            db_msg = {
                "character_id": msg.get("character_id", character_id),
                "user_id": msg.get("user_id", user_id),
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
                "inner_monologue": msg.get("inner_monologue"),
                "turn_id": msg.get("turn_id"),
                "metadata": {
                    "ts": msg.get("ts"),
                    "item_id": msg.get("item_id"),
                },
            }
            db_messages.append(db_msg)

        # 使用 Stone Repository 批量写入
        chat_repo = get_chat_repo()
        record_ids = await chat_repo.batch_insert(db_messages)
        logger.info(f"[MemoryScheduler] 成功写入 {len(record_ids)} 条消息到 chat_messages ({character_id}:{user_id})")

        # 事件提取（如果有消息写入成功）
        if record_ids:
            await self._extract_events_from_messages(
                messages=messages,
                record_ids=record_ids,
                character_id=character_id,
                user_id=user_id,
            )

        # 清空持久化队列
        await buffer.clear_persistent_queue()

        return len(record_ids)

    async def _extract_events_from_messages(
        self,
        messages: list[dict],
        record_ids: list[int],
        character_id: str,
        user_id: str,
    ) -> None:
        """从消息中提取关键事件
        
        Args:
            messages: 原始消息列表
            record_ids: 写入数据库后的消息ID列表
            character_id: 角色ID
            user_id: 用户ID
        """
        logger.info("[MemoryScheduler] 开始事件提取...")
        
        try:
            # 获取提取器
            extractor = get_memory_extractor()
            
            # 提取关键事件
            key_events = await extractor.extract_key_events(
                messages=messages,
                character_id=character_id,
                user_id=user_id,
                source_message_ids=record_ids,
            )
            
            # 写入关键事件
            if key_events:
                key_event_repo = get_key_event_repo()
                key_event_ids = await key_event_repo.batch_insert(key_events)
                logger.info(f"[MemoryScheduler] 写入 {len(key_event_ids)} 条关键事件")
            
            # 标记消息已提取
            chat_repo = get_chat_repo()
            marked_count = await chat_repo.mark_extracted(record_ids)
            logger.info(f"[MemoryScheduler] 标记 {marked_count} 条消息已提取")
            
        except Exception as e:
            logger.error(f"[MemoryScheduler] 事件提取失败: {e}")

    async def _job_generate_daily_diary(self) -> None:
        """定时任务：每天凌晨2点生成日记（遍历所有用户）

        ⭐ 多用户：遍历所有活跃的 (character_id, user_id) 组合
        ⭐ 新增：日记生成后执行理性好感度评估 + 结算
        """
        logger.info("[MemoryScheduler] 开始执行日记生成任务...")

        try:
            from datetime import date, timedelta
            yesterday = date.today() - timedelta(days=1)

            agent_state_repo = get_agent_state_repo()
            active_users = await agent_state_repo.get_all_active_users()
            if not active_users:
                logger.info("[MemoryScheduler] 无活跃用户，跳过日记生成")
                return

            logger.info(f"[MemoryScheduler] 发现 {len(active_users)} 个活跃用户，开始生成日记")
            generator = get_memory_generator()

            for user_info in active_users:
                character_id = user_info["character_id"]
                user_id = user_info["user_id"]

                try:
                    diary_id = await generator.generate_daily_diary(
                        character_id=character_id,
                        user_id=user_id,
                        target_date=yesterday,
                    )

                    if diary_id:
                        logger.info(f"[MemoryScheduler] 日记生成成功: {character_id}:{user_id}, id={diary_id}, date={yesterday}")
                        await self._settle_affection_on_diary(
                            character_id=character_id,
                            user_id=user_id,
                            diary_id=diary_id,
                            diary_date=yesterday,
                        )
                    else:
                        logger.info(f"[MemoryScheduler] 日记生成跳过: {character_id}:{user_id} (无数据)")

                except Exception as e:
                    logger.error(f"[MemoryScheduler] 用户 {character_id}:{user_id} 日记生成失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"[MemoryScheduler] 日记生成任务失败: {e}")
    
    async def _settle_affection_on_diary(
        self,
        character_id: str,
        user_id: str,
        diary_id: str,
        diary_date: "date",
    ) -> None:
        """日记生成后执行理性好感度评估 + 结算
        
        流程：
        1. 获取日记内容（用于理性评估）
        2. 获取好感度服务
        3. 构建理性评估 prompt（带人设）
        4. LLM 评估理性增量
        5. 执行 settle_on_diary（感性总结 + 理性增量 → 更新 base）
        
        Args:
            character_id: 角色 ID
            user_id: 用户 ID
            diary_id: 日记 ID
            diary_date: 日记日期
        """
        try:
            # 1. 获取日记内容（get_daily_diary 返回单个 dict 或 None）
            from app.agent.memory.retriever import get_daily_diary

            diary_row = await get_daily_diary(character_id, user_id, diary_date)
            if not diary_row:
                logger.info("[MemoryScheduler] 无日记内容，跳过好感度结算")
                return

            diary_content = diary_row.get("summary", "") or diary_row.get("content", "")

            if not diary_content.strip():
                logger.info("[MemoryScheduler] 日记内容为空，跳过好感度结算")
                return

            logger.info(f"[MemoryScheduler] 日记内容长度: {len(diary_content)} chars")
            
            # 2. 获取好感度服务
            from app.services.affection import get_affection_service
            affection_service = await get_affection_service()
            
            # 3. 获取角色人设（用于理性评估）
            personality_text = await self._load_personality_text(character_id)
            
            # 4. 构建理性评估 prompt 并调用 LLM
            from app.services.affection.prompts import build_rational_assessment_prompt
            from app.services.affection.models import AffectionAssessment
            from app.agent.llm.client import LLMClient
            
            # 获取当前好感度状态
            from app.services.affection.models import AffectionDimension
            affection_state = await affection_service.get_state(character_id, user_id)

            # 计算各维度感性总结（使用 AffectionService 共享方法）
            emotional_summaries = affection_service.compute_emotional_summaries(affection_state)

            # 构建理性评估 prompt
            rational_prompt = build_rational_assessment_prompt(
                affection_state=affection_state,
                emotional_summaries=emotional_summaries,
                diary_content=diary_content,
                personality_text=personality_text,
            )
            
            # 调用 LLM
            llm = LLMClient()
            response = await llm.chat(
                [{"role": "user", "content": rational_prompt}],
                temperature=0.3,
            )
            
            # 解析评估结果
            import json
            try:
                assessment_dict = json.loads(response.strip())
                rational_assessment = AffectionAssessment.from_dict(assessment_dict)
                logger.info(
                    "[MemoryScheduler] 理性好感度评估: trust={:.2f}, intimacy={:.2f}, respect={:.2f}",
                    rational_assessment.trust_delta,
                    rational_assessment.intimacy_delta,
                    rational_assessment.respect_delta,
                )
            except json.JSONDecodeError:
                logger.warning("[MemoryScheduler] 理性评估结果解析失败，使用默认值")
                rational_assessment = AffectionAssessment()
            
            # 5. 执行结算（感性总结 + 理性增量 → 更新 base）
            result = await affection_service.settle_on_diary(
                character_id=character_id,
                user_id=user_id,
                diary_rational_delta=rational_assessment,
            )
            
            logger.info(
                "[MemoryScheduler] 好感度结算完成: emotional={}, rational={}, new_bases={}",
                result.get("emotional_summaries"),
                result.get("rational_deltas"),
                result.get("new_bases"),
            )
            
        except Exception as e:
            logger.error(f"[MemoryScheduler] 好感度结算失败: {e}")
    
    async def _load_personality_text(self, character_id: str) -> str:
        """加载角色人设文本
        
        Args:
            character_id: 角色 ID
            
        Returns:
            人设文本（用于理性评估）
        """
        try:
            import os
            from app.config import settings
            
            # 获取角色配置路径
            config_path = getattr(settings, 'CHARACTER_CONFIG_PATH', 'config/characters/daji')
            
            # 解析路径
            if config_path.endswith('.json'):
                character_dir = config_path.replace('.json', '')
            else:
                character_dir = config_path
            
            # 构建 personality.md 路径
            if os.path.isabs(character_dir):
                personality_path = os.path.join(character_dir, 'personality.md')
            else:
                personality_path = os.path.join(os.getcwd(), character_dir, 'personality.md')
            
            if os.path.exists(personality_path):
                with open(personality_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 提取性格特点部分
                personality_text = ""
                sections = content.split('##')
                for section in sections:
                    section_lower = section.lower()
                    if '性格' in section_lower or '人设' in section_lower:
                        personality_text = section.strip()
                        break
                
                return personality_text
            else:
                logger.warning(f"[MemoryScheduler] personality.md 不存在: {personality_path}")
                return ""
                
        except Exception as e:
            logger.warning(f"[MemoryScheduler] 加载人设失败: {e}")
            return ""

    async def _job_generate_weekly_index(self) -> None:
        """定时任务：每周日凌晨3点生成周索引 + 清理旧聊天记录（遍历所有用户）

        ⭐ 多用户：遍历所有活跃的 (character_id, user_id) 组合
        """
        logger.info("[MemoryScheduler] 开始执行周索引生成任务...")

        try:
            from datetime import date, timedelta
            today = date.today()
            week_start = today - timedelta(days=today.weekday() + 7)
            week_end = week_start + timedelta(days=6)

            agent_state_repo = get_agent_state_repo()
            active_users = await agent_state_repo.get_all_active_users()
            if not active_users:
                logger.info("[MemoryScheduler] 无活跃用户，跳过周索引生成")
                return

            logger.info(f"[MemoryScheduler] 发现 {len(active_users)} 个活跃用户，开始生成周索引")
            generator = get_memory_generator()

            for user_info in active_users:
                character_id = user_info["character_id"]
                user_id = user_info["user_id"]

                try:
                    weekly_id = await generator.generate_weekly_index(
                        character_id=character_id,
                        user_id=user_id,
                        week_start=week_start,
                    )

                    if weekly_id:
                        logger.info(f"[MemoryScheduler] 周索引生成成功: {character_id}:{user_id}, id={weekly_id}, week={week_start}~{week_end}")
                    else:
                        logger.info(f"[MemoryScheduler] 周索引生成跳过: {character_id}:{user_id} (无日记)")

                    # 使用 Stone Repository 清理旧消息
                    chat_repo = get_chat_repo()
                    deleted_count = await chat_repo.cleanup(
                        character_id=character_id,
                        user_id=user_id,
                        days=14,
                    )
                    if deleted_count > 0:
                        logger.info(f"[MemoryScheduler] 清理完成: {character_id}:{user_id} 删除 {deleted_count} 条14天前的聊天记录")

                except Exception as e:
                    logger.error(f"[MemoryScheduler] 用户 {character_id}:{user_id} 周索引生成失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"[MemoryScheduler] 周索引生成任务失败: {e}")

    async def _job_generate_monthly_index(self) -> None:
        """定时任务：每月28号凌晨4点生成月索引（遍历所有用户）

        ⭐ 多用户：遍历所有活跃的 (character_id, user_id) 组合
        """
        logger.info("[MemoryScheduler] 开始执行月索引生成任务...")

        try:
            from datetime import date, timedelta
            today = date.today()
            if today.month == 1:
                year = today.year - 1
                month = 12
            else:
                year = today.year
                month = today.month - 1

            agent_state_repo = get_agent_state_repo()
            active_users = await agent_state_repo.get_all_active_users()
            if not active_users:
                logger.info("[MemoryScheduler] 无活跃用户，跳过月索引生成")
                return

            logger.info(f"[MemoryScheduler] 发现 {len(active_users)} 个活跃用户，开始生成月索引")
            generator = get_memory_generator()

            for user_info in active_users:
                character_id = user_info["character_id"]
                user_id = user_info["user_id"]

                try:
                    monthly_id = await generator.generate_monthly_index(
                        character_id=character_id,
                        user_id=user_id,
                        year=year,
                        month=month,
                    )

                    if monthly_id:
                        logger.info(f"[MemoryScheduler] 月索引生成成功: {character_id}:{user_id}, id={monthly_id}, {year}-{month}")
                    else:
                        logger.info(f"[MemoryScheduler] 月索引生成跳过: {character_id}:{user_id}, {year}-{month} (无周索引)")

                except Exception as e:
                    logger.error(f"[MemoryScheduler] 用户 {character_id}:{user_id} 月索引生成失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"[MemoryScheduler] 月索引生成任务失败: {e}")

    async def _job_generate_annual_index(self) -> None:
        """定时任务：每年12月31日凌晨5点生成年索引（遍历所有用户）

        ⭐ 多用户：遍历所有活跃的 (character_id, user_id) 组合
        """
        logger.info("[MemoryScheduler] 开始执行年索引生成任务...")

        try:
            from datetime import date
            year = date.today().year

            agent_state_repo = get_agent_state_repo()
            active_users = await agent_state_repo.get_all_active_users()
            if not active_users:
                logger.info("[MemoryScheduler] 无活跃用户，跳过年索引生成")
                return

            logger.info(f"[MemoryScheduler] 发现 {len(active_users)} 个活跃用户，开始生成年索引")
            generator = get_memory_generator()

            for user_info in active_users:
                character_id = user_info["character_id"]
                user_id = user_info["user_id"]

                try:
                    annual_id = await generator.generate_annual_index(
                        character_id=character_id,
                        user_id=user_id,
                        year=year,
                    )

                    if annual_id:
                        logger.info(f"[MemoryScheduler] 年索引生成成功: {character_id}:{user_id}, id={annual_id}, year={year}")
                    else:
                        logger.info(f"[MemoryScheduler] 年索引生成跳过: {character_id}:{user_id}, year={year} (无月索引)")

                except Exception as e:
                    logger.error(f"[MemoryScheduler] 用户 {character_id}:{user_id} 年索引生成失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"[MemoryScheduler] 年索引生成任务失败: {e}")

    async def flush_now(self, user_id: str = "default_user", character_id: str = "daji") -> int:
        """立即执行持久化队列写入（手动触发）
        
        Args:
            user_id: 用户ID
            character_id: 角色ID
        
        Returns:
            写入的消息条数
        """
        logger.info(f"[MemoryScheduler] 手动触发持久化队列写入: user={user_id}, character={character_id}")

        try:
            buffer = await get_conversation_buffer(user_id=user_id, character_id=character_id)
            messages = await buffer.get_all_persistent_messages()

            if not messages:
                return 0

            db_messages = []
            for msg in messages:
                db_msg = {
                    "character_id": msg.get("character_id", character_id),
                    "user_id": msg.get("user_id", user_id),
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                    "inner_monologue": msg.get("inner_monologue"),
                    "turn_id": msg.get("turn_id"),
                    "metadata": {
                        "ts": msg.get("ts"),
                        "item_id": msg.get("item_id"),
                    },
                }
                db_messages.append(db_msg)

            chat_repo = get_chat_repo()
            record_ids = await chat_repo.batch_insert(db_messages)
            await buffer.clear_persistent_queue()

            return len(record_ids)

        except Exception as e:
            logger.error(f"[MemoryScheduler] 手动写入失败: {e}")
            return 0

    async def run_all_tasks_now(self) -> dict:
        """手动触发所有定时任务（测试用）
        
        Returns:
            各任务的执行结果
        """
        logger.info("[MemoryScheduler] 手动触发所有定时任务...")
        
        results = {}
        
        try:
            # 执行持久化队列写入
            await self._job_flush_persistent_queue()
            results["flush"] = "done"
        except Exception as e:
            results["flush"] = f"error: {e}"
        
        try:
            # 执行日记生成
            await self._job_generate_daily_diary()
            results["diary"] = "done"
        except Exception as e:
            results["diary"] = f"error: {e}"
        
        try:
            # 执行周索引生成
            await self._job_generate_weekly_index()
            results["weekly"] = "done"
        except Exception as e:
            results["weekly"] = f"error: {e}"
        
        try:
            # 执行月索引生成
            await self._job_generate_monthly_index()
            results["monthly"] = "done"
        except Exception as e:
            results["monthly"] = f"error: {e}"
        
        try:
            # 执行年索引生成
            await self._job_generate_annual_index()
            results["annual"] = "done"
        except Exception as e:
            results["annual"] = f"error: {e}"
        
        # 注意：清理任务已合并到周索引任务中，不再单独执行
        
        logger.info(f"[MemoryScheduler] 所有任务执行完成: {results}")
        return results


# 全局实例
_scheduler: Optional[MemoryScheduler] = None


def get_memory_scheduler() -> MemoryScheduler:
    """获取全局调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = MemoryScheduler()
    return _scheduler


async def start_memory_scheduler() -> None:
    """启动全局调度器"""
    scheduler = get_memory_scheduler()
    await scheduler.start()


async def stop_memory_scheduler() -> None:
    """停止全局调度器"""
    global _scheduler
    if _scheduler:
        await _scheduler.stop()