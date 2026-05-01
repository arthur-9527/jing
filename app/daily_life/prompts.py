"""日常事务系统 LLM Prompt 模板

功能：
1. 根据时间、人设、短期记忆生成符合生活逻辑的场景
2. 产出：场景描述、台词、内心独白、情绪变化
3. 支持分享决策：AI 可选择是否向用户分享当前事件
"""

from datetime import datetime
from typing import Optional


def format_candidate_users(users: list[dict]) -> str:
    """格式化候选用户列表为 Prompt 文本

    Args:
        users: [{"user_id": "u_0001", "trust": 72.5, "intimacy": 68.3, "respect": 80.1}, ...]

    Returns:
        格式化后的文本，空列表返回空字符串
    """
    if not users:
        return ""

    lines = []
    for u in users:
        uid = u.get("user_id", "?")
        trust = u.get("trust", 0)
        intimacy = u.get("intimacy", 0)
        respect = u.get("respect", 0)
        lines.append(
            f"- {uid}: 信任={trust:.0f}, 亲密={intimacy:.0f}, 尊重={respect:.0f}"
        )
    return "\n".join(lines)


def build_daily_life_prompt(
    character_name: str,
    personality_text: str,
    short_term_memory: str,
    current_time: datetime,
    recent_daily_events: str = "",
    emotion_state: str = "",
    candidate_users_text: str = "",
) -> str:
    """构建日常事务场景生成 Prompt

    Args:
        character_name: 角色名称
        personality_text: 人设文本（来自 personality.md）
        short_term_memory: 短期记忆上下文
        current_time: 当前时间（用于时间感知场景）
        recent_daily_events: 最近做过的事（避免重复）
        emotion_state: 当前情绪状态（PAD）
        candidate_users_text: 候选用户列表及好感度（通过 format_candidate_users() 生成）

    Returns:
        LLM Prompt 字符串
    """
    # 格式化时间信息
    time_str = current_time.strftime("%H:%M")
    hour = current_time.hour
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][current_time.weekday()]

    # 根据时间段生成场景提示
    time_period_hint = _get_time_period_hint(hour)

    # 构建用户列表段落
    users_section = ""
    share_json = ""
    share_rules = ""
    share_fields = ""

    if candidate_users_text:
        users_section = f"""
## 可分享的用户

以下是当前与你有一定好感度的用户。如果你此刻的心情特别适合分享（比如特别开心、
做了一件有趣的事、想给某人展示），可以决定是否主动给其中一个用户发送消息。

{candidate_users_text}
"""
        share_rules = """
7. 如果你此刻的心情特别适合与某个用户分享，在 share 字段中指定目标用户。
   不需要分享时 target_user_id 填 null。
   - share_text: 你想对用户说的话（1-2句，用角色语气）
   - share_image_prompt: 想发一张自拍/照片时填写，描述照片场景（如"在厨房展示刚做好的草莓蛋糕"），不需要则 null
   - share_video_prompt: 想发一段视频时填写，描述视频内容（如"开心地跳舞"），不需要则 null
   - 注意：share_image_prompt 和 share_video_prompt 只能二选一，不能同时填写"""
        share_json = """,
  "share": {
    "target_user_id": null,
    "share_text": null,
    "share_image_prompt": null,
    "share_video_prompt": null
  }"""
        share_fields = """
- share: 分享决策（可选）
  - target_user_id: 要分享的用户ID，不分享则为 null
  - share_text: 分享文本，不分享则为 null
  - share_image_prompt: 图片描述，不需要则为 null（不能与 video 同时填）
  - share_video_prompt: 视频描述，不需要则为 null（不能与 image 同时填）"""

    prompt = f"""你是{character_name}，现在正在自主度过一天中的某个时刻。

## 角色人设
{personality_text}

## 当前状态
- 时间：{weekday} {time_str}
- 时间段：{time_period_hint}
- 当前情绪：{emotion_state}
{users_section}
## 近期记忆
{short_term_memory}

## 最近做过的事（避免重复）
{recent_daily_events if recent_daily_events else "无"}

## 任务
请生成一个你此刻正在做的事情的场景。

要求：
1. 场景要符合当前时间段的生活逻辑（早上做早餐、下午逛街、傍晚散步等）
2. 场景要符合角色性格特点
3. 场景要有生活感、有细节（不要太泛泛）
4. 避免重复最近已经做过的事
5. 台词用角色的语气和说话风格
6. 内心独白要体现角色的真实想法{share_rules}

## 输出格式
请严格按照以下 JSON 格式输出（不要输出其他内容）：

```json
{{{{"scenario": "场景名称（如：做蛋糕、逛街、看书）", "scenario_detail": "场景详细描述（30-50字，描述具体在做什么）", "dialogue": "角色此刻说的话（1-2句，用角色语气）", "inner_monologue": "内心独白（50字以内，体现真实想法）", "emotion_delta": {{{{ "P": 0.0, "A": 0.0, "D": 0.0 }}}}, "intensity": 0.3{share_json}}}}}
```

字段说明：
- scenario: 简短场景名（2-4字）
- scenario_detail: 详细描述，要有具体细节
- dialogue: 角色自言自语或说的话，体现性格
- inner_monologue: 内心想法
- emotion_delta: 情绪变化（P=愉悦度，A=激活度，D=支配度），范围 -0.3 到 0.3
- intensity: 情绪强度，范围 0.2 到 0.5{share_fields}

现在请生成场景，只输出 JSON："""

    return prompt


def _get_time_period_hint(hour: int) -> str:
    """根据小时数生成时间段提示"""
    if 6 <= hour < 9:
        return "清晨（适合：早起、做早餐、晨练、浇花、遛狗）"
    elif 9 <= hour < 12:
        return "上午（适合：逛街、购物、看书、学习、做家务）"
    elif 12 <= hour < 14:
        return "中午（适合：做午餐、午休、吃点心、喝咖啡）"
    elif 14 <= hour < 17:
        return "下午（适合：学跳舞、做手工、下午茶、散步、看电影）"
    elif 17 <= hour < 19:
        return "傍晚（适合：做晚饭、散步、看夕阳、浇花）"
    elif 19 <= hour < 22:
        return "晚间（适合：看电影、做蛋糕、写日记、听音乐、放松）"
    else:
        return "深夜（不适合活动，跳过）"


def format_recent_daily_events(events: list) -> str:
    """格式化最近日常事件（用于 Prompt）"""
    if not events:
        return "无"

    lines = []
    for event in events[-5:]:
        time_str = event.get("event_time", "")
        if hasattr(time_str, 'strftime'):
            time_str = time_str.strftime("%m-%d %H:%M")
        scenario = event.get("scenario", "")
        lines.append(f"- {time_str}: {scenario}")

    return "\n".join(lines)
