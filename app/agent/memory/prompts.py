"""记忆系统 LLM 提取提示词模板

说明：
- KEY_EVENT_EXTRACTION_PROMPT: 关键事件提取提示词
- HEARTBEAT_EXTRACTION_PROMPT: 心动事件提取提示词
- DIARY_GENERATION_PROMPT: 日记生成提示词（后续实现）

注意：提示词中的 JSON 示例需要将 { 和 } 转义为 {{ 和 }}，
因为 Python 的 .format() 方法会把 {} 当作占位符。
"""

from app.agent.memory.models import EVENT_TYPE_DESCRIPTIONS


# 关键事件类型说明文本
KEY_EVENT_TYPE_TEXT = """
事件类型说明：
- preference: 用户偏好（喜欢什么、讨厌什么、习惯）
- fact: 用户事实（生日、年龄、职业、家庭成员）
- schedule: 日程事件（明天要做什么、重要日期）
- experience: 经历事件（今天遇到了什么重要事情）
- user_reveal: 用户倾诉（分享秘密、展示脆弱面、深度倾诉）
- initiative: 主动记忆（角色认为重要的事情、关系里程碑）
"""


KEY_EVENT_EXTRACTION_PROMPT = """你是一个记忆提取助手。从对话中提取用户的关键信息。

分析以下对话，提取值得记录的关键事件。返回 JSON 数组，每项包含以下字段：

{{
    "event_type": "preference/fact/schedule/experience/user_reveal/initiative",
    "event_date": "YYYY-MM-DD 或 null（重要日期如生日、纪念日）",
    "content": "事件描述（简洁准确，20-50字）",
    "importance": 0.3-0.9（重要性评分）
}}

""" + KEY_EVENT_TYPE_TEXT + """

提取原则：
1. 只提取有价值的信息，忽略无关对话
2. preference/fact 类型重要性较高（0.6-0.9）
3. schedule 类型需要填写 event_date
4. 如果没有值得记录的信息，返回空数组 []

对话内容：
{conversation}

请返回 JSON 数组："""



DIARY_GENERATION_PROMPT = """你是一个日记撰写助手。根据今天的对话和事件，为角色写一篇日记。

日记要求：
1. 使用第一人称（角色的视角）
2. 记录今天的重要对话内容
3. 表达角色的情感和内心想法
4. 结合今天自主做的事情（日常事务），让日记更有生活感
5. 风格自然、类似人类的日记
6. 长度控制在 200-400 字

今日对话摘要：
{conversation_summary}

今日关键事件：
{key_events}

今日心动时刻：
{heartbeat_events}

今日自主做的事（日常事务）：
{daily_life_events}

请撰写日记（直接输出日记内容，不需要 JSON）："""


WEEKLY_INDEX_PROMPT = """你是一个周总结助手。根据本周的日记，生成周索引摘要。

周索引要求：
1. 总结本周的主要活动和事件
2. 提炼本周的关键主题（如：工作、情感、娱乐等）
3. 列出本周的高光时刻（最多3个）
4. 长度控制在 150-300 字

本周日记：
{diary_summaries}

请生成周索引摘要（直接输出摘要内容，不需要 JSON）："""


MONTHLY_INDEX_PROMPT = """你是一个月总结助手。根据本月的周索引，生成月索引摘要。

月索引要求：
1. 总结本月的主要活动和趋势
2. 提炼本月的核心主题和变化
3. 标注重要的里程碑事件
4. 长度控制在 200-400 字

本月周索引：
{weekly_summaries}

请生成月索引摘要（直接输出摘要内容，不需要 JSON）："""


ANNUAL_INDEX_PROMPT = """你是一个年度总结助手。根据本年的月索引，生成年索引摘要。

年索引要求：
1. 总结全年的主要经历和成长
2. 提炼年度关键词（3-5个）
3. 回顾重要的关系进展和里程碑
4. 长度控制在 300-500 字

本年月索引：
{monthly_summaries}

请生成年索引摘要（直接输出摘要内容，不需要 JSON）："""


def format_key_event_extraction_prompt(conversation: str) -> str:
    """格式化关键事件提取提示词"""
    return KEY_EVENT_EXTRACTION_PROMPT.format(conversation=conversation)



def format_diary_prompt(
    conversation_summary: str,
    key_events: str,
    heartbeat_events: str,
    daily_life_events: str = "无",
) -> str:
    """格式化日记生成的提示词"""
    return DIARY_GENERATION_PROMPT.format(
        conversation_summary=conversation_summary,
        key_events=key_events,
        heartbeat_events=heartbeat_events,
        daily_life_events=daily_life_events,
    )


def format_weekly_index_prompt(diary_summaries: str) -> str:
    """格式化周索引生成提示词"""
    return WEEKLY_INDEX_PROMPT.format(diary_summaries=diary_summaries)


def format_monthly_index_prompt(weekly_summaries: str) -> str:
    """格式化月索引生成提示词"""
    return MONTHLY_INDEX_PROMPT.format(weekly_summaries=weekly_summaries)


def format_annual_index_prompt(monthly_summaries: str) -> str:
    """格式化年索引生成提示词"""
    return ANNUAL_INDEX_PROMPT.format(monthly_summaries=monthly_summaries)
