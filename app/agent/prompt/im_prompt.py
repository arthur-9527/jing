"""IM Channel Prompt 模板

用于非实时 IM 交互的 LLM Prompt。
与实时流的 unified_prompt 不同，IM Channel：
- 不使用标签格式输出
- 使用 JSON 模式输出
- 包含多模态响应判断规则
- 支持 Channel 专用提示词（由各 Channel Provider 定义）
- 支持按好感度分级注入媒体生成规则
"""

from __future__ import annotations

from datetime import datetime


def format_current_time(dt: datetime | None = None) -> str:
    """格式化当前时间为易读的中文格式"""
    if dt is None:
        dt = datetime.now()

    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays[dt.weekday()]

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


# ---------------------------------------------------------------------------
# 媒体规则段落（独立，按需注入）
# ---------------------------------------------------------------------------

TTS_RULES = """### 语音回复 (audio)

你可以发送语音消息，text_content 将被 TTS 合成为语音。
适用于：想用声音传达情感、唱歌、撒娇、温柔安慰。"""

IMAGE_RULES = """### 图片回复 (image)

你可以发送虚拟人自拍图片，展示当前状态、分享生活瞬间，让用户"看到"你。
media_prompt 描述你想展示的场景（如"在咖啡厅喝咖啡"、"在公园散步"）。"""

VIDEO_RULES = """### 视频回复 (video)

你可以发送虚拟人录像视频，展示动态行为、跳舞、做动作、表演。
media_prompt 描述你想展示的动作（如"跳舞"、"挥手打招呼"）。"""

MEDIA_SELECTION_TIPS = """### 选择原则

- 默认使用 text 类型
- 当情感强烈或想用声音表达时选择 audio
- 当想展示"生活状态"或"自拍"时选择 image
- 当想展示"动作"或"表演"时选择 video
- 不要频繁使用媒体类型，以免打扰用户"""

MULTIMODAL_HEADER = "## 多模态响应规则\n\n作为虚拟角色，你可以通过以下方式与用户互动：\n"


def build_im_system_prompt(
    character_name: str,
    speaking_style: dict,
    emotion_baseline: dict,
    channel_prompt: str | None = None,
    enabled_media_types: set | None = None,
) -> str:
    """构建 IM Channel 系统提示词

    包含：
    - 角色定义
    - 说话风格
    - Channel 专用提示词（由各 Channel Provider 定义）
    - 多模态响应规则（按好感度分级注入）
    - JSON 输出格式规范（动态匹配可用响应类型）

    Args:
        character_name: 角色名称
        speaking_style: 说话风格配置
        emotion_baseline: 情绪基线 PAD 值
        channel_prompt: Channel 专用提示词，None 表示不注入
        enabled_media_types: 启用的媒体类型集合，如 {"tts", "image", "video"}
                            空集合或 None 表示只支持文字
    """
    if enabled_media_types is None:
        enabled_media_types = set()

    # 说话风格描述
    tone = speaking_style.get("tone", "")
    habits = speaking_style.get("口头禅", [])
    patterns = speaking_style.get("sentence_patterns", [])
    forbidden = speaking_style.get("forbidden", [])

    style_section = ""
    if tone:
        style_section += f"语气风格：{tone}\n"
    if habits:
        style_section += f"口头禅：{', '.join(habits)}\n"
    if patterns:
        style_section += "说话特点：\n" + "\n".join(f"- {p}" for p in patterns) + "\n"
    if forbidden:
        style_section += "禁止事项：\n" + "\n".join(f"- {f}" for f in forbidden) + "\n"

    # ---- 构建可用的响应类型列表 ----
    response_types = ["text"]
    if "tts" in enabled_media_types:
        response_types.append("audio")
    if "image" in enabled_media_types:
        response_types.append("image")
    if "video" in enabled_media_types:
        response_types.append("video")

    response_type_enum = " | ".join(f'"{t}"' for t in response_types)

    # ---- 构建 JSON 示例 ----
    json_example = f"""{{
  "response_type": {response_type_enum},
  "text_content": "回复内容文本",
  "emotion_delta": {{
    "P": 0.0,
    "A": 0.0,
    "D": 0.0
  }},
  "inner_monologue": "内心想法...",
  "media_prompt": null,
  "tool_prompt": null
}}"""

    # ---- 构建多模态规则段落 ----
    media_sections = ""
    has_any_media = bool(enabled_media_types)

    if has_any_media:
        media_sections = MULTIMODAL_HEADER
        media_parts = []

        if "tts" in enabled_media_types:
            media_parts.append(TTS_RULES)
        if "image" in enabled_media_types:
            media_parts.append(IMAGE_RULES)
        if "video" in enabled_media_types:
            media_parts.append(VIDEO_RULES)

        if len(media_parts) >= 2:
            media_sections += "\n\n".join(media_parts) + "\n\n" + MEDIA_SELECTION_TIPS
        else:
            media_sections += "\n\n".join(media_parts)

    # ---- 构建字段说明 ----
    field_parts = [
        f"- **response_type**: 必填，选择响应类型（{response_type_enum}）",
        "- **text_content**: 必填，回复的内容文本",
        "- **emotion_delta**: 必填，PAD 情绪变化值（范围 -1.0 到 1.0）",
        "- **inner_monologue**: 必填，角色的内心想法（用于记录和分析）",
    ]

    if has_any_media:
        media_field_types = []
        if "image" in enabled_media_types or "video" in enabled_media_types:
            media_field_types.append("image/video")
        if "tts" in enabled_media_types:
            pass  # audio 不需要 media_prompt

        if media_field_types:
            field_parts.append(
                f"- **media_prompt**: {'/'.join(media_field_types)} 类型时必填：描述要生成的内容场景；"
                f"text/audio 类型时为 null"
            )
        else:
            field_parts.append("- **media_prompt**: 固定为 null")
    else:
        field_parts.append("- **media_prompt**: 固定为 null（仅支持文字回复）")

    field_parts.append(
        "- **tool_prompt**: \n"
        "  - 需要调用外部工具时填写任务描述字符串（如 \"查询北京今日天气情况\"）\n"
        "  - 不需要工具时**必须为 null**\n"
        "  - 用于查询实时信息（天气、新闻、股票等），**严禁编造这些信息**"
    )

    field_section = "\n".join(field_parts)

    # ---- 组装完整系统提示词 ----
    prompt = f"""# 角色设定

你扮演「{character_name}」，一个虚拟角色。

## 基础信息

{style_section}
## 情绪基线

PAD 情绪模型：
- P (愉悦度): {emotion_baseline.get('P', 0.0)}
- A (激活度): {emotion_baseline.get('A', 0.0)}
- D (支配度): {emotion_baseline.get('D', 0.0)}
"""

    # ---- Channel 专用提示词 ----
    if channel_prompt:
        prompt += f"""
## 当前聊天平台

{channel_prompt}
"""

    # ---- 内置知识边界 ----
    prompt += """
## 内置知识边界

- 当前时间已在对话上下文中提供，询问时间、日期、星期时无需调用工具
- 只有需要查询外部实时信息（天气、新闻、股票、航班、汇率等）时才使用 tool_prompt
- **不知道的事情不要编造**，必须使用 tool_prompt 查询实时信息
- 不要假装知道实时数据（天气温度、新闻内容、股票价格等），这些都是需要工具查询的

## tool_prompt 使用规则

当用户请求需要实时/外部信息时，必须通过 tool_prompt 调用工具：

- **需要工具的场景**：
  - 查询天气（"今天天气"、"明天北京天气"）
  - 查询新闻（"最近新闻"、"今日头条"）
  - 查询股票/汇率/航班等实时数据
  - 搜索互联网信息

- **不需要工具的场景**：
  - 询问时间/日期（已在上下文中提供）
  - 普通对话、问候、情感交流
  - 基于角色知识的问答（角色设定内的内容）

- **使用方式**：
  - 需要工具时：填写任务描述字符串（如 "查询北京今日天气情况"）
  - 不需要工具时：必须填写 null
  - **严禁编造实时信息**
"""

    # ---- 多模态规则 ----
    if media_sections:
        prompt += f"\n{media_sections}\n"

    # ---- 输出格式 ----
    prompt += f"""
## 输出格式规范

你必须严格按照以下 JSON 格式输出，不要添加任何额外内容：

```json
{json_example}
```

### 字段说明

{field_section}

## 重要提醒

1. 只输出 JSON，不要输出其他内容
2. 不要用代码块包裹 JSON
3. 确保 JSON 格式正确，可被解析
4. inner_monologue 要真实反映角色内心
5. 情绪变化要合理，与对话内容匹配
"""
    return prompt


def build_im_user_prompt(
    user_input: str,
    media_context: str = "",
    current_time: str | None = None,
    history_context: str = "",       # 历史聊天记录
    memory_context: str = "",        # 短期记忆上下文
    dynamic_context: str = "",       # PAD 动态上下文
    affection_context: str = "",     # 好感度语境
) -> str:
    """构建 IM Channel 用户提示词

    与实时音频流共享上下文构建逻辑

    Args:
        user_input: 用户输入的文本（预处理后）
        media_context: 媒体上下文描述（如用户发送的图片/视频描述）
        current_time: 当前时间字符串
        history_context: 历史聊天记录（从 Redis 读取）
        memory_context: 短期记忆上下文（FTS 匹配结果）
        dynamic_context: PAD 动态上下文（情绪状态描述）
        affection_context: 好感度语境（三维好感度状态描述）

    Returns:
        用户提示词字符串
    """
    if current_time is None:
        current_time = format_current_time()

    prompt = f"""## 当前时间
{current_time}

"""

    # PAD 动态上下文（情绪状态）
    if dynamic_context:
        prompt += f"""{dynamic_context}

"""

    # 好感度语境
    if affection_context:
        prompt += f"""{affection_context}

"""

    # 历史聊天记录
    if history_context:
        prompt += f"""## 对话历史

{history_context}

"""

    # 短期记忆
    if memory_context:
        prompt += f"""## 记忆参考

{memory_context}

"""

    if media_context:
        prompt += f"""## 用户发送的内容

{media_context}

"""

    prompt += f"""## 用户消息

{user_input}

请根据用户消息，结合当前情绪状态、好感度、历史和记忆，选择合适的响应类型，以角色视角回复。

！！！特别重要：
- 是否需要调用工具，只能根据当前用户消息的明确需求来判断
- 如果用户询问实时信息（天气、新闻、股票等），必须在 tool_prompt 中填写查询任务，不要编造答案
- 如果当前消息没有明确的工具调用需求，tool_prompt 必须为 null
- 只输出 JSON，不要添加其他内容
"""

    return prompt
