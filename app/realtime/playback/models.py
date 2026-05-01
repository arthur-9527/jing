"""
播报任务数据模型

定义播报任务的数据结构，用于 Redis 存储和传输。
"""

import json
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class PlaybackTask:
    """播报任务
    
    包含播报所需的所有信息：台词内容、Panel 配置、动作配置等。
    
    Attributes:
        id: 任务唯一标识（通常来自 OpenClaw 任务 ID）
        content: 播报内容（台词文本）
        panel_html: Panel 配置（可选），包含 html、width、height、x、y 等
        action: 动作配置（可选），包含 matched_motion、trigger_char 等
        created_at: 任务入队时间（秒级时间戳）
    """
    
    id: str
    content: str
    panel_html: Optional[Dict[str, Any]] = None
    action: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 Redis 存储）
        
        注意：复杂类型（dict）需要 JSON 序列化。
        """
        data = {
            "id": self.id,
            "content": self.content,
            "created_at": self.created_at,
        }
        
        # 序列化复杂类型
        if self.panel_html is not None:
            data["panel_html"] = json.dumps(self.panel_html, ensure_ascii=False)
        if self.action is not None:
            data["action"] = json.dumps(self.action, ensure_ascii=False)
        
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlaybackTask":
        """从字典创建实例（用于 Redis 反序列化）
        
        注意：复杂类型（dict）需要 JSON 反序列化。
        """
        # 反序列化复杂类型
        panel_html = None
        if "panel_html" in data and data["panel_html"]:
            if isinstance(data["panel_html"], str):
                try:
                    panel_html = json.loads(data["panel_html"])
                except json.JSONDecodeError:
                    panel_html = None
            elif isinstance(data["panel_html"], dict):
                panel_html = data["panel_html"]
        
        action = None
        if "action" in data and data["action"]:
            if isinstance(data["action"], str):
                try:
                    action = json.loads(data["action"])
                except json.JSONDecodeError:
                    action = None
            elif isinstance(data["action"], dict):
                action = data["action"]
        
        return cls(
            id=data.get("id", ""),
            content=data.get("content", ""),
            panel_html=panel_html,
            action=action,
            created_at=data.get("created_at", time.time()),
        )
    
    def to_summary(self) -> str:
        """生成任务摘要（用于日志）"""
        content_preview = self.content[:50] if len(self.content) > 50 else self.content
        has_panel = bool(self.panel_html)
        has_action = bool(self.action)
        return f"task={self.id[:8]}, content='{content_preview}...', panel={has_panel}, action={has_action}"