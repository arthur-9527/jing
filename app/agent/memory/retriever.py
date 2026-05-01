"""记忆检索模块

说明：
- 混合检索策略：快速路径 + 向量层级检索
- Phase 1: 快速路径 - 最近记忆（日记向量搜索）
- Phase 2: 向量层级检索 - 久远记忆（年→月→周→日）
- 支持 PostgreSQL FTS 全文检索（关键事件、心动事件）
- Context 预算裁剪

检索流程：
  Step 1: 关键事件 FTS 精确匹配
  Step 2: 最近聊天记录 + 心动事件
  Step 3: 向量搜索（快速路径 → 层级检索）
  Step 4: 获取日记内容 + 关联事件

短期记忆检索（FTS 模式）：
  - 最近3天聊天记录（FTS 匹配）
  - 全部心动事件（FTS 匹配，不限时间）
  - 所有特殊事件（FTS 匹配）
  - 使用用户原始输入进行 FTS，利用 zhparser + scws 自动分词
  - 使用字数裁剪替代 token 计算，节省时间

长期记忆检索（向量模式）：
  - 由 long_term_deep.py 处理
  - 使用用户原始输入生成 embedding（语义更完整）
  - 层级向量检索（年→月→周→日）
  - heartbeat 心动时刻不在长期记忆检索范围内
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import tiktoken

from app.stone import (
    get_background_repo,
    get_key_event_repo,
    get_heartbeat_repo,
    get_chat_repo,
    get_diary_repo,
    get_weekly_repo,
    get_monthly_repo,
    get_annual_repo,
)
from app.agent.memory.embedding import get_embedding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 二级分拣策略：停用词 + 关键词提取
# ---------------------------------------------------------------------------

# 中文停用词表（用于 FTS 查询优化）
STOPWORDS_CN = {
    # 常见停用词
    "是", "的", "了", "在", "有", "和", "与", "或", "这", "那", "它", "他", "她",
    "我", "你", "们", "自己", "什么", "怎么", "为什么", "哪", "哪里", "哪个",
    "多少", "几", "怎样", "如何", "吗", "呢", "吧", "啊", "哦", "嗯", "呀",
    "就", "都", "也", "还", "又", "但", "却", "只", "能", "会", "要", "想",
    "说", "看", "做", "去", "来", "让", "给", "把", "被", "从", "到", "对",
    "很", "太", "挺", "好", "坏", "多", "少", "大", "小", "高", "低", "长", "短",
    # 时间停用词
    "今天", "昨天", "明天", "刚才", "现在", "以后", "之前", "当时", "那时候",
    # 常见动词（信息量低）
    "觉得", "认为", "以为", "知道", "了解", "明白", "理解", "记得", "忘记",
    # 常见形容词（信息量低）
    "一般", "普通", "正常", "特别", "非常", "十分", "极其",
}

def extract_core_keywords(query: str) -> list[str]:
    """提取核心关键词（去掉停用词）
    
    Args:
        query: 用户原始输入
    
    Returns:
        核心关键词列表（用于 OR 查询召回）
    
    Example:
        "我的生日是哪天" → ["生日"]
        "豆豆喜欢吃什么" → ["豆豆", "喜欢", "吃"]
        "我喜欢什么歌" → ["喜欢", "歌"]
    """
    import jieba
    
    # 使用 jieba 分词
    tokens = list(jieba.cut(query))
    
    # 过滤停用词和单字符（除非是关键单字如"歌"、"吃"等）
    core_keywords = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        # 跳过停用词
        if token in STOPWORDS_CN:
            continue
        # 跳过纯数字
        if token.isdigit():
            continue
        # 跳过单字符停用词
        if len(token) == 1 and token not in {"歌", "吃", "喝", "玩", "看", "听", "买", "送", "去", "来"}:
            continue
        core_keywords.append(token)
    
    logger.info(f"[二级分拣] 原始查询: '{query}' → 核心关键词: {core_keywords}")
    return core_keywords


def calculate_match_ratio(query_tokens: list[str], content: str) -> float:
    """计算查询词在内容中的占有率（二级分拣排序）
    
    Args:
        query_tokens: 原始查询分词列表（包含停用词）
        content: 记录内容
    
    Returns:
        占有率 (0.0 - 1.0)
    
    Example:
        query_tokens = ["我的", "生日", "是", "哪天"]  # 4个词
        content = "用户的生日是3月15日"  # 匹配 "生日" 和 "是"
        占有率 = 2/4 = 0.5
    """
    if not query_tokens or not content:
        return 0.0
    
    import jieba
    
    # 对内容分词
    content_tokens = set(jieba.cut(content))
    
    # 计算匹配数量
    matched_count = 0
    for token in query_tokens:
        if token in content_tokens:
            matched_count += 1
    
    ratio = matched_count / len(query_tokens)
    return ratio


def sort_by_match_ratio(results: list[dict], query: str, text_key: str = "content") -> list[dict]:
    """按占有率排序（二级分拣第二级）
    
    Args:
        results: FTS 返回的结果列表
        query: 原始查询
        text_key: 文本字段名
    
    Returns:
        按占有率降序排序的结果列表
    """
    import jieba
    
    # 原始查询分词
    query_tokens = list(jieba.cut(query))
    
    # 计算每条记录的占有率
    for item in results:
        content = item.get(text_key, "")
        ratio = calculate_match_ratio(query_tokens, content)
        item["_match_ratio"] = ratio
    
    # 按占有率降序排序（高占有率排在前面）
    results.sort(key=lambda x: (x.get("_match_ratio", 0), x.get("rank", 0)), reverse=True)
    
    # 打印排序结果（调试）
    if results:
        logger.info(f"[二级分拣] 排序结果:")
        for i, item in enumerate(results[:5]):
            logger.info(f"  #{i+1} 占有率={item.get('_match_ratio', 0):.2f}, content={item.get(text_key, '')[:30]}...")
    
    return results

# Context 预算（tokens）
BUDGET_BACKGROUND = 500
BUDGET_KEY_EVENTS = 400
BUDGET_HEARTBEAT = 300
BUDGET_CHAT = 300
BUDGET_DIARY = 400
BUDGET_LONG_TERM = 600

# 相似度阈值
SIMILARITY_THRESHOLD_HIGH = 0.75  # 高匹配阈值（直接返回）
SIMILARITY_THRESHOLD_LOW = 0.5    # 低匹配阈值（继续层级检索）

# tiktoken encoder 缓存（全局单例，避免每次创建）
_tiktoken_encoder = None


def _get_tiktoken_encoder():
    """获取 tiktoken encoder（全局单例缓存）"""
    global _tiktoken_encoder
    if _tiktoken_encoder is None:
        try:
            _tiktoken_encoder = tiktoken.encoding_for_model("gpt-4o")
        except KeyError:
            _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
        logger.debug("[Tiktoken] Encoder 已初始化")
    return _tiktoken_encoder


def _count_tokens(text: str) -> int:
    """使用 tiktoken 计算 token 数（使用缓存的 encoder）"""
    enc = _get_tiktoken_encoder()
    return len(enc.encode(text))


def _trim_to_budget(items: list[dict], text_key: str, budget: int) -> list[dict]:
    """按相似度/重要性排序裁剪到预算内"""
    result = []
    total_tokens = 0
    for item in items:
        text = item.get(text_key, "")
        tokens = _count_tokens(text)
        if total_tokens + tokens > budget:
            break
        result.append(item)
        total_tokens += tokens
    return result


# ---------------------------------------------------------------------------
# 核心检索函数
# ---------------------------------------------------------------------------

async def retrieve_memories(
    character_id: str,
    user_id: str,
    user_input: str,
    enable_long_term: bool = True,
) -> dict[str, str]:
    """
    混合检索记忆
    
    检索流程：
    1. Phase 1: 快速路径 - 最近记忆（并行检索）
    2. Phase 2: 向量层级检索 - 久远记忆（年→月→周→日）
    
    Args:
        character_id: 角色ID
        user_id: 用户ID
        user_input: 用户输入
        enable_long_term: 是否启用久远记忆检索
    
    Returns:
        包含各类型记忆上下文的字典
    """
    t0 = time.monotonic()
    
    # Step 1: 获取 embedding
    t_embed = time.monotonic()
    query_embedding = await get_embedding(user_input)
    logger.info(f"[Timing] get_embedding: {(time.monotonic()-t_embed)*1000:.0f}ms")
    
    # Step 2: Phase 1 - 快速路径（并行检索）
    t_phase1 = time.monotonic()
    
    # 使用 Stone Repository
    background_repo = get_background_repo()
    
    # 背景知识（向量检索）
    bg_task = background_repo.search_vector(character_id, query_embedding, limit=10)
    key_event_repo = get_key_event_repo()
    heartbeat_repo = get_heartbeat_repo()
    chat_repo = get_chat_repo()
    diary_repo = get_diary_repo()
    annual_repo = get_annual_repo()
    weekly_repo = get_weekly_repo()
    
    # 关键事件（最近7天）
    ke_task = key_event_repo.get_recent(character_id, user_id, days=7, limit=20)
    
    # 心动事件（高强度）
    hb_task = heartbeat_repo.get_high_intensity(character_id, user_id, min_intensity=0.5, limit=10)
    
    # 最近聊天记录
    chat_task = chat_repo.get_recent(character_id, user_id, limit=20, days=3)
    
    # 最近日记向量搜索（快速路径核心）
    diary_task = diary_repo.search_vector(character_id, user_id, query_embedding, limit=5)
    
    bg_results, ke_results, hb_results, chat_results, diary_results = await asyncio.gather(
        bg_task, ke_task, hb_task, chat_task, diary_task
    )
    
    logger.info(f"[Timing] Phase 1 快速路径: {(time.monotonic()-t_phase1)*1000:.0f}ms")
    logger.info(f"[Timing]   - background: {len(bg_results)} 条")
    logger.info(f"[Timing]   - key_events: {len(ke_results)} 条")
    logger.info(f"[Timing]   - heartbeat: {len(hb_results)} 条")
    logger.info(f"[Timing]   - chat: {len(chat_results)} 条")
    logger.info(f"[Timing]   - diary: {len(diary_results)} 条")
    
    # Step 3: 检查是否需要 Phase 2（久远记忆）
    long_term_context = ""
    
    if enable_long_term:
        # 判断是否需要层级检索
        need_hierarchy = _should_do_hierarchy_search(
            diary_results, user_input
        )
        
        if need_hierarchy:
            t_phase2 = time.monotonic()
            long_term_context = await _hierarchical_vector_search(
                character_id, user_id, query_embedding, user_input
            )
            logger.info(f"[Timing] Phase 2 层级检索: {(time.monotonic()-t_phase2)*1000:.0f}ms")
    
    # Step 4: 展开日记关联的事件
    diary_expanded = ""
    if diary_results:
        diary_expanded = await _expand_diary_events(diary_results)
    
    # Step 5: 裁剪到预算
    t_trim = time.monotonic()
    bg_trimmed = _trim_to_budget(bg_results, "chunk_text", BUDGET_BACKGROUND)
    ke_trimmed = _trim_to_budget(ke_results, "content", BUDGET_KEY_EVENTS)
    hb_trimmed = _trim_to_budget(hb_results, "trigger_text", BUDGET_HEARTBEAT)
    chat_trimmed = _trim_to_budget(chat_results, "content", BUDGET_CHAT)
    diary_trimmed = _trim_to_budget(diary_results, "summary", BUDGET_DIARY)
    
    logger.info(f"[Timing] _trim_to_budget: {(time.monotonic()-t_trim)*1000:.0f}ms")
    
    # Step 6: 格式化为上下文文本
    bg_context = _format_background(bg_trimmed)
    ke_context = _format_key_events(ke_trimmed)
    hb_context = _format_heartbeat(hb_trimmed)
    chat_context = _format_chat(chat_trimmed)
    diary_context = _format_diary(diary_trimmed)
    
    total_ms = (time.monotonic() - t0) * 1000
    logger.info(f"[Timing] retrieve_memories 总计: {total_ms:.0f}ms")
    
    # 构建返回结果
    result = {
        "background": bg_context,
        "key_events": ke_context,
        "heartbeat": hb_context,
        "chat": chat_context,
        "diary": diary_context,
        "diary_expanded": diary_expanded,
        "long_term": long_term_context,
        "combined": _build_combined_context(
            bg_context, ke_context, hb_context, chat_context, 
            diary_context, long_term_context
        ),
    }
    
    return result


def _should_do_hierarchy_search(diary_results: list[dict], user_input: str) -> bool:
    """
    判断是否需要层级检索
    
    条件：
    1. 最近日记匹配度不够高
    2. 用户输入包含时间锚点关键词（如"去年"、"夏天"、"很久以前"）
    """
    # 如果日记匹配度高，不需要层级检索
    if diary_results:
        best_similarity = diary_results[0].get("similarity", 0)
        if best_similarity >= SIMILARITY_THRESHOLD_HIGH:
            logger.info(f"[Hierarchical] 日记匹配度高 ({best_similarity:.2f})，跳过层级检索")
            return False
    
    # 检查时间锚点关键词
    time_keywords = [
        "去年", "前年", "大前年", "很久以前", "以前",
        "夏天", "冬天", "春天", "秋天",
        "几月", "哪个月", "什么时候",
        "第一次", "刚开始", "我们认识",
        "去年这个时候", "上次",
    ]
    
    for kw in time_keywords:
        if kw in user_input:
            logger.info(f"[Hierarchical] 检测到时间锚点 '{kw}'，启动层级检索")
            return True
    
    # 默认：如果日记没匹配到，也做层级检索
    if not diary_results:
        logger.info("[Hierarchical] 日记无匹配，启动层级检索")
        return True
    
    return False


async def _hierarchical_vector_search(
    character_id: str,
    user_id: str,
    query_embedding: list[float],
    user_input: str,
) -> str:
    """
    向量层级检索（年→月→周→日）
    
    流程：
    1. 搜索年索引 → 找到匹配年份
    2. 搜索该年的月索引 → 找到匹配月份
    3. 搜索该月的周索引 → 找到匹配周
    4. 搜索该周的日记 → 获取完整内容
    
    支持跳级优化：
    - 如果年索引匹配度高且能直接定位时间 → 跳级
    """
    logger.info("[Hierarchical] 开始层级向量检索")
    
    results = []
    
    # 获取 Repository 实例
    annual_repo = get_annual_repo()
    monthly_repo = get_monthly_repo()
    weekly_repo = get_weekly_repo()
    diary_repo = get_diary_repo()
    
    # Step 1: 搜索年索引
    annual_matches = await annual_repo.search_vector(
        character_id, user_id, query_embedding, limit=3
    )
    
    if not annual_matches:
        logger.info("[Hierarchical] 年索引无匹配，尝试直接搜索月/周/日")
        # Fallback: 并行搜索所有层级
        return await _parallel_vector_search(character_id, user_id, query_embedding)
    
    logger.info(f"[Hierarchical] 年索引匹配: {len(annual_matches)} 条")
    
    # Step 2: 对于每个匹配的年份，搜索月索引
    for annual in annual_matches:
        year = annual.get("year")
        annual_similarity = annual.get("similarity", 0)
        
        logger.info(f"[Hierarchical]   年: {year} (相似度: {annual_similarity:.2f})")
        
        # 如果年索引匹配度很高，可能可以跳级
        if annual_similarity >= SIMILARITY_THRESHOLD_HIGH:
            # 检查是否可以直接定位月份
            month_hint = _extract_month_hint(user_input)
            if month_hint:
                # 直接搜索该月
                logger.info(f"[Hierarchical]   跳级到 {year}年{month_hint}月")
                monthly = await monthly_repo.search_vector(
                    character_id, user_id, query_embedding, 
                    year=year, limit=3
                )
                # 过滤到目标月份
                monthly = [m for m in monthly if m.get("month") == month_hint]
                if monthly:
                    results.extend(await _search_down_from_monthly(
                        character_id, user_id, query_embedding, monthly
                    ))
                continue
        
        # 正常流程：搜索该年的月索引
        monthly_matches = await monthly_repo.search_vector(
            character_id, user_id, query_embedding, 
            year=year, limit=5
        )
        
        if not monthly_matches:
            logger.info(f"[Hierarchical]   {year}年 月索引无匹配")
            continue
        
        logger.info(f"[Hierarchical]   {year}年 月索引匹配: {len(monthly_matches)} 条")
        
        # Step 3: 对于每个匹配的月份，搜索周索引
        results.extend(await _search_down_from_monthly(
            character_id, user_id, query_embedding, monthly_matches
        ))
    
    # 格式化结果
    if results:
        return _format_long_term_results(results)
    
    return ""


async def _search_down_from_monthly(
    character_id: str,
    user_id: str,
    query_embedding: list[float],
    monthly_matches: list[dict],
) -> list[dict]:
    """从月索引向下搜索（周→日）"""
    results = []
    
    for monthly in monthly_matches:
        year = monthly.get("year")
        month = monthly.get("month")
        monthly_similarity = monthly.get("similarity", 0)
        
        logger.info(f"[Hierarchical]     {year}年{month}月 (相似度: {monthly_similarity:.2f})")
        
        # 如果月索引匹配度高，可以跳级直接查日记
        if monthly_similarity >= SIMILARITY_THRESHOLD_HIGH:
            # 查询该月的日记
            diary_matches = await diary_repo.search_vector(
                character_id, user_id, query_embedding, limit=5
            )
            # 过滤到该月
            diary_matches = [
                d for d in diary_matches 
                if str(d.get("diary_date", "")).startswith(f"{year}-{month:02d}")
            ]
            if diary_matches:
                results.extend(diary_matches)
                continue
        
        # 搜索该月的周索引
        weekly_matches = await weekly_repo.search_vector(
            character_id, user_id, query_embedding, limit=5
        )
        
        if not weekly_matches:
            logger.info(f"[Hierarchical]     {year}年{month}月 周索引无匹配")
            continue
        
        logger.info(f"[Hierarchical]     {year}年{month}月 周索引匹配: {len(weekly_matches)} 条")
        
        # Step 4: 对于每个匹配的周，搜索日记
        for weekly in weekly_matches:
            week_start = weekly.get("week_start")
            week_end = weekly.get("week_end")
            weekly_similarity = weekly.get("similarity", 0)
            
            logger.info(f"[Hierarchical]       周 {week_start}~{week_end} (相似度: {weekly_similarity:.2f})")
            
            # 搜索日记
            diary_matches = await diary_repo.search_vector(
                character_id, user_id, query_embedding, limit=5
            )
            
            # 过滤到该周
            diary_matches = [
                d for d in diary_matches 
                if str(week_start) <= str(d.get("diary_date", "")) <= str(week_end)
            ]
            
            if diary_matches:
                results.extend(diary_matches)
    
    return results


async def _parallel_vector_search(
    character_id: str,
    user_id: str,
    query_embedding: list[float],
) -> str:
    """并行搜索所有层级（Fallback）"""
    logger.info("[Hierarchical] 并行向量搜索")
    
    # 获取 Repository 实例
    annual_repo = get_annual_repo()
    monthly_repo = get_monthly_repo()
    weekly_repo = get_weekly_repo()
    diary_repo = get_diary_repo()
    
    # 并行搜索所有层级
    annual_task = annual_repo.search_vector(character_id, user_id, query_embedding, limit=3)
    monthly_task = monthly_repo.search_vector(character_id, user_id, query_embedding, limit=5)
    weekly_task = weekly_repo.search_vector(character_id, user_id, query_embedding, limit=7)
    diary_task = diary_repo.search_vector(character_id, user_id, query_embedding, limit=10)
    
    annual, monthly, weekly, diary = await asyncio.gather(
        annual_task, monthly_task, weekly_task, diary_task
    )
    
    logger.info(f"[Hierarchical]   - annual: {len(annual)} 条")
    logger.info(f"[Hierarchical]   - monthly: {len(monthly)} 条")
    logger.info(f"[Hierarchical]   - weekly: {len(weekly)} 条")
    logger.info(f"[Hierarchical]   - diary: {len(diary)} 条")
    
    # 合并所有结果，按相似度排序
    all_results = []
    all_results.extend(annual)
    all_results.extend(monthly)
    all_results.extend(weekly)
    all_results.extend(diary)
    
    # 按相似度排序
    all_results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    
    # 取前3个最匹配的
    top_results = all_results[:3]
    
    return _format_long_term_results(top_results)


def _extract_month_hint(user_input: str) -> Optional[int]:
    """从用户输入中提取月份提示"""
    # 注意：需要按长度降序排列，避免"十二月"被"二月"错误匹配
    month_keywords = [
        ("夏天", [6, 7, 8]),
        ("暑假", [7, 8]),
        ("冬天", [12, 1, 2]),
        ("寒假", [1, 2]),
        ("春天", [3, 4, 5]),
        ("秋天", [9, 10, 11]),
        ("十二月", 12),
        ("十一月", 11),
        ("十月", 10),
        ("九月", 9),
        ("八月", 8),
        ("七月", 7),
        ("六月", 6),
        ("五月", 5),
        ("四月", 4),
        ("三月", 3),
        ("二月", 2),
        ("一月", 1),
        ("正月", 1),
    ]
    
    for kw, months in month_keywords:
        if kw in user_input:
            if isinstance(months, list):
                return months[0]  # 返回第一个可能的月份
            return months
    
    return None


async def _expand_diary_events(diary_results: list[dict]) -> str:
    """
    展开日记关联的事件
    
    获取日记的 key_event_ids 和 heartbeat_ids，展开为完整内容
    """
    if not diary_results:
        return ""
    
    # 收集所有关联的事件ID
    key_event_ids = []
    heartbeat_ids = []
    
    for diary in diary_results:
        key_event_ids.extend(diary.get("key_event_ids", []) or [])
        heartbeat_ids.extend(diary.get("heartbeat_ids", []) or [])
    
    if not key_event_ids and not heartbeat_ids:
        return ""
    
    # 获取 Repository 实例
    key_event_repo = get_key_event_repo()
    heartbeat_repo = get_heartbeat_repo()
    
    # 批量获取事件详情
    key_events = []
    heartbeat_events = []
    
    if key_event_ids:
        key_events = await key_event_repo.get_by_ids(key_event_ids[:20])
    
    if heartbeat_ids:
        heartbeat_events = await heartbeat_repo.get_by_ids(heartbeat_ids[:10])
    
    # 格式化输出
    lines = []
    
    if key_events:
        lines.append("关联关键事件:")
        for evt in key_events:
            lines.append(f"  [{evt.get('event_type', 'unknown')}] {evt.get('content', '')}")
    
    if heartbeat_events:
        lines.append("关联心动时刻:")
        for evt in heartbeat_events:
            lines.append(f"  [{evt.get('event_node', 'unknown')}] {evt.get('trigger_text', '')}")
    
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 格式化函数
# ---------------------------------------------------------------------------

def _format_background(items: list[dict]) -> str:
    """格式化背景知识"""
    if not items:
        return "无"
    return "\n".join(item.get('chunk_text', '') for item in items)


def _format_key_events(items: list[dict]) -> str:
    """格式化关键事件"""
    if not items:
        return "无"
    return "\n".join(
        f"[{item.get('event_type', 'fact')}] {item.get('content', '')}"
        for item in items
    )


def _format_heartbeat(items: list[dict]) -> str:
    """格式化心动事件"""
    if not items:
        return "无"
    return "\n".join(
        f"[{item.get('event_node', 'unknown')}] {item.get('trigger_text', '')} (强度{item.get('intensity', 0):.1f})"
        for item in items
    )


def _format_chat(items: list[dict]) -> str:
    """格式化聊天记录"""
    if not items:
        return "无"
    return "\n".join(
        f"{item.get('role', 'unknown')}: {item.get('content', '')}"
        for item in items
    )


def _format_diary(items: list[dict]) -> str:
    """格式化日记"""
    if not items:
        return "无"
    return "\n".join(
        f"[{item.get('diary_date', '')}] {item.get('summary', '')}"
        for item in items
    )


def _format_long_term_results(items: list[dict]) -> str:
    """格式化久远记忆结果"""
    if not items:
        return ""
    
    lines = []
    for item in items:
        # 根据类型格式化
        if "year" in item and "month" in item:
            # 月索引
            lines.append(f"[{item['year']}年{item['month']}月] {item.get('summary', '')}")
        elif "year" in item:
            # 年索引
            lines.append(f"[{item['year']}年] {item.get('summary', '')}")
        elif "week_start" in item:
            # 周索引
            lines.append(f"[{item['week_start']}~{item['week_end']}] {item.get('summary', '')}")
        elif "diary_date" in item:
            # 日记
            lines.append(f"[{item['diary_date']}] {item.get('summary', '')}")
        else:
            lines.append(str(item.get('summary', '')))
    
    return "\n".join(lines)


def _build_combined_context(
    bg: str, ke: str, hb: str, chat: str, 
    diary: str, long_term: str
) -> str:
    """构建组合上下文"""
    parts = [
        f"### 角色背景\n{bg}",
        f"### 关键事件\n{ke}",
        f"### 心动时刻\n{hb}",
        f"### 最近对话\n{chat}",
        f"### 最近日记\n{diary}",
    ]
    
    if long_term:
        parts.append(f"### 久远记忆\n{long_term}")
    
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

async def retrieve_key_events_fts(
    character_id: str,
    user_id: str,
    query: str,
    event_types: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    PostgreSQL FTS 全文检索关键事件
    
    Args:
        character_id: 角色ID
        user_id: 用户ID
        query: 搜索查询（支持 & | ! 操作符）
        event_types: 限定事件类型列表
        limit: 返回条数
    
    Returns:
        匹配的关键事件列表
    """
    key_event_repo = get_key_event_repo()
    return await key_event_repo.search_fts(
        character_id=character_id,
        user_id=user_id,
        query=query,
        limit=limit,
        event_types=event_types,
    )


async def search_key_events_multi_keywords(
    character_id: str,
    user_id: str,
    keywords: list[str],
    event_types: list[str] | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    并行搜索多个关键词，合并去重

    流程：
    1. 并行搜索每个关键词
    2. 合并结果
    3. 按 rank 排序去重
    4. 返回 top N

    Args:
        character_id: 角色ID
        user_id: 用户ID
        keywords: 关键词列表
        event_types: 限定事件类型
        limit: 返回条数

    Returns:
        合并后的检索结果
    """
    if not keywords:
        return []

    key_event_repo = get_key_event_repo()

    # 并行搜索每个关键词
    tasks = [
        key_event_repo.search_fts(
            character_id, user_id, kw,
            limit=limit,
            event_types=event_types,
        )
        for kw in keywords
    ]

    results_list = await asyncio.gather(*tasks)

    # 合并去重
    seen_ids = set()
    merged_results = []

    for results in results_list:
        for r in results:
            r_id = r.get("id")
            if r_id and r_id not in seen_ids:
                seen_ids.add(r_id)
                merged_results.append(r)

    # 按 rank 降序排序
    merged_results.sort(key=lambda x: x.get("rank", 0), reverse=True)

    # 截取 limit 条
    return merged_results[:limit]


async def search_chat_messages_multi_keywords(
    character_id: str,
    user_id: str,
    keywords: list[str],
    days: int = 3,
    limit: int = 3,
) -> list[dict]:
    """
    并行搜索多个关键词，合并去重

    Args:
        character_id: 角色ID
        user_id: 用户ID
        keywords: 关键词列表
        days: 搜索最近N天
        limit: 返回条数

    Returns:
        合并后的检索结果
    """
    if not keywords:
        return []

    chat_repo = get_chat_repo()

    # 并行搜索
    tasks = [
        chat_repo.search_fts(character_id, user_id, kw, limit=limit, days=days)
        for kw in keywords
    ]

    results_list = await asyncio.gather(*tasks)

    # 合并去重
    seen_ids = set()
    merged_results = []

    for results in results_list:
        for r in results:
            r_id = r.get("id")
            if r_id and r_id not in seen_ids:
                seen_ids.add(r_id)
                merged_results.append(r)

    # 按 rank 排序
    merged_results.sort(key=lambda x: x.get("rank", 0), reverse=True)

    return merged_results[:limit]


async def search_heartbeat_multi_keywords(
    character_id: str,
    user_id: str,
    keywords: list[str],
    days: int = 7,
    limit: int = 3,
) -> list[dict]:
    """
    并行搜索多个关键词，合并去重

    Args:
        character_id: 角色ID
        user_id: 用户ID
        keywords: 关键词列表
        days: 搜索最近N天
        limit: 返回条数

    Returns:
        合并后的检索结果
    """
    if not keywords:
        return []

    heartbeat_repo = get_heartbeat_repo()

    # 并行搜索
    tasks = [
        heartbeat_repo.search_fts(character_id, user_id, kw, limit=limit, days=days)
        for kw in keywords
    ]

    results_list = await asyncio.gather(*tasks)

    # 合并去重
    seen_ids = set()
    merged_results = []

    for results in results_list:
        for r in results:
            r_id = r.get("id")
            if r_id and r_id not in seen_ids:
                seen_ids.add(r_id)
                merged_results.append(r)

    # 按 rank 排序
    merged_results.sort(key=lambda x: x.get("rank", 0), reverse=True)

    return merged_results[:limit]


# 字数限制（替代 token 计算，节省 ~50ms tiktoken 编码时间）
MAX_CHARS_CHAT = 500       # 最近聊天记录
MAX_CHARS_HEARTBEAT = 200  # 心动时刻
MAX_CHARS_SPECIAL = 500    # 特殊事件
MAX_CHARS_TOTAL = 1200     # 总字数限制


def _trim_by_chars(items: list[dict], text_key: str, max_chars: int) -> list[dict]:
    """按字数裁剪（替代 token 计算，节省时间）
    
    Args:
        items: 数据列表
        text_key: 文本字段名
        max_chars: 最大字数
    
    Returns:
        裁剪后的列表
    """
    result = []
    total_chars = 0
    for item in items:
        text = item.get(text_key, "")
        chars = len(text)
        if total_chars + chars > max_chars:
            # 如果单条超限，截断该条
            if not result and chars > max_chars:
                trimmed_item = item.copy()
                trimmed_item[text_key] = text[:max_chars]
                result.append(trimmed_item)
            break
        result.append(item)
        total_chars += chars
    return result


async def retrieve_short_term_memories(
    character_id: str,
    user_id: str,
    user_input: str = "",
) -> dict[str, str]:
    """
    短期记忆检索（二级分拣策略：停用词过滤 + OR召回 + 占有率排序）

    二级分拣策略：
    - 第一级：提取核心关键词，OR 查询召回（高召回率）
    - 第二级：按原始查询词在结果中的占有率排序（高准确率）
    
    根据用户输入进行 FTS 匹配，获取相关记忆：
    1. 聊天记录：FTS 匹配最近3天，获取上下文
    2. 心动事件：FTS 匹配全部（不限时间）
    3. 特殊事件：FTS 匹配
    
    如果 user_input 为空，则回退到固定加载模式。

    Args:
        character_id: 角色ID
        user_id: 用户ID
        user_input: 用户输入（用于 FTS 匹配）

    Returns:
        包含短期记忆上下文的字典
    """
    t0 = time.monotonic()

    # 获取 Repository 实例
    chat_repo = get_chat_repo()
    key_event_repo = get_key_event_repo()
    heartbeat_repo = get_heartbeat_repo()

    # 如果没有用户输入，回退到固定加载
    if not user_input or not user_input.strip():
        logger.info("[短期记忆] 无用户输入，回退到固定加载模式")
        return await _retrieve_short_term_memories_fallback(character_id, user_id)

    logger.info(f"[短期记忆] FTS 查询: '{user_input}'")
    
    # ============================================================
    # 二级分拣策略：第一级 - 提取核心关键词，OR 查询召回
    # ============================================================
    core_keywords = extract_core_keywords(user_input)
    
    # 如果提取到核心关键词，使用 OR 语义查询
    if core_keywords:
        logger.info(f"[二级分拣] 使用 OR 语义查询，核心关键词: {core_keywords}")
        
        # 并行执行三个 FTS 检索（使用 OR 语义）
        chat_fts_task = chat_repo.search_fts(
            character_id, user_id, " ".join(core_keywords), limit=10, days=3
        )
        hb_fts_task = heartbeat_repo.search_fts(
            character_id, user_id, " ".join(core_keywords), limit=10, days=None
        )
        se_fts_task = key_event_repo.search_fts(
            character_id, user_id, " ".join(core_keywords),
            limit=20,
            event_types=['preference', 'fact', 'schedule', 'initiative'],
        )
    else:
        # 没有核心关键词，直接使用原始输入
        logger.info("[短期记忆] 使用 zhparser + scws 自动分词")
        chat_fts_task = chat_repo.search_fts(
            character_id, user_id, user_input, limit=5, days=3
        )
        hb_fts_task = heartbeat_repo.search_fts(
            character_id, user_id, user_input, limit=5, days=None
        )
        se_fts_task = key_event_repo.search_fts(
            character_id, user_id, user_input,
            limit=10,
            event_types=['preference', 'fact', 'schedule', 'initiative']
        )
    
    chat_fts_results, hb_fts_results, se_fts_results = await asyncio.gather(
        chat_fts_task, hb_fts_task, se_fts_task
    )
    
    logger.info(f"[Timing] FTS 检索: {(time.monotonic()-t0)*1000:.0f}ms")
    logger.info(f"[Timing]   - chat FTS: {len(chat_fts_results)} 条")
    logger.info(f"[Timing]   - heartbeat FTS: {len(hb_fts_results)} 条")
    logger.info(f"[Timing]   - special_events FTS: {len(se_fts_results)} 条")
    
    # ============================================================
    # 二级分拣策略：第二级 - 按占有率排序
    # ============================================================
    if core_keywords and se_fts_results:
        se_fts_results = sort_by_match_ratio(se_fts_results, user_input, text_key="content")
        # 截取前 10 条
        se_fts_results = se_fts_results[:10]
        logger.info(f"[二级分拣] 特殊事件排序后: {len(se_fts_results)} 条")
    
    if core_keywords and hb_fts_results:
        hb_fts_results = sort_by_match_ratio(hb_fts_results, user_input, text_key="trigger_text")
        hb_fts_results = hb_fts_results[:5]
        logger.info(f"[二级分拣] 心动事件排序后: {len(hb_fts_results)} 条")
    
    # 处理聊天记录上下文
    t_chat = time.monotonic()
    chat_contexts = []
    seen_message_ids = set()
    
    for match in chat_fts_results:
        msg_id = match.get("id")
        if msg_id in seen_message_ids:
            continue
        seen_message_ids.add(msg_id)
        
        # 获取上下文（前后各1条 = 总共最多5条：前1+匹配1+后1，但可能有user/assistant交替）
        context = await chat_repo.get_context_around_message(
            character_id, user_id, msg_id,
            context_before=2, context_after=2
        )
        for ctx_msg in context:
            ctx_id = ctx_msg.get("id")
            if ctx_id not in seen_message_ids:
                seen_message_ids.add(ctx_id)
                chat_contexts.append(ctx_msg)
    
    # 按时间排序并去重
    chat_contexts.sort(key=lambda x: x.get("created_at", ""))
    logger.info(f"[Timing] 聊天上下文获取: {(time.monotonic()-t_chat)*1000:.0f}ms, {len(chat_contexts)} 条")
    
    # 格式化
    chat_context = _format_short_term_chat(chat_contexts)
    hb_context = _format_short_term_heartbeat(hb_fts_results)
    se_context = _format_short_term_special_events(se_fts_results)
    
    # 构建组合上下文
    combined = _build_short_term_combined(chat_context, hb_context, se_context)
    
    logger.info(f"[Timing] retrieve_short_term_memories 总计: {(time.monotonic()-t0)*1000:.0f}ms")
    
    return {
        "chat": chat_context,
        "heartbeat": hb_context,
        "special_events": se_context,
        "combined": combined,
    }


async def _retrieve_short_term_memories_fallback(
    character_id: str,
    user_id: str,
) -> dict[str, str]:
    """固定加载模式（无用户输入时的回退方案）"""
    t0 = time.monotonic()
    
    # 获取 Repository 实例
    chat_repo = get_chat_repo()
    heartbeat_repo = get_heartbeat_repo()
    key_event_repo = get_key_event_repo()
    
    # 并行检索三种数据
    chat_task = chat_repo.get_recent(character_id, user_id, limit=10, days=3)
    hb_task = heartbeat_repo.get_high_intensity(
        character_id, user_id, 
        min_intensity=0.5, 
        limit=5,
        days=7
    )
    se_task = key_event_repo.get_special_events(character_id, user_id, days_ahead=30)
    
    chat_results, hb_results, se_results = await asyncio.gather(
        chat_task, hb_task, se_task
    )
    
    logger.info(f"[Timing] 固定加载: {(time.monotonic()-t0)*1000:.0f}ms")
    
    # 字数裁剪
    chat_trimmed = _trim_by_chars(chat_results, "content", MAX_CHARS_CHAT)
    hb_trimmed = _trim_by_chars(hb_results, "trigger_text", MAX_CHARS_HEARTBEAT)
    se_trimmed = _trim_by_chars(se_results, "content", MAX_CHARS_SPECIAL)
    
    # 格式化
    chat_context = _format_short_term_chat(chat_trimmed)
    hb_context = _format_short_term_heartbeat(hb_trimmed)
    se_context = _format_short_term_special_events(se_trimmed)
    
    # 构建组合上下文
    combined = _build_short_term_combined(chat_context, hb_context, se_context)
    
    return {
        "chat": chat_context,
        "heartbeat": hb_context,
        "special_events": se_context,
        "combined": combined,
    }


def _format_short_term_chat(items: list[dict]) -> str:
    """格式化短期聊天记录（按时间正序）"""
    if not items:
        return "无"
    # 反转顺序，使最早的记录在前
    items_reversed = list(reversed(items))
    return "\n".join(
        f"{item.get('role', 'unknown')}: {item.get('content', '')}"
        for item in items_reversed
    )


def _format_short_term_heartbeat(items: list[dict]) -> str:
    """格式化心动时刻"""
    if not items:
        return "无"
    return "\n".join(
        f"[心动] {item.get('trigger_text', '')}"
        for item in items
    )


def _format_short_term_special_events(items: list[dict]) -> str:
    """格式化特殊事件（按类型分组）"""
    if not items:
        return "无"
    
    # 按类型分组
    grouped = {}
    for item in items:
        event_type = item.get('event_type', 'unknown')
        if event_type not in grouped:
            grouped[event_type] = []
        grouped[event_type].append(item)
    
    # 按优先级排序输出
    priority_order = ['preference', 'fact', 'initiative', 'schedule']
    lines = []
    
    for event_type in priority_order:
        if event_type in grouped:
            type_label = {
                'preference': '喜好偏好',
                'fact': '用户信息',
                'initiative': '重要时刻',
                'schedule': '日程安排',
            }.get(event_type, event_type)
            lines.append(f"[{type_label}]")
            for item in grouped[event_type]:
                lines.append(f"  - {item.get('content', '')}")
    
    return "\n".join(lines)


def _build_short_term_combined(
    chat: str, 
    heartbeat: str, 
    special_events: str
) -> str:
    """构建短期记忆组合上下文"""
    parts = []
    
    # 特殊事件（用户偏好/事实等，优先级最高）
    if special_events and special_events != "无":
        parts.append(f"### 用户信息与偏好\n{special_events}")
    
    # 心动时刻
    if heartbeat and heartbeat != "无":
        parts.append(f"### 心动时刻\n{heartbeat}")
    
    # 最近对话
    if chat and chat != "无":
        parts.append(f"### 最近对话\n{chat}")
    
    if not parts:
        return ""
    
    return "\n\n".join(parts)
