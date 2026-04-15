#!/usr/bin/env python3
"""
Agent 服务 - 管理 Pipecat Pipeline

功能：
1. 管理 pipecat pipeline 生命周期
2. 处理音频流（输入 -> STT -> EmotionalAgent LLM -> TTS -> 输出）
3. 集成口型分析并推送到 WebSocket
"""

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Callable
from loguru import logger

# 添加 pipecat 路径
PIPECAT_PATH = Path(__file__).parent.parent.parent.parent / "pipecat"
if PIPECAT_PATH.exists():
    sys.path.insert(0, str(PIPECAT_PATH))

from app.services.agent_ws_manager import AgentWSManager, AgentStatus
from app.services.lipsync_service import LipSyncService, LipMorph
from app.services.frame_queue import FrameQueueManager, IdleScheduler, MorphFrame, keyframe_to_vpd
from app.services.state_manager import StateManagerProcessor, AgentState
from app.services.init_gate import get_init_gate
from app.services.panel_manager import PanelManager


class AgentService:
    """
    Agent 服务 - 整合 pipecat pipeline

    负责：
    1. 管理 pipecat pipeline 生命周期
    2. 处理音频流（输入 -> STT -> LLM -> TTS -> 输出）
    3. 口型分析并推送 WebSocket
    """

    def __init__(
        self,
        ws_manager: AgentWSManager,
        config: dict = None,
    ):
        """
        初始化 Agent 服务

        Args:
            ws_manager: WebSocket 管理器
            config: 配置字典
        """
        self.ws_manager = ws_manager
        self.config = config or {}

        # 组件
        self._lip_sync_service: Optional[LipSyncService] = None
        self._frame_queue: Optional[FrameQueueManager] = None
        self._idle_scheduler: Optional[IdleScheduler] = None
        self._state_manager: Optional[StateManagerProcessor] = None  # ⭐ 状态管理器
        self._pipeline = None
        self._runner = None
        self._task = None
        # 状态
        self._initialized = False
        self._running = False

        # 回调
        self._on_llm_response: Optional[Callable[[str], None]] = None

        # 动作结构体队列（用于 TTS 字级时间戳触发）
        self._pending_actions: list[dict] = []
        self._tts_start_time: float | None = None
        self._tts_provider: str = "cosyvoice_ws"
        self._tts_voice_id: Optional[str] = None
        self._cartesia_word_anchor_pts_ns: int | None = None
        self._cartesia_word_anchor_monotonic: float | None = None

        # ⭐ Panel 状态管理器（统一管理 panel 显示/隐藏）
        self._panel_manager: Optional[PanelManager] = None
        
        # ⭐ 播报调度器（Redis 队列 + 1s 定时轮询）
        self._playback_scheduler: Optional["PlaybackScheduler"] = None

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def frame_queue(self) -> Optional[FrameQueueManager]:
        return self._frame_queue

    # ===== 生命周期 =====

    async def initialize(self):
        """初始化服务"""
        if self._initialized:
            logger.warning("[AgentService] 已初始化，跳过")
            return

        logger.info("[AgentService] 初始化...")

        # ⭐ 进入 INITING 状态
        await self.ws_manager.broadcast_status(AgentStatus.INITING)
        logger.info("[AgentService] 状态切换到 INITING")

        # 初始化口型同步服务
        self._lip_sync_service = LipSyncService(
            sensitivity=2.0,
            smoothing_factor=0.4,
            min_volume_threshold=0.05,
        )
        logger.info("[AgentService] 口型同步服务已初始化")

        # 初始化帧队列管理器（使用配置项，默认单帧推送）
        self._frame_queue = FrameQueueManager(
            ws_manager=self.ws_manager,
        )
        logger.info("[AgentService] 帧队列管理器已初始化")

        # ⭐ 注意：缓冲区空回调已移除（播报队列改由 PlaybackScheduler 管理）
        # 播报完成后的下一个任务由 PlaybackScheduler 的 1s 定时循环自动检查

        # ⭐ 注册口型帧耗尽回调（触发 handle_speech_end -> IDLE 状态）
        self._frame_queue.set_lip_frames_empty_callback(self.handle_speech_end)
        logger.info("[AgentService] 口型帧耗尽回调已注册")

        # ⭐ 初始化 Panel 状态管理器
        self._panel_manager = PanelManager(ws_manager=self.ws_manager)
        logger.info("[AgentService] Panel 状态管理器已初始化")

        # 初始化 idle 调度器
        from app.database import get_db_session
        self._idle_scheduler = IdleScheduler(
            frame_queue=self._frame_queue,
            db_session_factory=get_db_session,
            min_interval=10.0,
            max_interval=30.0,
        )
        logger.info("[AgentService] Idle 调度器已初始化")

        # 初始化 canonical action catalog
        from app.services.motion_catalog_service import get_motion_catalog_service

        motion_catalog = get_motion_catalog_service()
        await motion_catalog.initialize()
        logger.info("[AgentService] Motion catalog 已初始化")

        # 初始化标签目录服务（预加载所有 action/emotion 标签）
        from app.services.tag_catalog_service import get_tag_catalog_service

        tag_catalog = get_tag_catalog_service()
        await tag_catalog.initialize()
        logger.info("[AgentService] Tag catalog 已初始化")

        # 注册 WS 打断回调
        self.ws_manager.set_on_interrupt(self.handle_interrupt)

        # 设置 WebSocket 回调
        # TODO: 等前端支持 WebSocket 音频输入后再启用
        # self.ws_manager.set_on_audio_received(self._handle_audio_input)
        # self.ws_manager.set_on_text_received(self._handle_text_input)

        # ⭐ 注册需要等待的组件到初始化门控
        gate = get_init_gate()
        gate.register("pipeline")      # Pipeline StartFrame 到达终点
        gate.register("llm_agent")     # EmotionalAgent 初始化完成
        gate.register("task_system")   # ⭐ 任务系统启动（替代原 openclaw）
        gate.register("tts")           # TTS 服务预初始化完成
        logger.info("[AgentService] 初始化门控组件已注册")

        # 初始化 pipecat pipeline
        await self._init_pipeline()

        self._initialized = True
        logger.info("[AgentService] 初始化完成")

    async def _init_pipeline(self):
        """初始化 pipecat pipeline"""
        try:
            # 加载配置（必须在开头）
            from app.config import settings
            
            # 动态导入 pipecat 组件
            from pipecat.pipeline.pipeline import Pipeline
            from pipecat.pipeline.runner import PipelineRunner
            from pipecat.pipeline.task import PipelineParams, PipelineTask
            from pipecat.audio.vad.silero import SileroVADAnalyzer
            from pipecat.audio.vad.vad_analyzer import VADParams
            from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
            
            # STT 服务 - 根据 ASR_PROVIDER 配置选择
            asr_provider = (settings.ASR_PROVIDER or "qwen").lower().strip()
            logger.info(f"[AgentService] 设置 ASR provider: {asr_provider}")
            
            if asr_provider == "qwen":
                # 千问 ASR
                from app.services.stt.qwen_asr import QwenASRService, QwenASRSettings
                
                dashscope_api_key = os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY
                if not dashscope_api_key:
                    logger.warning("[AgentService] 未配置 DASHSCOPE_API_KEY")
                    return
                
                stt = QwenASRService(
                    api_key=dashscope_api_key,
                    model=settings.QWEN_ASR_MODEL,
                    sample_rate=settings.AUDIO_SAMPLE_RATE,
                    language=settings.QWEN_ASR_LANGUAGE,
                    settings=QwenASRSettings(
                        enable_server_vad=settings.QWEN_ASR_ENABLE_VAD,
                        vad_threshold=settings.QWEN_ASR_VAD_THRESHOLD,
                        vad_silence_duration_ms=settings.QWEN_ASR_VAD_SILENCE_MS,
                    ),
                )
                logger.info(f"[AgentService] 千问 ASR 已初始化: model={settings.QWEN_ASR_MODEL}")
            else:
                # Deepgram ASR (默认)
                from pipecat.services.deepgram.stt import DeepgramSTTService, DeepgramSTTSettings
                
                deepgram_api_key = os.getenv("DEEPGRAM_API_KEY") or settings.DEEPGRAM_API_KEY
                if not deepgram_api_key:
                    logger.warning("[AgentService] 未配置 DEEPGRAM_API_KEY")
                    return
                
                stt = DeepgramSTTService(
                    api_key=deepgram_api_key,
                    sample_rate=16000,
                    settings=DeepgramSTTSettings(
                        model=settings.DEEPGRAM_ASR_MODEL,
                        language=settings.DEEPGRAM_ASR_LANGUAGE,
                        smart_format=True,
                        # 优化延迟配置 - 方案A：只用 endpointing
                        endpointing=100,           # 100ms 静音后认为结束（关键优化！）
                        interim_results=True,      # 启用中间结果
                    ),
                )
                logger.info(f"[AgentService] Deepgram ASR 已初始化: model={settings.DEEPGRAM_ASR_MODEL}")
            
            # LLM 服务 (EmotionalAgent)
            from app.services.llm.emotional_agent import EmotionalAgentLLMService
            
            # Frame 类型
            from pipecat.frames.frames import (
                LLMMessagesAppendFrame,
                LLMRunFrame,
                EndFrame,
            )
            
            # LLM Context Aggregator（新版，消除废弃警告）
            from pipecat.processors.aggregators.llm_response_universal import (
                LLMAssistantAggregator,
                LLMAssistantAggregatorParams,
                LLMUserAggregatorParams,
            )
            from pipecat.processors.aggregators.llm_context import LLMContext
            
            # Redis History Aggregator
            from app.services.chat_history import RedisHistoryAggregator, get_conversation_buffer
            
            # ⭐ 动态静音策略
            from app.services.mute_strategy import get_mute_strategy
            
            # 加载配置
            from app.config import settings
            
            # DashScope API Key (用于 TTS 和千问 ASR)
            dashscope_api_key = os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY
            
            # 1. 本地音频传输
            logger.info("[AgentService] 设置 LocalAudioTransport...")

            def _get_audio_device_index(device_type: str, config_index: Optional[int]) -> Optional[int]:
                """获取音频设备索引
                
                Args:
                    device_type: "input" 或 "output"
                    config_index: 从配置读取的设备索引（None 表示使用系统默认）
                
                Returns:
                    设备索引，None 表示使用系统默认
                """
                import sounddevice as sd
                
                # 1. 如果配置中有指定有效索引，直接使用
                if config_index is not None and config_index >= 0:
                    try:
                        d = sd.query_devices(config_index)
                        channels_key = "max_input_channels" if device_type == "input" else "max_output_channels"
                        if d.get(channels_key, 0) > 0:
                            logger.info(
                                f"[AgentService] 使用配置指定的{device_type}设备: "
                                f"index={config_index}, name={d.get('name')}"
                            )
                            return config_index
                        else:
                            logger.warning(
                                f"[AgentService] 配置的{device_type}设备 index={config_index} 无效"
                            )
                    except Exception as e:
                        logger.warning(
                            f"[AgentService] 配置的{device_type}设备 index={config_index} 查询失败: {e}"
                        )
                
                # 2. 使用系统默认设备
                try:
                    device_tuple = sd.default.device
                    default_idx = device_tuple[0] if device_type == "input" else device_tuple[1]
                    if default_idx is not None and default_idx >= 0:
                        d = sd.query_devices(default_idx)
                        logger.info(
                            f"[AgentService] 使用系统默认{device_type}设备: "
                            f"index={default_idx}, name={d.get('name')}"
                        )
                        return default_idx
                except Exception as e:
                    logger.warning(f"[AgentService] 获取系统默认{device_type}设备失败: {e}")
                
                # 3. 返回 None，让 sounddevice 自己处理（使用其内部默认值）
                logger.info(f"[AgentService] {device_type}设备未指定，由系统自动选择")
                return None

            # 获取音频设备索引（从配置或系统默认）
            input_device_index = _get_audio_device_index("input", settings.AUDIO_INPUT_DEVICE_INDEX)
            output_device_index = _get_audio_device_index("output", settings.AUDIO_OUTPUT_DEVICE_INDEX)

            # ⭐ 禁用本地 VAD，依赖阿里云服务端 VAD
            # ESP32 麦克风自带硬件 VAD，只有检测到语音时才发送音频
            # 阿里云 QwenASR 的服务端 VAD 负责检测语音边界和转录提交
            transport = LocalAudioTransport(
                params=LocalAudioTransportParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    audio_in_sample_rate=settings.AUDIO_SAMPLE_RATE,
                    audio_out_sample_rate=settings.TTS_SAMPLE_RATE,
                    input_device_index=input_device_index,
                    output_device_index=output_device_index,
                    vad_enabled=False,  # 禁用本地 VAD，依赖服务端 VAD
                )
            )
            logger.info("[AgentService] 本地音频传输已设置")
            
            # ⭐ STT 服务已在上方根据 ASR_PROVIDER 配置创建
            
            # ⭐ 先获取 ConversationBuffer（用于 Redis 历史和 AI 回复写入）
            conversation_buffer = await get_conversation_buffer(user_id="default_user")
            
            # 3. LLM 服务 (EmotionalAgent)
            logger.info("[AgentService] 设置 EmotionalAgent LLM...")
            llm = EmotionalAgentLLMService(
                character_config_path=settings.CHARACTER_CONFIG_PATH,
                user_id="default_user",
                conversation_buffer=conversation_buffer,  # 传递 buffer 用于流式写入 AI 回复
            )
            
            # 系统提示词 - 由 EmotionalAgent 的角色配置提供
            system_prompt = ""
            
            messages = [{"role": "system", "content": system_prompt}]
            
            # ⭐ 设置记忆提取回调（Redis 消息超阈值时批量提取用户信息）
            async def _memory_extractor(messages: list[dict]) -> None:
                """批量提取用户信息并写入数据库"""
                from app.agent.memory.writer import extract_and_write_user_info
                from app.agent.llm.client import LLMClient
                
                llm_client = LLMClient()
                
                # 将消息列表转换为对话格式
                for i in range(0, len(messages) - 1, 2):
                    user_msg = messages[i] if i < len(messages) else None
                    assistant_msg = messages[i + 1] if i + 1 < len(messages) else None
                    
                    if user_msg and assistant_msg:
                        if user_msg.get("role") == "user" and assistant_msg.get("role") == "assistant":
                            try:
                                await extract_and_write_user_info(
                                    llm_client=llm_client,
                                    character_id="daji",  # 默认角色
                                    user_id="default_user",
                                    user_input=user_msg.get("content", ""),
                                    assistant_reply=assistant_msg.get("content", ""),
                                )
                            except Exception as e:
                                logger.warning(f"[MemoryExtractor] 提取失败: {e}")
                
                logger.info(f"[MemoryExtractor] 批量提取完成，处理了 {len(messages)} 条消息")
            
            conversation_buffer.set_memory_extractor(_memory_extractor)
            logger.info("[AgentService] 记忆提取回调已设置")
            
            # 获取静态系统提示词（严格动静分离，用于缓存优化）
            from app.agent.prompt.system_prompt import build_static_system_prompt
            from app.agent.character.loader import load_character

            config = load_character(settings.CHARACTER_CONFIG_PATH)

            # 构建完整静态 System Prompt（包含全量动作标签）
            static_system_prompt = build_static_system_prompt(config)
            logger.info(
                "[AgentService] 静态 System Prompt 已构建，长度: %d chars",
                len(static_system_prompt),
            )
            
            # ⭐ 创建 LLMContext（新版，替代 messages list）
            context = LLMContext(
                messages=[{"role": "system", "content": static_system_prompt}],
            )
            logger.info(f"[AgentService] LLMContext 已创建，system prompt 长度: {len(static_system_prompt)}")
            
            # ⭐ 获取静音策略（初始化时默认静音，进入 IDLE 后自动解除）
            mute_strategy = get_mute_strategy()
            logger.info("[AgentService] 静音策略已获取（默认静音，等待 IDLE）")
            
            # ⭐ 优化 Aggregator 参数（新版参数结构，含静音策略）
            user_agg_params = LLMUserAggregatorParams(
                user_turn_stop_timeout=0.05,  # 优化：50ms 后认为用户 turn 结束（关键！）
                user_idle_timeout=0.1,        # 优化：100ms idle 检测
                user_mute_strategies=[mute_strategy],  # ⭐ 添加静音策略
            )
            logger.info(f"[AgentService] Aggregator 参数: user_turn_stop_timeout=0.05s, user_mute_strategies=[DynamicMuteStrategy]")
            
            # 创建 RedisHistoryAggregator（新版，传入 LLMContext）
            user_aggregator = RedisHistoryAggregator(
                context=context,  # ⭐ 新版：传入 LLMContext
                user_id="default_user",
                conversation_buffer=conversation_buffer,
                max_history_items=10,
                system_prompt=static_system_prompt,
                params=user_agg_params,
            )
            logger.info("[AgentService] RedisHistoryAggregator 已初始化（新版）")
            
            # 创建 Assistant Aggregator（新版）
            assistant_aggregator = LLMAssistantAggregator(
                context=context,  # ⭐ 新版：共享同一个 LLMContext
                params=LLMAssistantAggregatorParams(),
            )
            
            # 4. TTS 服务（按 provider 分流）
            tts_provider = (settings.TTS_PROVIDER or "cosyvoice_ws").lower().strip()
            self._tts_provider = tts_provider
            logger.info(f"[AgentService] 设置 TTS provider: {tts_provider}")

            # TTS 时间戳/音频监听处理器（用于 Cartesia 路径）
            from app.services.tts.word_timestamp_processor import WordTimestampProcessor
            word_timestamp_processor = WordTimestampProcessor(agent_service=self)

            if tts_provider == "cartesia":
                from pipecat.services.cartesia.tts import CartesiaTTSService

                cartesia_api_key = os.getenv("CARTESIA_API_KEY") or settings.CARTESIA_API_KEY
                cartesia_voice_id = os.getenv("CARTESIA_VOICE_ID") or settings.CARTESIA_VOICE_ID
                if not cartesia_api_key:
                    logger.warning("[AgentService] 未配置 CARTESIA_API_KEY")
                    return
                if not cartesia_voice_id:
                    logger.warning("[AgentService] 未配置 CARTESIA_VOICE_ID")
                    return

                logger.info("[AgentService] 使用 CartesiaTTSService")
                tts = CartesiaTTSService(
                    api_key=cartesia_api_key,
                    voice_id=cartesia_voice_id,
                    model=settings.CARTESIA_MODEL,
                    sample_rate=settings.CARTESIA_SAMPLE_RATE,
                )
                self._tts_voice_id = cartesia_voice_id
                logger.info(f"[AgentService] 使用音色: {self._tts_voice_id}")
            else:
                # CosyVoice WebSocket TTS (默认)
                from app.services.tts.cosyvoice_ws import create_cosyvoice_tts_service

                logger.info("[AgentService] 使用 CosyVoice WebSocket TTS")
                tts = create_cosyvoice_tts_service(
                    api_key=dashscope_api_key,
                    model=settings.COSYVOICE_WS_MODEL,
                    clone_voice_audio_path=settings.COSYVOICE_WS_CLONE_AUDIO,
                    sample_rate=settings.TTS_SAMPLE_RATE,
                    enable_lipsync=True,
                )

                # ⭐ 新方案：设置音频数据推送回调（推送到 AudioBuffer）
                # TTS 音频推入 AudioBuffer，FrameQueue 每 33ms 取音频实时分析口型
                tts.set_on_audio_data(self._frame_queue.push_audio_data)
                logger.info("[AgentService] 音频数据推送回调已设置（新方案）")

                # 兼容旧方案：设置口型数据回调（新方案下不再使用）
                tts.set_on_lip_morphs(self._on_cosyvoice_lip_morphs)

                # 设置字级时间戳回调（用于触发动作）
                tts.set_on_word_timestamps(self._on_word_timestamps)

                # ⭐ 设置 TTS 完成回调（追加闭嘴帧到口型帧池末尾）
                tts.set_on_tts_finished(self._on_tts_finished)

                # ⭐ 预初始化 TTS 服务（同步等待，确保初始化完成后才进入 IDLE）
                # 在系统启动时完成音色查询/创建和 WebSocket 连接，避免首次请求延迟
                logger.info("[AgentService] 预初始化 CosyVoice TTS...")
                await tts.ensure_initialized()
                logger.info(f"[AgentService] CosyVoice WebSocket TTS 已初始化并就绪")
                
                # ⭐ 通知初始化门控：TTS 就绪
                get_init_gate().mark_ready("tts")
            
            # ⭐ 保存 TTS 服务引用（用于情绪设置）
            self._tts_service = tts
            logger.info("[AgentService] TTS 服务引用已保存（用于情绪设置）")

            # 5. ⭐ 创建状态管理处理器
            logger.info("[AgentService] 创建状态管理处理器...")
            self._state_manager = StateManagerProcessor(
                ws_manager=self.ws_manager,
                frame_queue=self._frame_queue,
                max_history=100,
                state_timeout=60.0,
            )
            logger.info("[AgentService] 状态管理处理器已创建")
            
            # ⭐ 注入 StateManager 到静音策略（用于检测 IDLE 状态）
            mute_strategy.set_state_manager(self._state_manager)
            logger.info("[AgentService] StateManager 已注入到静音策略")
            
            # ⭐ 连接 TTS 状态回调到 StateManager
            if hasattr(tts, 'set_on_tts_started') and hasattr(tts, 'set_on_tts_stopped'):
                tts.set_on_tts_started(self._state_manager.on_tts_started)
                tts.set_on_tts_stopped(self._state_manager.on_tts_stopped)
                logger.info("[AgentService] TTS 状态回调已连接到 StateManager")

            # ⭐ 设置 IdleScheduler 的状态检查回调
            if self._idle_scheduler:
                self._idle_scheduler.set_is_idle_callback(
                    lambda: self._state_manager.is_idle if self._state_manager else True
                )
                logger.info("[AgentService] IdleScheduler 状态检查回调已设置")
                
                # ⭐ 将 IdleScheduler 注入到 StateManager（用于 IDLE→LISTENING 时触发 thinking 动作）
                self._state_manager.set_idle_scheduler(self._idle_scheduler)
                logger.info("[AgentService] IdleScheduler 已注入到 StateManager")

            # 6. 构建 Pipeline（AudioMuteFilter 在 STT 之前阻断音频）
            logger.info("[AgentService] 构建 Pipeline...")
            
            # ⭐ 创建 AudioMuteFilter（在 STT 之前阻断音频，防止无 AEC 麦克风自打断）
            from app.services.audio_mute_filter import AudioMuteFilter
            audio_mute_filter = AudioMuteFilter()
            logger.info("[AgentService] AudioMuteFilter 已创建（STT 之前音频阻断）")
            
            logger.info("[AgentService] Pipeline 结构: 音频输入 -> [AudioMuteFilter] -> STT -> 状态管理器 -> 用户聚合器 -> LLM -> TTS -> 音频输出")

            self._pipeline = Pipeline([
                transport.input(),          # 音频输入
                audio_mute_filter,          # ⭐ 静音过滤器（STT 之前阻断）
                stt,                        # 语音识别
                self._state_manager,        # ⭐ 状态管理处理器
                user_aggregator,            # 聚合用户输入（含静音策略）
                llm,                        # 语言模型
                tts,                        # 语音合成
                word_timestamp_processor,   # Cartesia 时间戳/音频监听
                transport.output(),         # 音频输出（播放）
                assistant_aggregator,       # 聚合 AI 回复
            ])
            
            # 创建任务
            self._task = PipelineTask(
                self._pipeline,
                params=PipelineParams(
                    enable_metrics=True,
                    allow_interruptions=True,
                ),
                # ⭐ 禁用 Pipecat 默认空闲超时检测（使用我们自己的 IdleScheduler）
                idle_timeout_secs=None,  # None 表示禁用空闲超时检测
                cancel_on_idle_timeout=False,  # 即使触发也不自动取消
            )
            
            # 事件处理
            @transport.event_handler("on_client_connected")
            async def on_client_connected(transport, client):
                logger.info("[AgentService] 客户端连接，开始对话...")
                await self._task.queue_frames([
                    LLMMessagesAppendFrame(messages=[
                        {"role": "user", "content": "你好！"}
                    ]),
                    LLMRunFrame(),
                ])
            
            @transport.event_handler("on_client_disconnected")
            async def on_client_disconnected(transport, client):
                logger.info("[AgentService] 客户端断开")
                await self._task.queue_frames([EndFrame()])
            
            logger.info("[AgentService] Pipeline 构建完成")
            
        except ImportError as e:
            logger.warning(f"[AgentService] Pipecat 模块未安装，跳过 pipeline 初始化: {e}")
        except Exception as e:
            logger.error(f"[AgentService] Pipeline 初始化失败: {e}")
            import traceback
            traceback.print_exc()

    async def start(self):
        """启动服务
        
        ⭐ 初始化门控机制：
        1. 启动 Pipeline runner
        2. 等待所有关键组件就绪（事件驱动）
        3. 所有组件就绪后才切换到 IDLE 状态
        """
        if not self._initialized:
            await self.initialize()

        if self._running:
            logger.warning("[AgentService] 已在运行中，跳过")
            return

        logger.info("[AgentService] 启动...")

        # 启动帧队列推送
        if self._frame_queue:
            await self._frame_queue.start()

        # 启动 idle 调度器
        if self._idle_scheduler:
            await self._idle_scheduler.start()

        # ⭐ 启动记忆系统调度器（每小时从 Redis 持久化队列写入 PostgreSQL）
        from app.agent.memory.scheduler import start_memory_scheduler
        await start_memory_scheduler()
        logger.info("[AgentService] 记忆系统调度器已启动")

        # ⭐ 启动任务系统（阻塞初始化）
        from app.task_system import get_task_system
        task_system = get_task_system()
        await task_system.start()
        logger.info("[AgentService] 任务系统已启动")

        # ⭐ 启动播报调度器（Redis 队列 + 1s 定时轮询）
        from app.services.playback.redis_repo import get_playback_repository
        from app.services.playback.scheduler import PlaybackScheduler
        
        playback_repo = await get_playback_repository()
        self._playback_scheduler = PlaybackScheduler(
            state_manager=self._state_manager,
            agent_service=self,
            redis_repo=playback_repo,
            check_interval=1.0,  # 1s 检查间隔
        )
        await self._playback_scheduler.start()
        logger.info("[AgentService] 播报调度器已启动（Redis 队列 + 1s 定时轮询）")

        # 启动 pipeline runner（在后台运行）
        if self._task and self._pipeline:
            self._runner = asyncio.create_task(self._run_pipeline())

        # ⭐ 等待所有关键组件就绪（事件驱动，不轮询）
        gate = get_init_gate()
        await gate.wait_all(timeout=30.0)

        # ⭐ 所有组件就绪后才切换到 IDLE
        if self._state_manager:
            await self._state_manager.force_transition(
                AgentState.IDLE,
                reason="all_components_ready"
            )
            logger.info("[AgentService] 状态从 INITING 转换到 IDLE（所有组件已就绪）")
        else:
            # 如果 StateManager 未创建，直接广播状态
            await self.ws_manager.broadcast_status(AgentStatus.IDLE)

        self._running = True
        logger.info("[AgentService] 启动完成")

    async def _run_pipeline(self):
        """运行 pipeline
        
        ⭐ Pipeline 启动后通知初始化门控
        """
        try:
            from pipecat.pipeline.runner import PipelineRunner
            runner = PipelineRunner(handle_sigint=False)
            
            # ⭐ 在后台运行 pipeline，启动后通知门控
            # 注意：runner.run() 会阻塞直到 pipeline 停止
            # 我们需要等待 StartFrame 到达终点后才通知
            
            # 创建一个任务来等待 pipeline 启动
            async def wait_and_notify():
                """等待 Pipeline StartFrame 到达终点后通知门控"""
                try:
                    # PipelineTask 内部有 _wait_for_pipeline_start 方法
                    # 等待 StartFrame 到达 pipeline 终点
                    if self._task and hasattr(self._task, '_wait_for_pipeline_start'):
                        await self._task._wait_for_pipeline_start()
                        logger.info("[AgentService] Pipeline StartFrame 已到达终点")
                    
                    # 通知门控：Pipeline 就绪
                    get_init_gate().mark_ready("pipeline")
                    
                except Exception as e:
                    logger.warning(f"[AgentService] Pipeline 启动等待失败: {e}")
                    # 即使失败也标记就绪，不阻塞启动
                    get_init_gate().mark_ready("pipeline")
            
            # 启动等待任务（与 runner.run 并行）
            asyncio.create_task(wait_and_notify())
            
            # 运行 pipeline（阻塞）
            await runner.run(self._task)
            
        except asyncio.CancelledError:
            logger.info("[AgentService] Pipeline 任务已取消")
        except Exception as e:
            logger.error(f"[AgentService] Pipeline 运行错误: {e}")

    async def stop(self):
        """停止服务"""
        if not self._running:
            return

        logger.info("[AgentService] 停止...")

        # ⭐ 停止播报调度器
        if self._playback_scheduler:
            await self._playback_scheduler.stop()
            logger.info("[AgentService] 播报调度器已停止")

        # 停止 idle 调度器
        if self._idle_scheduler:
            await self._idle_scheduler.stop()

        # 停止帧队列
        if self._frame_queue:
            await self._frame_queue.stop()

        # ⭐ 停止记忆系统调度器
        from app.agent.memory.scheduler import stop_memory_scheduler
        await stop_memory_scheduler()
        logger.info("[AgentService] 记忆系统调度器已停止")

        # 取消 pipeline 任务
        if self._runner:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass

        # 更新状态
        await self.ws_manager.broadcast_status(AgentStatus.IDLE)

        # 重置口型
        if self._lip_sync_service:
            self._lip_sync_service.reset()

        self._running = False
        logger.info("[AgentService] 停止完成")

    # ===== 口型同步 =====

    def _on_cosyvoice_lip_morphs(self, morphs: list, audio_len: int):
        """
        CosyVoice TTS 口型数据回调

        将口型数据写入帧队列。此方法在 TTS 音频流处理线程中被调用，
        需要异步调度到帧队列。

        Args:
            morphs: LipMorph 对象列表
            audio_len: 音频数据长度（字节）
        """
        if not morphs or not self._frame_queue:
            return

        try:
            # 转换为 MorphFrame
            morph_frames = [
                MorphFrame(name=m.name, weight=m.weight)
                for m in morphs
            ]
            # 异步写入帧队列
            asyncio.create_task(
                self._frame_queue.set_lip_morphs(morph_frames, audio_len)
            )
        except Exception as e:
            logger.error(f"[AgentService] CosyVoice 口型回调失败: {e}")

    def _on_tts_finished(self):
        """
        TTS 完成回调（追加闭嘴帧到口型帧池末尾）

        当 CosyVoice 服务端发送 task-finished 事件时触发。
        此时所有音频数据已接收完毕，追加闭嘴帧让口型归零。
        """
        if not self._frame_queue:
            return

        try:
            # 异步追加闭嘴帧到口型帧池末尾
            asyncio.create_task(self._frame_queue.push_lip_reset_frame())
            logger.info("[AgentService] TTS 完成，已追加闭嘴帧到口型帧池末尾")
        except Exception as e:
            logger.error(f"[AgentService] 追加闭嘴帧失败: {e}")

    async def process_tts_audio(self, audio_data: bytes):
        """
        处理 TTS 输出的音频，进行口型分析

        口型数据写入帧队列，由调度器合并到动作帧中统一推送。

        Args:
            audio_data: TTS 音频数据
        """
        if not self._lip_sync_service or not audio_data:
            return

        try:
            # 分析口型
            morphs = self._lip_sync_service.analyze_frame(audio_data)

            # 写入帧队列（由调度器合并到帧中推送）
            if self._frame_queue and morphs:
                morph_frames = [
                    MorphFrame(name=m.name, weight=m.weight)
                    for m in morphs
                ]
                await self._frame_queue.set_lip_morphs(morph_frames)

        except Exception as e:
            logger.error(f"[AgentService] 口型分析失败: {e}")

    # ===== 动作帧推送 =====

    async def load_motion(self, motion_id: str, keyframes_db=None):
        """
        加载动作到帧队列。

        如果提供了 keyframes_db 则直接使用，否则从 DB 查询。

        Args:
            motion_id: 动作 UUID 字符串
            keyframes_db: 可选，已查询的 DB Keyframe 列表
        """
        if not self._frame_queue:
            return

        try:
            from uuid import UUID as _UUID
            from app.database import get_db_session
            from app.services.motion_service import MotionService

            async with get_db_session() as db:
                service = MotionService(db)
                motion = await service.get_motion_by_id(_UUID(motion_id))
                if not motion:
                    logger.warning(f"[AgentService] 动作不存在: {motion_id}")
                    return

                if keyframes_db is None:
                    keyframes_db = await service.get_keyframes(_UUID(motion_id))

                if not keyframes_db:
                    logger.warning(f"[AgentService] 动作无关键帧: {motion_id}")
                    return

                # 转换为 VPDFrame
                vpd_frames = [keyframe_to_vpd(kf) for kf in keyframes_db]

                # 暂停 idle 调度器
                if self._idle_scheduler:
                    self._idle_scheduler.pause()

                # 队首插入到帧队列（高优先级）
                await self._frame_queue.insert_motion_head(
                    motion_id=motion_id,
                    frames=vpd_frames,
                )

                logger.info(
                    f"[AgentService] 加载动作: {motion.display_name or motion.name} "
                    f"({motion_id})"
                )

        except Exception as e:
            logger.error(f"[AgentService] 加载动作失败: {e}")

    async def handle_matched_motion(self, matched_motion: dict):
        """
        处理 LLM 返回的匹配动作。

        由 EmotionalAgentLLMService 在获得 motion match 结果后调用。

        Args:
            matched_motion: {"id": uuid_str, "display_name": str, ...}
        """
        if not matched_motion or "id" not in matched_motion:
            return
        await self.load_motion(str(matched_motion["id"]))

    async def handle_interrupt(self):
        """
        处理打断信号。

        截断帧队列，插入过渡帧，清空口型，清空延迟队列，恢复 idle 调度。
        停止播报，关闭panel。
        ⭐ 通知状态管理器切换到 LISTENING。
        ⭐ 取消所有投机采样请求。
        """
        logger.info("[AgentService] 收到打断信号，停止播报")

        # ⭐ 将打断记录写入聊天记录
        from app.services.chat_history import get_conversation_buffer
        try:
            conversation_buffer = await get_conversation_buffer(user_id="default_user")
            await conversation_buffer.append_user_message(text="打断说话")
            logger.info("[AgentService] 打断已写入聊天记录")
        except Exception as e:
            logger.warning(f"[AgentService] 写入打断记录失败: {e}")

        # ⭐ 取消所有投机采样请求
        try:
            from app.services.speculative_sampler import get_speculative_sampler
            sampler = get_speculative_sampler()
            await sampler.on_interrupt()
            logger.info("[AgentService] 投机采样请求已取消")
        except Exception as e:
            logger.warning(f"[AgentService] 取消投机采样请求失败: {e}")

        # ⭐ 通知状态管理器切换到 LISTENING
        if self._state_manager:
            await self._state_manager.force_to_listening()
            logger.info("[AgentService] 状态管理器已切换到 LISTENING")

        if self._frame_queue:
            # ⭐ interrupt() 内部已经调用了 clear_and_reset() 清空口型并推入归零帧
            await self._frame_queue.interrupt()
            # clear_lip_delay_queue() 是空方法（兼容旧接口），不再需要

        # 清空动作队列
        self._pending_actions.clear()
        self._tts_start_time = None
        self._cartesia_word_anchor_pts_ns = None
        self._cartesia_word_anchor_monotonic = None

        # 重置口型服务状态
        if self._lip_sync_service:
            self._lip_sync_service.reset()

        # ⭐ 使用 PanelManager 强制关闭 panel（打断场景）
        if self._panel_manager:
            await self._panel_manager.force_hide_panel(
                source="handle_interrupt",
                reason="interrupt"
            )
        else:
            await self.ws_manager.broadcast_panel_html({"visible": False})

        # ⭐ 丢弃当前播报任务（由 PlaybackScheduler 管理）
        if self._playback_scheduler:
            self._playback_scheduler.discard_current()

        # 恢复 idle 调度
        if self._idle_scheduler:
            self._idle_scheduler.resume()

        logger.info("[AgentService] 打断处理完成，播报已停止")

    async def handle_speech_end(self):
        """
        TTS 播放结束时调用（由口型帧耗尽回调触发）。

        清空口型，恢复 idle 调度，隐藏 panel。
        重置播报状态。
        
        注意：
        - CosyVoiceTTSService 会发送 TTSStoppedFrame，StateManagerProcessor
          会自动处理状态转换到 IDLE（等待口型帧耗尽）。这里作为兜底机制。
        - 播报队列由 PlaybackScheduler 管理，下一个任务会在 1s 定时循环中
          自动检查并执行（IDLE + 队列有任务）。
        """
        # 状态转换由 StateManagerProcessor 处理，这里只做清理
        if self._state_manager and self._state_manager.is_speaking:
            # 兜底：如果还在 SPEAKING 状态，手动触发 IDLE
            await self._state_manager._transition_to(
                AgentState.IDLE,
                reason="tts_end_lip_empty",
                frame_type="handle_speech_end",
            )
            logger.info("[AgentService] 状态切换到 IDLE（口型帧耗尽）")

        if self._frame_queue:
            await self._frame_queue.clear_lip_morphs()

        # 清空未触发的动作
        if self._pending_actions:
            logger.info(f"[ActionTiming] TTS 结束，清空 {len(self._pending_actions)} 个未触发动作")
            self._pending_actions.clear()
        self._tts_start_time = None
        self._cartesia_word_anchor_pts_ns = None
        self._cartesia_word_anchor_monotonic = None

        if self._lip_sync_service:
            self._lip_sync_service.reset()

        # ⭐ Panel 关闭由前端根据 duration 自动处理，不再后端主动关闭

        # 清空当前播报任务标记（由 PlaybackScheduler 管理）
        if self._playback_scheduler:
            self._playback_scheduler.discard_current()

        logger.info("[AgentService] 播报完成，状态已重置")

        # ⭐ 恢复 idle 调度
        if self._idle_scheduler:
            self._idle_scheduler.resume()

        # ⭐ 注意：下一个播报任务由 PlaybackScheduler 的 1s 定时循环自动处理
        # 不需要在这里手动触发，实现解耦

    async def push_motion_frame(self, motion_id: str, frame_index: int, bone_data: dict):
        """推送动作帧到前端（保留旧接口兼容）"""
        await self.ws_manager.broadcast_motion_frame(motion_id, frame_index, bone_data)

    # ===== 文本消息推送 =====

    async def push_text(self, role: str, content: str):
        """推送文本消息到前端"""
        await self.ws_manager.broadcast_text(role, content)

    async def speak_followup_text(self, content: str, actions: Optional[list[dict]] = None, panel_html: Optional[dict] = None):
        """异步补播一段 assistant 文本。
        
        通过 TTSSpeakFrame 触发 TTS 合成。
        CosyVoiceTTSService 会自动发送 TTSStartedFrame/TTSStoppedFrame，
        StateManagerProcessor 会自动处理状态转换。
        """
        if not content:
            return

        actions = actions or []
        if actions:
            await self.queue_action_structs(actions)

        await self.push_text("assistant", content)

        # ⭐ 使用 PanelManager 显示 panel（播报开始）
        if panel_html:
            if self._panel_manager:
                await self._panel_manager.show_panel(
                    panel_html=panel_html,
                    source="speak_followup_text",
                )
            else:
                # 兜底：直接推送
                if panel_html.get("html"):
                    await self.ws_manager.broadcast_panel_html(panel_html)
                    logger.info("[AgentService] Panel HTML 已推送显示（兜底）")

        # 去除 <a> 标签用于 TTS（保留标签外的文本）
        tts_content = re.sub(r'<a>.*?</a>', '', content, flags=re.DOTALL).strip()
        if tts_content != content:
            logger.debug("[AgentService] TTS 文本已去除 <a> 标签")

        if not self._task or not self._running:
            logger.warning("[AgentService] Pipeline 未运行，跳过补播: %s", tts_content[:80])
            return

        # 状态转换由 TTSSpeakFrame -> TTSStartedFrame -> StateManagerProcessor 自动处理
        # 不需要手动触发 SPEAKING 状态

        try:
            from pipecat.frames.frames import TTSSpeakFrame

            await self._task.queue_frames([
                TTSSpeakFrame(tts_content, append_to_context=True),
            ])
            logger.info("[AgentService] 已注入异步补播文本（TTSSpeakFrame）")
        except Exception as e:
            logger.error(f"[AgentService] 异步补播失败: {e}")

    # ===== 状态管理 =====

    async def set_status(self, status: AgentStatus):
        """设置并广播状态"""
        await self.ws_manager.broadcast_status(status)

    # ===== LLM 响应处理 =====

    def set_on_llm_response(self, callback: Callable[[str], None]):
        """设置 LLM 响应回调"""
        self._on_llm_response = callback

    async def _handle_llm_response(self, text: str):
        """处理 LLM 响应"""
        # 推送文本给前端
        await self.push_text("assistant", text)

        # 回调
        if self._on_llm_response:
            self._on_llm_response(text)

    # ===== 动作结构体队列（用于 TTS 时间戳触发）=====

    async def queue_action_structs(self, actions: list[dict]):
        """将动作结构体加入队列，等待 TTS 字级时间戳触发"""
        if not actions:
            return
        
        # ⭐ 入队时记录动作时长日志
        for action in actions:
            motion = action.get("matched_motion")
            if motion:
                motion_duration = motion.get("duration", 0)
                action_name = action.get("action_name", "未知")
                trigger_char = action.get("trigger_char", "")
                logger.info(
                    f"[ActionTiming] 动作入队: '{action_name}' → "
                    f"motion_duration={motion_duration:.2f}s, trigger='{trigger_char}'"
                )
        
        self._pending_actions.extend(actions)
        logger.info(f"[ActionTiming] 动作结构体入队: {len(actions)} 个, 总计: {len(self._pending_actions)}")

    async def _on_word_timestamps(self, words: list[dict]):
        """处理阿里云 TTS 字级时间戳，触发动作"""
        # ⭐ 记录自然句播放时长（用于验证猜想）
        if words:
            first_word = words[0]
            last_word = words[-1]
            first_begin_time = first_word.get("begin_time", 0)
            last_end_time = last_word.get("end_time", 0)
            sentence_duration_ms = last_end_time - first_begin_time
            sentence_duration_sec = sentence_duration_ms / 1000.0
            sentence_text = "".join(w.get("text", "") for w in words)
            logger.info(
                f"[ActionTiming] 自然句时间戳: duration={sentence_duration_sec:.2f}s, "
                f"words={len(words)}, text='{sentence_text[:30]}...'"
            )
        
        if not self._pending_actions:
            return

        if self._tts_start_time is None:
            logger.debug("[ActionTiming] 未设置 TTS 基准时间，忽略字级时间戳")
            return

        now = time.monotonic()
        elapsed = now - self._tts_start_time

        for action in self._pending_actions[:]:
            trigger_char = action.get("trigger_char")
            if not trigger_char:
                continue
            for word in words:
                if word.get("text") == trigger_char:
                    begin_time_ms = word.get("begin_time", 0)
                    target_sec = begin_time_ms / 1000.0
                    delay_sec = max(target_sec - elapsed, 0)
                    logger.info(
                        f"[ActionTiming][Aliyun] 匹配触发字: {trigger_char}, "
                        f"begin_time={begin_time_ms}ms, 延迟={delay_sec:.2f}s"
                    )
                    await self._schedule_pending_action(action, delay_sec)
                    break

    async def on_tts_audio_frame(self, frame):
        """处理 TTS 音频帧（用于 Cartesia 路径的口型分析）
        
        注意：新的 CosyVoiceTTSService 通过继承 WebsocketTTSService 自动发送
        TTSStartedFrame，StateManagerProcessor 会自动处理状态转换。
        这里只处理 Cartesia 路径的本地口型分析。
        """
        # 设置 TTS 基准时间（用于动作时间戳计算）
        if self._tts_start_time is None and frame.audio:
            self._tts_start_time = time.monotonic()
            logger.info(f"[ActionTiming][{self._tts_provider.upper()}] TTS 开始输出，基准时间已设置")

        # Cartesia 路径使用本地口型分析
        if self._tts_provider == "cartesia" and self._frame_queue and frame.audio:
            try:
                morphs = self._lip_sync_service.analyze_frame(frame.audio)
                if morphs:
                    morph_frames = [
                        MorphFrame(name=m.name, weight=m.weight)
                        for m in morphs
                    ]
                    await self._frame_queue.set_lip_morphs(morph_frames, len(frame.audio))
            except Exception as e:
                logger.debug(f"[LipSync][Cartesia] 分析失败: {e}")

    async def on_tts_text_frame(self, frame):
        """处理 TTS 文本时间戳帧（主要用于 Cartesia 路径）。"""
        if self._tts_provider != "cartesia":
            return
        if not self._pending_actions:
            return

        token_text = (getattr(frame, "text", "") or "").strip()
        if not token_text:
            return

        pts_ns = getattr(frame, "pts", None)
        if not isinstance(pts_ns, int) or pts_ns < 0:
            logger.debug("[ActionTiming][Cartesia] TTSTextFrame 无有效 pts，跳过")
            return

        now = time.monotonic()
        if self._cartesia_word_anchor_pts_ns is None:
            self._cartesia_word_anchor_pts_ns = pts_ns
            self._cartesia_word_anchor_monotonic = now

        elapsed_sec = 0.0
        if self._cartesia_word_anchor_monotonic is not None:
            elapsed_sec = now - self._cartesia_word_anchor_monotonic

        target_sec = (pts_ns - self._cartesia_word_anchor_pts_ns) / 1_000_000_000
        delay_sec = max(target_sec - elapsed_sec, 0)

        for action in self._pending_actions[:]:
            trigger_char = action.get("trigger_char")
            if not trigger_char:
                continue
            if trigger_char in token_text:
                logger.info(
                    f"[ActionTiming][Cartesia] 匹配触发字: {trigger_char}, "
                    f"token={token_text}, 目标={target_sec:.3f}s, 延迟={delay_sec:.3f}s"
                )
                await self._schedule_pending_action(action, delay_sec)

    async def _schedule_pending_action(self, action: dict, delay_sec: float):
        """按延迟调度动作并从 pending 列表移除。"""
        matched_motion = action.get("matched_motion")
        motion_id = matched_motion.get("id") if matched_motion else None
        action_name = action.get("action_name")
        if not motion_id:
            return

        async def trigger_action():
            await asyncio.sleep(delay_sec)
            try:
                await self.load_motion(str(motion_id))
                logger.info(f"[ActionTiming] 已触发动作: {action_name}")
            except Exception as e:
                logger.error(f"[ActionTiming] 触发动作失败: {e}")

        asyncio.ensure_future(trigger_action())
        if action in self._pending_actions:
            self._pending_actions.remove(action)

    # ===== 播报队列（已移除，由 PlaybackScheduler 管理）=====
    
    # ⭐ 注意：播报队列已从内存移至 Redis，由 PlaybackScheduler 管理。
    # - 入队：由 OpenClaw TaskManager 调用 PlaybackQueueRepository.enqueue()
    # - 出队：由 PlaybackScheduler 的 1s 定时循环自动检查并执行
    # - 打断丢弃：由 PlaybackScheduler.discard_current() 处理
    # 
    # 旧的 enqueue_playback / _pop_and_play / _on_buffer_empty 方法已删除。


# 全局实例
_agent_service: Optional['AgentService'] = None


def get_agent_service() -> 'AgentService':
    """获取全局 Agent 服务实例"""
    global _agent_service
    if _agent_service is None:
        from app.services.agent_ws_manager import agent_ws_manager
        _agent_service = AgentService(ws_manager=agent_ws_manager)
    return _agent_service


async def initialize_agent_service() -> 'AgentService':
    """初始化全局 Agent 服务"""
    global _agent_service
    if _agent_service is None:
        from app.services.agent_ws_manager import agent_ws_manager
        _agent_service = AgentService(ws_manager=agent_ws_manager)
    await _agent_service.initialize()
    return _agent_service


async def start_agent_service() -> 'AgentService':
    """启动全局 Agent 服务"""
    service = await initialize_agent_service()
    await service.start()
    return service


async def stop_agent_service():
    """停止全局 Agent 服务"""
    global _agent_service
    if _agent_service:
        await _agent_service.stop()
        _agent_service = None