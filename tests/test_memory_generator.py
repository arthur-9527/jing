"""测试记忆生成器功能

测试内容：
1. 日记生成 - 从当天的聊天记录和事件生成日记
2. 周索引生成 - 从本周日记生成周索引
3. 月索引生成 - 从本月周索引生成月索引
4. 年索引生成 - 从本年月索引生成年索引

运行方式：
    python tests/test_memory_generator.py diary    # 测试日记生成
    python tests/test_memory_generator.py weekly   # 测试周索引生成
    python tests/test_memory_generator.py all      # 测试全部
"""

import asyncio
import sys
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

from loguru import logger

from app.agent.memory.generator import get_memory_generator, reset_memory_generator
from app.agent.db.memory_models import (
    get_daily_diary,
    get_recent_daily_diaries,
    get_weekly_index_by_date,
    get_monthly_index,
    get_annual_index,
)


async def test_diary_generation():
    """测试日记生成"""
    print("\n" + "=" * 60)
    print("测试日记生成")
    print("=" * 60)
    
    character_id = "default"
    user_id = "default_user"
    
    # 使用今天的数据（因为数据库里有今天的消息）
    target_date = date.today()
    
    print(f"\n[Step 1] 生成 {target_date} 的日记...")
    
    generator = get_memory_generator()
    diary_id = await generator.generate_daily_diary(
        character_id=character_id,
        user_id=user_id,
        target_date=target_date,
    )
    
    if diary_id:
        print(f"✅ 日记生成成功: id={diary_id}")
        
        # 验证写入
        print("\n[Step 2] 验证日记内容...")
        diary = await get_daily_diary(
            character_id=character_id,
            user_id=user_id,
            diary_date=target_date,
        )
        
        if diary:
            print(f"  - ID: {diary['id']}")
            print(f"  - 日期: {diary['diary_date']}")
            print(f"  - 内容长度: {len(diary['summary'])} 字")
            print(f"  - 关键事件数: {len(diary.get('key_event_ids', []))}")
            print(f"  - 心动事件数: {len(diary.get('heartbeat_ids', []))}")
            print(f"  - Embedding: {'有' if diary.get('embedding') else '无'}")
            print(f"\n日记内容预览:")
            print("-" * 40)
            print(diary['summary'][:500] + "..." if len(diary['summary']) > 500 else diary['summary'])
            print("-" * 40)
        else:
            print("❌ 无法读取日记")
    else:
        print("❌ 日记生成失败（可能没有聊天记录）")
    
    return diary_id


async def test_weekly_generation():
    """测试周索引生成"""
    print("\n" + "=" * 60)
    print("测试周索引生成")
    print("=" * 60)
    
    character_id = "default"
    user_id = "default_user"
    
    # 先检查是否有日记
    print("\n[Step 1] 检查现有日记...")
    diaries = await get_recent_daily_diaries(
        character_id=character_id,
        user_id=user_id,
        days=14,
        limit=14,
    )
    
    print(f"  最近日记数: {len(diaries)}")
    
    if not diaries:
        print("❌ 没有日记，无法生成周索引")
        return None
    
    # 使用本周的日期范围
    today = date.today()
    days_since_monday = today.weekday()
    week_start = today - timedelta(days=days_since_monday)
    week_end = week_start + timedelta(days=6)
    
    print(f"\n[Step 2] 生成本周索引: {week_start} ~ {week_end}...")
    
    generator = get_memory_generator()
    weekly_id = await generator.generate_weekly_index(
        character_id=character_id,
        user_id=user_id,
        week_start=week_start,
    )
    
    if weekly_id:
        print(f"✅ 周索引生成成功: id={weekly_id}")
        
        # 验证
        print("\n[Step 3] 验证周索引内容...")
        weekly = await get_weekly_index_by_date(
            character_id=character_id,
            user_id=user_id,
            target_date=week_start,
        )
        
        if weekly:
            print(f"  - ID: {weekly['id']}")
            print(f"  - 周范围: {weekly['week_start']} ~ {weekly['week_end']}")
            print(f"  - 内容长度: {len(weekly['summary'])} 字")
            print(f"  - 日记数: {len(weekly.get('diary_ids', []))}")
            print(f"\n周索引内容预览:")
            print("-" * 40)
            print(weekly['summary'][:300] + "..." if len(weekly['summary']) > 300 else weekly['summary'])
            print("-" * 40)
        else:
            print("❌ 无法读取周索引")
    else:
        print("❌ 周索引生成失败")
    
    return weekly_id


async def test_monthly_generation():
    """测试月索引生成"""
    print("\n" + "=" * 60)
    print("测试月索引生成")
    print("=" * 60)
    
    character_id = "default"
    user_id = "default_user"
    
    # 使用当前月份
    today = date.today()
    year = today.year
    month = today.month
    
    print(f"\n[Step 1] 生成 {year}-{month:02d} 月索引...")
    
    generator = get_memory_generator()
    monthly_id = await generator.generate_monthly_index(
        character_id=character_id,
        user_id=user_id,
        year=year,
        month=month,
    )
    
    if monthly_id:
        print(f"✅ 月索引生成成功: id={monthly_id}")
        
        # 验证
        print("\n[Step 2] 验证月索引内容...")
        monthly = await get_monthly_index(
            character_id=character_id,
            user_id=user_id,
            year=year,
            month=month,
        )
        
        if monthly:
            print(f"  - ID: {monthly['id']}")
            print(f"  - 年月: {monthly['year']}-{monthly['month']:02d}")
            print(f"  - 内容长度: {len(monthly['summary'])} 字")
            print(f"  - 周索引数: {len(monthly.get('weekly_ids', []))}")
            print(f"\n月索引内容预览:")
            print("-" * 40)
            print(monthly['summary'][:300] + "..." if len(monthly['summary']) > 300 else monthly['summary'])
            print("-" * 40)
        else:
            print("❌ 无法读取月索引")
    else:
        print("❌ 月索引生成失败（可能没有周索引）")
    
    return monthly_id


async def test_annual_generation():
    """测试年索引生成"""
    print("\n" + "=" * 60)
    print("测试年索引生成")
    print("=" * 60)
    
    character_id = "default"
    user_id = "default_user"
    
    # 使用当前年份
    year = date.today().year
    
    print(f"\n[Step 1] 生成 {year} 年索引...")
    
    generator = get_memory_generator()
    annual_id = await generator.generate_annual_index(
        character_id=character_id,
        user_id=user_id,
        year=year,
    )
    
    if annual_id:
        print(f"✅ 年索引生成成功: id={annual_id}")
        
        # 验证
        print("\n[Step 2] 验证年索引内容...")
        annual = await get_annual_index(
            character_id=character_id,
            user_id=user_id,
            year=year,
        )
        
        if annual:
            print(f"  - ID: {annual['id']}")
            print(f"  - 年份: {annual['year']}")
            print(f"  - 内容长度: {len(annual['summary'])} 字")
            print(f"  - 月索引数: {len(annual.get('monthly_ids', []))}")
            print(f"\n年索引内容预览:")
            print("-" * 40)
            print(annual['summary'][:300] + "..." if len(annual['summary']) > 300 else annual['summary'])
            print("-" * 40)
        else:
            print("❌ 无法读取年索引")
    else:
        print("❌ 年索引生成失败（可能没有月索引）")
    
    return annual_id


async def test_all():
    """测试全部生成流程"""
    print("\n" + "=" * 60)
    print("完整测试：日记 → 周索引 → 月索引 → 年索引")
    print("=" * 60)
    
    # 1. 日记
    diary_id = await test_diary_generation()
    
    # 2. 周索引（依赖日记）
    weekly_id = await test_weekly_generation()
    
    # 3. 月索引（依赖周索引）
    monthly_id = await test_monthly_generation()
    
    # 4. 年索引（依赖月索引）
    annual_id = await test_annual_generation()
    
    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)
    print(f"日记 ID: {diary_id or '失败'}")
    print(f"周索引 ID: {weekly_id or '失败'}")
    print(f"月索引 ID: {monthly_id or '失败'}")
    print(f"年索引 ID: {annual_id or '失败'}")


def main():
    if len(sys.argv) < 2:
        print("使用方式:")
        print("  python tests/test_memory_generator.py diary    # 测试日记生成")
        print("  python tests/test_memory_generator.py weekly   # 测试周索引生成")
        print("  python tests/test_memory_generator.py monthly  # 测试月索引生成")
        print("  python tests/test_memory_generator.py annual   # 测试年索引生成")
        print("  python tests/test_memory_generator.py all      # 测试全部")
        sys.exit(1)
    
    mode = sys.argv[1]
    
    if mode == "diary":
        asyncio.run(test_diary_generation())
    elif mode == "weekly":
        asyncio.run(test_weekly_generation())
    elif mode == "monthly":
        asyncio.run(test_monthly_generation())
    elif mode == "annual":
        asyncio.run(test_annual_generation())
    elif mode == "all":
        asyncio.run(test_all())
    else:
        print(f"未知模式: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()