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
from app.stone import (
    get_chat_repo,
    get_key_event_repo,
    get_heartbeat_repo,
    get_diary_repo,
    get_weekly_repo,
    get_monthly_repo,
    get_annual_repo,
    get_daily_life_repo,
)
from app.agent.memory.embedding import get_embedding


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
        
        # 计算目标日期对应的 UTC 时间范围
        tz_shanghai = timezone(timedelta(hours=8))
        local_start = datetime.combine(target_date, datetime.min.time(), tzinfo=tz_shanghai)
        local_end = datetime.combine(target_date, datetime.max.time(), tzinfo=tz_shanghai)
        target_start = local_start.astimezone(timezone.utc)
        target_end = local_end.astimezone(timezone.utc)

        # 使用 Stone Repository
        chat_repo = get_chat_repo()
        key_event_repo = get_key_event_repo()
        heartbeat_repo = get_heartbeat_repo()
        diary_repo = get_diary_repo()
        
        # 1. 获取当天的聊天记录
        chat_messages = await chat_repo.get_by_date_range(
            character_id=character_id,
            user_id=user_id,
            start_time=target_start,
            end_time=target_end,
            limit=100,
        )

        if not chat_messages:
            logger.warning(f"[MemoryGenerator] {target_date} 没有聊天记录，跳过日记生成")
            return None

        logger.info(f"[MemoryGenerator] 获取到 {len(chat_messages)} 条聊天记录")

        # 2. 获取当天的关键事件
        key_events = await key_event_repo.get_by_date_range(
            character_id=character_id,
            user_id=user_id,
            start_time=target_start,
            end_time=target_end,
            limit=50,
        )
        logger.info(f"[MemoryGenerator] 获取到 {len(key_events)} 条关键事件")

        # 3. 获取当天的心动事件
        heartbeat_events = await heartbeat_repo.get_by_date_range(
            character_id=character_id,
            user_id=user_id,
            start_time=target_start,
            end_time=target_end,
            limit=50,
        )
        logger.info(f"[MemoryGenerator] 获取到 {len(heartbeat_events)} 条心动事件")
        # 4. 获取当天的日常事务事件
        daily_life_repo = get_daily_life_repo()
        daily_life_events = await daily_life_repo.get_recent(
            character_id=character_id,
            user_id=user_id,
            days=1,
            limit=20,
        )
        
        # 过滤目标日期
        day_daily_life = [
            evt for evt in daily_life_events
            if evt.get("event_time", datetime.min.replace(tzinfo=timezone.utc)).date() == target_date
        ]
        
        logger.info(f"[MemoryGenerator] 获取到 {len(day_daily_life)} 条日常事务事件")
        
        # 5. 格式化对话摘要
        conversation_summary = self._format_conversation_summary(chat_messages)
        key_events_text = self._format_key_events(key_events)
        heartbeat_text = self._format_heartbeat_events(heartbeat_events)
        daily_life_text = self._format_daily_life_events(daily_life_events)
        
        # 6. LLM 生成日记
        prompt = format_diary_prompt(
            conversation_summary=conversation_summary,
            key_events=key_events_text,
            heartbeat_events=heartbeat_text,
            daily_life_events=daily_life_text,
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
        key_event_ids = [evt["id"] for evt in key_events]
        heartbeat_ids = [evt["id"] for evt in heartbeat_events]
        message_ids = [msg["id"] for msg in chat_messages]

        diary_id = await diary_repo.insert(
            character_id=character_id,
            user_id=user_id,
            diary_date=target_date,
            summary=diary_content,
            embedding=embedding,
            key_event_ids=key_event_ids,
            heartbeat_ids=heartbeat_ids,
            source_message_ids=message_ids,
            highlight_count=len(heartbeat_events),
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
        
        # 使用 Stone Repository
        diary_repo = get_diary_repo()
        weekly_repo = get_weekly_repo()
        
        # 1. 获取本周的日记
        diaries = await diary_repo.get_recent(
            character_id=character_id,
            user_id=user_id,
            limit=14,
            days=14,
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
        
        weekly_id = await weekly_repo.insert(
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
        
        # 使用 Stone Repository
        weekly_repo = get_weekly_repo()
        monthly_repo = get_monthly_repo()
        
        # 查询本月所有周索引
        # 这里简化处理：通过日期范围查询
        weekly_indices = []
        current = month_start
        while current <= month_end:
            weekly = await weekly_repo.get_by_week(
                character_id=character_id,
                user_id=user_id,
                week_start=current,
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
        
        monthly_id = await monthly_repo.insert(
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
        
        # 使用 Stone Repository
        monthly_repo = get_monthly_repo()
        annual_repo = get_annual_repo()
        
        # 1. 获取本年的月索引
        monthly_indices = []
        for month in range(1, 13):
            monthly = await monthly_repo.get_by_month(
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
        
        annual_id = await annual_repo.insert(
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
    
    def _format_daily_life_events(self, events: list[dict]) -> str:
        """格式化日常事务事件"""
        if not events:
            return "无"
        
        lines = []
        for evt in events:
            time_str = evt.get("event_time", "")
            if hasattr(time_str, 'strftime'):
                time_str = time_str.strftime("%H:%M")
            scenario = evt.get("scenario", "")
            detail = evt.get("scenario_detail", "")
            dialogue = evt.get("dialogue", "")
            
            # 组合格式
            event_line = f"- [{time_str}] {scenario}"
            if detail:
                event_line += f": {detail}"
            if dialogue:
                event_line += f" 「{dialogue}」"
            lines.append(event_line)
        
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