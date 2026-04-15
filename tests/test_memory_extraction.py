"""测试记忆系统提取功能

测试流程：
1. 从 Redis 持久化队列读取消息
2. 写入 PostgreSQL chat_messages 表
3. LLM 提取关键事件 + 心动事件
4. 写入 key_events / heartbeat_events 表
5. 验证结果
"""

import asyncio
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载环境变量
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from loguru import logger

from app.services.chat_history.conversation_buffer import get_conversation_buffer
from app.agent.db.memory_models import (
    batch_insert_chat_messages,
    batch_insert_key_events,
    batch_insert_heartbeat_events,
    mark_messages_extracted,
    get_recent_key_events,
    get_recent_heartbeat_events,
)
from app.agent.memory.extractor import get_memory_extractor


async def test_extraction():
    """测试完整提取流程"""
    
    logger.info("=" * 60)
    logger.info("开始测试记忆提取功能")
    logger.info("=" * 60)
    
    # 1. 从 Redis 获取消息
    logger.info("\n[Step 1] 从 Redis 持久化队列获取消息...")
    
    character_id = "default"
    user_id = "default_user"
    
    buffer = await get_conversation_buffer(user_id=user_id, character_id=character_id)
    
    # 获取持久化队列长度
    persistent_len = await buffer.get_persistent_queue_length()
    logger.info(f"持久化队列消息数: {persistent_len}")
    
    if persistent_len == 0:
        logger.warning("持久化队列为空，无法测试提取功能")
        return
    
    # 获取所有消息
    messages = await buffer.get_all_persistent_messages()
    logger.info(f"获取到 {len(messages)} 条消息")
    
    # 显示前几条消息
    for i, msg in enumerate(messages[:3]):
        role = msg.get("role", "user")
        content = msg.get("content", "")[:50]
        logger.info(f"  [{i}] {role}: {content}...")
    
    # 2. 写入 PostgreSQL chat_messages
    logger.info("\n[Step 2] 写入 PostgreSQL chat_messages 表...")
    
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
    
    record_ids = await batch_insert_chat_messages(db_messages)
    logger.info(f"成功写入 {len(record_ids)} 条消息到 chat_messages")
    logger.info(f"消息 ID 范围: {record_ids[0]} - {record_ids[-1]}")
    
    # 3. LLM 提取关键事件 + 心动事件
    logger.info("\n[Step 3] LLM 提取关键事件和心动事件...")
    
    extractor = get_memory_extractor()
    
    # 只提取最近 10 条消息（减少 LLM 调用成本）
    recent_messages = messages[-10:]
    recent_ids = record_ids[-10:]
    
    logger.info(f"提取最近 {len(recent_messages)} 条消息...")
    
    try:
        key_events, heartbeat_events = await extractor.extract_all(
            messages=recent_messages,
            character_id=character_id,
            user_id=user_id,
            source_message_ids=recent_ids,
        )
        
        logger.info(f"提取到 {len(key_events)} 条关键事件")
        logger.info(f"提取到 {len(heartbeat_events)} 条心动事件")
        
        # 显示提取结果
        for i, event in enumerate(key_events[:3]):
            event_type = event.get("event_type", "unknown")
            content = event.get("content", "")[:60]
            importance = event.get("importance", 0.5)
            logger.info(f"  关键事件[{i}] type={event_type}, importance={importance:.2f}: {content}...")
        
        for i, event in enumerate(heartbeat_events[:3]):
            event_node = event.get("event_node", "unknown")
            trigger = event.get("trigger_text", "")[:40]
            intensity = event.get("intensity", 0.5)
            logger.info(f"  心动事件[{i}] node={event_node}, intensity={intensity:.2f}: {trigger}...")
        
    except Exception as e:
        logger.error(f"LLM 提取失败: {e}")
        key_events = []
        heartbeat_events = []
    
    # 4. 写入 key_events / heartbeat_events
    logger.info("\n[Step 4] 写入事件表...")
    
    if key_events:
        key_event_ids = await batch_insert_key_events(key_events)
        logger.info(f"成功写入 {len(key_event_ids)} 条关键事件到 key_events")
    
    if heartbeat_events:
        heartbeat_ids = await batch_insert_heartbeat_events(heartbeat_events)
        logger.info(f"成功写入 {len(heartbeat_ids)} 条心动事件到 heartbeat_events")
    
    # 5. 标记消息已提取
    logger.info("\n[Step 5] 标记消息已提取...")
    
    marked_count = await mark_messages_extracted(record_ids)
    logger.info(f"标记 {marked_count} 条消息已提取")
    
    # 6. 验证结果
    logger.info("\n[Step 6] 验证结果...")
    
    # 查询最近的关键事件
    recent_key_events = await get_recent_key_events(character_id, user_id, limit=5)
    logger.info(f"查询到 {len(recent_key_events)} 条最近关键事件")
    
    # 查询最近的心动事件
    recent_heartbeat = await get_recent_heartbeat_events(character_id, user_id, limit=5)
    logger.info(f"查询到 {len(recent_heartbeat)} 条最近心动事件")
    
    # 7. 清空持久化队列（可选）
    logger.info("\n[Step 7] 清空 Redis 持久化队列...")
    
    # 注意：不清空，保留数据供后续测试
    # await buffer.clear_persistent_queue()
    logger.info("保留 Redis 数据供后续测试（未清空）")
    
    logger.info("\n" + "=" * 60)
    logger.info("测试完成！")
    logger.info("=" * 60)
    
    # 返回结果摘要
    return {
        "messages_written": len(record_ids),
        "key_events_extracted": len(key_events),
        "heartbeat_events_extracted": len(heartbeat_events),
        "key_events_in_db": len(recent_key_events),
        "heartbeat_in_db": len(recent_heartbeat),
    }


async def test_only_redis_read():
    """仅测试 Redis 读取（不调用 LLM）"""
    
    logger.info("=" * 60)
    logger.info("测试 Redis 数据读取（不调用 LLM）")
    logger.info("=" * 60)
    
    character_id = "default"
    user_id = "default_user"
    
    buffer = await get_conversation_buffer(user_id=user_id, character_id=character_id)
    
    # 活动队列
    active_len = await buffer.get_length()
    logger.info(f"活动队列 (chat_history:{user_id}): {active_len} 条消息")
    
    # 持久化队列
    persistent_len = await buffer.get_persistent_queue_length()
    logger.info(f"持久化队列 (memory:buffer:chat:{character_id}:{user_id}): {persistent_len} 条消息")
    
    # 获取持久化队列内容
    messages = await buffer.get_all_persistent_messages()
    
    logger.info(f"\n持久化队列消息预览（前5条）:")
    for i, msg in enumerate(messages[:5]):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        ts = msg.get("ts", 0)
        logger.info(f"  [{i}] role={role}, ts={ts}, content={content[:60]}...")
    
    logger.info(f"\n持久化队列消息预览（后5条）:")
    for i, msg in enumerate(messages[-5:]):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        ts = msg.get("ts", 0)
        idx = len(messages) - 5 + i
        logger.info(f"  [{idx}] role={role}, ts={ts}, content={content[:60]}...")
    
    return {
        "active_queue_len": active_len,
        "persistent_queue_len": persistent_len,
        "messages": messages,
    }


if __name__ == "__main__":
    # 选择测试模式
    mode = sys.argv[1] if len(sys.argv) > 1 else "read"
    
    if mode == "full":
        # 完整测试（包括 LLM 提取）
        asyncio.run(test_extraction())
    elif mode == "read":
        # 仅读取 Redis
        asyncio.run(test_only_redis_read())
    else:
        logger.info(f"用法: python {sys.argv[0]} [read|full]")
        logger.info("  read - 仅读取 Redis 数据")
        logger.info("  full - 完整测试（包括 LLM 提取）")