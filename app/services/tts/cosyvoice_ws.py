#!/usr/bin/env python3
"""
CosyVoice WebSocket API 流式实现

基于阿里云 DashScope CosyVoice WebSocket API 实现真正的流式语音合成。
继承 WebsocketTTSService，实现与 Pipecat 架构完全兼容的流式 TTS。

关键设计：
1. 继承 WebsocketTTSService，使用后台接收循环
2. run_tts 只发送增量文本，yield None（音频通过后台循环推送）
3. 音频帧通过 append_to_audio_context 推送到播放队列
4. 支持打断（on_audio_context_interrupted 发送取消消息）
5. ⭐ 支持情绪指令（instruct_text）- 根据 PAD 状态动态设置语音情绪

参考文档：https://help.aliyun.com/zh/dashscope/developer-use-cosyvoice-websocket-api
"""


def pad_to_emotion_instruction(pad: dict) -> str | None:
    """将 PAD 值映射到 CosyVoice instruct_text 情绪指令
    
    基于 PAD 三维情绪模型映射到中文情绪指令文本：
    - P (Pleasure): 愉悦度，正=愉悦/开心，负=不悦/悲伤/生气
    - A (Arousal): 激活度，正=兴奋/激动，负=平静/放松
    - D (Dominance): 支配度，正=自信/坚定，负=顺从/温和
    
    Args:
        pad: {"P": float, "A": float, "D": float} 范围 -1.0 到 1.0
        
    Returns:
        情绪指令文本（如 "开心的语气"、"平静自然的语气"）或 None（默认）
    """
    p = pad.get("P", 0.0)
    a = pad.get("A", 0.0)
    d = pad.get("D", 0.0)
    
    # 基于 PA 二维情绪模型 + D 维度微调
    # 参考研究：Russell's Circumplex Model of Affect
    
    # 高愉悦 + 高激活 = 开心/兴奋
    if p > 0.4 and a > 0.3:
        base = "开心兴奋的语气"
    # 高愉悦 + 中等激活 = 愉悦/满足
    elif p > 0.4 and a > 0:
        base = "愉悦满足的语气"
    # 高愉悦 + 低激活 = 平静满足/放松
    elif p > 0.4 and a <= 0:
        base = "平静放松的语气"
    # 中等愉悦 + 高激活 = 激动/热情
    elif p > 0 and a > 0.4:
        base = "激动热情的语气"
    # 中等愉悦 + 中等激活 = 自然/友好
    elif abs(p) <= 0.3 and abs(a) <= 0.3:
        base = "自然友好的语气"
    # 低愉悦 + 高激活 = 生气/愤怒
    elif p < -0.3 and a > 0.3:
        base = "生气愤怒的语气"
    # 低愉悦 + 中等激活 = 不满/抱怨
    elif p < -0.3 and a > 0:
        base = "不满抱怨的语气"
    # 低愉悦 + 低激活 = 悲伤/失落
    elif p < -0.3 and a <= 0:
        base = "悲伤失落的语气"
    # 中等愉悦 + 低激活 = 冷淡/疲惫
    elif p > -0.3 and a < -0.3:
        base = "冷淡疲惫的语气"
    else:
        base = None
    
    # D 维度微调（支配度）
    if base:
        if d > 0.4:
            # 高支配：添加自信/坚定
            return f"自信坚定的{base.replace('语气', '')}语气"
        elif d < -0.4:
            # 低支配：添加温和/谦逊
            return f"温和谦逊的{base.replace('语气', '')}语气"
        else:
            return base
    
    # 默认无情绪指令
    return None

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncGenerator, Awaitable, Callable, Dict, List, Optional

from loguru import logger

try:
    from websockets.asyncio.client import connect as websocket_connect
    from websockets.protocol import State
except ModuleNotFoundError as e:
    logger.error(f"Exception: {e}")
    logger.error("In order to use CosyVoice, you need to `pip install websockets`.")
    raise Exception(f"Missing module: {e}")

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TextAggregationMode, WebsocketTTSService

from app.services.lipsync_service import LipSyncService


# WebSocket URL
COSYVOICE_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"

# 文本长度限制
MAX_TEXT_LENGTH = 20000  # 单次 continue-task 最大字符数


class TaskState(Enum):
    """任务状态"""
    IDLE = "idle"
    RUNNING = "running"
    WAITING_TEXT = "waiting_text"  # ⭐ 预创建状态：task_id已分配，等待run_tts调用
    FINISHED = "finished"
    FAILED = "failed"


@dataclass
class TaskContext:
    """任务上下文"""
    task_id: str
    state: TaskState = TaskState.IDLE
    audio_buffer: List[bytes] = field(default_factory=list)
    total_characters: int = 0


class CosyVoiceTTSService(WebsocketTTSService):
    """CosyVoice 流式语音合成服务 (WebSocket 实现)

    继承 WebsocketTTSService，实现真正的流式 TTS：
    - LLM 输出 token → 增量发送到 CosyVoice → 流式合成音频
    - 音频帧通过后台接收循环推送，不影响 run_tts 执行
    - 支持打断、字级时间戳、口型同步

    Args:
        api_key: 阿里云 DashScope API Key
        model: TTS 模型名称，默认 "cosyvoice-v3-flash"
        sample_rate: 输出音频采样率（Hz），默认 16000
        format: 音频格式，默认 "pcm"
        enable_lipsync: 是否启用水型同步
        clone_voice_audio_path: 克隆音色音频文件路径（可选）
        clone_voice_id: 克隆音色 ID（可选，若提供则直接使用）
        **kwargs: 其他参数
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "cosyvoice-v3-flash",
        sample_rate: int = 16000,
        format: str = "pcm",
        enable_lipsync: bool = True,
        clone_voice_audio_path: Optional[str] = None,
        clone_voice_id: Optional[str] = None,
        **kwargs,
    ):
        # 初始化设置
        settings = TTSSettings(model=model, voice="", language=None)
        
        # WebsocketTTSService 初始化
        # 关键参数：
        # - text_aggregation_mode=TOKEN: token 级别流式，不等待完整句子
        # - push_start_frame=True: 自动创建 audio context 并发送 TTSStartedFrame
        # - push_text_frames=False: 不自动发送文本帧（我们通过时间戳控制）
        super().__init__(
            sample_rate=sample_rate,
            text_aggregation_mode=TextAggregationMode.TOKEN,  # ⭐ token 级别流式
            push_start_frame=True,
            push_text_frames=False,
            pause_frame_processing=False,
            settings=settings,
            **kwargs,
        )
        
        self._api_key = api_key
        self._model = model
        self._format = format
        self._enable_lipsync = enable_lipsync
        self._clone_voice_audio_path = clone_voice_audio_path
        self._clone_voice_id = clone_voice_id
        
        # 实际使用的音色
        if clone_voice_id:
            self._effective_voice = clone_voice_id
        elif clone_voice_audio_path:
            self._effective_voice = ""  # 稍后初始化
        else:
            raise ValueError("必须提供 clone_voice_audio_path 或 clone_voice_id")
        
        # 克隆音色初始化标志
        self._clone_voice_initialized = clone_voice_id is not None
        
        # 任务上下文映射 (context_id -> TaskContext)
        self._task_contexts: Dict[str, TaskContext] = {}
        
        # 接收任务
        self._receive_task: Optional[asyncio.Task] = None
        
        # 口型同步服务
        self._lip_sync_service: Optional[LipSyncService] = None
        if self._enable_lipsync:
            self._lip_sync_service = LipSyncService(
                sensitivity=5.0,
                smoothing_factor=0.55,
                min_volume_threshold=0.02,
            )
        
        # 回调
        self._on_lip_morphs: Optional[Callable] = None  # 兼容旧接口
        self._on_word_timestamps: Optional[Callable] = None
        self._on_tts_finished: Optional[Callable] = None  # ⭐ TTS 完成回调（追加闭嘴帧）
        
        # ⭐ 新方案：音频数据推送回调（推送到 AudioBuffer）
        self._on_audio_data: Optional[Callable[[bytes], int]] = None
        
        # ⭐ TTS 状态回调（用于 StateManager）
        self._on_tts_started: Optional[Callable[[str], Awaitable[None]]] = None
        self._on_tts_stopped: Optional[Callable[[str], Awaitable[None]]] = None
        self._first_audio_sent: Dict[str, bool] = {}  # context_id -> 是否已发送首帧音频
        
        # ⭐ 情绪指令（根据 PAD 状态动态设置）
        self._current_instruct_text: Optional[str] = None

    @staticmethod
    def _generate_prefix_from_audio(audio_bytes: bytes, filename: str) -> str:
        """从音频内容和文件名生成音色前缀"""
        audio_hash = hashlib.md5(audio_bytes).hexdigest()[:8]
        name = Path(filename).stem
        clean_name = re.sub(r"[^a-zA-Z0-9]", "", name)[:6]
        if clean_name:
            remaining = 10 - len(clean_name)
            return f"{clean_name}{audio_hash[:remaining]}"
        else:
            return f"v{audio_hash[:9]}"

    async def ensure_initialized(self) -> bool:
        """确保服务已初始化（预初始化克隆音色和 WebSocket）
        
        注意：此方法在 pipeline 创建时调用，此时 TaskManager 尚未初始化。
        接收任务会在 _connect() 中启动（pipeline 运行后）。
        """
        if self._clone_voice_initialized and self._websocket:
            return True
        
        logger.info("[CosyVoice TTS] 开始预初始化...")
        
        # 只初始化克隆音色（不启动接收任务，因为 TaskManager 未初始化）
        if self._clone_voice_audio_path and not self._clone_voice_initialized:
            success = await self._init_clone_voice()
            if not success:
                logger.error("[CosyVoice TTS] 克隆音色初始化失败")
                return False
        
        # 预连接 WebSocket
        if not self._websocket:
            try:
                await self._connect_websocket()
            except Exception as e:
                logger.error(f"[CosyVoice TTS] WebSocket 连接失败：{e}")
                return False
        
        # 接收任务在 _connect() 中启动（pipeline 运行后 TaskManager 才会初始化）
        
        logger.info("[CosyVoice TTS] 预初始化完成")
        return True

    async def _init_clone_voice(self) -> bool:
        """初始化克隆音色"""
        if self._clone_voice_initialized or not self._clone_voice_audio_path:
            return True
        
        try:
            from app.services.tts.voice_enrollment import (
                VoiceEnrollmentService,
                load_voice_cache,
                save_voice_cache,
                compute_audio_md5,
            )
            
            audio_path = Path(self._clone_voice_audio_path)
            if not audio_path.exists():
                logger.error(f"[CosyVoice TTS] 音频文件不存在：{audio_path}")
                return False
            
            audio_bytes = audio_path.read_bytes()
            audio_md5 = compute_audio_md5(audio_bytes)
            
            # 检查缓存
            cache = load_voice_cache()
            if audio_md5 in cache:
                cached_data = cache[audio_md5]
                voice_id = cached_data.get("voice_id")
                
                # 验证音色
                service = VoiceEnrollmentService(self._api_key)
                status = await service.get_voice_status(voice_id)
                await service.close()
                
                if status and status.get("state") == "ready":
                    self._effective_voice = voice_id
                    self._clone_voice_initialized = True
                    return True
                else:
                    del cache[audio_md5]
                    save_voice_cache(cache)
            
            # 创建新音色
            prefix = self._generate_prefix_from_audio(audio_bytes, audio_path.name)
            service = VoiceEnrollmentService(self._api_key)
            
            voice_id = await service.find_voice_by_prefix(self._model, prefix)
            if not voice_id:
                voice_id = await service.create_voice(
                    target_model=self._model,
                    prefix=prefix,
                    audio_bytes=audio_bytes,
                )
                
                # 等待就绪
                for i in range(30):
                    await asyncio.sleep(1)
                    status = await service.get_voice_status(voice_id)
                    if status and status.get("state") == "ready":
                        break
            
            await service.close()
            
            # 保存缓存
            import datetime
            cache[audio_md5] = {
                "voice_id": voice_id,
                "prefix": prefix,
                "created_at": datetime.datetime.now().isoformat(),
            }
            save_voice_cache(cache)
            
            self._effective_voice = voice_id
            self._clone_voice_initialized = True
            return True
            
        except Exception as e:
            logger.error(f"[CosyVoice TTS] 克隆音色初始化失败：{e}")
            return False

    async def start(self, frame):
        """启动 TTS 服务（重写以启动 WebSocket 连接）"""
        await super().start(frame)
        
        # 初始化克隆音色
        if not self._clone_voice_initialized and self._clone_voice_audio_path:
            await self._init_clone_voice()
        
        # 连接 WebSocket
        await self._connect_websocket()
        
        # 启动接收任务
        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(
                self._receive_task_handler(self._report_error)
            )
            logger.info("[CosyVoice TTS] 接收任务已启动")

    def can_generate_metrics(self) -> bool:
        return True

    def set_on_lip_morphs(self, callback: Callable):
        """设置口型数据回调"""
        self._on_lip_morphs = callback

    def set_on_word_timestamps(self, callback: Callable):
        """设置字级时间戳回调"""
        self._on_word_timestamps = callback

    def set_on_tts_started(self, callback: Callable[[str], Awaitable[None]]):
        """设置 TTS 开始回调（用于 StateManager 状态转换）"""
        self._on_tts_started = callback

    def set_on_tts_stopped(self, callback: Callable[[str], Awaitable[None]]):
        """设置 TTS 停止回调（用于 StateManager 状态转换）"""
        self._on_tts_stopped = callback

    def set_on_tts_finished(self, callback: Callable):
        """设置 TTS 完成回调（用于追加闭嘴帧）"""
        self._on_tts_finished = callback

    def set_on_audio_data(self, callback: Callable[[bytes], int]):
        """
        设置音频数据推送回调（新方案：推送到 AudioBuffer）
        
        ⭐ 新方案核心接口：
        - TTS 收到音频 chunk 后，调用此回调推入 AudioBuffer
        - FrameQueue 每 33ms 从 AudioBuffer 取音频，实时分析口型
        
        Args:
            callback: 回调函数，接收 bytes，返回推入的字节数
        """
        self._on_audio_data = callback
        logger.info("[CosyVoice TTS] 音频数据推送回调已设置（新方案）")

    def set_emotion_from_pad(self, pad: dict) -> None:
        """根据 PAD 状态设置情绪指令
        
        将 PAD 值转换为情绪指令文本，用于 CosyVoice 的 instruct_text 参数。
        应在 LLM 输出 emotion_delta 后立即调用，以便在 TTS 开始合成前设置情绪。
        
        Args:
            pad: {"P": float, "A": float, "D": float} 范围 -1.0 到 1.0
        """
        instruct_text = pad_to_emotion_instruction(pad)
        self._current_instruct_text = instruct_text
        
        if instruct_text:
            logger.info(f"[CosyVoice TTS] 设置情绪指令: {instruct_text} (PAD={pad})")
        else:
            logger.debug(f"[CosyVoice TTS] 使用默认语气 (PAD={pad})")

    # ===== WebSocket 连接管理 =====

    async def _connect(self):
        """连接服务"""
        await super()._connect()
        
        # 初始化克隆音色
        if not self._clone_voice_initialized and self._clone_voice_audio_path:
            await self._init_clone_voice()
        
        # 连接 WebSocket
        await self._connect_websocket()
        
        # 启动接收任务
        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(
                self._receive_task_handler(self._report_error)
            )
            logger.debug("[CosyVoice TTS] 接收任务已启动")

    async def _disconnect(self):
        """断开服务"""
        await super()._disconnect()
        
        # 取消接收任务
        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None
        
        # 断开 WebSocket
        await self._disconnect_websocket()
        
        # 清理任务上下文
        self._task_contexts.clear()

    async def _connect_websocket(self):
        """建立 WebSocket 连接"""
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return
            
            logger.debug("[CosyVoice TTS] 连接 WebSocket...")
            self._websocket = await websocket_connect(
                COSYVOICE_WS_URL,
                additional_headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "User-Agent": "CosyVoice-WebSocket-Client/1.0",
                },
            )
            await self._call_event_handler("on_connected")
            logger.info("[CosyVoice TTS] WebSocket 已连接")
        except Exception as e:
            await self.push_error(error_msg=f"WebSocket 连接失败: {e}", exception=e)
            self._websocket = None
            await self._call_event_handler("on_connection_error", f"{e}")

    async def _disconnect_websocket(self):
        """关闭 WebSocket 连接"""
        try:
            await self.stop_all_metrics()
            
            if self._websocket:
                logger.debug("[CosyVoice TTS] 关闭 WebSocket...")
                await self._websocket.close()
        except Exception as e:
            await self.push_error(error_msg=f"WebSocket 关闭失败: {e}", exception=e)
        finally:
            await self.remove_active_audio_context()
            self._websocket = None
            await self._call_event_handler("on_disconnected")

    def _get_websocket(self):
        """获取 WebSocket 连接"""
        if self._websocket and self._websocket.state is State.OPEN:
            return self._websocket
        raise Exception("WebSocket 未连接")

    # ===== 任务生命周期管理 =====

    async def on_turn_context_created(self, context_id: str):
        """创建新任务上下文
        
        在 LLM turn 开始时调用。
        
        ⭐ 关键改动：预创建 TaskContext（分配 task_id），但不发送 run-task。
        - task_id 在打断时用于发送 finish-task 取消服务端任务
        - run-task 延迟到 run_tts 首次调用时发送（避免无用任务）
        - 状态设为 WAITING_TEXT，表示等待文本输入
        """
        logger.info(f"[CosyVoice TTS] 创建任务上下文: {context_id}")
        
        # ⭐ 确保 WebSocket 已连接
        if not self._websocket or self._websocket.state is not State.OPEN:
            logger.info("[CosyVoice TTS] WebSocket 未连接，正在连接...")
            try:
                await self._connect_websocket()
            except Exception as e:
                logger.error(f"[CosyVoice TTS] WebSocket 连接失败：{e}")
                return
        
        # ⭐ 启动接收任务（如果未启动）
        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(
                self._receive_task_handler(self._report_error)
            )
            logger.info("[CosyVoice TTS] 接收任务已启动")
        
        # ⭐ 预创建 TaskContext（分配 task_id，状态为 WAITING_TEXT）
        # 这样在打断时即使 run_tts 还没被调用，也能正确处理
        if context_id not in self._task_contexts:
            task_id = uuid.uuid4().hex
            self._task_contexts[context_id] = TaskContext(
                task_id=task_id,
                state=TaskState.WAITING_TEXT,  # 等待文本输入
            )
            logger.info(
                f"[CosyVoice TTS] 预创建任务上下文: context={context_id}, "
                f"task_id={task_id}, state=WAITING_TEXT"
            )
        else:
            logger.debug(f"[CosyVoice TTS] 任务上下文已存在: {context_id}")

    async def flush_audio(self, context_id: Optional[str] = None):
        """结束当前任务
        
        发送 finish-task，告知服务端文本发送完毕。
        """
        flush_id = context_id or self.get_active_audio_context_id()
        if not flush_id or not self._websocket:
            return
        
        task_ctx = self._task_contexts.get(flush_id)
        if not task_ctx:
            return
        
        logger.trace(f"[CosyVoice TTS] flush_audio: {flush_id}")
        
        # 发送 finish-task
        finish_task_cmd = self._build_finish_task_command(task_ctx.task_id)
        try:
            await self._get_websocket().send(json.dumps(finish_task_cmd))
            logger.debug(f"[CosyVoice TTS] 发送 finish-task: {task_ctx.task_id}")
        except Exception as e:
            logger.error(f"[CosyVoice TTS] 发送 finish-task 失败: {e}")

    async def on_audio_context_interrupted(self, context_id: str):
        """处理打断
        
        当用户打断时调用。根据任务状态进行不同处理：
        
        ⭐ 状态处理逻辑：
        - RUNNING: 已发送 run-task，需要发送 finish-task 取消服务端任务
        - WAITING_TEXT: 只预创建了 task_id，未发送 run-task，无需发送 finish-task
        - 其他状态: 仅清理本地状态
        
        这样可以确保在打断时正确处理各种情况，避免服务端任务残留。
        """
        await self.stop_all_metrics()
        
        # ⭐ 根据状态处理打断
        task_ctx = self._task_contexts.get(context_id)
        if task_ctx:
            if task_ctx.state == TaskState.RUNNING:
                # ⭐ 正在运行：发送 finish-task 取消服务端任务
                if self._websocket:
                    finish_task_cmd = self._build_finish_task_command(task_ctx.task_id)
                    try:
                        await self._websocket.send(json.dumps(finish_task_cmd))
                        logger.info(
                            f"[CosyVoice TTS] 打断 RUNNING 任务，发送 finish-task: "
                            f"task_id={task_ctx.task_id}, context={context_id}"
                        )
                    except Exception as e:
                        logger.warning(f"[CosyVoice TTS] 发送 finish-task 失败: {e}")
                        
            elif task_ctx.state == TaskState.WAITING_TEXT:
                # ⭐ 等待文本状态：未发送 run-task，无需发送 finish-task
                # 这种情况发生在 LLM turn 创建后、run_tts 调用前被打断
                logger.info(
                    f"[CosyVoice TTS] 打断 WAITING_TEXT 任务，无需发送 finish-task: "
                    f"task_id={task_ctx.task_id}, context={context_id}"
                )
                
            else:
                # 其他状态（FINISHED, FAILED, IDLE）
                logger.debug(
                    f"[CosyVoice TTS] 打断任务，状态={task_ctx.state}: "
                    f"task_id={task_ctx.task_id}, context={context_id}"
                )
        
        # 清理任务上下文
        if context_id in self._task_contexts:
            del self._task_contexts[context_id]
            logger.debug(f"[CosyVoice TTS] 打断，清理上下文: {context_id}")
        
        # 清理首帧标记
        if context_id in self._first_audio_sent:
            del self._first_audio_sent[context_id]
        
        await super().on_audio_context_interrupted(context_id)

    async def on_audio_context_completed(self, context_id: str):
        """任务完成
        
        音频播放完毕后调用。
        """
        # 清理任务上下文
        if context_id in self._task_contexts:
            del self._task_contexts[context_id]
            logger.debug(f"[CosyVoice TTS] 任务完成，清理上下文: {context_id}")
        
        await super().on_audio_context_completed(context_id)

    # ===== 消息构建 =====

    def _build_run_task_command(self, task_id: str) -> Dict:
        """构建 run-task 指令
        
        ⭐ 支持情绪指令 (instruct_text)：
        - 如果设置了 _current_instruct_text，则添加 instruct_text 参数
        - instruct_text 用于指导语音合成的情绪风格（如"开心的语气"）
        """
        parameters = {
            "text_type": "PlainText",
            "voice": self._effective_voice,
            "format": self._format,
            "sample_rate": self.sample_rate,
            "word_timestamp_enabled": True,
        }
        
        # ⭐ 添加情绪指令（如果有）
        if self._current_instruct_text:
            parameters["instruct_text"] = self._current_instruct_text
            logger.debug(f"[CosyVoice TTS] run-task 包含情绪指令: {self._current_instruct_text}")
        
        return {
            "header": {
                "action": "run-task",
                "task_id": task_id,
                "streaming": "duplex"
            },
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self._model,
                "parameters": parameters,
                "input": {}
            }
        }

    def _build_continue_task_command(self, task_id: str, text: str) -> Dict:
        """构建 continue-task 指令"""
        return {
            "header": {
                "action": "continue-task",
                "task_id": task_id,
                "streaming": "duplex"
            },
            "payload": {
                "input": {
                    "text": text
                }
            }
        }

    def _build_finish_task_command(self, task_id: str) -> Dict:
        """构建 finish-task 指令"""
        return {
            "header": {
                "action": "finish-task",
                "task_id": task_id,
                "streaming": "duplex"
            },
            "payload": {
                "input": {}
            }
        }

    def _parse_result_event(self, event: Dict) -> Optional[Dict]:
        """解析 result-generated 事件"""
        try:
            header = event.get("header", {})
            payload = event.get("payload", {})
            output = payload.get("output", {})
            
            event_type = output.get("type")
            sentence = output.get("sentence", {})
            original_text = output.get("original_text", "")
            words = sentence.get("words", [])
            
            return {
                "task_id": header.get("task_id"),
                "event": header.get("event"),
                "type": event_type,
                "sentence_index": sentence.get("index", 0),
                "original_text": original_text,
                "words": words,
            }
        except Exception as e:
            logger.error(f"[CosyVoice TTS] 解析事件失败: {e}")
            return None

    # ===== 消息接收循环 =====

    async def _receive_messages(self):
        """后台接收消息循环
        
        持续接收 WebSocket 消息，处理音频帧和时间戳。
        """
        async for message in self._get_websocket():
            if isinstance(message, str):
                # JSON 事件
                try:
                    event = json.loads(message)
                    await self._handle_event(event)
                except json.JSONDecodeError as e:
                    logger.error(f"[CosyVoice TTS] JSON 解析失败: {e}")
            elif isinstance(message, bytes):
                # 二进制音频数据
                await self._handle_audio_data(message)
            else:
                logger.warning(f"[CosyVoice TTS] 未知消息类型: {type(message)}")

    async def _handle_event(self, event: Dict):
        """处理 JSON 事件"""
        header = event.get("header", {})
        event_name = header.get("event")
        task_id = header.get("task_id")
        
        # 查找对应的 context_id
        context_id = self._find_context_id_by_task_id(task_id)
        if not context_id and event_name not in ["task-started", "task-failed"]:
            logger.debug(f"[CosyVoice TTS] 未找到 context_id for task_id: {task_id}")
            return
        
        if event_name == "task-started":
            logger.debug(f"[CosyVoice TTS] 任务启动: {task_id}")
            # 通知框架开始计时
            await self.stop_ttfb_metrics()
        
        elif event_name == "result-generated":
            parsed = self._parse_result_event(event)
            if parsed:
                event_type = parsed["type"]
                words = parsed["words"]
                
                if event_type == "sentence-end":
                    # 句子结束，处理时间戳
                    if words:
                        # 添加字级时间戳
                        word_times = [(w["text"], w["begin_time"] / 1000.0) for w in words]
                        await self.add_word_timestamps(word_times, context_id)
                        
                        # 触发回调
                        if self._on_word_timestamps:
                            asyncio.create_task(self._on_word_timestamps(words))
                        
                        logger.debug(f"[CosyVoice TTS] 句子结束，时间戳: {len(words)} words")
        
        elif event_name == "task-finished":
            # 任务完成
            payload = event.get("payload", {})
            usage = payload.get("usage", {})
            characters = usage.get("characters", 0)
            logger.info(f"[CosyVoice TTS] 任务完成，计费字符: {characters}")
            
            # ⭐ 触发 TTS 完成回调（追加闭嘴帧到口型帧池末尾）
            if self._on_tts_finished:
                try:
                    self._on_tts_finished()
                    logger.info("[CosyVoice TTS] TTS 完成回调已触发，已追加闭嘴帧")
                except Exception as e:
                    logger.error(f"[CosyVoice TTS] TTS 完成回调失败: {e}")
            
            # ⭐ 触发 TTS 停止回调
            if self._on_tts_stopped:
                try:
                    await self._on_tts_stopped(context_id)
                    logger.debug(f"[CosyVoice TTS] TTS 停止回调已触发，context={context_id}")
                except Exception as e:
                    logger.error(f"[CosyVoice TTS] TTS 停止回调失败: {e}")
            
            # 清理首帧标记
            if context_id in self._first_audio_sent:
                del self._first_audio_sent[context_id]
            
            # 发送 TTSStoppedFrame
            await self.stop_ttfb_metrics()
            await self.append_to_audio_context(context_id, TTSStoppedFrame(context_id=context_id))
            await self.remove_audio_context(context_id)
        
        elif event_name == "task-failed":
            error_msg = header.get("error_message", "Unknown error")
            error_code = header.get("error_code", "Unknown")
            logger.error(f"[CosyVoice TTS] 任务失败: [{error_code}] {error_msg}")
            
            # 清理首帧标记
            if context_id in self._first_audio_sent:
                del self._first_audio_sent[context_id]
            
            # 发送错误帧
            await self.append_to_audio_context(
                context_id,
                ErrorFrame(error=f"{error_code}: {error_msg}")
            )
            await self.remove_audio_context(context_id)

    async def _handle_audio_data(self, audio_data: bytes):
        """处理音频数据
        
        ⭐ 新方案核心：
        - 音频数据推送到 AudioBuffer（由 FrameQueue 实时分析口型）
        - 同时也兼容旧方案（可选：直接口型分析）
        """
        # 查找活跃的 context
        context_id = self.get_active_audio_context_id()
        if not context_id:
            logger.debug("[CosyVoice TTS] 收到音频但无活跃 context")
            return
        
        # ⭐ 首次收到音频时触发 TTS 开始回调
        if self._on_tts_started and not self._first_audio_sent.get(context_id):
            self._first_audio_sent[context_id] = True
            try:
                await self._on_tts_started(context_id)
                logger.debug(f"[CosyVoice TTS] TTS 开始回调已触发，context={context_id}")
            except Exception as e:
                logger.error(f"[CosyVoice TTS] TTS 开始回调失败: {e}")
        
        # ⭐ 新方案：推送音频数据到 AudioBuffer（由 FrameQueue 实时分析口型）
        if self._on_audio_data:
            try:
                pushed_bytes = self._on_audio_data(audio_data)
                logger.trace(
                    f"[CosyVoice TTS] 音频推送到 AudioBuffer: {len(audio_data)} bytes "
                    f"(实际推入 {pushed_bytes} bytes)"
                )
            except Exception as e:
                logger.warning(f"[CosyVoice TTS] 音频推送回调失败: {e}")
        
        # 兼容旧方案：口型分析（如果新回调未设置）
        # 注意：新方案下，此路径不再使用（口型由 FrameQueue 实时分析）
        if self._enable_lipsync and self._lip_sync_service and not self._on_audio_data:
            try:
                morphs = self._lip_sync_service.analyze_frame(audio_data)
                if self._on_lip_morphs and morphs:
                    self._on_lip_morphs(morphs, len(audio_data))
            except Exception as e:
                logger.debug(f"[LipSync] 分析失败: {e}")
        
        # 创建音频帧并推送到播放队列
        frame = TTSAudioRawFrame(
            audio=audio_data,
            sample_rate=self.sample_rate,
            num_channels=1,
            context_id=context_id,
        )
        
        await self.append_to_audio_context(context_id, frame)
        logger.trace(f"[CosyVoice TTS] 音频帧推送: {len(audio_data)} bytes")

    def _find_context_id_by_task_id(self, task_id: str) -> Optional[str]:
        """根据 task_id 查找 context_id"""
        for ctx_id, task_ctx in self._task_contexts.items():
            if task_ctx.task_id == task_id:
                return ctx_id
        return None

    # ===== TTS 合成 =====

    @staticmethod
    def _is_valid_tts_text(text: str) -> bool:
        """检查文本是否有效"""
        stripped = text.strip()
        if not stripped:
            return False
        has_content = bool(re.search(r'[a-zA-Z0-9\u4e00-\u9fff]', stripped))
        return has_content

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """流式语音合成
        
        只发送增量文本到 CosyVoice，音频通过后台循环推送。
        yield None 告知框架音频将通过 append_to_audio_context 推送。
        
        ⭐ 状态机逻辑：
        - WAITING_TEXT: 预创建状态，需要发送 run-task 启动服务端任务
        - RUNNING: 已在运行，直接发送 continue-task
        - IDLE/其他: 兜底创建新任务
        
        Args:
            text: 要合成的文本
            context_id: TTS 上下文 ID
        
        Yields:
            None（音频通过后台循环推送）
        """
        # 过滤无效文本
        if not self._is_valid_tts_text(text):
            logger.warning(f"[CosyVoice TTS] 文本无效，跳过：'{text[:50]}'")
            return
        
        logger.debug(f"[CosyVoice TTS] run_tts: '{text[:50]}...' (context={context_id})")
        
        try:
            task_ctx = self._task_contexts.get(context_id)
            
            # ⭐ 根据状态决定是否发送 run-task
            if not task_ctx:
                # 兜底：没有任务上下文，创建新的
                task_id = uuid.uuid4().hex
                task_ctx = TaskContext(task_id=task_id, state=TaskState.RUNNING)
                self._task_contexts[context_id] = task_ctx
                
                run_task_cmd = self._build_run_task_command(task_id)
                try:
                    await self._get_websocket().send(json.dumps(run_task_cmd))
                    logger.info(f"[CosyVoice TTS] 发送 run-task (新创建): {task_id}")
                except Exception as e:
                    logger.error(f"[CosyVoice TTS] 发送 run-task 失败: {e}")
                    del self._task_contexts[context_id]
                    yield ErrorFrame(error=f"Failed to send run-task: {e}")
                    return
                    
            elif task_ctx.state == TaskState.WAITING_TEXT:
                # ⭐ 预创建状态：发送 run-task 启动服务端任务
                run_task_cmd = self._build_run_task_command(task_ctx.task_id)
                try:
                    await self._get_websocket().send(json.dumps(run_task_cmd))
                    task_ctx.state = TaskState.RUNNING
                    logger.info(f"[CosyVoice TTS] 发送 run-task (WAITING_TEXT -> RUNNING): {task_ctx.task_id}")
                except Exception as e:
                    logger.error(f"[CosyVoice TTS] 发送 run-task 失败: {e}")
                    yield ErrorFrame(error=f"Failed to send run-task: {e}")
                    return
                    
            elif task_ctx.state == TaskState.RUNNING:
                # 已在运行，直接发送文本
                logger.debug(f"[CosyVoice TTS] 任务已运行，发送文本: {task_ctx.task_id}")
                
            else:
                # 其他状态（FINISHED, FAILED, IDLE），重新创建
                logger.warning(f"[CosyVoice TTS] 任务状态异常: {task_ctx.state}，重新创建")
                task_id = uuid.uuid4().hex
                task_ctx = TaskContext(task_id=task_id, state=TaskState.RUNNING)
                self._task_contexts[context_id] = task_ctx
                
                run_task_cmd = self._build_run_task_command(task_id)
                try:
                    await self._get_websocket().send(json.dumps(run_task_cmd))
                    logger.info(f"[CosyVoice TTS] 发送 run-task (重建): {task_id}")
                except Exception as e:
                    logger.error(f"[CosyVoice TTS] 发送 run-task 失败: {e}")
                    del self._task_contexts[context_id]
                    yield ErrorFrame(error=f"Failed to send run-task: {e}")
                    return
            
            # 检查文本长度
            if len(text) > MAX_TEXT_LENGTH:
                logger.warning(f"[CosyVoice TTS] 文本过长 ({len(text)} chars)，将分段")
                # 分段发送
                chunks = [text[i:i+MAX_TEXT_LENGTH] for i in range(0, len(text), MAX_TEXT_LENGTH)]
                for chunk in chunks:
                    await self._send_continue_task(task_ctx.task_id, chunk)
            else:
                await self._send_continue_task(task_ctx.task_id, text)
            
            await self.start_tts_usage_metrics(text)
            
            # yield None 告知框架音频将通过后台循环推送
            yield None
            
        except Exception as e:
            logger.error(f"[CosyVoice TTS] run_tts 异常: {e}")
            yield ErrorFrame(error=str(e))

    async def _send_continue_task(self, task_id: str, text: str):
        """发送 continue-task 指令"""
        cmd = self._build_continue_task_command(task_id, text)
        try:
            await self._get_websocket().send(json.dumps(cmd))
            logger.trace(f"[CosyVoice TTS] 发送 continue-task: {len(text)} chars")
        except Exception as e:
            logger.error(f"[CosyVoice TTS] 发送 continue-task 失败: {e}")
            # 尝试重连
            await self._disconnect()
            await self._connect()


# 便捷函数
def create_cosyvoice_tts_service(
    api_key: str,
    model: str = "cosyvoice-v3-flash",
    sample_rate: int = 16000,
    clone_voice_audio_path: Optional[str] = None,
    clone_voice_id: Optional[str] = None,
    **kwargs,
) -> CosyVoiceTTSService:
    """创建 CosyVoice TTS 服务实例"""
    return CosyVoiceTTSService(
        api_key=api_key,
        model=model,
        sample_rate=sample_rate,
        clone_voice_audio_path=clone_voice_audio_path,
        clone_voice_id=clone_voice_id,
        **kwargs,
    )