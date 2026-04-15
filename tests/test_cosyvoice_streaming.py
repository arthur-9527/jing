#!/usr/bin/env python3
"""
测试 CosyVoice 流式 TTS 实现

验证新的 WebsocketTTSService 架构：
1. 继承 WebsocketTTSService，使用后台接收循环
2. run_tts 只发送增量文本，yield None
3. 音频帧通过 append_to_audio_context 推送
4. 支持打断和字级时间戳
"""

import asyncio
import os
import sys
from pathlib import Path
from loguru import logger

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings


async def test_cosyvoice_streaming():
    """测试 CosyVoice 流式 TTS"""
    
    # 获取 API Key
    api_key = os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY
    if not api_key:
        logger.error("未配置 DASHSCOPE_API_KEY")
        return
    
    logger.info("开始测试 CosyVoice 流式 TTS...")
    
    # 创建 TTS 服务
    from app.services.tts.cosyvoice_ws import CosyVoiceTTSService
    
    tts = CosyVoiceTTSService(
        api_key=api_key,
        model=settings.COSYVOICE_WS_MODEL,
        clone_voice_audio_path=settings.COSYVOICE_WS_CLONE_AUDIO,
        sample_rate=settings.TTS_SAMPLE_RATE,
        enable_lipsync=False,  # 测试时禁用口型
    )
    
    # 设置回调
    audio_frames_received = []
    word_timestamps_received = []
    
    def on_lip_morphs(morphs, audio_len):
        logger.debug(f"收到口型数据: {len(morphs)} 个, 音频长度: {audio_len}")
    
    def on_word_timestamps(words):
        logger.info(f"收到字级时间戳: {len(words)} 个字")
        word_timestamps_received.extend(words)
    
    tts.set_on_lip_morphs(on_lip_morphs)
    tts.set_on_word_timestamps(on_word_timestamps)
    
    # 初始化
    logger.info("初始化 TTS 服务...")
    await tts.ensure_initialized()
    logger.info("TTS 服务初始化完成")
    
    # 测试文本（分段测试流式能力）
    test_texts = [
        "你好，",
        "我是一个流式语音合成测试。",
        "这段话会分段发送到服务端。",
    ]
    
    # 模拟 Pipecat 框架的调用方式
    logger.info("开始流式合成测试...")
    
    # 创建模拟的 context
    context_id = "test-context-001"
    
    # 模拟 on_turn_context_created（发送 run-task）
    await tts.on_turn_context_created(context_id)
    logger.info(f"创建任务上下文: {context_id}")
    
    # 等待 task-started
    await asyncio.sleep(0.5)
    
    # 分段发送文本（流式）
    for i, text in enumerate(test_texts):
        logger.info(f"发送文本片段 {i+1}: '{text}'")
        
        # 调用 run_tts
        generator = tts.run_tts(text, context_id)
        
        # 消费 generator（run_tts yield None，音频通过后台循环推送）
        async for frame in generator:
            if frame is None:
                logger.debug(f"文本片段 {i+1} 已发送，等待音频...")
            else:
                logger.debug(f"收到帧: {frame.__class__.__name__}")
        
        # 等待一小段时间（模拟流式输入）
        await asyncio.sleep(0.3)
    
    # 发送 finish-task
    logger.info("发送 finish-task...")
    await tts.flush_audio(context_id)
    
    # 等待音频完成
    logger.info("等待音频合成完成...")
    await asyncio.sleep(5.0)
    
    # 统计结果
    logger.info(f"测试完成!")
    logger.info(f"收到字级时间戳: {len(word_timestamps_received)} 个")
    
    # 打印时间戳详情
    if word_timestamps_received:
        logger.info("字级时间戳详情:")
        for w in word_timestamps_received[:10]:
            logger.info(f"  '{w.get('text')}' @ {w.get('begin_time')}ms")
    
    # 清理
    await tts._disconnect()
    logger.info("测试完成，服务已断开")


async def test_interruption():
    """测试打断功能"""
    
    api_key = os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY
    if not api_key:
        logger.error("未配置 DASHSCOPE_API_KEY")
        return
    
    logger.info("开始测试打断功能...")
    
    from app.services.tts.cosyvoice_ws import CosyVoiceTTSService
    
    tts = CosyVoiceTTSService(
        api_key=api_key,
        model=settings.COSYVOICE_WS_MODEL,
        clone_voice_audio_path=settings.COSYVOICE_WS_CLONE_AUDIO,
        sample_rate=settings.TTS_SAMPLE_RATE,
        enable_lipsync=False,
    )
    
    await tts.ensure_initialized()
    
    context_id = "test-interrupt-001"
    await tts.on_turn_context_created(context_id)
    
    # 发送长文本
    long_text = "这是一段很长的话，用来测试打断功能。如果打断成功，剩余的音频应该不会继续播放。"
    
    generator = tts.run_tts(long_text, context_id)
    async for frame in generator:
        pass
    
    # 等待一小段时间
    await asyncio.sleep(1.0)
    
    # 触发打断
    logger.info("触发打断...")
    await tts.on_audio_context_interrupted(context_id)
    
    # 验证清理
    logger.info(f"任务上下文是否已清理: {context_id not in tts._task_contexts}")
    
    await tts._disconnect()
    logger.info("打断测试完成")


async def main():
    """主测试入口"""
    
    # 配置日志
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")
    
    # 运行测试
    await test_cosyvoice_streaming()
    
    print("\n" + "="*50 + "\n")
    
    await test_interruption()


if __name__ == "__main__":
    asyncio.run(main())