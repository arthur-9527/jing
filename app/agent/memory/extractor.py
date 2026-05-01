"""记忆提取器模块

功能：
1. 关键事件提取 - 从对话中提取 preference/fact/schedule 等
2. 心动事件提取 - 检测情绪峰值、关系进展等
3. 批量处理 - 每小时定时任务调用

使用方式：
    extractor = get_memory_extractor()
    key_events = await extractor.extract_key_events(messages)
    heartbeat_events = await extractor.extract_heartbeat_events(messages)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Optional

from loguru import logger

from app.agent.llm.client import LLMClient
from app.agent.memory.prompts import (
    format_key_event_extraction_prompt,
)


class MemoryExtractor:
    """记忆提取器
    
    负责：
    1. 调用 LLM 从对话中提取关键事件
    2. 调用 LLM 从对话中提取心动事件
    3. 格式化对话文本供 LLM 分析
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        """
        Args:
            llm_client: LLM 客户端实例（可选，默认创建新实例）
        """
        self._llm = llm_client
        self._initialized = False
    
    @property
    def llm(self) -> LLMClient:
        """懒加载 LLM 客户端"""
        if self._llm is None:
            self._llm = LLMClient()
            self._initialized = True
            logger.info("[MemoryExtractor] LLM Client 已初始化")
        return self._llm
    
    def format_conversation(self, messages: list[dict]) -> str:
        """将消息列表格式化为对话文本
        
        Args:
            messages: 消息列表，每项包含 role, content, inner_monologue 等
        
        Returns:
            格式化的对话文本
        """
        lines = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            inner_monologue = msg.get("inner_monologue", "")
            
            if role == "user":
                lines.append(f"用户：{content}")
            elif role == "assistant":
                # 角色回复包含内心独白
                if inner_monologue:
                    lines.append(f"角色：{content}（内心：{inner_monologue}）")
                else:
                    lines.append(f"角色：{content}")
        
        return "\n".join(lines)
    
    async def extract_key_events(
        self,
        messages: list[dict],
        character_id: str,
        user_id: str,
        source_message_ids: Optional[list[int]] = None,
    ) -> list[dict]:
        """提取关键事件
        
        Args:
            messages: 消息列表
            character_id: 角色ID
            user_id: 用户ID
            source_message_ids: 来源消息ID列表
        
        Returns:
            提取的关键事件列表（已格式化，可直接写入数据库）
        """
        if not messages:
            return []
        
        t0 = datetime.now()
        conversation = self.format_conversation(messages)
        
        logger.info(f"[MemoryExtractor] 开始提取关键事件，对话长度: {len(conversation)} 字")
        
        try:
            # 调用 LLM 提取（使用快速模型）
            prompt = format_key_event_extraction_prompt(conversation)
            logger.debug(f"[MemoryExtractor] 关键事件提取 Prompt 长度: {len(prompt)} 字")
            
            # ⭐ 修复：删除重复 LLM 调用
            # 原代码调用了两次：chat() 用于 debug 日志 + chat_json() 用于解析
            # 现在直接使用 chat_json()，减少一半的 LLM 成本和延迟
            result = await self.llm.chat_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                use_fast=True,
            )
            
            # debug 日志使用解析后的结果
            logger.debug(f"[MemoryExtractor] LLM 响应: {result}")
            
            # 解析结果
            if isinstance(result, dict):
                items = result.get("items", result.get("events", []))
            elif isinstance(result, list):
                items = result
            else:
                items = []
            
            # 格式化为数据库格式
            formatted_events = []
            for item in items:
                event_type = item.get("event_type", "fact")
                content = item.get("content", "")
                importance = item.get("importance", 0.5)
                event_date_str = item.get("event_date")
                
                if not content:
                    continue
                
                # 解析日期
                event_date = None
                if event_date_str:
                    try:
                        event_date = date.fromisoformat(event_date_str)
                    except ValueError:
                        logger.warning(f"[MemoryExtractor] 日期格式错误: {event_date_str}")
                
                formatted_events.append({
                    "character_id": character_id,
                    "user_id": user_id,
                    "event_type": event_type,
                    "event_date": event_date,
                    "content": content,
                    "importance": importance,
                    "source_message_ids": source_message_ids or [],
                })
            
            elapsed = (datetime.now() - t0).total_seconds() * 1000
            logger.info(
                f"[MemoryExtractor] 关键事件提取完成: {len(formatted_events)} 条，耗时 {elapsed:.0f}ms"
            )
            
            return formatted_events
            
        except Exception as e:
            logger.error(f"[MemoryExtractor] 关键事件提取失败: {e}", exc_info=True)
            return []
    

# 全局实例
_extractor: Optional[MemoryExtractor] = None


def get_memory_extractor(llm_client: Optional[LLMClient] = None) -> MemoryExtractor:
    """获取全局提取器实例
    
    Args:
        llm_client: LLM 客户端实例（可选，用于共享客户端）
    
    Returns:
        MemoryExtractor 实例
    """
    global _extractor
    if _extractor is None:
        _extractor = MemoryExtractor(llm_client)
        logger.info("[MemoryExtractor] 全局提取器已创建")
    return _extractor


def reset_memory_extractor() -> None:
    """重置全局提取器（用于测试）"""
    global _extractor
    _extractor = None