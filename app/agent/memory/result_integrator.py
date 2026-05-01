"""结果整合模块（长期记忆 Deep Path - Step 4）

功能：
- 接收裁剪后的记忆片段
- LLM 整合为连贯的记忆上下文
- 输出结构化的整合文本

整合要求：
- 按时间顺序组织记忆
- 保留重要细节，去除冗余信息
- 明确标注信息来源
- 输出控制在 1000-2000 字
"""

from __future__ import annotations

import logging

from app.agent.llm.client import LLMClient

logger = logging.getLogger(__name__)


# ── 整合 Prompt ──────────────────────────────────────────────────────────────

RESULT_INTEGRATION_PROMPT = """你是一个记忆整合助手。

根据检索到的记忆片段，整合成一段连贯的记忆上下文，帮助回答用户的问题。

整合要求：
1. 按时间顺序组织记忆（最早的在前）
2. 保留重要细节，去除冗余和无关信息
3. 明确标注信息来源，格式为 [来源类型]
   - [关键事件]: 关键事件记录
   - [聊天]: 聊天记录
   - [心动时刻]: 心动事件
   - [日记]: 日记记录
   - [年记]: 年度索引
   - [月记]: 月度索引
4. 突出与用户问题相关的内容
5. 整合后的文本控制在 1000-2000 字

来源类型说明：
- preference: 用户偏好（喜欢/讨厌）
- fact: 用户事实（生日/职业等）
- schedule: 日程安排
- initiative: 关系里程碑
- emotion_peak: 情绪峰值时刻
- relationship: 关系进展
- user_reveal: 用户倾诉
- special_moment: 特殊时刻

用户原始问题：
{user_input}

检索到的记忆片段：
{retrieved_memories}

请整合记忆上下文（直接输出文本，不需要 JSON，不需要标题）："""


# ── 核心函数 ──────────────────────────────────────────────────────────────────────

async def integrate_results(
    llm_client: LLMClient,
    user_input: str,
    trimmed_memories: list[dict],
) -> str:
    """
    LLM 整合裁剪后的记忆
    
    Args:
        llm_client: LLM 客户端
        user_input: 用户原始输入
        trimmed_memories: 裁剪后的记忆片段（1000-2000字）
    
    Returns:
        整合后的记忆上下文文本
    
    示例：
        context = await integrate_results(
            llm_client,
            "去年我们去哪玩了?",
            [
                {"source": "diary", "content": "...", "metadata": {"date": "2025-07-15"}},
                {"source": "key_events", "content": "...", "metadata": {"type": "experience"}},
            ]
        )
    """
    if not trimmed_memories:
        return ""
    
    # 格式化记忆片段
    memories_text = _format_memories_for_integration(trimmed_memories)
    
    # 构建 Prompt
    prompt = RESULT_INTEGRATION_PROMPT.format(
        user_input=user_input,
        retrieved_memories=memories_text,
    )
    
    messages = [{"role": "user", "content": prompt}]
    
    try:
        # 调用 LLM（文本模式）
        result = await llm_client.chat(
            messages,
            temperature=0.5,  # 中等温度，保持连贯性
        )
        
        # 清理结果
        context = _clean_integration_result(result)
        
        logger.info(f"[结果整合] 完成: {len(context)} 字")
        
        return context
        
    except Exception as e:
        logger.error(f"[结果整合] LLM 调用失败: {e}")
        # 失败时返回原始记忆片段（格式化后）
        return memories_text


def _format_memories_for_integration(memories: list[dict]) -> str:
    """
    格式化记忆片段用于整合
    
    Args:
        memories: 记忆片段列表
    
    Returns:
        格式化后的文本
    """
    lines = []
    
    # 按时间排序（如果有日期信息）
    sorted_memories = _sort_memories_by_date(memories)
    
    for item in sorted_memories:
        source = item.get("source", "unknown")
        content = item.get("content", "")
        metadata = item.get("metadata", {})
        
        # 构建来源标签
        source_label = _get_source_label(source, metadata)
        
        # 构建日期标签
        date_label = metadata.get("date", "")
        if date_label:
            date_label = f"[{date_label}]"
        
        # 格式化单条记忆
        line = f"{date_label} {source_label}: {content}"
        lines.append(line)
    
    return "\n".join(lines)


def _sort_memories_by_date(memories: list[dict]) -> list[dict]:
    """
    按日期排序记忆片段
    
    Args:
        memories: 记忆片段列表
    
    Returns:
        按日期正序排列的记忆列表
    """
    def get_sort_key(item: dict) -> str:
        metadata = item.get("metadata", {})
        date = metadata.get("date", "")
        
        # 处理不同格式的日期
        if "~" in date:  # 周索引格式：2025-01-01~2025-01-07
            return date.split("~")[0]
        elif "年" in date and "月" in date:  # 月索引格式：2025年7月
            # 转换为标准格式
            year = date.replace("年", "-").replace("月", "").strip()
            return year
        elif "年" in date:  # 年索引格式：2025年
            return date.replace("年", "").strip()
        else:
            return date
    
    # 尝试排序
    try:
        return sorted(memories, key=get_sort_key)
    except Exception:
        # 排序失败时保持原序
        return memories


def _get_source_label(source: str, metadata: dict) -> str:
    """
    获取来源标签
    
    Args:
        source: 数据源名称
        metadata: 元数据
    
    Returns:
        来源标签文本
    """
    # 基础来源标签
    base_labels = {
        "key_events": "关键事件",
        "chat": "聊天",
        "heartbeat": "心动时刻",
        "diary": "日记",
        "annual": "年记",
        "monthly": "月记",
        "weekly": "周记",
    }
    
    base_label = base_labels.get(source, source)
    
    # 添加类型信息（如果有）
    type_info = ""
    
    if source == "key_events":
        event_type = metadata.get("type", "")
        if event_type:
            type_labels = {
                "preference": "偏好",
                "fact": "事实",
                "schedule": "日程",
                "initiative": "里程碑",
                "experience": "经历",
                "emotion_trigger": "情绪",
            }
            type_info = type_labels.get(event_type, event_type)
    
    elif source == "heartbeat":
        node = metadata.get("node", "")
        if node:
            node_labels = {
                "emotion_peak": "情绪峰值",
                "relationship": "关系进展",
                "user_reveal": "倾诉",
                "special_moment": "特殊时刻",
            }
            type_info = node_labels.get(node, node)
    
    if type_info:
        return f"[{base_label}-{type_info}]"
    else:
        return f"[{base_label}]"


def _clean_integration_result(result: str) -> str:
    """
    清理整合结果
    
    Args:
        result: LLM 返回的原始文本
    
    Returns:
        清理后的文本
    """
    # 去除可能的前缀/后缀
    result = result.strip()
    
    # 去除可能的 Markdown 标题
    if result.startswith("#"):
        lines = result.split("\n")
        # 去除标题行
        result = "\n".join(lines[1:] if lines[0].startswith("#") else lines)
    
    # 去除可能的引号包裹
    if result.startswith('"') and result.endswith('"'):
        result = result[1:-1]
    
    return result.strip()