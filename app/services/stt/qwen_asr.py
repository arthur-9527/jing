#!/usr/bin/env python3
"""
千问实时语音识别服务 (Qwen ASR)

基于阿里云 DashScope 千问实时语音识别 WebSocket API 实现。
支持流式语音识别，多语种识别，情感识别。

参考文档：https://help.aliyun.com/zh/model-studio/developer-reference/qwen-asr-realtime-api
"""

import asyncio
import base64
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

from loguru import logger
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.protocol import State

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import NOT_GIVEN, STTSettings, _NotGiven, is_given
from pipecat.services.stt_service import WebsocketSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

import websockets

from app.services.text_utils import is_valid_asr_input


# WebSocket URL - 北京地域
QWEN_ASR_WS_URL_BEIJING = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
# WebSocket URL - 新加坡地域
QWEN_ASR_WS_URL_SINGAPORE = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"

# 默认 TTFS P99 延迟（从语音结束到最终转录的时间）
QWEN_ASR_TTFS_P99 = 0.3  # 500ms


@dataclass
class QwenASRSettings(STTSettings):
    """千问 ASR 服务配置

    继承 STTSettings 的 model 和 language 字段。

    Args:
        enable_server_vad: 是否启用服务端 VAD（自动检测语音结束）
        vad_threshold: VAD 灵敏度阈值 (0.0-1.0)，0 表示最灵敏
        vad_silence_duration_ms: 静音持续时间 (ms)，超过则认为语音结束
        enable_emotion: 是否启用情感识别
        input_audio_format: 输入音频格式 (pcm/opus)
        interim_results: 是否返回中间结果
    """
    enable_server_vad: bool | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    vad_threshold: float | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    vad_silence_duration_ms: int | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    enable_emotion: bool | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    input_audio_format: str | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    interim_results: bool | _NotGiven = field(default_factory=lambda: NOT_GIVEN)

    def apply_update(self, delta: "QwenASRSettings") -> None:
        """应用配置更新，合并 delta 中已设置的值"""
        super().apply_update(delta)

        # Qwen 特定字段
        if is_given(delta.enable_server_vad):
            self.enable_server_vad = delta.enable_server_vad
        if is_given(delta.vad_threshold):
            self.vad_threshold = delta.vad_threshold
        if is_given(delta.vad_silence_duration_ms):
            self.vad_silence_duration_ms = delta.vad_silence_duration_ms
        if is_given(delta.enable_emotion):
            self.enable_emotion = delta.enable_emotion
        if is_given(delta.input_audio_format):
            self.input_audio_format = delta.input_audio_format
        if is_given(delta.interim_results):
            self.interim_results = delta.interim_results


class QwenASRService(WebsocketSTTService):
    """千问实时语音识别服务

    通过 WebSocket 连接调用阿里云千问实时语音识别服务。
    支持多语种高精度识别、情感识别、服务端 VAD。

    Event handlers:
        on_connected: WebSocket 连接成功时调用
        on_disconnected: WebSocket 断开时调用
        on_connection_error: 连接错误时调用
        on_speech_started: 检测到语音开始时调用（server_vad 模式）
        on_speech_stopped: 检测到语音停止时调用（server_vad 模式）

    Example::

        @stt.event_handler("on_connected")
        async def on_connected(stt):
            logger.info("Qwen ASR connected")

        @stt.event_handler("on_speech_started")
        async def on_speech_started(stt):
            logger.info("User started speaking")

    Args:
        api_key: 阿里云 DashScope API Key
        model: ASR 模型名称，默认 "qwen3-asr-flash-realtime"
        region: API 区域，"beijing" 或 "singapore"，默认 "beijing"
        sample_rate: 音频采样率，默认 16000
        settings: 服务配置
        **kwargs: 其他参数
    """

    Settings = QwenASRSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "qwen3-asr-flash-realtime",
        region: str = "beijing",
        sample_rate: int = 16000,
        settings: Optional[QwenASRSettings] = None,
        ttfs_p99_latency: Optional[float] = QWEN_ASR_TTFS_P99,
        **kwargs,
    ):
        # 1. 初始化默认配置
        default_settings = self.Settings(
            model=model,
            language="zh",
            enable_server_vad=True,
            vad_threshold=0.0,
            vad_silence_duration_ms=400,
            enable_emotion=True,
            input_audio_format="pcm",
            interim_results=True,
        )

        # 2. 应用用户配置
        if settings is not None:
            default_settings.apply_update(settings)

        # 初始化父类
        super().__init__(
            sample_rate=sample_rate,
            settings=default_settings,
            ttfs_p99_latency=ttfs_p99_latency,
            **kwargs,
        )

        self._api_key = api_key
        self._model = model
        self._region = region

        # 选择 WebSocket URL
        if region == "singapore":
            self._ws_url = QWEN_ASR_WS_URL_SINGAPORE
        else:
            self._ws_url = QWEN_ASR_WS_URL_BEIJING

        # 会话状态
        self._session_id: Optional[str] = None
        self._session_ready = asyncio.Event()

        # 音频发送锁
        self._send_lock = asyncio.Lock()

        # 接收任务
        self._receive_task: Optional[asyncio.Task] = None

        # 注册事件处理器
        self._register_event_handler("on_speech_started")
        self._register_event_handler("on_speech_stopped")

        logger.info(f"[QwenASR] 初始化完成: model={model}, region={region}, sample_rate={sample_rate}")

    def can_generate_metrics(self) -> bool:
        """支持生成处理指标"""
        return True

    @property
    def vad_enabled(self) -> bool:
        """检查服务端 VAD 是否启用"""
        s = self._settings
        return is_given(s.enable_server_vad) and s.enable_server_vad

    # ==================== WebsocketService 抽象方法实现 ====================

    async def _connect_websocket(self):
        """建立 WebSocket 连接 - 实现 WebsocketService 抽象方法"""
        # 构建 URL
        url = f"{self._ws_url}?model={self._model}"
        logger.debug(f"[QwenASR] 连接到: {url}")

        # 建立 WebSocket 连接
        self._websocket = await websockets.connect(
            url,
            additional_headers={
                "Authorization": f"Bearer {self._api_key}",
                "OpenAI-Beta": "realtime=v1",
            },
        )

        logger.info("[QwenASR] WebSocket 连接已建立")

        # 发送会话配置
        await self._send_session_update()

        # 等待会话创建确认
        try:
            await asyncio.wait_for(self._session_ready.wait(), timeout=5.0)
            logger.info(f"[QwenASR] 会话已创建: {self._session_id}")
        except asyncio.TimeoutError:
            logger.warning("[QwenASR] 等待会话创建超时，继续...")
            self._session_ready.set()

        # 触发连接事件
        await self._call_event_handler("on_connected")

    async def _disconnect_websocket(self):
        """断开 WebSocket 连接 - 实现 WebsocketService 抽象方法"""
        if self._websocket:
            try:
                # 发送结束会话
                await self._send_session_finish()
                await self._websocket.close()
            except Exception as e:
                logger.debug(f"[QwenASR] 关闭连接时出错: {e}")
            finally:
                self._websocket = None
                self._session_id = None
                self._session_ready.clear()
                logger.info("[QwenASR] WebSocket 连接已断开")

        # 触发断开事件
        await self._call_event_handler("on_disconnected")

    async def _receive_messages(self):
        """接收 WebSocket 消息 - 实现 WebsocketService 抽象方法"""
        try:
            async for message in self._websocket:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    logger.warning(f"[QwenASR] JSON 解析失败: {e}")
                except Exception as e:
                    logger.error(f"[QwenASR] 处理消息失败: {e}")
                    # 不中断接收循环，继续处理后续消息

        except ConnectionClosedOK:
            # 正常关闭
            logger.debug("[QwenASR] 连接正常关闭")
        except ConnectionClosedError as e:
            # 异常关闭
            logger.warning(f"[QwenASR] 连接异常关闭: {e}")
            raise
        except Exception as e:
            logger.error(f"[QwenASR] 接收消息异常: {e}")
            raise

    # ==================== WebsocketSTTService 覆盖方法 ====================

    async def _connect(self):
        """连接服务并启动消息接收任务"""
        await super()._connect()

        try:
            await self._connect_websocket()
        except Exception as e:
            # 连接失败时报告错误
            await self._report_error(ErrorFrame(f"连接失败: {e}"))
            raise

        # 启动消息接收任务（使用 WebsocketService 的标准模式）
        self._receive_task = self.create_task(
            self._receive_task_handler(report_error=self._report_error),
            name="qwen_asr_receive",
        )

        # 启动 keepalive 任务（继承自 STTService）
        self._create_keepalive_task()

    async def _disconnect(self):
        """断开连接并清理任务"""
        logger.debug("[QwenASR] 断开连接...")

        # 取消接收任务
        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None

        # 取消 keepalive 任务
        await self._cancel_keepalive_task()

        # 断开 WebSocket
        await self._disconnect_websocket()

        await super()._disconnect()

    async def _verify_connection(self) -> bool:
        """验证 WebSocket 连接状态"""
        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                return False
            await self._websocket.ping()
            return True
        except Exception as e:
            logger.error(f"[QwenASR] 连接验证失败: {e}")
            return False

    async def _report_error(self, error: ErrorFrame):
        """报告错误 - 集成 pipecat 错误流"""
        await self._call_event_handler("on_connection_error", error.error)
        await self.push_error_frame(error)

    # ==================== 消息处理 ====================

    async def _handle_message(self, data: dict):
        """处理服务端消息"""
        event_type = data.get("type", "")

        # 转录相关事件调试日志
        if "transcription" in event_type or "speech" in event_type:
            logger.debug(f"[QwenASR] 事件: {event_type} -> {json.dumps(data, ensure_ascii=False)[:200]}")

        handler_map = {
            "session.created": self._on_session_created,
            "session.updated": self._on_session_updated,
            "input_audio_buffer.speech_started": self._on_speech_started,
            "input_audio_buffer.speech_stopped": self._on_speech_stopped,
            "input_audio_buffer.transcription": self._handle_transcription_legacy,
            "conversation.item.input_audio_transcription.text": self._handle_transcription_text,
            "conversation.item.input_audio_transcription.completed": self._handle_transcription_completed,
            "input_audio_buffer.committed": self._on_audio_committed,
            "conversation.item.created": self._on_item_created,
            "session.finished": self._on_session_finished,
            "error": self._on_server_error,
        }

        handler = handler_map.get(event_type)
        if handler:
            await handler(data)
        else:
            logger.debug(f"[QwenASR] 未处理事件: {event_type}")

    async def _on_session_created(self, data: dict):
        """处理会话创建事件"""
        self._session_id = data.get("session_id")
        self._session_ready.set()
        logger.debug(f"[QwenASR] 会话已创建: {self._session_id}")

    async def _on_session_updated(self, data: dict):
        """处理会话配置更新事件"""
        self._session_ready.set()
        logger.debug("[QwenASR] 会话配置已更新")

    async def _on_speech_started(self, data: dict):
        """处理语音开始事件"""
        logger.debug("[QwenASR] 检测到语音开始")

        # 触发事件处理器
        await self._call_event_handler("on_speech_started")

        # 广播 UserStartedSpeakingFrame
        await self.broadcast_frame(UserStartedSpeakingFrame)

        # 启动处理指标
        await self.start_processing_metrics()

    async def _on_speech_stopped(self, data: dict):
        """处理语音停止事件"""
        logger.debug("[QwenASR] 检测到语音停止")

        # 触发事件处理器
        await self._call_event_handler("on_speech_stopped")

        # 广播 UserStoppedSpeakingFrame
        await self.broadcast_frame(UserStoppedSpeakingFrame)

    async def _on_audio_committed(self, data: dict):
        """处理音频缓冲区提交事件"""
        logger.debug("[QwenASR] 音频缓冲区已提交")

    async def _on_item_created(self, data: dict):
        """处理对话项创建事件"""
        logger.debug("[QwenASR] 对话项已创建")

    async def _on_session_finished(self, data: dict):
        """处理会话结束事件"""
        transcript = data.get("transcript", "")
        logger.info(f"[QwenASR] 会话结束，最终文本: {transcript}")

    async def _on_server_error(self, data: dict):
        """处理服务端错误事件"""
        error = data.get("error", {})
        error_msg = error.get("message", str(error))
        logger.error(f"[QwenASR] 服务错误: {error_msg}")

        # 推送错误帧
        await self.push_error_frame(ErrorFrame(f"Qwen ASR 服务错误: {error_msg}"))

    async def _handle_transcription_legacy(self, data: dict):
        """处理识别结果（旧格式 - input_audio_buffer.transcription）"""
        transcript = data.get("transcript", "")
        is_final = data.get("is_final", False)
        language = data.get("language")
        emotion = data.get("emotion")

        if not transcript:
            return

        user_id = self._user_id or ""
        timestamp = time_now_iso8601()

        lang = self._parse_language(language)

        if is_final:
            await self._push_final_transcription(transcript, user_id, timestamp, lang, data, emotion)
        else:
            await self._push_interim_transcription(transcript, user_id, timestamp, lang, data)

        logger.debug(f"[QwenASR] 识别结果: {'[最终]' if is_final else '[中间]'} {transcript}")

    async def _handle_transcription_text(self, data: dict):
        """处理流式识别结果（新格式 - conversation.item.input_audio_transcription.text）

        事件格式示例：
        {
            "type": "conversation.item.input_audio_transcription.text",
            "item_id": "item_xxx",
            "content_index": 0,
            "text": "",
            "stash": "你好",  // 中间结果在 stash 字段
            "language": "zh",
            "emotion": "neutral"
        }

        注意：千问 ASR 的中间结果在 `stash` 字段

        ⭐ 投机采样：检测句尾标点时触发投机 LLM 请求
        """
        # 优先使用 stash 字段（中间结果）
        stash = data.get("stash", "")
        text = data.get("text", "")
        interim_text = stash if stash else text

        if not interim_text:
            return

        user_id = self._user_id or ""
        timestamp = time_now_iso8601()
        item_id = data.get("item_id", "")
        language = data.get("language")

        lang = self._parse_language(language)

        # ⭐ 投机采样：检测句尾标点，触发投机 LLM 请求（但需先验证输入有效性）
        try:
            from app.services.speculative_sampler import is_sentence_end, get_speculative_sampler

            if is_sentence_end(interim_text) and is_valid_asr_input(interim_text):
                sampler = get_speculative_sampler()
                # 异步触发投机请求（不阻塞当前流程）
                asyncio.create_task(sampler.on_sentence_end(interim_text, item_id))
                logger.debug(f"[QwenASR] 句尾检测，触发投机: {interim_text}")
            elif is_sentence_end(interim_text):
                logger.debug(f"[QwenASR] 句尾检测但输入无效，跳过投机: {interim_text}")
        except ImportError:
            pass  # 投机采样模块未安装，跳过

        # 推送中间结果
        await self._push_interim_transcription(interim_text, user_id, timestamp, lang, data)
        logger.debug(f"[QwenASR] 中间识别: {interim_text}")

    async def _handle_transcription_completed(self, data: dict):
        """处理转录完成事件（conversation.item.input_audio_transcription.completed）

        这是最终识别结果的关键事件。
        ⭐ 在推送帧之前进行有效性过滤，防止无效输入进入 Redis 和后续流程。
        """
        transcript = data.get("transcript", "")

        if not transcript:
            logger.debug("[QwenASR] 转录完成但无文本内容")
            return

        # ⭐ 过滤无效输入（单字语气词），不推送帧，不入 Redis
        if not is_valid_asr_input(transcript):
            logger.info(f"[QwenASR] 转录完成但输入无效，跳过后续流程: {transcript}")
            await self.stop_processing_metrics()
            return

        user_id = self._user_id or ""
        timestamp = time_now_iso8601()

        # 推送最终识别结果
        await self._push_final_transcription(transcript, user_id, timestamp, None, data)
        logger.info(f"[QwenASR] 转录完成，最终文本: {transcript}")

    async def _push_final_transcription(
        self,
        text: str,
        user_id: str,
        timestamp: str,
        language: Optional[Language],
        result: dict,
        emotion: Optional[str] = None,
    ):
        """推送最终转录结果"""
        frame = TranscriptionFrame(
            text=text,
            user_id=user_id,
            timestamp=timestamp,
            language=language,
            result=result,
        )
        await self.push_frame(frame)
        await self.stop_processing_metrics()

        if emotion:
            logger.debug(f"[QwenASR] 情感识别: {emotion}")

    async def _push_interim_transcription(
        self,
        text: str,
        user_id: str,
        timestamp: str,
        language: Optional[Language],
        result: dict,
    ):
        """推送中间转录结果"""
        frame = InterimTranscriptionFrame(
            text=text,
            user_id=user_id,
            timestamp=timestamp,
            language=language,
            result=result,
        )
        await self.push_frame(frame)

    def _parse_language(self, language: Optional[str]) -> Optional[Language]:
        """解析语言代码"""
        if not language:
            return None
        try:
            return Language(language)
        except ValueError:
            return None

    # ==================== 消息发送 ====================

    async def _send_session_update(self):
        """发送会话配置"""
        s = self._settings

        session_config = {
            "event_id": f"event_{uuid.uuid4().hex[:8]}",
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "input_audio_format": s.input_audio_format if is_given(s.input_audio_format) else "pcm",
                "sample_rate": self.sample_rate,
                "input_audio_transcription": {
                    "language": str(s.language) if is_given(s.language) and s.language else "zh",
                },
            },
        }

        # 配置 VAD
        if is_given(s.enable_server_vad) and s.enable_server_vad:
            session_config["session"]["turn_detection"] = {
                "type": "server_vad",
                "threshold": s.vad_threshold if is_given(s.vad_threshold) else 0.0,
                "silence_duration_ms": s.vad_silence_duration_ms if is_given(s.vad_silence_duration_ms) else 400,
            }
        else:
            session_config["session"]["turn_detection"] = None

        await self._websocket.send(json.dumps(session_config))
        logger.debug(f"[QwenASR] 发送会话配置: VAD={'启用' if self.vad_enabled else '禁用'}")

    async def _send_session_finish(self):
        """发送会话结束"""
        if not self._websocket:
            return

        try:
            finish_event = {
                "event_id": f"event_{uuid.uuid4().hex[:8]}",
                "type": "session.finish",
            }
            await self._websocket.send(json.dumps(finish_event))
            logger.debug("[QwenASR] 发送会话结束")
        except Exception as e:
            logger.debug(f"[QwenASR] 发送会话结束失败: {e}")

    async def _send_audio_buffer_commit(self):
        """提交音频缓冲区（非 VAD 模式）"""
        if not self._websocket:
            return

        commit_event = {
            "event_id": f"event_{uuid.uuid4().hex[:8]}",
            "type": "input_audio_buffer.commit",
        }
        await self._websocket.send(json.dumps(commit_event))
        logger.debug("[QwenASR] 提交音频缓冲区")

    # ==================== STT 接口 ====================

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """发送音频数据进行识别

        Args:
            audio: PCM 音频数据 (16-bit, 单声道)

        Yields:
            Frame: None（识别结果通过 WebSocket 回调处理）
        """
        if not self._websocket:
            logger.warning("[QwenASR] WebSocket 未连接，无法发送音频")
            yield None
            return

        encoded_audio = base64.b64encode(audio).decode("utf-8")

        audio_event = {
            "event_id": f"event_{uuid.uuid4().hex[:8]}",
            "type": "input_audio_buffer.append",
            "audio": encoded_audio,
        }

        async with self._send_lock:
            try:
                await self._websocket.send(json.dumps(audio_event))
            except Exception as e:
                logger.warning(f"[QwenASR] 发送音频失败: {e}")

        yield None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """处理帧"""
        await super().process_frame(frame, direction)

        if isinstance(frame, VADUserStartedSpeakingFrame):
            # 如果服务端 VAD 未启用，启动处理指标
            if not self.vad_enabled:
                await self.start_processing_metrics()

        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            # 非 VAD 模式下，手动提交音频缓冲区
            if not self.vad_enabled and self._websocket:
                await self._send_audio_buffer_commit()

    # ==================== 生命周期 ====================

    async def start(self, frame: StartFrame):
        """启动服务"""
        await super().start(frame)
        await self._connect()
        logger.info("[QwenASR] 服务已启动")

    async def stop(self, frame: EndFrame):
        """停止服务"""
        await self._disconnect()
        await super().stop(frame)
        logger.info("[QwenASR] 服务已停止")

    async def cancel(self, frame: CancelFrame):
        """取消服务"""
        await self._disconnect()
        await super().cancel(frame)
        logger.info("[QwenASR] 服务已取消")

    # ==================== Keepalive ====================

    async def _send_keepalive(self, silence: bytes):
        """发送静音包保活 - 覆盖 STTService 方法"""
        if not self._websocket:
            return

        encoded_silence = base64.b64encode(silence).decode("utf-8")

        keepalive_event = {
            "event_id": f"event_{uuid.uuid4().hex[:8]}",
            "type": "input_audio_buffer.append",
            "audio": encoded_silence,
        }

        try:
            await self._websocket.send(json.dumps(keepalive_event))
            logger.trace("[QwenASR] 发送 keepalive 静音")
        except Exception as e:
            logger.warning(f"[QwenASR] Keepalive 发送失败: {e}")

    # ==================== Settings 更新 ====================

    async def _update_settings(self, delta: STTSettings) -> dict[str, Any]:
        """应用配置更新，如果关键参数变化则重连"""
        changed = await super()._update_settings(delta)

        if not changed:
            return changed

        # 如果 VAD 或 language 相关设置变化，需要重新连接
        vad_changed = any(k in changed for k in [
            "enable_server_vad", "vad_threshold", "vad_silence_duration_ms"
        ])
        lang_changed = "language" in changed
        format_changed = "input_audio_format" in changed

        if vad_changed or lang_changed or format_changed:
            logger.info(f"[QwenASR] 关键配置已更新，重新连接...")
            if self._websocket:
                await self._disconnect()
                await self._connect()

        return changed


def create_qwen_asr_service(
    api_key: str,
    model: str = "qwen3-asr-flash-realtime",
    region: str = "beijing",
    sample_rate: int = 16000,
    language: str = "zh",
    enable_server_vad: bool = True,
    vad_threshold: float = 0.0,
    vad_silence_duration_ms: int = 400,
    **kwargs,
) -> QwenASRService:
    """创建千问 ASR 服务实例

    Args:
        api_key: 阿里云 DashScope API Key
        model: ASR 模型名称
        region: API 区域 ("beijing" 或 "singapore")
        sample_rate: 音频采样率
        language: 识别语言
        enable_server_vad: 是否启用服务端 VAD
        vad_threshold: VAD 灵敏度阈值
        vad_silence_duration_ms: 静音持续时间
        **kwargs: 其他参数

    Returns:
        QwenASRService 实例
    """
    settings = QwenASRSettings(
        model=model,
        language=language,
        enable_server_vad=enable_server_vad,
        vad_threshold=vad_threshold,
        vad_silence_duration_ms=vad_silence_duration_ms,
    )

    return QwenASRService(
        api_key=api_key,
        model=model,
        region=region,
        sample_rate=sample_rate,
        settings=settings,
        **kwargs,
    )