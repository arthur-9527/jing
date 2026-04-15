"""长期记忆检索测试脚本

测试步骤：
1. 查看数据库中现有记忆数据
2. 测试时间锚点提取
3. 测试长期记忆检索功能
"""

import asyncio
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime


async def check_db_data():
    """查看数据库中的记忆数据"""
    from app.database import get_db_session
    from sqlalchemy import text
    
    print("\n=== 查看数据库记忆数据 ===\n")
    
    async with get_db_session() as session:
        # 查看日记数量
        result = await session.execute(text("""
            SELECT character_id, user_id, COUNT(*) as count, 
                   MIN(diary_date) as min_date, MAX(diary_date) as max_date
            FROM daily_diary 
            GROUP BY character_id, user_id
        """))
        diaries = result.mappings().all()
        print(f"日记统计:")
        for row in diaries:
            print(f"  character={row['character_id']}, user={row['user_id']}: {row['count']} 条, 日期范围 {row['min_date']} ~ {row['max_date']}")
        
        if not diaries:
            print("  (无日记数据)")
        
        # 查看月索引数量
        result = await session.execute(text("""
            SELECT character_id, user_id, COUNT(*) as count,
                   MIN(year) as min_year, MAX(year) as max_year
            FROM monthly_index 
            GROUP BY character_id, user_id
        """))
        monthly = result.mappings().all()
        print(f"\n月索引统计:")
        for row in monthly:
            print(f"  character={row['character_id']}, user={row['user_id']}: {row['count']} 条, 年份范围 {row['min_year']} ~ {row['max_year']}")
        
        if not monthly:
            print("  (无月索引数据)")
        
        # 查看年索引数量
        result = await session.execute(text("""
            SELECT character_id, user_id, COUNT(*) as count, array_agg(year) as years
            FROM annual_index 
            GROUP BY character_id, user_id
        """))
        annual = result.mappings().all()
        print(f"\n年索引统计:")
        for row in annual:
            print(f"  character={row['character_id']}, user={row['user_id']}: {row['count']} 条, 年份 {row['years']}")
        
        if not annual:
            print("  (无年索引数据)")
        
        # 查看关键事件数量
        result = await session.execute(text("""
            SELECT character_id, user_id, event_type, COUNT(*) as count
            FROM key_events 
            WHERE is_active = true
            GROUP BY character_id, user_id, event_type
            ORDER BY character_id, user_id, event_type
        """))
        events = result.mappings().all()
        print(f"\n关键事件统计:")
        for row in events:
            print(f"  character={row['character_id']}, user={row['user_id']}, type={row['event_type']}: {row['count']} 条")
        
        if not events:
            print("  (无关键事件数据)")
        
        # 查看心动事件数量
        result = await session.execute(text("""
            SELECT character_id, user_id, COUNT(*) as count
            FROM heartbeat_events 
            GROUP BY character_id, user_id
        """))
        heartbeats = result.mappings().all()
        print(f"\n心动事件统计:")
        for row in heartbeats:
            print(f"  character={row['character_id']}, user={row['user_id']}: {row['count']} 条")
        
        if not heartbeats:
            print("  (无心动事件数据)")
        
        # 返回第一个 character_id 和 user_id 用于后续测试
        if diaries:
            return diaries[0]['character_id'], diaries[0]['user_id']
        elif events:
            return events[0]['character_id'], events[0]['user_id']
        
        return "daji", "default_user"


async def test_time_anchor_extraction():
    """测试时间锚点提取"""
    from app.agent.memory.long_term_retrieval import extract_time_anchor
    
    print("\n=== 测试时间锚点提取 ===\n")
    
    test_cases = [
        "去年那个蜜雪冰城真好喝",
        "前年夏天我们去过海边",
        "去年暑假的时候",
        "今年冬天",
        "刚认识的时候",
        "十二月那次",
        "今天天气真好",
        "还记得那次我们去海边吗",
        "以前我们经常一起吃饭",
    ]
    
    for case in test_cases:
        result = extract_time_anchor(case)
        print(f"  '{case}' → {result}")


async def test_long_term_retrieval(character_id: str, user_id: str):
    """测试长期记忆检索"""
    from app.agent.memory.long_term_retrieval import retrieve_long_term_memories
    
    print(f"\n=== 测试长期记忆检索 ===\n")
    print(f"character_id: {character_id}, user_id: {user_id}\n")
    
    test_queries = [
        "去年发生了什么",
        "还记得我们一起做什么吗",
        "前几天有什么重要的事",
        "刚认识的时候",
    ]
    
    for query in test_queries:
        print(f"查询: '{query}'")
        result = await retrieve_long_term_memories(character_id, user_id, query)
        print(f"  时间锚点: {result['time_anchor']}")
        print(f"  是否匹配: {result['has_match']}")
        if result['long_term']:
            print(f"  检索结果:\n{result['long_term']}")
        else:
            print(f"  检索结果: (无匹配)")
        print()


async def main():
    """主测试流程"""
    print("=" * 60)
    print("长期记忆检索测试")
    print("=" * 60)
    
    # Step 1: 查看数据库数据
    character_id, user_id = await check_db_data()
    
    # Step 2: 测试时间锚点提取
    await test_time_anchor_extraction()
    
    # Step 3: 测试长期记忆检索
    await test_long_term_retrieval(character_id, user_id)
    
    print("=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())