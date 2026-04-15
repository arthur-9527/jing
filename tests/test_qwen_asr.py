#!/usr/bin/env python3
"""
千问 ASR 服务测试脚本

测试 QwenASRService 的基本功能：
1. WebSocket 连接
2. 会话创建
3. 音频发送
4. 识别结果接收

使用方法：
    python tests/test_qwen_asr.py [audio_file.pcm]

如果没有提供音频文件，将生成测试音频（静音）。
"""

import asyncio
import os
import sys
import wave
import struct
import tempfile
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 加载 .env 文件
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from loguru import logger

from app.services.stt.qwen_asr import QwenASRService, QwenASRSettings


def generate_test_audio(output_path: str, duration_sec: float = 3.0, sample_rate: int = 16000):
    """生成测试音频文件（静音）
    
    Args:
        output_path: 输出文件路径
        duration_sec: 音频时长（秒）
        sample_rate: 采样率
    """
    num_samples = int(duration_sec * sample_rate)
    
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        
        # 生成静音
        silence = struct.pack("<" + "h" * num_samples, *([0] * num_samples))
        wf.writeframes(silence)
    
    logger.info(f"[Test] 生成测试音频: {output_path} ({duration_sec}s)")


async def test_qwen_asr(audio_file: str = None):
    """测试千问 ASR 服务
    
    Args:
        audio_file: PCM 音频文件路径（可选）
    """
    # 获取 API Key
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        logger.error("[Test] 请设置 DASHSCOPE_API_KEY 环境变量")
        return
    
    logger.info("[Test] 开始测试千问 ASR 服务...")
    
    # 准备测试音频
    if audio_file and Path(audio_file).exists():
        # 使用提供的音频文件
        test_audio_path = audio_file
        logger.info(f"[Test] 使用音频文件: {test_audio_path}")
    else:
        # 生成测试音频（静音）
        test_audio_path = tempfile.mktemp(suffix=".wav")
        generate_test_audio(test_audio_path, duration_sec=5.0)
    
    # 读取音频数据
    with open(test_audio_path, "rb") as f:
        if test_audio_path.endswith(".wav"):
            # WAV 文件需要跳过头部
            with wave.open(test_audio_path, "rb") as wf:
                audio_data = wf.readframes(wf.getnframes())
        else:
            # 假设是原始 PCM
            audio_data = f.read()
    
    logger.info(f"[Test] 音频数据大小: {len(audio_data)} bytes")
    
    # 创建 ASR 服务
    asr_service = QwenASRService(
        api_key=api_key,
        model="qwen3-asr-flash-realtime",
        sample_rate=16000,
        language="zh",
        settings=QwenASRSettings(
            enable_server_vad=True,
            vad_threshold=0.0,
            vad_silence_duration_ms=400,
        ),
    )
    
    # 收集识别结果
    results = []
    
    # 注册事件处理器
    @asr_service.event_handler("on_connected")
    async def on_connected(service):
        logger.info("[Test Event] 已连接到千问 ASR")
    
    @asr_service.event_handler("on_disconnected")
    async def on_disconnected(service):
        logger.info("[Test Event] 已断开连接")
    
    @asr_service.event_handler("on_connection_error")
    async def on_connection_error(service, error):
        logger.error(f"[Test Event] 连接错误: {error}")
    
    @asr_service.event_handler("on_speech_started")
    async def on_speech_started(service):
        logger.info("[Test Event] 检测到语音开始")
    
    @asr_service.event_handler("on_speech_stopped")
    async def on_speech_stopped(service):
        logger.info("[Test Event] 检测到语音停止")

    try:
        # 启动服务
        from pipecat.frames.frames import StartFrame
        await asr_service.start(StartFrame())
        
        # 分批发送音频（模拟实时流）
        chunk_size = 3200  # 100ms @ 16kHz
        num_chunks = len(audio_data) // chunk_size
        
        logger.info(f"[Test] 开始发送音频，共 {num_chunks} 个分片...")
        
        for i in range(0, len(audio_data), chunk_size):
            chunk = audio_data[i:i + chunk_size]
            if len(chunk) < chunk_size:
                # 填充最后一个分片
                chunk += b'\x00' * (chunk_size - len(chunk))
            
            # 发送音频
            async for frame in asr_service.run_stt(chunk):
                if frame:
                    results.append(frame)
            
            # 模拟实时发送间隔
            await asyncio.sleep(0.1)  # 100ms
            
            if (i // chunk_size) % 10 == 0:
                logger.debug(f"[Test] 已发送 {i // chunk_size + 1}/{num_chunks} 个分片")
        
        logger.info("[Test] 音频发送完成")
        
        # 等待识别结果
        logger.info("[Test] 等待识别结果...")
        await asyncio.sleep(3.0)  # 等待 3 秒
        
    except Exception as e:
        logger.error(f"[Test] 测试失败: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # 停止服务
        from pipecat.frames.frames import EndFrame
        await asr_service.stop(EndFrame())
        
        # 清理临时文件
        if not audio_file and test_audio_path.startswith(tempfile.gettempdir()):
            try:
                Path(test_audio_path).unlink()
            except:
                pass
    
    # 打印结果
    logger.info(f"[Test] 测试完成，收到 {len(results)} 个识别结果")
    for i, result in enumerate(results):
        logger.info(f"[Test] 结果 {i + 1}: {result}")
    
    return results


async def test_connection_only():
    """仅测试连接功能（不发送音频）"""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        logger.error("[Test] 请设置 DASHSCOPE_API_KEY 环境变量")
        return
    
    logger.info("[Test] 测试 WebSocket 连接...")
    
    asr_service = QwenASRService(
        api_key=api_key,
        model="qwen3-asr-flash-realtime",
        sample_rate=16000,
        language="zh",
    )
    
    try:
        # 手动初始化 TaskManager（通过 setup）
        from pipecat.processors.frame_processor import FrameProcessorSetup
        from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams
        from pipecat.clocks.system_clock import SystemClock
        
        clock = SystemClock()
        task_manager = TaskManager()
        task_manager.setup(TaskManagerParams(loop=asyncio.get_event_loop()))
        setup = FrameProcessorSetup(
            clock=clock,
            task_manager=task_manager,
            observer=None,
        )
        await asr_service.setup(setup)
        
        # 启动服务
        from pipecat.frames.frames import StartFrame
        await asr_service.start(StartFrame())
        
        logger.info("[Test] 连接成功，等待 5 秒...")
        await asyncio.sleep(5.0)
        
    except Exception as e:
        logger.error(f"[Test] 连接失败: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        from pipecat.frames.frames import EndFrame
        await asr_service.stop(EndFrame())
        await asr_service.cleanup()
    
    logger.info("[Test] 连接测试完成")


def main():
    """主入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="测试千问 ASR 服务")
    parser.add_argument("audio_file", nargs="?", help="PCM/WAV 音频文件路径")
    parser.add_argument("--connection-only", action="store_true", help="仅测试连接")
    parser.add_argument("--debug", action="store_true", help="启用调试日志")
    
    args = parser.parse_args()
    
    # 配置日志
    log_level = "DEBUG" if args.debug else "INFO"
    logger.remove()
    logger.add(sys.stderr, level=log_level, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>")
    
    # 运行测试
    if args.connection_only:
        asyncio.run(test_connection_only())
    else:
        asyncio.run(test_qwen_asr(args.audio_file))


if __name__ == "__main__":
    main()