"""记忆系统写入模块

- write_heartbeat_event: 写入心动事件
- extract_and_write_key_events: 提取关键事件
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Optional

from app.stone import get_heartbeat_repo, get_key_event_repo
from app.agent.llm.client import LLMClient
from app.agent.memory.embedding import get_embedding

logger = logging.getLogger(__name__)

# 事件类型映射（旧类型 -> 新类型）
EVENT_TYPE_MAP = {
    "fact": "fact",
    "emotion": "emotion_trigger",
    "preference": "preference",
    "taboo": "preference",  # 禁忌归为偏好类
}


async def write_heartbeat_event(
    character_id: str,
    user_id: str,
    inner_monologue: str,
    pad_state: dict[str, float],
    emotion_intensity: float,
    trigger_keywords: list[str],
    event_node: str = "emotion_peak",
    event_subtype: Optional[str] = None,
    source_message_id: Optional[int] = None,
) -> int | None:
    """
    当情绪强度超过阈值时，将心动事件写入 heartbeat_events 表
    
    Args:
        character_id: 角色ID
        user_id: 用户ID
        inner_monologue: 内心独白
        pad_state: PAD 情绪状态 {"P": x, "A": y, "D": z}
        emotion_intensity: 情绪强度 (0-1)
        trigger_keywords: 触发关键词列表
        event_node: 事件节点类型（默认 emotion_peak）
        event_subtype: 事件子类型（如 joy_peak, sad_peak）
        source_message_id: 来源消息ID
    
    Returns:
        记录ID，如果强度低于阈值则返回 None
    """
    threshold = float(os.getenv("EMOTION_INTENSITY_THRESHOLD", "0.3"))
    if emotion_intensity < threshold:
        logger.debug("情绪强度 %.3f 低于阈值 %.3f，跳过写入", emotion_intensity, threshold)
        return None

    # 构建触发文本
    trigger_text = trigger_keywords[0] if trigger_keywords else "情绪事件"

    # 使用 Stone Repository
    heartbeat_repo = get_heartbeat_repo()
    record_id = await heartbeat_repo.insert(
        character_id=character_id,
        user_id=user_id,
        event_node=event_node,
        event_subtype=event_subtype,
        trigger_text=trigger_text,
        emotion_state=pad_state,
        intensity=emotion_intensity,
        inner_monologue=inner_monologue,
        source_message_id=source_message_id,
    )
    logger.info("心动事件已写入，id=%d, intensity=%.3f", record_id, emotion_intensity)
    return record_id


async def extract_and_write_key_events(
    llm_client: LLMClient,
    character_id: str,
    user_id: str,
    user_input: str,
    assistant_reply: str,
    source_message_ids: Optional[list[int]] = None,
) -> list[int]:
    """
    调用 LLM 提取用户关键信息并写入 key_events 表
    
    提取类型：preference / fact / schedule / experience / emotion_trigger / initiative
    
    Args:
        llm_client: LLM 客户端
        character_id: 角色ID
        user_id: 用户ID
        user_input: 用户输入
        assistant_reply: 角色回复
        source_message_ids: 来源消息ID列表
    
    Returns:
        写入的记录ID列表
    """
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个记忆提取助手。从对话中提取用户的关键信息。\n"
                "返回 JSON 数组，每项包含：\n"
                '- event_type: "preference"（偏好）/ "fact"（事实）/ "schedule"（日程）/ "experience"（经历）/ "emotion_trigger"（情绪触发）/ "initiative"（主动记忆）\n'
                "- content: 提取到的信息（简洁准确）\n"
                "- importance: 重要性评分 (0.0-1.0)\n"
                '- event_date: 重要日期（格式 YYYY-MM-DD，如生日、纪念日），无则不填\n'
                '如果没有值得记录的信息，返回空数组 []。\n'
                '示例：[{"event_type": "fact", "content": "用户生日是3月15日", "importance": 0.8, "event_date": "2026-03-15"}]'
            ),
        },
        {
            "role": "user",
            "content": f"用户说：{user_input}\n角色回复：{assistant_reply}\n\n请提取用户的关键信息：",
        },
    ]

    try:
        result = await llm_client.chat_json(messages, temperature=0.3, use_fast=True)
        # result 可能是 {"items": [...]} 或直接是 [...]
        if isinstance(result, dict):
            items = result.get("items", result.get("data", []))
        elif isinstance(result, list):
            items = result
        else:
            items = []
    except Exception as e:
        logger.warning("关键事件提取失败: %s", e)
        return []

    record_ids = []
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
                logger.warning("日期格式错误: %s", event_date_str)
        
        # 使用 Stone Repository
        key_event_repo = get_key_event_repo()
        record_id = await key_event_repo.insert(
            character_id=character_id,
            user_id=user_id,
            event_type=event_type,
            content=content,
            event_date=event_date,
            importance=importance,
            source_message_ids=source_message_ids,
        )
        record_ids.append(record_id)
        logger.info("关键事件已写入，id=%d, type=%s", record_id, event_type)

    return record_ids


