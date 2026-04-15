"""长期记忆检索模块（简化版，无 LLM 意图分析）

说明：
- 纯向量检索 + 规则匹配，不使用 LLM 意图分析
- 利用 embedding 的语义理解能力直接检索
- 时间锚点通过规则匹配提取（"去年"→year-1）
- Context 预算裁剪：最多 5 条，每条 ≤ 100 字，总 ≤ 500 字

检索流程：
  Step 1: 提取时间锚点（规则匹配）
  Step 2: 生成查询向量
  Step 3: 并行向量检索（年→月→日层级）
  Step 4: 合并 + 按相似度排序
  Step 5: Context 预算裁剪
  Step 6: 格式化输出

示例：
  输入: "去年那个蜜雪冰城真好喝"
  时间锚点: {year: 2025}
  输出: "[2025-07-15] 和主人一起去蜜雪冰城喝柠檬茶..."
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from app.agent.memory.embedding import get_embedding
from app.agent.db.memory_models import (
    search_annual_by_embedding,
    search_monthly_by_embedding,
    search_diary_by_embedding,
    get_annual_index,
    get_monthly_index,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 时间锚点规则定义
# ---------------------------------------------------------------------------

# 时间锚点关键词 → 提取函数
# 注意：按关键词长度降序排列，避免"十二月"被"二月"匹配
TIME_ANCHOR_RULES: dict[str, callable] = {
    # 年份相关
    "大前年": lambda year: {"year": year - 3},
    "前年": lambda year: {"year": year - 2},
    "去年": lambda year: {"year": year - 1},
    "很久以前": lambda: {"min_year": None},  # 不限制年份
    "以前": lambda: {"min_year": None},
    "当初": lambda: {"min_year": None},
    
    # 季节相关
    "暑假": lambda: {"months": [7, 8]},
    "寒假": lambda: {"months": [1, 2]},
    "夏天": lambda: {"months": [6, 7, 8]},
    "冬天": lambda: {"months": [12, 1, 2]},
    "春天": lambda: {"months": [3, 4, 5]},
    "秋天": lambda: {"months": [9, 10, 11]},
    
    # 月份相关（按长度降序）
    "十二月": lambda: {"month": 12},
    "十一月": lambda: {"month": 11},
    "十月": lambda: {"month": 10},
    "九月": lambda: {"month": 9},
    "八月": lambda: {"month": 8},
    "七月": lambda: {"month": 7},
    "六月": lambda: {"month": 6},
    "五月": lambda: {"month": 5},
    "四月": lambda: {"month": 4},
    "三月": lambda: {"month": 3},
    "二月": lambda: {"month": 2},
    "一月": lambda: {"month": 1},
    "正月": lambda: {"month": 1},
    
    # 特殊时间点
    "刚认识": lambda: {"event_type": "initiative"},  # 查找里程碑事件
    "第一次": lambda: {"event_type": "initiative"},
    "我们认识": lambda: {"event_type": "initiative"},
    "刚开始": lambda: {"event_type": "initiative"},
}

# 按关键词长度降序排列的关键词列表（用于匹配）
_SORTED_TIME_KEYWORDS = sorted(TIME_ANCHOR_RULES.keys(), key=len, reverse=True)


# ---------------------------------------------------------------------------
# 时间锚点提取
# ---------------------------------------------------------------------------

def extract_time_anchor(user_input: str) -> dict | None:
    """
    提取时间锚点（规则匹配，支持多锚点组合）
    
    Args:
        user_input: 用户输入文本
    
    Returns:
        时间锚点字典，如：
        - {"year": 2025} - 去年
        - {"year": 2025, "months": [6, 7, 8]} - 去年夏天
        - {"months": [6, 7, 8]} - 夏天
        - {"month": 7} - 七月
        - {"event_type": "initiative"} - 刚认识
        - None - 无时间锚点
    
    示例：
        extract_time_anchor("去年那个蜜雪冰城真好喝") → {"year": 2025}
        extract_time_anchor("去年夏天我们去海边") → {"year": 2025, "months": [6, 7, 8]}
    """
    current_year = datetime.now().year
    result = {}
    
    # 定义锚点类型优先级（先匹配年份，再匹配季节/月份）
    anchor_types = {
        "year": ["大前年", "前年", "去年"],
        "season": ["暑假", "寒假", "夏天", "冬天", "春天", "秋天"],
        "month": ["十二月", "十一月", "十月", "九月", "八月", "七月", 
                  "六月", "五月", "四月", "三月", "二月", "一月", "正月"],
        "event": ["刚认识", "第一次", "我们认识", "刚开始"],
        "general": ["很久以前", "以前", "当初"],
    }
    
    # 按类型顺序匹配
    for anchor_type, keywords in anchor_types.items():
        for keyword in keywords:
            if keyword in user_input:
                extractor = TIME_ANCHOR_RULES[keyword]
                try:
                    extracted = extractor(current_year)
                except TypeError:
                    extracted = extractor()
                
                # 合并结果
                if extracted:
                    result.update(extracted)
                    logger.debug(f"[时间锚点] 匹配 '{keyword}' → {extracted}")
                break  # 每种类型只匹配一次
    
    return result if result else None


# ---------------------------------------------------------------------------
# Context 预算裁剪
# ---------------------------------------------------------------------------

def _trim_to_budget(
    items: list[dict],
    max_items: int = 5,
    max_chars_per_item: int = 100,
    max_total_chars: int = 500,
) -> list[dict]:
    """
    Context 预算裁剪
    
    Args:
        items: 检索结果列表（已按相似度排序）
        max_items: 最多条数（默认 5）
        max_chars_per_item: 每条最大字数（默认 100）
        max_total_chars: 总字数上限（默认 500）
    
    Returns:
        裁剪后的结果列表
    
    示例：
        items = [
            {"similarity": 0.9, "summary": "很长的内容..."},
            {"similarity": 0.8, "summary": "另一个内容..."},
        ]
        result = _trim_to_budget(items)  # 裁剪到 5 条 × 100 字
    """
    result = []
    total_chars = 0
    
    for item in items:
        # 检查条数限制
        if len(result) >= max_items:
            logger.debug(f"[裁剪] 达到条数上限 {max_items}")
            break
        
        # 获取并截断单条内容
        summary = item.get("summary", "")
        if len(summary) > max_chars_per_item:
            summary = summary[:max_chars_per_item] + "..."
            logger.debug(f"[裁剪] 单条截断: {len(item['summary'])} → {len(summary)}")
        
        # 检查总字数限制
        if total_chars + len(summary) > max_total_chars:
            logger.debug(f"[裁剪] 达到总字数上限 {max_total_chars}")
            break
        
        # 添加到结果
        trimmed_item = item.copy()
        trimmed_item["summary"] = summary
        result.append(trimmed_item)
        total_chars += len(summary)
    
    logger.info(f"[裁剪] 最终保留 {len(result)} 条，总字数 {total_chars}")
    return result


# ---------------------------------------------------------------------------
# 核心检索函数
# ---------------------------------------------------------------------------

async def retrieve_long_term_memories(
    character_id: str,
    user_id: str,
    user_input: str,
) -> dict[str, Any]:
    """
    长期记忆检索（简化版，无 LLM 意图分析）
    
    利用向量检索的语义理解能力，直接检索相关记忆。
    时间锚点通过规则匹配提取。
    
    Args:
        character_id: 角色ID
        user_id: 用户ID
        user_input: 用户输入文本
    
    Returns:
        {
            "long_term": "格式化的长期记忆文本（最多 5 条 × 100 字）",
            "has_match": bool,  # 是否有匹配结果
            "time_anchor": dict | None,  # 提取的时间锚点
        }
    
    限制：
        - 最多 5 条记忆
        - 每条最多 100 字
        - 总字数 ≤ 500 字
    
    示例：
        result = await retrieve_long_term_memories(
            "daji", "default_user", "去年那个蜜雪冰城真好喝"
        )
        # result["long_term"] = "[2025-07-15] 和主人一起去蜜雪冰城..."
    """
    t0 = time.monotonic()
    
    # Step 1: 提取时间锚点
    time_anchor = extract_time_anchor(user_input)
    logger.info(f"[长期记忆] 时间锚点: {time_anchor}")
    
    # Step 2: 生成查询向量
    t_embed = time.monotonic()
    query_embedding = await get_embedding(user_input)
    logger.info(f"[Timing] get_embedding: {(time.monotonic()-t_embed)*1000:.0f}ms")
    
    # Step 3: 并行向量检索
    t_search = time.monotonic()
    tasks = []
    
    # 年索引检索（有时间锚点时，通过 get_annual_index 获取指定年份）
    if time_anchor and time_anchor.get("year"):
        year = time_anchor["year"]
        # 直接获取指定年份的年索引（不用向量检索）
        # 年索引的 summary 已经包含了该年的重要事件概括
        logger.info(f"[长期记忆] 年份定向检索: {year}")
    
    # 月索引向量检索（有时间锚点时）
    if time_anchor and time_anchor.get("year"):
        year = time_anchor["year"]
        tasks.append(
            search_monthly_by_embedding(
                character_id, user_id, query_embedding,
                year=year, limit=5
            )
        )
    
    # 日记检索（始终执行）
    tasks.append(
        search_diary_by_embedding(
            character_id, user_id, query_embedding, limit=10
        )
    )
    
    # 如果有年份锚点，额外获取该年份的年索引
    annual_summary = None
    if time_anchor and time_anchor.get("year"):
        year = time_anchor["year"]
        annual_record = await get_annual_index(character_id, user_id, year)
        if annual_record:
            annual_summary = {
                "source": "annual",
                "similarity": 1.0,  # 直接匹配，相似度为 1
                "date": f"{year}年",
                "summary": annual_record.get("summary", ""),
            }
            logger.info(f"[长期记忆] 找到 {year} 年索引")
    
    results_list = await asyncio.gather(*tasks, return_exceptions=True)
    logger.info(f"[Timing] 向量检索: {(time.monotonic()-t_search)*1000:.0f}ms")
    
    # Step 4: 合并 + 排序
    t_merge = time.monotonic()
    all_results = []
    
    for i, results in enumerate(results_list):
        if isinstance(results, Exception):
            logger.warning(f"[长期记忆] 检索任务 {i} 失败: {results}")
            continue
        
        for item in results:
            # 判断来源类型
            if "year" in item and "month" not in item:
                source = "annual"
                date_str = f"{item['year']}年"
            elif "month" in item:
                source = "monthly"
                date_str = f"{item['year']}年{item['month']}月"
            elif "diary_date" in item:
                source = "diary"
                date_str = str(item["diary_date"])
            else:
                source = "unknown"
                date_str = "未知时间"
            
            all_results.append({
                "source": source,
                "similarity": item.get("similarity", 0),
                "date": date_str,
                "summary": item.get("summary", ""),
                "raw": item,  # 保留原始数据用于调试
            })
    
    # 添加年索引结果（如果有）
    if annual_summary:
        all_results.append(annual_summary)
    
    # 按相似度排序
    all_results.sort(key=lambda x: x["similarity"], reverse=True)
    logger.info(f"[长期记忆] 合并结果: {len(all_results)} 条")
    
    # Step 5: Context 预算裁剪
    t_trim = time.monotonic()
    trimmed = _trim_to_budget(
        all_results,
        max_items=5,
        max_chars_per_item=100,
        max_total_chars=500,
    )
    logger.info(f"[Timing] 裁剪: {(time.monotonic()-t_trim)*1000:.0f}ms")
    
    # Step 6: 格式化输出
    if not trimmed:
        total_ms = (time.monotonic() - t0) * 1000
        logger.info(f"[长期记忆] 无匹配结果，耗时 {total_ms:.0f}ms")
        return {
            "long_term": "",
            "has_match": False,
            "time_anchor": time_anchor,
        }
    
    # 格式化为简洁文本
    lines = []
    for item in trimmed:
        source_label = {
            "annual": "年记",
            "monthly": "月记",
            "diary": "日记",
        }.get(item["source"], "")
        
        lines.append(f"[{item['date']}] {item['summary']}")
    
    long_term_text = "\n".join(lines)
    
    total_ms = (time.monotonic() - t0) * 1000
    logger.info(f"[长期记忆] 检索完成: {len(trimmed)} 条, 总字数 {len(long_term_text)}, 耗时 {total_ms:.0f}ms")
    
    return {
        "long_term": long_term_text,
        "has_match": True,
        "time_anchor": time_anchor,
    }


# ---------------------------------------------------------------------------
# 辅助函数：里程碑事件检索（用于"刚认识"等特殊时间点）
# ---------------------------------------------------------------------------

async def _search_initiative_events(
    character_id: str,
    user_id: str,
) -> list[dict]:
    """
    搜索里程碑事件（initiative 类型）
    
    用于"刚认识"、"第一次"等特殊时间锚点。
    
    Args:
        character_id: 角色ID
        user_id: 用户ID
    
    Returns:
        里程碑事件列表（按时间正序，最早的在前）
    """
    from app.agent.db.memory_models import get_key_events_by_type
    
    events = await get_key_events_by_type(
        character_id, user_id,
        event_type="initiative",
        limit=5,
        active_only=True,
    )
    
    if events:
        # 按时间正序（最早的在前）
        events.sort(key=lambda x: x.get("created_at") or x.get("event_date") or "")
        logger.info(f"[里程碑] 找到 {len(events)} 条里程碑事件")
    
    return events


# ---------------------------------------------------------------------------
# 检测是否需要长期记忆检索
# ---------------------------------------------------------------------------

def should_trigger_long_term_recall(
    user_input: str,
    short_term_result: dict,
) -> bool:
    """
    判断是否需要触发长期记忆检索
    
    判断条件：
    1. 用户输入包含时间锚点关键词
    2. 短期记忆无匹配，但用户明显在提及过去
    
    Args:
        user_input: 用户输入
        short_term_result: 短期记忆检索结果
    
    Returns:
        是否需要触发长期记忆检索
    
    示例：
        should_trigger_long_term_recall("去年那个蜜雪冰城", {"has_match": False}) → True
    """
    # 条件1：包含时间锚点
    if extract_time_anchor(user_input):
        logger.info(f"[触发判断] 检测到时间锚点，触发长期记忆检索")
        return True
    
    # 条件2：短期记忆无匹配 + 过去相关的关键词
    if not short_term_result.get("has_match"):
        past_keywords = ["还记得", "记得", "那次", "那回", "以前我们", "那时候"]
        for kw in past_keywords:
            if kw in user_input:
                logger.info(f"[触发判断] 检测到过去关键词 '{kw}' + 无短期匹配，触发检索")
                return True
    
    return False