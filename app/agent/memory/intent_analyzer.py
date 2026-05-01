"""意图分析模块（长期记忆 Deep Path - Step 1）

功能：
- 分析用户输入和对话上下文
- 返回检索意图 JSON（含 confidence、memory_types、keywords、synonyms）
- confidence < 0.6 视为意图不明确

意图类型：
- preference_query: 查询用户偏好（喜欢什么、讨厌什么）
- fact_query: 查询用户事实信息（生日、职业等）
- schedule_query: 查询日程安排
- emotion_query: 查询情感相关记忆
- relationship_query: 查询关系进展
- general: 一般性查询
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agent.llm.client import LLMClient

logger = logging.getLogger(__name__)


# ── 意图分析 Prompt ──────────────────────────────────────────────────────────────

INTENT_ANALYSIS_PROMPT = """你是一个记忆检索意图分析助手。

分析用户的输入和对话上下文，判断用户想要检索什么类型的记忆。

返回 JSON 格式（严格遵守）：
{{
  "intent": "查询意图类型",
  "entities": ["提到的实体"],
  "time_range": "时间范围",
  "memory_types": ["应检索的数据源"],
  "keywords": ["检索关键词"],
  "synonyms": ["同义词/相关词"],
  "search_depth": "检索深度",
  "confidence": 0.0-1.0 的置信度
}}

意图类型说明：
- preference_query: 查询用户偏好（喜欢什么、讨厌什么、习惯）
- fact_query: 查询用户事实信息（生日、年龄、职业、家庭成员）
- schedule_query: 查询日程安排（明天要做什么、重要日期）
- emotion_query: 查询情感相关记忆（开心、难过、感动的时刻）
- relationship_query: 查询关系进展（初遇、信任建立、亲密时刻）
- general: 一般性查询（不明确类型）

数据源说明（长期记忆）：
- diary: 日记（每日记录，用于向量语义检索）
- annual: 年度索引（年度总结）
- monthly: 月度索引（月度总结）
- weekly: 周度索引（周度总结）

注意：heartbeat 心动时刻由短期记忆处理（FTS 全文检索），不在长期记忆检索范围内

时间范围说明：
- recent: 最近（最近几天到一周）
- month: 最近一个月
- year: 最近一年或指定年份（如"去年"）
- all: 不限时间

检索深度说明：
- shallow: 浅层检索（快速返回，少量结果）
- medium: 中等检索（常规检索）
- deep: 深层检索（层级检索，更多结果）

关键词和同义词生成原则（非常重要）：
1. keywords: 用户直接提到的关键词（精确提取）
2. synonyms: 与关键词语义相关的同义词/近义词（扩展搜索范围）
   
   同义词扩展示例：
   - "生日" → synonyms: ["出生日期", "出生", "哪天出生", "几月几日"]
   - "爱好" → synonyms: ["兴趣", "喜欢做什么", "特长", "嗜好"]
   - "喜欢吃什么" → synonyms: ["爱吃", "美食", "食物偏好", "口味"]
   - "什么时候" → synonyms: ["哪天", "时间", "日期", "几点"]
   - "去过哪里" → synonyms: ["去哪", "地方", "游玩", "旅行", "景点"]
   - "聊过什么" → synonyms: ["说过", "讨论", "话题", "谈过"]

判断原则：
1. confidence >= 0.6 才认为是有效意图，否则返回 confidence < 0.6
2. keywords 必须从用户输入中精确提取，synonyms 进行语义扩展
3. 时间锚点关键词（"去年"、"夏天"、"很久以前"）→ time_range
4. 喜好相关词汇（"喜欢"、"讨厌"、"最爱"）→ preference_query
5. 个人信息词汇（"生日"、"年龄"、"职业"）→ fact_query
6. 日程相关词汇（"明天"、"下周"、"计划"）→ schedule_query
7. 情感词汇（"开心"、"难过"、"感动"）→ emotion_query
8. 关系词汇（"我们认识"、"第一次"、"刚开始"）→ relationship_query

用户输入：
{user_input}

对话上下文：
{conversation_context}

请返回 JSON（不要添加任何其他文本）："""


# ── 置信度阈值 ───────────────────────────────────────────────────────────────────

CONFIDENCE_THRESHOLD = 0.6  # 低于此阈值视为意图不明确


# ── 核心函数 ──────────────────────────────────────────────────────────────────────

async def analyze_intent(
    llm_client: LLMClient,
    user_input: str,
    conversation_context: list[dict],
) -> dict[str, Any]:
    """
    LLM 意图分析
    
    Args:
        llm_client: LLM 客户端
        user_input: 用户输入文本
        conversation_context: 对话上下文（Redis 中的历史消息）
    
    Returns:
        意图分析结果 JSON：
        {
            "intent": "preference_query | fact_query | ...",
            "entities": ["实体列表"],
            "time_range": "recent | month | year | all",
            "memory_types": ["diary", "annual", "monthly", "weekly"],
            "keywords": ["检索关键词"],
            "synonyms": ["同义词/相关词"],
            "search_depth": "shallow | medium | deep",
            "confidence": 0.0-1.0
        }
        
        如果分析失败，返回 {"confidence": 0.0, "error": "错误信息"}
    
    示例：
        result = await analyze_intent(llm_client, "去年我们去哪玩了?", [])
        # result["intent"] = "general"
        # result["time_range"] = "year"
        # result["keywords"] = ["去哪玩"]
        # result["synonyms"] = ["地方", "游玩", "旅行"]
        # result["confidence"] = 0.8
    """
    # 格式化对话上下文
    context_text = _format_conversation_context(conversation_context)
    
    # 构建 Prompt
    prompt = INTENT_ANALYSIS_PROMPT.format(
        user_input=user_input,
        conversation_context=context_text,
    )
    
    messages = [{"role": "user", "content": prompt}]
    
    try:
        # 调用 LLM（JSON 模式）
        result = await llm_client.chat_json(
            messages,
            temperature=0.3,  # 低温度，更稳定的输出
        )
        
        # 验证必要字段
        required_fields = ["intent", "memory_types", "keywords", "confidence"]
        for field in required_fields:
            if field not in result:
                logger.warning(f"[意图分析] 缺少必要字段: {field}")
                result[field] = _get_default_value(field)
        
        # 验证 synonyms 字段（可选但重要）
        if "synonyms" not in result:
            result["synonyms"] = []
        if not isinstance(result.get("synonyms"), list):
            result["synonyms"] = []
        
        # 验证 confidence 范围
        confidence = result.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)):
            confidence = 0.0
        confidence = max(0.0, min(1.0, float(confidence)))
        result["confidence"] = confidence
        
        # 验证 memory_types（长期记忆数据源，不含 heartbeat）
        valid_types = ["diary", "annual", "monthly", "weekly"]
        memory_types = result.get("memory_types", [])
        if not isinstance(memory_types, list):
            memory_types = []
        # 过滤掉 heartbeat（由短期记忆处理）
        memory_types = [t for t in memory_types if t in valid_types]
        if not memory_types:
            memory_types = ["diary"]  # 默认检索 diary
        result["memory_types"] = memory_types
        
        logger.info(f"[意图分析] 结果: intent={result.get('intent')}, "
                   f"confidence={confidence:.2f}, "
                   f"keywords={result.get('keywords', [])}, "
                   f"synonyms={result.get('synonyms', [])}, "
                   f"memory_types={memory_types}")
        
        return result
        
    except json.JSONDecodeError as e:
        logger.error(f"[意图分析] JSON 解析失败: {e}")
        return {"confidence": 0.0, "error": "JSON解析失败"}
    
    except Exception as e:
        logger.error(f"[意图分析] LLM 调用失败: {e}")
        return {"confidence": 0.0, "error": str(e)}


def _format_conversation_context(conversation_context: list[dict]) -> str:
    """
    格式化对话上下文
    
    Args:
        conversation_context: 对话历史消息列表
    
    Returns:
        格式化后的文本（最多保留最近 5 条）
    """
    if not conversation_context:
        return "无"
    
    # 只保留最近 5 条
    recent = conversation_context[-5:]
    
    lines = []
    for msg in recent:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content:
            lines.append(f"{role}: {content}")
    
    if not lines:
        return "无"
    
    return "\n".join(lines)


def _get_default_value(field: str) -> Any:
    """获取字段的默认值"""
    defaults = {
        "intent": "general",
        "entities": [],
        "time_range": "all",
        "memory_types": ["diary"],  # 长期记忆数据源（不含 heartbeat）
        "keywords": [],
        "synonyms": [],
        "search_depth": "medium",
        "confidence": 0.0,
    }
    return defaults.get(field, None)


def is_intent_valid(intent: dict) -> bool:
    """
    判断意图是否有效
    
    Args:
        intent: 意图分析结果
    
    Returns:
        True 如果 confidence >= 0.6
    """
    confidence = intent.get("confidence", 0.0)
    return confidence >= CONFIDENCE_THRESHOLD