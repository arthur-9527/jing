#!/usr/bin/env python3
"""测试中文 FTS 搜索功能

验证 zhparser 中文分词和 websearch_to_tsquery 是否正常工作。

运行方式：
    python tests/test_chinese_fts.py
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger


async def test_websearch_to_tsquery():
    """直接测试 PostgreSQL websearch_to_tsquery 函数"""
    from app.database import get_db_session
    from sqlalchemy import text
    
    async with get_db_session() as session:
        # 测试 zhparser 分词
        stmt = text("SELECT to_tsvector('chinese_zh', '我喜欢吃苹果')")
        result = await session.execute(stmt)
        row = result.first()
        logger.info(f"zhparser 分词结果: {row[0]}")
        
        # 测试 websearch_to_tsquery
        stmt = text("SELECT websearch_to_tsquery('chinese_zh', '喜欢 苹果')")
        result = await session.execute(stmt)
        row = result.first()
        logger.info(f"websearch_to_tsquery 结果: {row[0]}")
        
        # 测试匹配 - zhparser 将"我喜欢"作为一个整体分词
        # 所以用"我喜欢"来匹配
        stmt = text("""
            SELECT to_tsvector('chinese_zh', '我喜欢吃苹果和香蕉') 
                   @@ websearch_to_tsquery('chinese_zh', '我喜欢 苹果') AS matches
        """)
        result = await session.execute(stmt)
        row = result.first()
        logger.info(f"匹配测试(websearch, '我喜欢 苹果'): {row[0]}")
        
        # 测试 plainto_tsquery 更宽松匹配
        stmt = text("""
            SELECT to_tsvector('chinese_zh', '我喜欢吃苹果和香蕉') 
                   @@ plainto_tsquery('chinese_zh', '喜欢苹果') AS matches
        """)
        result = await session.execute(stmt)
        row = result.first()
        logger.info(f"匹配测试(plainto, '喜欢苹果'): {row[0]}")
        
        # 测试实际场景：用户输入"我喜欢吃苹果"
        stmt = text("SELECT to_tsvector('chinese_zh', '今天天气很好')")
        result = await session.execute(stmt)
        row = result.first()
        logger.info(f"'今天天气很好' 分词结果: {row[0]}")


async def test_chinese_fts_search():
    """测试中文 FTS 搜索功能"""
    from app.agent.db.memory_models import (
        search_chat_messages_fts,
        search_key_events_fts,
        search_heartbeat_events_fts,
    )
    
    character_id = "daji"
    user_id = "default_user"
    
    # 测试查询（自然语言输入）
    test_queries = [
        "喜欢苹果",
        "我喜欢吃水果",
        "今天天气",
        "心情",
    ]
    
    for query in test_queries:
        logger.info(f"\n=== 测试查询: '{query}' ===")
        
        # 测试聊天记录搜索
        try:
            chat_results = await search_chat_messages_fts(
                character_id, user_id, query, limit=3, days=7
            )
            logger.info(f"聊天记录: {len(chat_results)} 条")
            for r in chat_results[:2]:
                logger.info(f"  - rank={r.get('rank', 0):.3f}, content={r.get('content', '')[:50]}...")
        except Exception as e:
            logger.warning(f"聊天记录搜索失败: {e}")
        
        # 测试关键事件搜索
        try:
            ke_results = await search_key_events_fts(
                character_id, user_id, query, limit=5,
                event_types=['preference', 'fact', 'schedule', 'initiative']
            )
            logger.info(f"关键事件: {len(ke_results)} 条")
            for r in ke_results[:2]:
                logger.info(f"  - type={r.get('event_type')}, content={r.get('content', '')[:50]}...")
        except Exception as e:
            logger.warning(f"关键事件搜索失败: {e}")
        
        # 测试心动事件搜索
        try:
            hb_results = await search_heartbeat_events_fts(
                character_id, user_id, query, limit=3, days=7
            )
            logger.info(f"心动事件: {len(hb_results)} 条")
            for r in hb_results[:2]:
                logger.info(f"  - intensity={r.get('intensity', 0):.2f}, trigger={r.get('trigger_text', '')[:50]}...")
        except Exception as e:
            logger.warning(f"心动事件搜索失败: {e}")


async def test_batch_get_chat_contexts():
    """测试批量上下文获取"""
    from app.agent.db.memory_models import batch_get_chat_contexts
    
    character_id = "daji"
    user_id = "default_user"
    
    # 测试空列表
    result = await batch_get_chat_contexts(character_id, user_id, [])
    assert result == [], "空列表应返回空结果"
    
    logger.info("批量上下文获取函数测试通过")


async def main():
    """手动运行测试"""
    logger.info("=== 中文 FTS 搜索测试 ===\n")
    
    # 测试 PostgreSQL 函数
    try:
        await test_websearch_to_tsquery()
    except Exception as e:
        logger.error(f"PostgreSQL 函数测试失败: {e}")
        return
    
    # 测试搜索函数
    try:
        await test_chinese_fts_search()
    except Exception as e:
        logger.error(f"FTS 搜索测试失败: {e}")
    
    # 测试批量上下文
    try:
        await test_batch_get_chat_contexts()
    except Exception as e:
        logger.error(f"批量上下文测试失败: {e}")
    
    logger.info("\n=== 测试完成 ===")


if __name__ == "__main__":
    asyncio.run(main())