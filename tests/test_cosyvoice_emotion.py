#!/usr/bin/env python3
"""
测试 CosyVoice 情绪合成功能

验证阿里云 DashScope CosyVoice WebSocket API 是否支持情绪参数：
1. 测试基础功能（当前配置）
2. 测试 instruct_text 参数
3. 测试不同情绪指令

参考文档：
- https://help.aliyun.com/zh/dashscope/developer-reference/cosyvoice-api
- 开源 CosyVoice 支持通过 instruct_text 控制情绪
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings

# WebSocket URL
COSYVOICE_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"


async def test_cosyvoice_emotion():
    """测试 CosyVoice 情绪合成功能"""
    
    # 动态导入 websockets
    try:
        from websockets.asyncio.client import connect as websocket_connect
    except ImportError:
        logger.error("需要安装 websockets: pip install websockets")
        return
    
    # 获取 API Key
    api_key = os.getenv("DASHSCOPE_API_KEY") or settings.DASHSCOPE_API_KEY
    if not api_key:
        logger.error("未配置 DASHSCOPE_API_KEY")
        return
    
    # 获取克隆音色
    clone_voice_audio_path = settings.COSYVOICE_WS_CLONE_AUDIO
    if not clone_voice_audio_path:
        logger.error("未配置 COSYVOICE_WS_CLONE_AUDIO")
        return
    
    # 获取或创建克隆音色 ID
    voice_id = await get_or_create_voice_id(api_key, clone_voice_audio_path)
    if not voice_id:
        logger.error("无法获取克隆音色 ID")
        return
    
    logger.info(f"使用克隆音色 ID: {voice_id}")
    
    # 测试用例
    test_cases = [
        {
            "name": "基础测试（无情绪参数）",
            "text": "你好，很高兴见到你。",
            "extra_params": {},
        },
        {
            "name": "测试 instruct_text - 开心",
            "text": "今天天气真好，我很开心！",
            "extra_params": {
                "instruct_text": "开心的语气",
            },
        },
        {
            "name": "测试 instruct_text - 悲伤",
            "text": "今天发生了一些让人难过的事情。",
            "extra_params": {
                "instruct_text": "悲伤的语气",
            },
        },
        {
            "name": "测试 instruct_text - 生气",
            "text": "你怎么能这样做呢！",
            "extra_params": {
                "instruct_text": "生气的语气",
            },
        },
        {
            "name": "测试 instruct_text - 英文指令",
            "text": "I'm so happy to see you today!",
            "extra_params": {
                "instruct_text": "Happy and energetic tone",
            },
        },
        {
            "name": "测试 style 参数",
            "text": "大家好，欢迎来到直播间！",
            "extra_params": {
                "style": "happy",
            },
        },
        {
            "name": "测试 emotion 参数",
            "text": "我真的很惊讶！",
            "extra_params": {
                "emotion": "surprised",
            },
        },
    ]
    
    # 连接 WebSocket
    logger.info("连接 CosyVoice WebSocket...")
    async with websocket_connect(
        COSYVOICE_WS_URL,
        additional_headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "CosyVoice-Emotion-Test/1.0",
        },
    ) as ws:
        logger.info("WebSocket 已连接")
        
        for i, test_case in enumerate(test_cases):
            logger.info(f"\n{'='*60}")
            logger.info(f"测试 {i+1}/{len(test_cases)}: {test_case['name']}")
            logger.info(f"文本: {test_case['text']}")
            logger.info(f"额外参数: {test_case['extra_params']}")
            
            result = await run_single_test(
                ws=ws,
                task_id=f"test-emotion-{i}",
                voice_id=voice_id,
                text=test_case["text"],
                extra_params=test_case["extra_params"],
                model=settings.COSYVOICE_WS_MODEL,
            )
            
            if result["success"]:
                logger.info(f"✅ 测试成功！收到 {result['audio_bytes']} 字节音频")
                if result.get("warning"):
                    logger.warning(f"⚠️ 警告: {result['warning']}")
            else:
                logger.error(f"❌ 测试失败: {result['error']}")
            
            # 等待一下，避免请求过快
            await asyncio.sleep(1.0)
    
    logger.info("\n测试完成！")


async def run_single_test(
    ws,
    task_id: str,
    voice_id: str,
    text: str,
    extra_params: dict,
    model: str,
) -> dict:
    """运行单个测试"""
    
    # 构建 run-task 命令
    parameters = {
        "text_type": "PlainText",
        "voice": voice_id,
        "format": "pcm",
        "sample_rate": 16000,
        "word_timestamp_enabled": True,
    }
    
    # 添加额外参数
    parameters.update(extra_params)
    
    run_task_cmd = {
        "header": {
            "action": "run-task",
            "task_id": task_id,
            "streaming": "duplex"
        },
        "payload": {
            "task_group": "audio",
            "task": "tts",
            "function": "SpeechSynthesizer",
            "model": model,
            "parameters": parameters,
            "input": {}
        }
    }
    
    # 发送 run-task
    await ws.send(json.dumps(run_task_cmd))
    logger.debug(f"发送 run-task: {json.dumps(run_task_cmd, ensure_ascii=False, indent=2)}")
    
    # 发送文本
    continue_cmd = {
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
    await ws.send(json.dumps(continue_cmd))
    
    # 发送 finish-task
    finish_cmd = {
        "header": {
            "action": "finish-task",
            "task_id": task_id,
            "streaming": "duplex"
        },
        "payload": {
            "input": {}
        }
    }
    await ws.send(json.dumps(finish_cmd))
    
    # 接收响应
    audio_bytes = 0
    task_finished = False
    task_failed = False
    error_message = None
    warning_message = None
    
    while not task_finished and not task_failed:
        try:
            message = await asyncio.wait_for(ws.recv(), timeout=30.0)
            
            if isinstance(message, str):
                event = json.loads(message)
                event_name = event.get("header", {}).get("event")
                
                if event_name == "task-started":
                    logger.debug("任务已启动")
                    
                elif event_name == "result-generated":
                    # 收到音频数据会在二进制消息中
                    pass
                    
                elif event_name == "task-finished":
                    task_finished = True
                    usage = event.get("payload", {}).get("usage", {})
                    logger.debug(f"任务完成，计费字符: {usage.get('characters', 0)}")
                    
                elif event_name == "task-failed":
                    task_failed = True
                    error_message = event.get("header", {}).get("error_message", "Unknown error")
                    error_code = event.get("header", {}).get("error_code", "Unknown")
                    logger.error(f"任务失败: [{error_code}] {error_message}")
                    
                    # 检查是否是参数不支持
                    if "instruct_text" in extra_params or "style" in extra_params or "emotion" in extra_params:
                        if "parameter" in error_message.lower() or "unsupported" in error_message.lower():
                            warning_message = f"参数可能不支持: {extra_params}"
                    
            elif isinstance(message, bytes):
                audio_bytes += len(message)
                
        except asyncio.TimeoutError:
            task_failed = True
            error_message = "接收超时"
            break
    
    return {
        "success": task_finished,
        "audio_bytes": audio_bytes,
        "error": error_message,
        "warning": warning_message,
    }


async def get_or_create_voice_id(api_key: str, audio_path: str) -> Optional[str]:
    """获取或创建克隆音色 ID"""
    
    if not Path(audio_path).exists():
        logger.error(f"音频文件不存在: {audio_path}")
        return None
    
    # 检查缓存
    from app.services.tts.voice_enrollment import (
        VoiceEnrollmentService,
        load_voice_cache,
        save_voice_cache,
        compute_audio_md5,
    )
    
    audio_bytes = Path(audio_path).read_bytes()
    audio_md5 = compute_audio_md5(audio_bytes)
    
    cache = load_voice_cache()
    if audio_md5 in cache:
        cached_data = cache[audio_md5]
        voice_id = cached_data.get("voice_id")
        
        # 验证音色状态
        service = VoiceEnrollmentService(api_key)
        status = await service.get_voice_status(voice_id)
        await service.close()
        
        if status and status.get("state") == "ready":
            logger.info(f"使用缓存的音色 ID: {voice_id}")
            return voice_id
        else:
            del cache[audio_md5]
            save_voice_cache(cache)
    
    # 创建新音色
    logger.info("创建新的克隆音色...")
    service = VoiceEnrollmentService(api_key)
    
    # 生成前缀
    import hashlib
    import re
    audio_hash = hashlib.md5(audio_bytes).hexdigest()[:8]
    name = Path(audio_path).stem
    clean_name = re.sub(r"[^a-zA-Z0-9]", "", name)[:6]
    prefix = f"{clean_name}{audio_hash[:10-len(clean_name)]}" if clean_name else f"v{audio_hash[:9]}"
    
    voice_id = await service.create_voice(
        target_model=settings.COSYVOICE_WS_MODEL,
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
    
    return voice_id


async def main():
    """主入口"""
    
    # 配置日志
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    await test_cosyvoice_emotion()


if __name__ == "__main__":
    asyncio.run(main())