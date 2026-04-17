"""测试记忆系统数据库接口

运行方式：
    cd /home/test/raspi_mmd/agent_backend
    python tests/test_memory_models.py
"""

import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.agent.db.memory_models import (
    # chat_messages
    insert_chat_message,
    get_recent_chat_messages,
    mark_messages_extracted,
    # key_events
    insert_key_event,
    get_key_events_by_type,
    get_recent_key_events,
    deactivate_key_event,
    # heartbeat_events
    insert_heartbeat_event,
    get_recent_heartbeat_events,
    get_high_intensity_heartbeat_events,
    # daily_diary
    insert_daily_diary,
    get_daily_diary,
    get_recent_daily_diaries,
    # weekly_index
    insert_weekly_index,
    get_weekly_index_by_date,
    # monthly_index
    insert_monthly_index,
    get_monthly_index,
    # annual_index
    insert_annual_index,
    get_annual_index,
)

# 测试参数
CHARACTER_ID = "daji"
USER_ID = "test_user_001"


async def test_chat_messages():
    """测试聊天记录表接口"""
    print("\n" + "=" * 50)
    print("测试 chat_messages 表")
    print("=" * 50)
    
    # 1. 插入 user 消息
    msg_id_1 = await insert_chat_message(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        role="user",
        content="你好，我是测试用户，今天是我的生日！",
        turn_id=1,
    )
    print(f"✅ insert_chat_message (user): id={msg_id_1}")
    
    # 2. 插入 assistant 消息（带内心独白）
    msg_id_2 = await insert_chat_message(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        role="assistant",
        content="祝你生日快乐！今天是一个特别的日子呢~",
        inner_monologue="用户告诉我今天是他的生日，这是一个重要的日子，我应该记住。",
        turn_id=1,
    )
    print(f"✅ insert_chat_message (assistant): id={msg_id_2}")
    
    # 3. 获取最近聊天记录
    messages = await get_recent_chat_messages(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        limit=10,
        days=3,
    )
    print(f"✅ get_recent_chat_messages: {len(messages)} 条")
    for msg in messages[:3]:
        print(f"   - [{msg['role']}] {msg['content'][:50]}...")
    
    # 4. 标记消息已提取
    count = await mark_messages_extracted([msg_id_1, msg_id_2])
    print(f"✅ mark_messages_extracted: {count} 条已标记")
    
    return True


async def test_key_events():
    """测试关键事件表接口"""
    print("\n" + "=" * 50)
    print("测试 key_events 表")
    print("=" * 50)
    
    # 1. 插入不同类型的关键事件
    events = [
        ("fact", "用户的生日是今天（4月4日）", date.today(), 0.9),
        ("preference", "用户喜欢蓝色", None, 0.6),
        ("schedule", "用户明天要开会", None, 0.5),
        ("emotion_trigger", "生日祝福让用户很开心", None, 0.7),
    ]
    
    event_ids = []
    for event_type, content, event_date, importance in events:
        event_id = await insert_key_event(
            character_id=CHARACTER_ID,
            user_id=USER_ID,
            event_type=event_type,
            content=content,
            event_date=event_date,
            importance=importance,
        )
        event_ids.append(event_id)
        print(f"✅ insert_key_event ({event_type}): id={event_id}")
    
    # 2. 按类型获取事件
    facts = await get_key_events_by_type(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        event_type="fact",
        limit=10,
    )
    print(f"✅ get_key_events_by_type (fact): {len(facts)} 条")
    for fact in facts[:2]:
        print(f"   - {fact['content']}")
    
    # 3. 获取最近关键事件
    recent = await get_recent_key_events(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        days=7,
        limit=10,
    )
    print(f"✅ get_recent_key_events: {len(recent)} 条")
    
    # 4. 失效一个事件
    if event_ids:
        success = await deactivate_key_event(event_ids[-1])
        print(f"✅ deactivate_key_event: success={success}")
    
    return True


async def test_heartbeat_events():
    """测试心动事件表接口"""
    print("\n" + "=" * 50)
    print("测试 heartbeat_events 表")
    print("=" * 50)
    
    # 1. 插入心动事件
    events = [
        ("emotion_peak", "joy_peak", "生日快乐！", {"P": 0.7, "A": 0.5, "D": 0.6}, 0.8),
        ("relationship", "first_meeting", "第一次见面", {"P": 0.3, "A": 0.4, "D": 0.5}, 0.6),
        ("user_reveal", "secret_reveal", "用户分享了秘密", {"P": 0.2, "A": 0.6, "D": 0.3}, 0.7),
    ]
    
    event_ids = []
    for node, subtype, trigger, emotion, intensity in events:
        event_id = await insert_heartbeat_event(
            character_id=CHARACTER_ID,
            user_id=USER_ID,
            event_node=node,
            event_subtype=subtype,
            trigger_text=trigger,
            emotion_state=emotion,
            intensity=intensity,
            inner_monologue=f"这是一个{node}类型的触动时刻",
        )
        event_ids.append(event_id)
        print(f"✅ insert_heartbeat_event ({node}): id={event_id}, intensity={intensity}")
    
    # 2. 获取最近心动事件
    recent = await get_recent_heartbeat_events(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        days=7,
        limit=10,
    )
    print(f"✅ get_recent_heartbeat_events: {len(recent)} 条")
    for hb in recent[:2]:
        print(f"   - [{hb['event_node']}] {hb['trigger_text']} (强度: {hb['intensity']})")
    
    # 3. 获取高强度心动事件
    high = await get_high_intensity_heartbeat_events(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        min_intensity=0.6,
        limit=10,
    )
    print(f"✅ get_high_intensity_heartbeat_events: {len(high)} 条")
    
    return True


async def test_daily_diary():
    """测试日记表接口"""
    print("\n" + "=" * 50)
    print("测试 daily_diary 表")
    print("=" * 50)
    
    diary_date = date.today()
    
    # 1. 插入日记
    diary_id = await insert_daily_diary(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        diary_date=diary_date,
        summary="今天和用户聊了很多，知道了今天是用户的生日，感到很开心。",
        key_event_ids=[1],
        heartbeat_ids=[1],
        mood_summary={"avg_P": 0.5, "avg_A": 0.4},
        highlight_count=2,
    )
    print(f"✅ insert_daily_diary: id={diary_id}, date={diary_date}")
    
    # 2. 获取指定日期日记
    diary = await get_daily_diary(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        diary_date=diary_date,
    )
    if diary:
        print(f"✅ get_daily_diary: 找到日记")
        print(f"   - 摘要: {diary['summary'][:50]}...")
        print(f"   - 高光时刻: {diary['highlight_count']}")
    else:
        print(f"⚠️ get_daily_diary: 未找到日记（可能已存在）")
    
    # 3. 获取最近日记列表
    diaries = await get_recent_daily_diaries(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        days=7,
        limit=5,
    )
    print(f"✅ get_recent_daily_diaries: {len(diaries)} 条")
    
    return True


async def test_weekly_index():
    """测试周索引表接口"""
    print("\n" + "=" * 50)
    print("测试 weekly_index 表")
    print("=" * 50)
    
    today = date.today()
    # 计算本周开始（周一）
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    
    # 1. 插入周索引
    weekly_id = await insert_weekly_index(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        week_start=week_start,
        week_end=week_end,
        summary="这一周和用户的互动很愉快，了解了用户的生日和喜好。",
        diary_ids=[1],
        highlight_events={"top_event": "用户生日"},
    )
    print(f"✅ insert_weekly_index: id={weekly_id}, week={week_start}~{week_end}")
    
    # 2. 根据日期获取周索引
    weekly = await get_weekly_index_by_date(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        target_date=today,
    )
    if weekly:
        print(f"✅ get_weekly_index_by_date: 找到周索引")
        print(f"   - 摘要: {weekly['summary'][:50]}...")
    else:
        print(f"⚠️ get_weekly_index_by_date: 未找到周索引")
    
    return True


async def test_monthly_index():
    """测试月索引表接口"""
    print("\n" + "=" * 50)
    print("测试 monthly_index 表")
    print("=" * 50)
    
    today = date.today()
    year = today.year
    month = today.month
    
    # 1. 插入月索引
    monthly_id = await insert_monthly_index(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        year=year,
        month=month,
        summary=f"{year}年{month}月，用户分享了很多个人信息，建立了良好的互动关系。",
        weekly_ids=[1],
    )
    print(f"✅ insert_monthly_index: id={monthly_id}, {year}-{month}")
    
    # 2. 获取月索引
    monthly = await get_monthly_index(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        year=year,
        month=month,
    )
    if monthly:
        print(f"✅ get_monthly_index: 找到月索引")
        print(f"   - 摘要: {monthly['summary'][:50]}...")
    else:
        print(f"⚠️ get_monthly_index: 未找到月索引")
    
    return True


async def test_annual_index():
    """测试年索引表接口"""
    print("\n" + "=" * 50)
    print("测试 annual_index 表")
    print("=" * 50)
    
    today = date.today()
    year = today.year
    
    # 1. 插入年索引
    annual_id = await insert_annual_index(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        year=year,
        summary=f"{year}年，和用户进行了多次愉快对话，建立了深厚的友谊。",
        monthly_ids=[1],
    )
    print(f"✅ insert_annual_index: id={annual_id}, year={year}")
    
    # 2. 获取年索引
    annual = await get_annual_index(
        character_id=CHARACTER_ID,
        user_id=USER_ID,
        year=year,
    )
    if annual:
        print(f"✅ get_annual_index: 找到年索引")
        print(f"   - 摘要: {annual['summary'][:50]}...")
    else:
        print(f"⚠️ get_annual_index: 未找到年索引")
    
    return True


async def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("开始测试记忆系统数据库接口")
    print("=" * 60)
    
    results = []
    
    # 按顺序运行测试
    tests = [
        ("chat_messages", test_chat_messages),
        ("key_events", test_key_events),
        ("heartbeat_events", test_heartbeat_events),
        ("daily_diary", test_daily_diary),
        ("weekly_index", test_weekly_index),
        ("monthly_index", test_monthly_index),
        ("annual_index", test_annual_index),
    ]
    
    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, result, None))
        except Exception as e:
            results.append((name, False, str(e)))
            print(f"❌ {name} 测试失败: {e}")
    
    # 输出总结
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    success_count = 0
    for name, result, error in results:
        if result:
            print(f"✅ {name}: 成功")
            success_count += 1
        else:
            print(f"❌ {name}: 失败 - {error}")
    
    print(f"\n总计: {success_count}/{len(results)} 测试通过")
    
    return success_count == len(results)


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)