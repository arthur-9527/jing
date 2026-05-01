"""记忆系统事件类型定义

说明：
- EventType: 关键事件类型枚举
- HeartbeatNode: 心动事件节点类型枚举
- HeartbeatSubtype: 心动事件子类型枚举
"""

from enum import Enum


class EventType(str, Enum):
    """关键事件类型"""
    
    # 用户偏好类
    PREFERENCE = "preference"      # 用户喜欢/讨厌什么
    
    # 用户事实类
    FACT = "fact"                  # 用户基本信息（生日、年龄、职业等）
    
    # 日程事件类
    SCHEDULE = "schedule"          # 重要日期和计划
    
    # 经历事件类
    EXPERIENCE = "experience"      # 用户经历的事情
    
    # 用户倾诉类
    USER_REVEAL = "user_reveal"      # 用户分享秘密、展示脆弱面、深度倾诉
    
    # 主动记忆类
    INITIATIVE = "initiative"      # 角色主动记录的事


class HeartbeatNode(str, Enum):
    """心动事件节点类型"""
    
    # 情绪峰值类
    EMOTION_PEAK = "emotion_peak"    # 情绪达到极值
    
    # 关系进展类
    RELATIONSHIP = "relationship"    # 关系状态变化
    
    # 用户倾诉类
    USER_REVEAL = "user_reveal"      # 用户分享深层信息
    
    # 特殊时刻类
    SPECIAL_MOMENT = "special_moment"  # 特殊的互动时刻


class HeartbeatSubtype(str, Enum):
    """心动事件子类型"""
    
    # 情绪峰值子类型
    JOY_PEAK = "joy_peak"          # 开心到极点
    SAD_PEAK = "sad_peak"          # 非常难过
    ANGRY_PEAK = "angry_peak"      # 情绪爆发
    TOUCHED_PEAK = "touched_peak"  # 感动流泪
    ANXIOUS_PEAK = "anxious_peak"  # 焦虑紧张
    
    # 关系进展子类型
    FIRST_MEETING = "first_meeting"      # 初遇
    TRUST_BUILD = "trust_build"          # 信任建立
    INTIMACY_RISE = "intimacy_rise"      # 亲密感提升
    CONFLICT_RESOLVE = "conflict_resolve"  # 冲突化解
    RECONCILIATION = "reconciliation"    # 和好
    
    # 用户倾诉子类型
    SECRET_REVEAL = "secret_reveal"      # 用户分享秘密
    VULNERABILITY_SHOW = "vulnerability_show"  # 用户展示脆弱面
    DEEP_SHARE = "deep_share"            # 用户深度倾诉
    PAST_SHARE = "past_share"            # 用户分享过去
    
    # 特殊时刻子类型
    GIFT_RECEIVED = "gift_received"      # 收到礼物
    SURPRISE_EVENT = "surprise_event"    # 意外惊喜
    ACHIEVEMENT_SHARE = "achievement_share"  # 用户分享成就
    COMFORT_GIVEN = "comfort_given"      # 给予用户安慰


# 事件类型中文说明（用于提示词）
EVENT_TYPE_DESCRIPTIONS = {
    EventType.PREFERENCE: "用户偏好（喜欢什么、讨厌什么、习惯）",
    EventType.FACT: "用户事实（生日、年龄、职业、家庭成员）",
    EventType.SCHEDULE: "日程事件（明天要做什么、重要日期）",
    EventType.EXPERIENCE: "经历事件（今天遇到了什么重要事情）",
    EventType.USER_REVEAL: "用户倾诉（分享秘密、展示脆弱面、深度倾诉）",
    EventType.INITIATIVE: "主动记忆（角色认为重要的事情、关系里程碑）",
}

HEARTBEAT_NODE_DESCRIPTIONS = {
    HeartbeatNode.EMOTION_PEAK: "情绪峰值（开心/难过/感动等达到极点）",
    HeartbeatNode.RELATIONSHIP: "关系进展（初遇、信任建立、亲密感提升等）",
    HeartbeatNode.USER_REVEAL: "用户倾诉（分享秘密、展示脆弱面、深度倾诉）",
    HeartbeatNode.SPECIAL_MOMENT: "特殊时刻（收到礼物、意外惊喜、给予安慰）",
}