"""测试向量层级检索功能

测试内容：
1. 快速路径 - 最近日记匹配
2. 层级检索 - 年→月→周→日
3. 时间锚点检测
4. 展开日记关联事件

运行方式：
    python tests/test_hierarchical_retrieval.py
"""

import asyncio
import sys
from datetime import date

from dotenv import load_dotenv
load_dotenv()

from loguru import logger

from app.agent.memory.retriever import (
    retrieve_memories,
    _should_do_hierarchy_search,
    _extract_month_hint,
)


async def test_quick_path():
    """测试快速路径 - 最近日记匹配"""
    print("\n" + "=" * 60)
    print("测试快速路径 - 最近日记匹配")
    print("=" * 60)
    
    character_id = "default"
    user_id = "default_user"
    
    # 测试一个可能与今天日记相关的问题
    user_input = "今天我们聊了什么？"
    
    print(f"\n[Query] {user_input}")
    print("\n[Step 1] 调用 retrieve_memories...")
    
    result = await retrieve_memories(
        character_id=character_id,
        user_id=user_id,
        user_input=user_input,
        enable_long_term=False,  # 禁用久远记忆，只测试快速路径
    )
    
    print("\n[结果]")
    print(f"  - 背景: {result['background'][:100]}..." if len(result['background']) > 100 else f"  - 背景: {result['background']}")
    print(f"  - 关键事件: {result['key_events'][:100]}..." if len(result['key_events']) > 100 else f"  - 关键事件: {result['key_events']}")
    print(f"  - 心动时刻: {result['heartbeat'][:100]}..." if len(result['heartbeat']) > 100 else f"  - 心动时刻: {result['heartbeat']}")
    print(f"  - 最近日记: {result['diary'][:100]}..." if len(result['diary']) > 100 else f"  - 最近日记: {result['diary']}")


async def test_hierarchical_search():
    """测试层级检索 - 久远记忆"""
    print("\n" + "=" * 60)
    print("测试层级检索 - 久远记忆")
    print("=" * 60)
    
    character_id = "default"
    user_id = "default_user"
    
    # 测试一个需要久远记忆的问题
    user_input = "去年夏天我们聊了什么？"
    
    print(f"\n[Query] {user_input}")
    print("\n[Step 1] 调用 retrieve_memories (启用久远记忆)...")
    
    result = await retrieve_memories(
        character_id=character_id,
        user_id=user_id,
        user_input=user_input,
        enable_long_term=True,
    )
    
    print("\n[结果]")
    print(f"  - 最近日记: {result['diary'][:100]}..." if len(result['diary']) > 100 else f"  - 最近日记: {result['diary']}")
    print(f"  - 久远记忆: {result['long_term'][:200]}..." if len(result['long_term']) > 200 else f"  - 久远记忆: {result['long_term']}")


async def test_time_anchor_detection():
    """测试时间锚点检测"""
    print("\n" + "=" * 60)
    print("测试时间锚点检测")
    print("=" * 60)
    
    test_cases = [
        ("今天我们聊了什么？", False),
        ("昨天发生了什么？", False),
        ("去年夏天我们做了什么？", True),
        ("前年冬天呢？", True),
        ("我们第一次见面是什么时候？", True),
        ("很久以前我们聊过什么？", True),
        ("三月的时候我们说了什么？", True),
    ]
    
    print("\n测试 _should_do_hierarchy_search:")
    for query, expected in test_cases:
        # 模拟日记结果
        diary_results = []  # 空结果，应该触发层级检索
        result = _should_do_hierarchy_search(diary_results, query)
        status = "✅" if result == expected else "❌"
        print(f"  {status} '{query}' → 层级检索={result}, 预期={expected}")
    
    print("\n测试 _extract_month_hint:")
    month_tests = [
        ("去年夏天", [6, 7, 8]),
        ("冬天的时候", [12, 1, 2]),
        ("三月", 3),
        ("十二月", 12),
        ("暑假", [7, 8]),
    ]
    
    for query, expected in month_tests:
        result = _extract_month_hint(query)
        print(f"  '{query}' → 月份={result}, 预期={expected}")


async def test_full_retrieval():
    """测试完整检索流程"""
    print("\n" + "=" * 60)
    print("测试完整检索流程")
    print("=" * 60)
    
    character_id = "default"
    user_id = "default_user"
    
    # 测试一个通用问题
    user_input = "我们之前聊过什么有趣的事情？"
    
    print(f"\n[Query] {user_input}")
    print("\n[Step 1] 调用 retrieve_memories...")
    
    result = await retrieve_memories(
        character_id=character_id,
        user_id=user_id,
        user_input=user_input,
        enable_long_term=True,
    )
    
    print("\n[完整结果]")
    print("-" * 40)
    print(result['combined'])
    print("-" * 40)


async def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("向量层级检索测试")
    print("=" * 60)
    
    # 测试时间锚点检测（不需要数据库）
    await test_time_anchor_detection()
    
    # 测试快速路径
    await test_quick_path()
    
    # 测试层级检索
    await test_hierarchical_search()
    
    # 测试完整流程
    await test_full_retrieval()
    
    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())