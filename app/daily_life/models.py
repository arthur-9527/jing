"""日常事务事件数据模型

定义 DailyLifeEvent 数据结构，用于数据库存储和传输。
"""

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass
class DailyLifeEvent:
    """日常事务事件
    
    角色自主活动的记录，作为日记素材。
    
    Attributes:
        id: 事件唯一标识（数据库自增）
        character_id: 角色ID
        user_id: 用户ID
        event_time: 事件发生时间
        scenario: 场景名称（如"逛街"、"做蛋糕"）
        scenario_detail: 场景详细描述
        dialogue: 角色说的话（1-2句）
        inner_monologue: 内心独白
        emotion_delta: 情绪变化 {"P": 0.0, "A": 0.0, "D": 0.0}
        emotion_state: 当时的 PAD 快照
        intensity: 事件情绪强度 (0-1)
        heartbeat_event_id: 关联的心动事件ID
        created_at: 创建时间
    """
    
    id: Optional[int] = None
    character_id: str = ""
    user_id: str = "default_user"
    event_time: datetime = field(default_factory=datetime.now)
    scenario: str = ""
    scenario_detail: Optional[str] = None
    dialogue: Optional[str] = None
    inner_monologue: Optional[str] = None
    emotion_delta: Dict[str, float] = field(default_factory=lambda: {"P": 0.0, "A": 0.0, "D": 0.0})
    emotion_state: Optional[Dict[str, float]] = None
    intensity: float = 0.3
    heartbeat_event_id: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
    # 分享决策
    target_user_id: Optional[str] = None       # 分享目标用户，null 表示不分享
    share_text: Optional[str] = None           # 分享文本
    share_image_prompt: Optional[str] = None   # 分享图片生成提示词
    share_video_prompt: Optional[str] = None   # 分享视频生成提示词
    share_executed: bool = False               # 是否已执行分享
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于数据库插入）"""
        return {
            "id": self.id,
            "character_id": self.character_id,
            "user_id": self.user_id,
            "event_time": self.event_time.isoformat() if isinstance(self.event_time, datetime) else self.event_time,
            "scenario": self.scenario,
            "scenario_detail": self.scenario_detail or "",
            "dialogue": self.dialogue or "",
            "inner_monologue": self.inner_monologue or "",
            "emotion_delta": json.dumps(self.emotion_delta, ensure_ascii=False),
            "emotion_state": json.dumps(self.emotion_state, ensure_ascii=False) if self.emotion_state else None,
            "intensity": self.intensity,
            "heartbeat_event_id": self.heartbeat_event_id,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            # 分享决策
            "target_user_id": self.target_user_id,
            "share_text": self.share_text or "",
            "share_image_prompt": self.share_image_prompt or "",
            "share_video_prompt": self.share_video_prompt or "",
            "share_executed": self.share_executed,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DailyLifeEvent":
        """从字典创建实例（用于数据库读取）"""
        # 解析时间字段
        def parse_datetime(value):
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except:
                    return None
            return None
        
        # 解析 JSON 字段
        def parse_json_field(value):
            if value is None:
                return None
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except:
                    return None
            return None
        
        return cls(
            id=data.get("id"),
            character_id=data.get("character_id", ""),
            user_id=data.get("user_id", "default_user"),
            event_time=parse_datetime(data.get("event_time")) or datetime.now(),
            scenario=data.get("scenario", ""),
            scenario_detail=data.get("scenario_detail"),
            dialogue=data.get("dialogue"),
            inner_monologue=data.get("inner_monologue"),
            emotion_delta=parse_json_field(data.get("emotion_delta")) or {"P": 0.0, "A": 0.0, "D": 0.0},
            emotion_state=parse_json_field(data.get("emotion_state")),
            intensity=data.get("intensity", 0.3),
            heartbeat_event_id=data.get("heartbeat_event_id"),
            created_at=parse_datetime(data.get("created_at")) or datetime.now(),
            # 分享决策
            target_user_id=data.get("target_user_id"),
            share_text=data.get("share_text"),
            share_image_prompt=data.get("share_image_prompt"),
            share_video_prompt=data.get("share_video_prompt"),
            share_executed=data.get("share_executed", False),
        )
    
    def to_summary(self) -> str:
        """生成事件摘要（用于日志和日记）"""
        time_str = self.event_time.strftime("%H:%M") if isinstance(self.event_time, datetime) else ""
        detail_preview = (self.scenario_detail[:30] + "...") if self.scenario_detail and len(self.scenario_detail) > 30 else (self.scenario_detail or "")
        return f"[{time_str}] {self.scenario}: {detail_preview}"
    
    def to_diary_format(self) -> str:
        """生成日记格式文本（用于日记素材）"""
        time_str = self.event_time.strftime("%H:%M") if isinstance(self.event_time, datetime) else ""
        lines = []
        lines.append(f"【{time_str}】{self.scenario}")
        if self.scenario_detail:
            lines.append(f"  {self.scenario_detail}")
        if self.dialogue:
            lines.append(f"  说了：「{self.dialogue}」")
        if self.inner_monologue:
            lines.append(f"  心想：{self.inner_monologue}")
        return "\n".join(lines)