"""检索策略路由模块（长期记忆 Deep Path - Step 2）

功能：
- 层级向量检索（年→月→周→日）- 核心检索方式
- 向量匹配后 FTS 精细定位（在匹配范围内搜索关键词）
- 展开 diary 关联事件
- 合并结果，按相似度排序

数据源（长期记忆）：
- diary: 日记（向量检索）
- annual/monthly/weekly: 层级索引（向量检索）
- key_events: 关键事件（向量匹配后 FTS 精细定位）

注意：heartbeat 心动时刻由短期记忆处理（FTS 全文检索，不限时间）
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from app.stone import (
    get_key_event_repo,
    get_diary_repo,
    get_weekly_repo,
    get_monthly_repo,
    get_annual_repo,
)
from app.agent.memory.embedding import get_embedding

logger = logging.getLogger(__name__)


# ── 核心函数 ──────────────────────────────────────────────────────────────────────

async def execute_search(
    character_id: str,
    user_id: str,
    intent: dict,
    user_input: str = "",
) -> list[dict]:
    """
    根据意图执行长期记忆检索（纯向量检索）
    
    流程：
    1. 使用用户原始输入生成 embedding（语义更完整）
    2. 层级向量检索（年→月→周→日）
    3. 向量匹配后，用关键词 FTS 精细定位 key_events
    4. 展开 diary.key_event_ids 获取关联事件
    5. 合并结果
    
    注意：heartbeat 心动时刻由短期记忆处理，不在此检索
    
    Args:
        character_id: 角色ID
        user_id: 用户ID
        intent: 意图分析结果（包含 keywords, memory_types, time_range）
        user_input: 用户原始输入（用于生成 embedding，语义更完整）
    
    Returns:
        检索结果列表，每项包含：
        {
            "source": "数据源名称",
            "similarity": 相似度分数,
            "content": "内容文本",
            "metadata": {"date": "...", "type": "..."},
        }
        
        按相似度降序排列
    
    示例：
        results = await execute_search("daji", "user1", {
            "intent": "fact_query",
            "keywords": ["生日"],
            "memory_types": ["diary"],
        }, user_input="我的生日是哪天")
    """
    t0 = time.monotonic()
    
    # 获取关键词（用于 FTS 精细定位）
    keywords = intent.get("keywords", [])
    
    # 使用用户原始输入生成 embedding（语义更完整）
    # 如果没有 user_input，则用 keywords 作为备用
    embedding_input = user_input if user_input else " ".join(keywords)
    
    if not embedding_input:
        logger.warning("[检索路由] 无有效输入，无法生成 embedding")
        return []
    
    logger.info(f"[检索路由] user_input='{user_input}', keywords={keywords}, "
               f"memory_types={intent.get('memory_types', [])}")
    
    # 生成查询向量（使用用户原始输入，语义更完整）
    query_embedding = await get_embedding(embedding_input)
    
    # 获取要检索的数据源（移除 heartbeat，由短期记忆处理）
    memory_types = intent.get("memory_types", ["diary"])
    memory_types = [t for t in memory_types if t != "heartbeat"]
    
    if not memory_types:
        memory_types = ["diary"]  # 默认检索 diary
    
    # ── Step 1: 层级向量检索（年→月→周→日）────────────────────────────────────
    vector_results = []
    matched_diary_ids = []
    
    # 并行执行层级向量检索
    vector_tasks = []
    vector_sources = []
    
    # 使用 Stone Repository
    diary_repo = get_diary_repo()
    weekly_repo = get_weekly_repo()
    annual_repo = get_annual_repo()
    
    if "annual" in memory_types:
        vector_tasks.append(annual_repo.search_vector(character_id, user_id, query_embedding, limit=3))
        vector_sources.append("annual")
    
    if "monthly" in memory_types:
        # monthly_repo 暂未实现 search_vector，跳过
        pass
    
    if "weekly" in memory_types:
        vector_tasks.append(weekly_repo.search_vector(character_id, user_id, query_embedding, limit=7))
        vector_sources.append("weekly")
    
    if "diary" in memory_types:
        vector_tasks.append(diary_repo.search_vector(character_id, user_id, query_embedding, limit=10))
        vector_sources.append("diary")
    
    if vector_tasks:
        vector_results_list = await asyncio.gather(*vector_tasks, return_exceptions=True)
        
        for i, results in enumerate(vector_results_list):
            if isinstance(results, Exception):
                logger.warning(f"[检索路由] {vector_sources[i]} 向量检索失败: {results}")
                continue
            
            for item in results:
                vector_results.append({
                    "source": vector_sources[i],
                    "similarity": item.get("similarity", 0),
                    "content": _extract_content(item, vector_sources[i]),
                    "metadata": _extract_metadata(item, vector_sources[i]),
                    "raw": item,
                })
                
                # 收集 diary ID（用于展开关联事件）
                if vector_sources[i] == "diary" and item.get("id"):
                    matched_diary_ids.append(item.get("id"))
                
                # 从层级索引中提取 diary_ids
                if vector_sources[i] in ["annual", "monthly", "weekly"]:
                    diary_ids = item.get("diary_ids", [])
                    matched_diary_ids.extend(diary_ids)
        
        logger.info(f"[检索路由] 层级向量检索: {len(vector_results)} 条, "
                   f"matched_diary_ids={len(matched_diary_ids)} 个")
    
    # ── Step 2: 向量匹配后 FTS 精细定位 key_events ───────────────────────────────
    # 使用意图分析的关键词在 key_events 中精确搜索
    fts_results = []
    
    if keywords:
        try:
            # 使用 Stone Repository FTS 搜索
            key_event_repo = get_key_event_repo()
            fts_raw = await key_event_repo.search_fts(
                character_id, user_id, " ".join(keywords),
                limit=10,
            )
            
            for item in fts_raw:
                raw_score = item.get("rank", 0) or 0
                # FTS rank 归一化
                normalized_score = min(1.0, raw_score * 3.0) if raw_score > 0 else 0.3
                
                fts_results.append({
                    "source": "key_events_fts",
                    "similarity": normalized_score,
                    "content": item.get("content", ""),
                    "metadata": {
                        "source": "key_events_fts",
                        "type": item.get("event_type", ""),
                        "date": str(item.get("event_date") or item.get("created_at", "")),
                        "importance": item.get("importance", 0.5),
                    },
                    "raw": item,
                })
            
            logger.info(f"[检索路由] key_events FTS 精细定位: {len(fts_results)} 条")
        except Exception as e:
            logger.warning(f"[检索路由] key_events FTS 失败: {e}")
    
    # ── Step 3: 展开 diary.key_event_ids 获取关联事件 ───────────────────────────
    expanded_events = []
    
    # 从 diary 向量结果中提取 key_event_ids
    key_event_ids = []
    for item in vector_results:
        if item["source"] == "diary":
            raw = item.get("raw", {})
            ids = raw.get("key_event_ids", [])
            key_event_ids.extend(ids)
    
    if key_event_ids:
        try:
            # 使用 Stone Repository
            key_event_repo = get_key_event_repo()
            expanded_events_raw = await key_event_repo.get_by_ids(key_event_ids)
            
            for item in expanded_events_raw:
                expanded_events.append({
                    "source": "key_events",
                    "similarity": 0.5,  # 展开的事件默认相似度
                    "content": item.get("content", ""),
                    "metadata": {
                        "source": "key_events",
                        "type": item.get("event_type", ""),
                        "date": str(item.get("event_date") or item.get("created_at", "")),
                        "importance": item.get("importance", 0.5),
                    },
                    "raw": item,
                })
            
            logger.info(f"[检索路由] 展开关联事件: {len(expanded_events)} 条")
        except Exception as e:
            logger.warning(f"[检索路由] 展开事件失败: {e}")
    
    # ── Step 4: 合并结果 ─────────────────────────────────────────────────────────
    all_results = []
    
    # 向量结果（核心）
    all_results.extend(vector_results)
    
    # FTS 精细定位结果
    all_results.extend(fts_results)
    
    # 展开的事件
    all_results.extend(expanded_events)
    
    # 按相似度排序
    all_results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    
    # 过滤低相似度结果
    min_similarity = 0.1  # 降低阈值以容纳更多结果
    filtered_results = [
        r for r in all_results 
        if r.get("similarity", 0) >= min_similarity
    ]
    
    elapsed = (time.monotonic() - t0) * 1000
    logger.info(f"[检索路由] 完成: {len(filtered_results)} 条结果, "
               f"耗时 {elapsed:.0f}ms")
    
    return filtered_results


# ── 辅助函数 ──────────────────────────────────────────────────────────────────────

def _extract_content(item: dict, source: str) -> str:
    """
    从检索结果中提取内容文本
    
    Args:
        item: 检索结果项
        source: 数据源名称
    
    Returns:
        内容文本
    """
    content_fields = {
        "diary": "summary",
        "annual": "summary",
        "monthly": "summary",
        "weekly": "summary",
        "key_events": "content",
        "key_events_fts": "content",
    }
    
    field = content_fields.get(source, "content")
    return item.get(field, "") or item.get("summary", "") or item.get("content", "")


def _extract_metadata(item: dict, source: str) -> dict:
    """
    从检索结果中提取元数据
    
    Args:
        item: 检索结果项
        source: 数据源名称
    
    Returns:
        元数据字典 {"date": "...", "type": "..."}
    """
    metadata = {"source": source}
    
    if source == "diary":
        metadata["date"] = str(item.get("diary_date", ""))
        metadata["key_event_ids"] = item.get("key_event_ids", [])
    
    elif source == "annual":
        metadata["date"] = f"{item.get('year', '')}年"
        metadata["diary_ids"] = item.get("diary_ids", [])
    
    elif source == "monthly":
        metadata["date"] = f"{item.get('year', '')}年{item.get('month', '')}月"
        metadata["diary_ids"] = item.get("diary_ids", [])
    
    elif source == "weekly":
        metadata["date"] = f"{item.get('week_start', '')}~{item.get('week_end', '')}"
        metadata["diary_ids"] = item.get("diary_ids", [])
    
    elif source in ["key_events", "key_events_fts"]:
        metadata["type"] = item.get("event_type", "")
        metadata["date"] = str(item.get("event_date") or item.get("created_at", ""))
        metadata["importance"] = item.get("importance", 0.5)
    
    return metadata


def _extract_year_from_intent(intent: dict) -> int | None:
    """
    从意图中提取年份
    
    Args:
        intent: 意图分析结果
    
    Returns:
        年份（如果指定）
    """
    # 从 time_range 中解析
    time_range = intent.get("time_range", "")
    if time_range == "year":
        # 如果用户说"去年"，返回去年的年份
        return datetime.now().year - 1
    
    # 从 keywords 或 entities 中解析年份
    keywords = intent.get("keywords", [])
    entities = intent.get("entities", [])
    
    for word in keywords + entities:
        if isinstance(word, str) and word.isdigit() and len(word) == 4:
            return int(word)
    
    return None


# ── 模块导出 ──────────────────────────────────────────────────────────────────────

__all__ = [
    "execute_search",
]