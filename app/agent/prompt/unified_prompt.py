"""动态 User Prompt 构建（严格动静分离）

此模块只构建每轮变化的动态 User Prompt：
- current_time: 当前时间（日期、星期、时刻）
- dynamic_context: 当前 PAD 状态
- memory_context: 检索到的相关记忆
- conversation_history: 对话历史（会被拆分为历史对话和当前输入）

所有静态内容（角色定义、标签目录、动作规则、输出格式）
都在 system_prompt.py 的 build_static_system_prompt() 中构建。

架构本质：补全模式而非对话模式
- System Prompt = 角色剧本（完全静态，初始化时构建，之后永远不变）
- User Prompt = 当前场景描述（每轮变化）
- LLM 输出 = 按剧本补全当前场景下的角色台词
"""

from __future__ import annotations

from datetime import datetime


def format_current_time(dt: datetime | None = None) -> str:
    """
    格式化当前时间为易读的中文格式
    
    Args:
        dt: datetime 对象，默认使用当前时间
    
    Returns:
        格式化的时间字符串，如 "2026年09月04日 星期四 21:17 晚上"
    """
    if dt is None:
        dt = datetime.now()
    
    # 星期映射
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays[dt.weekday()]
    
    # 时段映射
    hour = dt.hour
    if 5 <= hour < 9:
        period = "早晨"
    elif 9 <= hour < 12:
        period = "上午"
    elif 12 <= hour < 14:
        period = "中午"
    elif 14 <= hour < 18:
        period = "下午"
    elif 18 <= hour < 22:
        period = "晚上"
    else:
        period = "深夜"
    
    return f"{dt.strftime('%Y年%m月%d日')} {weekday} {dt.strftime('%H:%M')} {period}"


def _split_conversation_history(conversation_history: str) -> tuple[str, str]:
    """
    将对话历史拆分为历史对话和当前输入
    
    Args:
        conversation_history: 格式化的对话历史字符串
            格式: "user: xxx\nassistant: xxx\nuser: yyy"
    
    Returns:
        (history_section, current_input)
        - history_section: 除最后一次 user 输入外的所有历史
        - current_input: 最后一次 user 输入的纯文本内容（不含 "user:" 前缀）
    
    示例:
        输入: "user: 你好\nassistant: 你好呀\nuser: 今天天气怎么样"
        返回: ("user: 你好\nassistant: 你好呀", "今天天气怎么样")
    """
    if not conversation_history or not conversation_history.strip():
        return "", ""
    
    # 按换行符分割
    lines = conversation_history.strip().split("\n")
    
    # 找到最后一个 user 开头的行（支持 "user:" 和 "user：" 两种格式）
    last_user_idx = -1
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if line.startswith("user:") or line.startswith("user："):
            last_user_idx = i
            break
    
    if last_user_idx == -1:
        # 没有找到 user 行，整个历史作为历史部分
        return conversation_history, ""
    
    # 提取当前输入（去掉 "user:" 或 "user：" 前缀）
    last_user_line = lines[last_user_idx].strip()
    # 支持 "user:" 和 "user："（中文冒号）
    if last_user_line.startswith("user:"):
        current_input = last_user_line[5:].strip()  # "user:" 长度为5
    elif last_user_line.startswith("user："):
        current_input = last_user_line[5:].strip()  # "user：" 长度为5（中文冒号也是1个字符，但显示为2个字节）
    else:
        current_input = last_user_line
    
    # 构建历史部分（去掉最后一个 user 行）
    history_lines = lines[:last_user_idx]
    history_section = "\n".join(history_lines) if history_lines else ""
    
    return history_section, current_input


def build_unified_prompt(
    memory_context: str,
    *,
    dynamic_context: str = "",
    conversation_history: str = "",
    current_time: str | None = None,
) -> str:
    """
    构建动态 User Prompt（每轮变化的内容）

    Args:
        memory_context: 检索到的相关记忆
        dynamic_context: 当前 PAD 状态
        conversation_history: 对话历史（会被拆分为历史对话和当前输入）
        current_time: 当前时间字符串，如 "2026年09月04日 星期四 21:17 晚上"
                      默认为 None 时自动获取当前时间

    Returns:
        动态 User Prompt 字符串

    注意:
        - 所有静态内容已在 system_prompt.py 中构建
        - conversation_history 被拆分为"对话历史"和"当前输入"两部分
        - 末尾添加格式提醒（LLM 通常对最后的内容更关注）
        - 此函数每轮调用，生成新的 user prompt
    """
    # 构建当前时间部分
    time_section = ""
    if current_time is None:
        current_time = format_current_time()
    if current_time:
        time_section = f"""## 当前时间
{current_time}

"""

    # 构建动态状态部分
    context_section = f"{dynamic_context}\n\n" if dynamic_context else ""

    # 构建记忆部分
    memory_section = ""
    if memory_context:
        memory_section = f"""## 记忆参考
{memory_context}

"""

    # 拆分对话历史：历史对话 + 当前输入
    history_section, current_input = _split_conversation_history(conversation_history)
    
    # 构建历史对话部分
    history_part = ""
    if history_section:
        history_part = f"""## 对话历史
{history_section}

"""
    else:
        history_part = """## 对话历史
（这是第一轮对话）

"""

    # 构建当前输入部分
    current_input_part = ""
    if current_input:
        current_input_part = f"""## 当前输入
{current_input}

"""

    # 格式提醒（放在最后，LLM 通常对末尾内容更关注）
    format_reminder = """
    ！！！特别重要：
     - 请严格按照输出格式规范输出：<t>标签</t>角色台词<m>标签</m>，不要添加多余符号！
     - 是否需要调用工具，只能根据当前输入的明确需求来判断，不能基于历史对话分析工具调用需求！如果当前输入没有明确的工具调用需求，请务必输出 `<t>{"tool_prompt": null}</t>` 来表示不需要工具调用。
    """

    prompt = f"""{time_section}{context_section}{memory_section}{history_part}{current_input_part}请根据当前输入，结合对话历史上下文，充分理解用户意图后，以角色视角进行回复。
{format_reminder}"""

    return prompt
