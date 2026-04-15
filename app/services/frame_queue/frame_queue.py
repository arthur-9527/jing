"""
帧队列管理器 - 核心调度器

职责：
1. 管理环形缓冲区（帧的存储和读取）
2. 加载动作：直接从 DB 读取完整帧数据，写入缓冲区
3. 定时从缓冲区取帧，合并口型数据，通过 WS 单帧推送
4. ⭐ 新方案：音频缓冲区 + 实时口型分析，完全同步
5. 低水位自动预加载 idle 动作
6. 处理打断：截断队列，插入过渡帧
7. 动作切换：自动生成过渡帧，平滑切换

口型同步原理：
- TTS 音频推入 AudioBuffer（音频缓冲区）
- FrameQueue 每 33ms 从 AudioBuffer 取一帧音频
- 实时 FFT 分析该音频片段，生成口型帧
- 口型帧与音频播放使用同一音频源，完全同步
"""

import asyncio
import time
from collections import deque
from typing import Optional, TYPE_CHECKING
from loguru import logger

from .ring_buffer import RingBuffer
from .audio_buffer import AudioBuffer
from .lip_frame_pool import LipFramePool
from .types import VPDFrame, MorphFrame, BoneFrame, SingleFrame, FrameQueueMetrics
from .interpolator import interpolate_transition
from app.services.lipsync_service import LipSyncService
from app.config import settings

if TYPE_CHECKING:
    from app.services.agent_ws_manager import AgentWSManager
    from .idle_scheduler import IdleScheduler

# 从配置读取
LIPSYNC_DELAY_MS = settings.LIPSYNC_DELAY_MS
LIPSYNC_DELAY_FRAMES = max(1, int(LIPSYNC_DELAY_MS / 1000 * settings.FRAME_TARGET_FPS))
LOW_WATER_MARK = settings.IDLE_LOW_WATER_MARK
MOTION_TRANSITION_FRAMES = settings.MOTION_TRANSITION_FRAMES
MOTION_HEAD_OFFSET_FRAMES = settings.MOTION_HEAD_OFFSET_FRAMES


class FrameQueueManager:
    """帧队列管理器"""

    def __init__(
        self,
        ws_manager: "AgentWSManager",
        buffer_size: int = None,
        target_fps: int = None,
        batch_size: int = None,
    ):
        """
        Args:
            ws_manager: WebSocket 管理器，用于广播帧数据
            buffer_size: 环形缓冲区大小（帧数）
            target_fps: 目标帧率
            batch_size: 每次 WS 推送的帧数（默认 1 = 单帧推送）
        """
        self._ws_manager = ws_manager
        self._target_fps = target_fps or settings.FRAME_TARGET_FPS
        self._batch_size = batch_size or settings.FRAME_BATCH_SIZE
        self._batch_interval = self._batch_size / self._target_fps  # 推送间隔（秒）
        self._buffer = RingBuffer[VPDFrame](buffer_size or settings.FRAME_BUFFER_SIZE)

        # 状态
        self._seq = 0
        self._current_motion_id = ""
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None

        # ⭐ 音频缓冲区（新方案：TTS 音频推入此缓冲区）
        self._audio_buffer = AudioBuffer(
            sample_rate=settings.TTS_SAMPLE_RATE,
            frame_duration_ms=self._batch_interval * 1000,  # 33.33ms
        )
        
        # ⭐ 口型同步服务（实时 FFT 分析）
        self._lip_sync_service = LipSyncService(
            sensitivity=5.0,
            smoothing_factor=0.55,
            min_volume_threshold=0.02,
        )
        
        # 口型帧池（保留用于兼容，但新方案主要使用音频缓冲区）
        self._lip_frame_pool = LipFramePool(
            sample_rate=settings.TTS_SAMPLE_RATE,
            target_fps=self._target_fps,
        )

        # 兼容：旧接口保留，但不再使用
        self._lip_morphs: list[MorphFrame] = []
        self._lip_lock = asyncio.Lock()

        # idle 预加载
        self._idle_scheduler: Optional["IdleScheduler"] = None
        self._idle_loading = False
        self._idle_load_task: Optional[asyncio.Task] = None

        # 监控指标
        self._metrics = FrameQueueMetrics(
            buffer_capacity=self._buffer.size,
        )
        self._push_intervals: deque[float] = deque(maxlen=100)
        self._last_push_time: float = 0.0

        # panel 关闭回调（口型帧耗尽时调用）
        self._panel_close_callback: Optional[callable] = None
        self._has_pushed_lip_frames: bool = False  # 标记是否曾经推送过口型帧

        # ⭐ 缓冲区空回调（口型帧耗尽时触发播报队列检查）
        self._buffer_empty_callback: Optional[callable] = None

        # ⭐ 口型帧耗尽回调（触发 handle_speech_end）
        self._lip_frames_empty_callback: Optional[callable] = None

        logger.info(
            f"[FrameQueue] 初始化: buffer={self._buffer.size}, "
            f"fps={self._target_fps}, batch={self._batch_size}, "
            f"interval={self._batch_interval*1000:.1f}ms, "
            f"lip_delay={LIPSYNC_DELAY_MS}ms ({LIPSYNC_DELAY_FRAMES}帧)"
        )

    def set_idle_scheduler(self, scheduler: "IdleScheduler") -> None:
        """设置 idle 调度器引用（由外部注入，避免循环依赖）"""
        self._idle_scheduler = scheduler

    def set_panel_close_callback(self, callback: callable) -> None:
        """设置 panel 关闭回调（口型帧耗尽时调用）"""
        self._panel_close_callback = callback
        # 重置标记（新的 TTS 开始）
        self._has_pushed_lip_frames = False
        logger.debug("[FrameQueue] Panel 关闭回调已设置")

    def set_buffer_empty_callback(self, callback: callable) -> None:
        """设置缓冲区空回调（口型帧耗尽时触发播报队列检查）

        Args:
            callback: 回调函数，签名 async def callback()
        """
        self._buffer_empty_callback = callback
        logger.debug("[FrameQueue] 缓冲区空回调已设置")

    def set_lip_frames_empty_callback(self, callback: callable) -> None:
        """设置口型帧耗尽回调（触发 handle_speech_end）

        Args:
            callback: 回调函数，签名 async def callback()
        """
        self._lip_frames_empty_callback = callback
        logger.info("[FrameQueue] 口型帧耗尽回调已设置")

    # === 动作管理 ===

    async def load_motion(
        self,
        motion_id: str,
        frames: list[VPDFrame],
        append: bool = False,
    ) -> int:
        """
        加载动作到缓冲区。

        数据库中存储的已经是完整的 30fps 帧数据，直接写入缓冲区。

        Args:
            motion_id: 动作 ID
            frames: 完整帧数据列表（已从 DB 读取并转换）
            append: True=追加到现有帧后面, False=清空后写入

        Returns:
            写入的帧数
        """
        # 如果正在加载 idle，取消它
        if self._idle_load_task and not self._idle_load_task.done():
            self._idle_load_task.cancel()
            self._idle_loading = False

        if not frames:
            logger.warning(f"[FrameQueue] 动作 {motion_id} 无帧数据")
            return 0

        if not append:
            self._buffer.clear()

        self._current_motion_id = motion_id
        self._buffer.write_batch(frames)

        logger.info(
            f"[FrameQueue] 加载动作 {motion_id}: {len(frames)} 帧, "
            f"缓冲区: {self._buffer.count}/{self._buffer.size}"
        )

        return len(frames)

    async def interrupt(
        self,
        idle_frame: Optional[VPDFrame] = None,
        transition_steps: int = None,
    ) -> None:
        """
        打断当前动作。

        截断缓冲区中未播放的帧，插入过渡帧平滑回到 idle 姿态。

        Args:
            idle_frame: idle 姿态帧（None 则使用零位）
            transition_steps: 过渡帧数
        """
        if transition_steps is None:
            transition_steps = settings.IDLE_TRANSITION_FRAMES

        # 取消正在进行的 idle 加载
        if self._idle_load_task and not self._idle_load_task.done():
            self._idle_load_task.cancel()
            self._idle_loading = False

        # 获取当前最后一帧作为过渡起点
        current_frame = self._buffer.peek_last()
        if current_frame is None:
            logger.debug("[FrameQueue] 打断时缓冲区为空，跳过")
            return

        # 目标帧：idle 或零位
        if idle_frame is None:
            idle_frame = VPDFrame(
                bones=[
                    BoneFrame(
                        name=b.name,
                        translation=[0.0, 0.0, 0.0],
                        quaternion=[0.0, 0.0, 0.0, 1.0],
                    )
                    for b in current_frame.bones
                ],
                morphs=[],
            )

        # 生成过渡帧
        transition_frames = interpolate_transition(
            from_frame=current_frame,
            to_frame=idle_frame,
            steps=transition_steps,
        )

        # 截断缓冲区所有未播放帧，写入过渡帧
        remaining = self._buffer.count
        self._buffer.insert_from_end(remaining, transition_frames)

        # ⭐ 清空口型帧池并推入归零帧（打断时立即闭嘴）
        await self._lip_frame_pool.clear_and_reset()

        # ⭐ 清空音频缓冲区并重置口型状态（新方案）
        await self.clear_audio_buffer()

        # 重置口型推送标记
        self._has_pushed_lip_frames = False

        logger.info(
            f"[FrameQueue] 打断: 截断 {remaining} 帧, "
            f"插入 {len(transition_frames)} 过渡帧, "
            f"口型已归零, 音频缓冲区已清空"
        )

    async def insert_motion_tail(
        self,
        motion_id: str,
        frames: list[VPDFrame],
        transition_steps: int | None = None,
    ) -> int:
        """
        低优先级动作：从队尾插入。

        使用队尾最后一帧与新动作首帧做过渡插值，然后追加过渡帧 + 新动作帧。
        """
        if self._idle_load_task and not self._idle_load_task.done():
            self._idle_load_task.cancel()
            self._idle_loading = False

        transition_steps = (
            MOTION_TRANSITION_FRAMES if transition_steps is None else transition_steps
        )

        if not frames:
            logger.warning(f"[FrameQueue] 动作 {motion_id} 无帧数据")
            return 0

        transition_frames: list[VPDFrame] = []
        last_frame = self._buffer.peek_last()
        if last_frame is not None:
            transition_frames = interpolate_transition(
                from_frame=last_frame,
                to_frame=frames[0],
                steps=transition_steps,
                start_fi=0,
            )

        self._current_motion_id = motion_id
        self._buffer.write_batch(transition_frames + frames)

        logger.info(
            f"[FrameQueue] 队尾插入动作 {motion_id}: "
            f"+{len(transition_frames)} 过渡帧, "
            f"{len(frames)} 帧"
        )
        return len(transition_frames) + len(frames)

    async def insert_motion_head(
        self,
        motion_id: str,
        frames: list[VPDFrame],
        transition_steps: int | None = None,
        head_offset: int | None = None,
    ) -> int:
        """
        高优先级动作：从队首插入。

        保留队首 head_offset 帧（默认 5），用该帧与新动作首帧做过渡插值，
        清空其后所有帧，追加过渡帧 + 新动作帧。
        """
        if self._idle_load_task and not self._idle_load_task.done():
            self._idle_load_task.cancel()
            self._idle_loading = False

        transition_steps = (
            MOTION_TRANSITION_FRAMES if transition_steps is None else transition_steps
        )
        head_offset = (
            MOTION_HEAD_OFFSET_FRAMES if head_offset is None else head_offset
        )

        if not frames:
            logger.warning(f"[FrameQueue] 动作 {motion_id} 无帧数据")
            return 0

        start_frame = self._buffer.peek_from_start(head_offset - 1)
        if start_frame is None:
            # 缓冲区不足，直接清空并写入
            self._buffer.clear()
            self._current_motion_id = motion_id
            self._buffer.write_batch(frames)
            logger.info(
                f"[FrameQueue] 队首插入动作 {motion_id}: 缓冲区不足，直接写入 "
                f"{len(frames)} 帧"
            )
            return len(frames)

        transition_frames = interpolate_transition(
            from_frame=start_frame,
            to_frame=frames[0],
            steps=transition_steps,
            start_fi=0,
        )

        self._current_motion_id = motion_id
        self._buffer.replace_after_prefix(head_offset, transition_frames + frames)

        logger.info(
            f"[FrameQueue] 队首插入动作 {motion_id}: 保留 {head_offset} 帧, "
            f"+{len(transition_frames)} 过渡帧, "
            f"{len(frames)} 帧"
        )
        return head_offset + len(transition_frames) + len(frames)

    # === 音频缓冲区管理（新方案） ===

    def push_audio_data(self, audio_data: bytes) -> int:
        """
        推入音频数据到缓冲区（同步方法，供 TTS 回调调用）
        
        ⭐ 新方案核心接口：
        - TTS 收到音频 chunk 后，调用此方法推入 AudioBuffer
        - FrameQueue 每 33ms 从 AudioBuffer 取音频，实时分析口型
        
        Args:
            audio_data: PCM 音频数据（16-bit）
            
        Returns:
            推入的字节数
        """
        return self._audio_buffer.push(audio_data)
    
    async def push_audio_data_async(self, audio_data: bytes) -> int:
        """
        推入音频数据到缓冲区（异步方法）
        
        Args:
            audio_data: PCM 音频数据
            
        Returns:
            推入的字节数
        """
        return await self._audio_buffer.push_async(audio_data)

    async def clear_audio_buffer(self) -> None:
        """清空音频缓冲区（打断/TTS结束时调用）"""
        await self._audio_buffer.clear()
        self._lip_sync_service.reset()  # 重置口型状态

    @property
    def audio_buffer_available_frames(self) -> int:
        """音频缓冲区可生成的口型帧数"""
        return self._audio_buffer.available_frames

    # === 口型合并（兼容旧接口） ===

    async def set_lip_morphs(self, morphs: list[MorphFrame], audio_bytes: int = 0) -> None:
        """
        更新口型数据并推入帧池（由 LipSync 服务调用）- 兼容旧接口
        
        ⭐ 新方案下此方法不再使用，口型由 FrameQueue 实时分析生成。

        Args:
            morphs: 口型数据
            audio_bytes: 音频字节数，用于计算需要生成的口型帧数
        """
        if audio_bytes > 0:
            # 根据音频时长计算帧数，推入池子
            await self._lip_frame_pool.push_frames_by_bytes(audio_bytes, morphs)
        else:
            # 兼容：旧接口，推入 1 帧
            await self._lip_frame_pool.push_frames(morphs)

    async def clear_lip_morphs(self) -> None:
        """清空口型数据（停止说话时调用）"""
        await self._lip_frame_pool.clear()

    async def push_lip_reset_frame(self) -> None:
        """
        推入归零帧到口型帧池末尾（TTS 完成时调用）

        当 CosyVoice 服务端发送 task-finished 事件时调用，
        追加闭嘴帧到口型帧池末尾，让口型在所有帧推送完后自然归零。
        """
        await self._lip_frame_pool.push_reset_frame()
        logger.info("[FrameQueue] 已追加归零帧到口型帧池末尾")

    def clear_lip_delay_queue(self) -> None:
        """清空口型延迟队列（打断时调用）- 兼容旧接口"""
        # 新的帧池机制不需要延迟队列，直接清空
        pass

    # === 调度器 ===

    async def start(self) -> None:
        """启动推帧循环"""
        if self._running:
            logger.warning("[FrameQueue] 调度器已在运行")
            return

        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("[FrameQueue] 调度器启动")

    async def stop(self) -> None:
        """停止推帧循环"""
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        if self._idle_load_task and not self._idle_load_task.done():
            self._idle_load_task.cancel()
        logger.info("[FrameQueue] 调度器停止")

    async def _scheduler_loop(self) -> None:
        """
        核心推帧循环。

        ⭐ 新方案：音频缓冲区 + 实时口型分析
        
        每 batch_interval 从缓冲区取 1 帧，
        同时从 AudioBuffer 取音频数据，实时 FFT 分析生成口型，
        合并后通过 WS 单帧推送。
        低水位时自动触发 idle 预加载。
        """
        logger.info(
            f"[FrameQueue] 推帧循环启动（新方案）: "
            f"每 {self._batch_interval*1000:.0f}ms 推 {self._batch_size} 帧, "
            f"音频帧大小={self._audio_buffer._frame_bytes} bytes"
        )

        while self._running:
            try:
                loop_start = time.monotonic()

                # 从缓冲区取帧
                frames = self._buffer.read_batch(self._batch_size)

                if frames:
                    # 逐帧推送
                    now = time.time()
                    for i, frame in enumerate(frames):
                        # ⭐ 新方案：从 AudioBuffer 取一帧音频，实时分析口型
                        audio_slice = await self._audio_buffer.pop_frame_audio()
                        if audio_slice:
                            # 实时 FFT 分析
                            morphs = self._lip_sync_service.analyze_frame(audio_slice)
                            if morphs:
                                # 转换为 MorphFrame 格式
                                frame.morphs = [
                                    MorphFrame(name=m.name, weight=m.weight)
                                    for m in morphs
                                ]
                                # 标记曾经推送过口型帧
                                if not self._has_pushed_lip_frames:
                                    self._has_pushed_lip_frames = True
                                    logger.debug("[FrameQueue] 开始推送口型帧（新方案）")
                        
                        # 兼容：如果没有音频数据，尝试从旧口型帧池获取
                        elif not frame.morphs:
                            lip_morphs = await self._lip_frame_pool.pop_frame()
                            if lip_morphs:
                                frame.morphs = lip_morphs

                        # 构造单帧消息
                        msg = SingleFrame(
                            seq=self._seq,
                            ts=now + (i * self._batch_interval),
                            motion_id=self._current_motion_id,
                            frame=frame.to_dict(),
                        )

                        # WS 广播
                        await self._ws_manager.broadcast(msg.to_dict())
                        self._seq += 1
                        self._metrics.frames_pushed += 1

                    # 记录推帧间隔
                    if self._last_push_time > 0:
                        interval_ms = (loop_start - self._last_push_time) * 1000
                        self._push_intervals.append(interval_ms)
                    self._last_push_time = loop_start

                # ⭐ 事件：缓冲区为空时触发回调（检查播报队列）
                elif self._buffer_empty_callback:
                    # 缓冲区为空，触发回调检查播报队列
                    try:
                        callback = self._buffer_empty_callback
                        if asyncio.iscoroutinefunction(callback):
                            await callback()
                        else:
                            callback()
                    except Exception as e:
                        logger.error(f"[FrameQueue] 缓冲区空回调执行失败: {e}")

                # ⭐ 新方案：检测音频缓冲区耗尽（从有到无的转换）
                # 这表示 TTS 音频已全部分析完毕
                if self._has_pushed_lip_frames and self._audio_buffer.is_empty:
                    # 音频已全部分析完毕
                    self._has_pushed_lip_frames = False  # 重置标记
                    logger.info("[FrameQueue] 检测到音频缓冲区耗尽，触发回调")
                    
                    # ⭐ 显式推入归零帧（确保口型归零闭嘴）
                    await self._lip_frame_pool.push_reset_frame()
                    logger.debug("[FrameQueue] 音频缓冲区耗尽，已推入归零帧")
                    
                    # ⭐ 触发口型帧耗尽回调（handle_speech_end）
                    if self._lip_frames_empty_callback:
                        try:
                            callback = self._lip_frames_empty_callback
                            if asyncio.iscoroutinefunction(callback):
                                await callback()
                            else:
                                callback()
                        except Exception as e:
                            logger.error(f"[FrameQueue] 口型帧耗尽回调执行失败: {e}")
                    
                    # 触发 panel 关闭回调
                    if self._panel_close_callback:
                        try:
                            callback = self._panel_close_callback
                            if asyncio.iscoroutinefunction(callback):
                                await callback()
                            else:
                                callback()
                        except Exception as e:
                            logger.error(f"[FrameQueue] Panel 关闭回调执行失败: {e}")

                # 检查低水位，触发 idle 预加载（移到 if frames 外面，缓冲区空时也能触发）
                remaining = self._buffer.count
                if (
                    remaining <= LOW_WATER_MARK
                    and not self._idle_loading
                    and self._idle_scheduler is not None
                ):
                    self._idle_loading = True
                    self._idle_load_task = asyncio.create_task(self._preload_idle())

                # 精确控制推帧节奏
                elapsed = time.monotonic() - loop_start
                sleep_time = max(0, self._batch_interval - elapsed)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[FrameQueue] 推帧循环异常: {e}")
                await asyncio.sleep(self._batch_interval)

    async def _preload_idle(self) -> None:
        """缓冲区低水位时预加载 idle 动作"""
        try:
            if self._idle_scheduler is None:
                return
            last_frame = self._buffer.peek_last()
            await self._idle_scheduler.load_random_idle(transition_from=last_frame)
            self._metrics.idle_preloads += 1
        except asyncio.CancelledError:
            logger.debug("[FrameQueue] idle 预加载被取消")
        except Exception as e:
            logger.error(f"[FrameQueue] idle 预加载失败: {e}")
        finally:
            self._idle_loading = False

    # === 工具方法 ===

    @property
    def buffer_count(self) -> int:
        """当前缓冲区帧数"""
        return self._buffer.count

    @property
    def buffer_usage(self) -> float:
        """缓冲区使用率 0.0 ~ 1.0"""
        return self._buffer.usage

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_motion_id(self) -> str:
        return self._current_motion_id

    def get_metrics(self) -> FrameQueueMetrics:
        """获取运行时监控指标"""
        self._metrics.buffer_count = self._buffer.count
        self._metrics.buffer_capacity = self._buffer.size
        self._metrics.lip_delay_queue_len = self._lip_frame_pool.count
        self._metrics.last_motion_id = self._current_motion_id or None
        if self._push_intervals:
            self._metrics.avg_push_interval_ms = (
                sum(self._push_intervals) / len(self._push_intervals)
            )
        return self._metrics

    def get_stats(self) -> dict:
        """获取统计信息"""
        metrics = self.get_metrics()
        return {
            "running": self._running,
            "buffer_count": metrics.buffer_count,
            "buffer_size": metrics.buffer_capacity,
            "buffer_usage": round(self._buffer.usage, 3),
            "seq": self._seq,
            "current_motion_id": self._current_motion_id,
            "target_fps": self._target_fps,
            "batch_size": self._batch_size,
            "frames_pushed": metrics.frames_pushed,
            "avg_push_interval_ms": metrics.avg_push_interval_ms,
            "lip_delay_queue_len": metrics.lip_delay_queue_len,
            "idle_preloads": metrics.idle_preloads,
        }
