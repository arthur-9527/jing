"""记忆生成器模块

功能：
1. 日记生成 - 从当天的聊天记录和事件生成日记
2. 周索引生成 - 从本周日记生成周索引
3. 月索引生成 - 从本月周索引生成月索引
4. 年索引生成 - 从本年月索引生成年索引

使用方式：
    generator = get_memory_generator()
    diary_id = await generator.generate_daily_diary(character_id, user_id, target_date)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from loguru import logger

from app.agent.llm.client import LLMClient
from app.agent.memory.prompts import (
    format_diary_prompt,
    format_weekly_index_prompt,
    format_monthly_index_prompt,
    format_annual_index_prompt,
)
from app.agent.db.memory_models import (
    # 日记相关
    get_recent_chat_messages,
    get_recent_key_events,
    get_recent_heartbeat_events,
    upsert_daily_diary,
    get_daily_diary,
    get_recent_daily_diaries,
    # 周索引相关
    insert_weekly_index,
    get_weekly_index_by_date,
    # 月索引相关
    insert_monthly_index,
    get_monthly_index,
    # 年索引相关
    insert_annual_index,
    get_annual_index,
)
from app.services.local_embedding import get_embedding


class MemoryGenerator:
    """记忆生成器
    
    负责：
    1. 日记生成（从聊天记录和事件）
    2. 周索引生成（从日记）
    3. 月索引生成（从周索引）
    4. 年索引生成（从月索引）
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        """
        Args:
            llm_client: LLM 客户端实例（可选，默认创建新实例）
        """
        self._llm = llm_client
    
    @property
    def llm(self) -> LLMClient:
        """懒加载 LLM 客户端"""
        if self._llm is None:
            self._llm = LLMClient()
            logger.info("[MemoryGenerator] LLM Client 已初始化")
        return self._llm
    
    async def generate_daily_diary(
        self,
        character_id: str,
        user_id: str,
        target_date: Optional[date] = None,
    ) -> Optional[int]:
        """生成指定日期的日记
        
        流程：
        1. 获取当天的聊天记录
        2. 获取当天的关键事件
        3. 获取当天的心动事件
        4. LLM 生成日记内容
        5. 生成 embedding
        6. 写入数据库
        
        Args:
            character_id: 角色ID
            user_id: 用户ID
            target_date: 目标日期（默认为昨天）
        
        Returns:
            日记ID，如果没有数据则返回 None
        """
        if target_date is None:
            target_date = date.today() - timedelta(days=1)  # 默认昨天
        
        logger.info(f"[MemoryGenerator] 开始生成日记: {target_date}")
        
        t0 = datetime.now()
        
        # 1. 获取当天的聊天记录
        chat_messages = await get_recent_chat_messages(
            character_id=character_id,
            user_id=user_id,
            limit=100,
            days=1,  # 只获取当天的
        )
        
        # 过滤出目标日期的消息（注意数据库存储的是 UTC 时间）
        # 本地日期 target_date 对应的 UTC 时间范围
        tz_shanghai = timezone(timedelta(hours=8))
        
        # 将本地日期转换为 UTC 时间范围
        local_start = datetime.combine(target_date, datetime.min.time(), tzinfo=tz_shanghai)
        local_end = datetime.combine(target_date, datetime.max.time(), tzinfo=tz_shanghai)
        
        # 转换为 UTC
        target_datetime_start = local_start.astimezone(timezone.utc)
        target_datetime_end = local_end.astimezone(timezone.utc)
        
        day_messages = [
            msg for msg in chat_messages
            if target_datetime_start <= msg.get("created_at", datetime.min.replace(tzinfo=timezone.utc)) <= target_datetime_end
        ]
        
        if not day_messages:
            logger.warning(f"[MemoryGenerator] {target_date} 没有聊天记录，跳过日记生成")
            return None
        
        logger.info(f"[MemoryGenerator] 获取到 {len(day_messages)} 条聊天记录")
        
        # 2. 获取当天的关键事件
        key_events = await get_recent_key_events(
            character_id=character_id,
            user_id=user_id,
            days=1,
            limit=50,
        )
        
        # 过滤目标日期
        day_key_events = [
            evt for evt in key_events
            if evt.get("created_at", datetime.min.replace(tzinfo=timezone.utc)).date() == target_date
        ]
        
        logger.info(f"[MemoryGenerator] 获取到 {len(day_key_events)} 条关键事件")
        
        # 3. 获取当天的心动事件
        heartbeat_events = await get_recent_heartbeat_events(
            character_id=character_id,
            user_id=user_id,
            days=1,
            limit=50,
        )
        
        # 过滤目标日期
        day_heartbeat = [
            evt for evt in heartbeat_events
            if evt.get("created_at", datetime.min.replace(tzinfo=timezone.utc)).date() == target_date
        ]
        
        logger.info(f"[MemoryGenerator] 获取到 {len(day_heartbeat)} 条心动事件")
        
        # 4. 格式化对话摘要
        conversation_summary = self._format_conversation_summary(day_messages)
        key_events_text = self._format_key_events(day_key_events)
        heartbeat_text = self._format_heartbeat_events(day_heartbeat)
        
        # 5. LLM 生成日记
        prompt = format_diary_prompt(
            conversation_summary=conversation_summary,
            key_events=key_events_text,
            heartbeat_events=heartbeat_text,
        )
        
        try:
            diary_content = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                use_fast=False,  # 日记生成使用主模型
            )
            
            logger.debug(f"[MemoryGenerator] 日记内容: {diary_content[:100]}...")
            
        except Exception as e:
            logger.error(f"[MemoryGenerator] LLM 生成日记失败: {e}")
            return None
        
        # 6. 生成 embedding
        try:
            embedding = await get_embedding(diary_content)
            logger.debug(f"[MemoryGenerator] Embedding 维度: {len(embedding)}")
        except Exception as e:
            logger.warning(f"[MemoryGenerator] 生成 embedding 失败: {e}")
            embedding = None
        
        # 7. 写入数据库
        key_event_ids = [evt["id"] for evt in day_key_events]
        heartbeat_ids = [evt["id"] for evt in day_heartbeat]
        message_ids = [msg["id"] for msg in day_messages]
        
        diary_id = await upsert_daily_diary(
            character_id=character_id,
            user_id=user_id,
            diary_date=target_date,
            summary=diary_content,
            embedding=embedding,
            key_event_ids=key_event_ids,
            heartbeat_ids=heartbeat_ids,
            source_message_ids=message_ids,
            highlight_count=len(day_heartbeat),
        )
        
        elapsed = (datetime.now() - t0).total_seconds() * 1000
        logger.info(f"[MemoryGenerator] 日记生成完成: id={diary_id}, 耗时 {elapsed:.0f}ms")
        
        return diary_id
    
    async def generate_weekly_index(
        self,
        character_id: str,
        user_id: str,
        week_start: Optional[date] = None,
    ) -> Optional[int]:
        """生成周索引
        
        Args:
            character_id: 角色ID
            user_id: 用户ID
            week_start: 周起始日期（默认为上周一）
        
        Returns:
            周索引ID
        """
        if week_start is None:
            # 默认上周一
            today = date.today()
            days_since_monday = today.weekday()
            this_monday = today - timedelta(days=days_since_monday)
            week_start = this_monday - timedelta(days=7)
        
        week_end = week_start + timedelta(days=6)
        
        logger.info(f"[MemoryGenerator] 开始生成周索引: {week_start} ~ {week_end}")
        
        t0 = datetime.now()
        
        # 1. 获取本周的日记
        diaries = await get_recent_daily_diaries(
            character_id=character_id,
            user_id=user_id,
            days=14,  # 获取最近两周
            limit=14,
        )
        
        # 过滤出本周的日记
        week_diaries = [
            d for d in diaries
            if week_start <= d.get("diary_date", date.min) <= week_end
        ]
        
        if not week_diaries:
            logger.warning(f"[MemoryGenerator] 本周没有日记，跳过周索引生成")
            return None
        
        logger.info(f"[MemoryGenerator] 获取到 {len(week_diaries)} 篇日记")
        
        # 2. 格式化日记摘要
        diary_summaries = "\n\n---\n\n".join([
            f"【{d['diary_date']}】\n{d['summary']}"
            for d in sorted(week_diaries, key=lambda x: x['diary_date'])
        ])
        
        # 3. LLM 生成周索引
        prompt = format_weekly_index_prompt(diary_summaries=diary_summaries)
        
        try:
            summary = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                use_fast=False,
            )
        except Exception as e:
            logger.error(f"[MemoryGenerator] LLM 生成周索引失败: {e}")
            return None
        
        # 4. 生成 embedding
        try:
            embedding = await get_embedding(summary)
        except Exception as e:
            logger.warning(f"[MemoryGenerator] 生成 embedding 失败: {e}")
            embedding = None
        
        # 5. 写入数据库
        diary_ids = [d["id"] for d in week_diaries]
        
        weekly_id = await insert_weekly_index(
            character_id=character_id,
            user_id=user_id,
            week_start=week_start,
            week_end=week_end,
            summary=summary,
            embedding=embedding,
            diary_ids=diary_ids,
        )
        
        elapsed = (datetime.now() - t0).total_seconds() * 1000
        logger.info(f"[MemoryGenerator] 周索引生成完成: id={weekly_id}, 耗时 {elapsed:.0f}ms")
        
        return weekly_id
    
    async def generate_monthly_index(
        self,
        character_id: str,
        user_id: str,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> Optional[int]:
        """生成月索引
        
        Args:
            character_id: 角色ID
            user_id: 用户ID
            year: 年份（默认去年）
            month: 月份（默认上月）
        
        Returns:
            月索引ID
        """
        if year is None or month is None:
            # 默认上月
            today = date.today()
            if month is None:
                month = today.month - 1
                if month == 0:
                    month = 12
                    year = today.year - 1
                else:
                    year = today.year
            elif year is None:
                year = today.year
        
        logger.info(f"[MemoryGenerator] 开始生成月索引: {year}-{month:02d}")
        
        t0 = datetime.now()
        
        # 1. 获取本月的周索引
        # 计算本月第一天和最后一天
        month_start = date(year, month, 1)
        if month == 12:
            month_end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = date(year, month + 1, 1) - timedelta(days=1)
        
        # 查询本月所有周索引
        # 这里简化处理：通过日期范围查询
        weekly_indices = []
        current = month_start
        while current <= month_end:
            weekly = await get_weekly_index_by_date(
                character_id=character_id,
                user_id=user_id,
                target_date=current,
            )
            if weekly and weekly not in weekly_indices:
                weekly_indices.append(weekly)
            current += timedelta(days=7)
        
        if not weekly_indices:
            logger.warning(f"[MemoryGenerator] 本月没有周索引，跳过月索引生成")
            return None
        
        logger.info(f"[MemoryGenerator] 获取到 {len(weekly_indices)} 个周索引")
        
        # 2. 格式化周索引摘要
        weekly_summaries = "\n\n---\n\n".join([
            f"【第{w['week_start']}周】\n{w['summary']}"
            for w in sorted(weekly_indices, key=lambda x: x['week_start'])
        ])
        
        # 3. LLM 生成月索引
        prompt = format_monthly_index_prompt(weekly_summaries=weekly_summaries)
        
        try:
            summary = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                use_fast=False,
            )
        except Exception as e:
            logger.error(f"[MemoryGenerator] LLM 生成月索引失败: {e}")
            return None
        
        # 4. 生成 embedding
        try:
            embedding = await get_embedding(summary)
        except Exception as e:
            logger.warning(f"[MemoryGenerator] 生成 embedding 失败: {e}")
            embedding = None
        
        # 5. 写入数据库
        weekly_ids = [w["id"] for w in weekly_indices]
        
        monthly_id = await insert_monthly_index(
            character_id=character_id,
            user_id=user_id,
            year=year,
            month=month,
            summary=summary,
            embedding=embedding,
            weekly_ids=weekly_ids,
        )
        
        elapsed = (datetime.now() - t0).total_seconds() * 1000
        logger.info(f"[MemoryGenerator] 月索引生成完成: id={monthly_id}, 耗时 {elapsed:.0f}ms")
        
        return monthly_id
    
    async def generate_annual_index(
        self,
        character_id: str,
        user_id: str,
        year: Optional[int] = None,
    ) -> Optional[int]:
        """生成年索引
        
        Args:
            character_id: 角色ID
            user_id: 用户ID
            year: 年份（默认去年）
        
        Returns:
            年索引ID
        """
        if year is None:
            year = date.today().year - 1  # 默认去年
        
        logger.info(f"[MemoryGenerator] 开始生成年索引: {year}")
        
        t0 = datetime.now()
        
        # 1. 获取本年的月索引
        monthly_indices = []
        for month in range(1, 13):
            monthly = await get_monthly_index(
                character_id=character_id,
                user_id=user_id,
                year=year,
                month=month,
            )
            if monthly:
                monthly_indices.append(monthly)
        
        if not monthly_indices:
            logger.warning(f"[MemoryGenerator] 本年没有月索引，跳过年索引生成")
            return None
        
        logger.info(f"[MemoryGenerator] 获取到 {len(monthly_indices)} 个月索引")
        
        # 2. 格式化月索引摘要
        monthly_summaries = "\n\n---\n\n".join([
            f"【{m['year']}年{m['month']}月】\n{m['summary']}"
            for m in sorted(monthly_indices, key=lambda x: (x['year'], x['month']))
        ])
        
        # 3. LLM 生成年索引
        prompt = format_annual_index_prompt(monthly_summaries=monthly_summaries)
        
        try:
            summary = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                use_fast=False,
            )
        except Exception as e:
            logger.error(f"[MemoryGenerator] LLM 生成年索引失败: {e}")
            return None
        
        # 4. 生成 embedding
        try:
            embedding = await get_embedding(summary)
        except Exception as e:
            logger.warning(f"[MemoryGenerator] 生成 embedding 失败: {e}")
            embedding = None
        
        # 5. 写入数据库
        monthly_ids = [m["id"] for m in monthly_indices]
        
        annual_id = await insert_annual_index(
            character_id=character_id,
            user_id=user_id,
            year=year,
            summary=summary,
            embedding=embedding,
            monthly_ids=monthly_ids,
        )
        
        elapsed = (datetime.now() - t0).total_seconds() * 1000
        logger.info(f"[MemoryGenerator] 年索引生成完成: id={annual_id}, 耗时 {elapsed:.0f}ms")
        
        return annual_id
    
    # -------------------------------------------------------------------------
    # 辅助方法
    # -------------------------------------------------------------------------
    
    def _format_conversation_summary(self, messages: list[dict]) -> str:
        """格式化对话摘要"""
        lines = []
        for msg in messages:
            role = "用户" if msg.get("role") == "user" else "角色"
            content = msg.get("content", "")
            lines.append(f"{role}：{content}")
        
        return "\n".join(lines)
    
    def _format_key_events(self, events: list[dict]) -> str:
        """格式化关键事件"""
        if not events:
            return "无"
        
        lines = []
        for evt in events:
            event_type = evt.get("event_type", "unknown")
            content = evt.get("content", "")
            lines.append(f"- [{event_type}] {content}")
        
        return "\n".join(lines)
    
    def _format_heartbeat_events(self, events: list[dict]) -> str:
        """格式化心动事件"""
        if not events:
            return "无"
        
        lines = []
        for evt in events:
            node = evt.get("event_node", "unknown")
            trigger = evt.get("trigger_text", "")
            intensity = evt.get("intensity", 0)
            lines.append(f"- [{node}] {trigger} (强度: {intensity:.2f})")
        
        return "\n".join(lines)


# 全局实例
_generator: Optional[MemoryGenerator] = None


def get_memory_generator(llm_client: Optional[LLMClient] = None) -> MemoryGenerator:
    """获取全局生成器实例"""
    global _generator
    if _generator is None:
        _generator = MemoryGenerator(llm_client)
        logger.info("[MemoryGenerator] 全局生成器已创建")
    return _generator


def reset_memory_generator() -> None:
    """重置全局生成器（用于测试）"""
    global _generator
    _generator = None