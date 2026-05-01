"""帧队列数据类型定义 - 与前端 VPDFrame 对齐"""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BoneFrame:
    """骨骼帧数据"""
    name: str
    translation: list[float]  # [x, y, z]
    quaternion: list[float]   # [x, y, z, w]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "translation": self.translation,
            "quaternion": self.quaternion,
        }


@dataclass
class MorphFrame:
    """口型/表情 morph 数据"""
    name: str
    weight: float  # 0.0 ~ 1.0

    def to_dict(self) -> dict:
        return {"name": self.name, "weight": self.weight}


@dataclass
class VPDFrame:
    """
    单帧数据，与前端 VPDFrame 接口完全对齐:
    {
      bones: [{ name, translation, quaternion }],
      morphs: [{ name, weight }],
      fi: frame_index (帧序号)
    }
    """
    bones: list[BoneFrame]
    morphs: list[MorphFrame] = field(default_factory=list)
    fi: int = 0  # 当前动作内的帧序号

    def to_dict(self) -> dict:
        d = {
            "fi": self.fi,
            "bones": [b.to_dict() for b in self.bones],
        }
        if self.morphs:
            d["morphs"] = [m.to_dict() for m in self.morphs]
        else:
            d["morphs"] = []
        return d


@dataclass
class SingleFrame:
    """
    WS 推送的单帧消息结构:
    {
      type: "frame",
      seq: 递增序号,
      ts: 该帧应被渲染的精确时间,
      motion_id: 当前动作 ID,
      frame: VPDFrame dict
    }
    """
    seq: int = 0
    ts: float = 0.0
    motion_id: str = ""
    frame: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": "frame",
            "seq": self.seq,
            "ts": self.ts or time.time(),
            "motion_id": self.motion_id,
            "frame": self.frame,
        }


@dataclass
class FrameBatch:
    """
    WS 推送的批次消息结构（保留兼容）:
    {
      type: "frames",
      seq: 递增序号,
      ts: 发送时间戳,
      motion_id: 当前动作 ID,
      fps: 帧率,
      frames: [VPDFrame...]
    }
    """
    seq: int = 0
    ts: float = 0.0
    motion_id: str = ""
    fps: int = 30
    frames: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "type": "frames",
            "seq": self.seq,
            "ts": self.ts or time.time(),
            "motion_id": self.motion_id,
            "fps": self.fps,
            "frames": self.frames,
        }


@dataclass
class FrameQueueMetrics:
    """运行时监控指标"""
    buffer_count: int = 0
    buffer_capacity: int = 0
    lip_delay_queue_len: int = 0
    frames_pushed: int = 0
    frames_dropped: int = 0
    idle_preloads: int = 0
    avg_push_interval_ms: float = 0.0
    last_motion_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "type": "metrics",
            "buffer_count": self.buffer_count,
            "buffer_capacity": self.buffer_capacity,
            "lip_delay_queue_len": self.lip_delay_queue_len,
            "frames_pushed": self.frames_pushed,
            "frames_dropped": self.frames_dropped,
            "idle_preloads": self.idle_preloads,
            "avg_push_interval_ms": round(self.avg_push_interval_ms, 2),
            "last_motion_id": self.last_motion_id,
        }
