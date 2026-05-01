"""
帧队列模块

将动作帧队列从前端迁移到后端，实现：
- 环形缓冲区管理帧数据
- 直接使用数据库中的完整 30fps 帧数据
- 口型数据合并到帧中统一推送（带延迟补偿）
- 单帧推送 + 时间戳对齐
- 低水位自动预加载 idle 动作
- 打断时平滑过渡
- 动作切换时自动生成过渡帧

使用方式:
    from app.realtime.frame_queue import FrameQueueManager, VPDFrame, BoneFrame, MorphFrame

    # 创建管理器（默认从 config 读取参数）
    fq = FrameQueueManager(ws_manager)

    # 启动推帧循环
    await fq.start()

    # 加载动作（直接使用完整帧数据）
    await fq.load_motion(motion_id, frames)

    # LipSync 更新口型
    await fq.set_lip_morphs([MorphFrame("あ", 0.8)])

    # 打断
    await fq.interrupt()

    # 停止
    await fq.stop()
"""

from .types import VPDFrame, BoneFrame, MorphFrame, FrameBatch, SingleFrame, FrameQueueMetrics
from .ring_buffer import RingBuffer
from .audio_buffer import AudioBuffer
from .interpolator import interpolate_keyframes, interpolate_transition
from .frame_queue import FrameQueueManager
from .idle_scheduler import IdleScheduler, keyframe_to_vpd
from .lip_frame_pool import LipFramePool

__all__ = [
    "FrameQueueManager",
    "IdleScheduler",
    "LipFramePool",
    "AudioBuffer",
    "VPDFrame",
    "BoneFrame",
    "MorphFrame",
    "FrameBatch",
    "SingleFrame",
    "FrameQueueMetrics",
    "RingBuffer",
    "interpolate_keyframes",
    "interpolate_transition",
    "keyframe_to_vpd",
]
