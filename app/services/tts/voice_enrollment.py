#!/usr/bin/env python3
"""
阿里云 CosyVoice 音色克隆 HTTP API 实现

基于阿里云 DashScope CosyVoice WebSocket API 实现音色克隆功能。
通过 HTTP API 管理克隆音色（创建、查询、删除），支持 Base64 上传。

参考文档：https://help.aliyun.com/zh/dashscope/developer-reference/use-voice-enrollment-api

音色缓存机制：
- 音色 ID 会缓存到本地 JSON 文件
- 系统重启后可直接读取缓存，无需重新查询/创建
- 缓存基于音频内容 MD5 Hash，相同音频自动复用
"""

import base64
import hashlib
import httpx
import json
import re
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger

# 音色缓存文件路径
VOICE_CACHE_FILE = Path("/tmp/cosyvoice_voice_cache.json")


def load_voice_cache() -> Dict[str, any]:
    """加载音色缓存
    
    Returns:
        缓存字典，格式：{audio_md5: {"voice_id": str, "created_at": str, "prefix": str}}
    """
    if not VOICE_CACHE_FILE.exists():
        return {}
    
    try:
        with open(VOICE_CACHE_FILE, "r") as f:
            cache = json.load(f)
        logger.debug(f"[VoiceEnrollment] 加载音色缓存：{len(cache)} 条记录")
        return cache
    except Exception as e:
        logger.warning(f"[VoiceEnrollment] 加载缓存失败：{e}")
        return {}


def save_voice_cache(cache: Dict[str, any]) -> None:
    """保存音色缓存
    
    Args:
        cache: 缓存字典
    """
    try:
        with open(VOICE_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
        logger.debug(f"[VoiceEnrollment] 保存音色缓存：{len(cache)} 条记录")
    except Exception as e:
        logger.warning(f"[VoiceEnrollment] 保存缓存失败：{e}")


def compute_audio_md5(audio_bytes: bytes) -> str:
    """计算音频内容的 MD5 Hash
    
    Args:
        audio_bytes: 音频文件字节数据
    
    Returns:
        MD5 Hash 字符串（32 位）
    """
    return hashlib.md5(audio_bytes).hexdigest()


# Voice Enrollment API 端点 - 正确的 API URL
VOICE_ENROLLMENT_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"

# 模型名称 - 固定为 voice-enrollment
VOICE_ENROLLMENT_MODEL = "voice-enrollment"


class VoiceEnrollmentError(Exception):
    """音色克隆错误异常"""
    pass


class VoiceEnrollmentService:
    """音色克隆服务 (HTTP API 实现)
    
    管理克隆音色的创建、查询和删除。
    使用阿里云 DashScope Voice Enrollment API，支持 Base64 上传。
    
    注意：
    - 音色克隆使用 HTTP API，不是 WebSocket
    - 创建的音色 ID 格式：{model}-{prefix}
    - 创建音色可能需要几分钟时间
    - 创建后音色会自动注册，可以在 TTS 中使用
    """

    def __init__(self, api_key: str):
        """初始化音色克隆服务
        
        Args:
            api_key: 阿里云 DashScope API Key
        """
        self._api_key = api_key
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        return self._client

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def list_voices(
        self, 
        prefix: Optional[str] = None, 
        page_index: int = 0, 
        page_size: int = 100
    ) -> List[Dict]:
        """列出所有已创建的音色
        
        Args:
            prefix: 音色 ID 前缀过滤
            page_index: 页码（0-based）
            page_size: 每页数量
        
        Returns:
            音色列表，每个元素包含 voice_id 等信息
        
        Raises:
            VoiceEnrollmentError: API 调用失败
        """
        try:
            client = await self._get_client()
            
            # 构建请求体 - 使用 POST 方法
            payload = {
                "model": VOICE_ENROLLMENT_MODEL,
                "input": {
                    "action": "list_voice",
                    "page_index": page_index,
                    "page_size": page_size,
                }
            }
            
            # prefix 限制为不超过 10 个字符，只允许数字和英文字母
            if prefix:
                clean_prefix = re.sub(r"[^a-zA-Z0-9]", "", prefix)[:10]
                payload["input"]["prefix"] = clean_prefix
            
            logger.debug(f"[VoiceEnrollment] 列出音色: prefix={prefix}, page={page_index}")
            
            response = await client.post(
                VOICE_ENROLLMENT_API_URL,
                headers=self._headers,
                json=payload,
            )
            
            if response.status_code != 200:
                error_msg = f"API 返回错误: {response.status_code} - {response.text}"
                logger.error(f"[VoiceEnrollment] 列出音色失败: {error_msg}")
                raise VoiceEnrollmentError(error_msg)
            
            result = response.json()
            
            # 调试：打印完整响应
            logger.debug(f"[VoiceEnrollment] API 响应: {result}")
            
            # 解析响应 - 正确的格式是 output.voice_list
            voices = result.get("output", {}).get("voice_list", [])
            
            logger.debug(f"[VoiceEnrollment] 列出音色: {len(voices)} 个")
            return voices
            
        except httpx.HTTPError as e:
            logger.error(f"[VoiceEnrollment] HTTP 错误: {e}")
            raise VoiceEnrollmentError(f"HTTP 错误: {e}")
        except Exception as e:
            logger.error(f"[VoiceEnrollment] 列出音色失败: {e}")
            raise VoiceEnrollmentError(f"列出音色失败: {e}")

    async def find_voice_by_prefix(
        self, 
        model: str, 
        prefix: str
    ) -> Optional[str]:
        """根据前缀查找已存在的音色
        
        Args:
            model: TTS 模型名称
            prefix: 音色前缀
        
        Returns:
            匹配的 voice_id，若未找到则返回 None
        """
        try:
            # 构建目标前缀
            target_prefix = f"{model}-{prefix}"
            logger.debug(f"[VoiceEnrollment] 查找音色前缀: {target_prefix}")
            
            # 列出所有音色
            voices = await self.list_voices()
            
            # 匹配音色 ID
            for voice in voices:
                voice_id = voice.get("voice_id", "")
                if voice_id.startswith(target_prefix):
                    logger.info(f"[VoiceEnrollment] 找到匹配音色: {voice_id}")
                    return voice_id
            
            logger.debug(f"[VoiceEnrollment] 未找到匹配音色: {target_prefix}")
            return None
            
        except Exception as e:
            logger.error(f"[VoiceEnrollment] 查找音色失败: {e}")
            return None

    async def create_voice(
        self,
        target_model: str,
        prefix: str,
        audio_base64: Optional[str] = None,
        audio_bytes: Optional[bytes] = None,
    ) -> str:
        """创建新的克隆音色
        
        Args:
            target_model: TTS 模型名称（如 "cosyvoice-v3.5-plus"）
            prefix: 音色前缀（用于生成 voice_id）
            audio_base64: Base64 编码的音频数据
            audio_bytes: 原始音频字节（会转为 base64）
        
        Returns:
            新创建的 voice_id
        
        Raises:
            VoiceEnrollmentError: 创建失败
        
        Note:
            - 创建音色是异步的，可能需要几分钟
            - 创建后音色会自动注册到账户
            - 可以在 TTS WebSocket 中通过 voice 参数使用
        """
        try:
            logger.info(f"[VoiceEnrollment] 创建音色: model={target_model}, prefix={prefix}")
            
            # 处理音频数据
            if audio_bytes and not audio_base64:
                audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
                logger.debug(f"[VoiceEnrollment] Base64 编码完成: {len(audio_base64)} 字符")
            
            if not audio_base64:
                raise ValueError("必须提供 audio_base64 或 audio_bytes")
            
            # 检查 Base64 数据大小
            size_mb = len(audio_base64) / (1024 * 1024)
            logger.debug(f"[VoiceEnrollment] Base64 数据大小: {size_mb:.2f} MB")
            
            if size_mb > 10:
                logger.warning(f"[VoiceEnrollment] Base64 数据较大 ({size_mb:.2f} MB)，建议 < 10MB")
            
            client = await self._get_client()
            
            # 构建请求体 - 使用正确的 API 格式
            payload = {
                "model": VOICE_ENROLLMENT_MODEL,
                "input": {
                    "action": "create_voice",
                    "target_model": target_model,
                    "prefix": prefix,
                    "url": f"data:audio/mp3;base64,{audio_base64}",  # Base64 URL 格式
                }
            }
            
            response = await client.post(
                VOICE_ENROLLMENT_API_URL,
                headers=self._headers,
                json=payload,
            )
            
            if response.status_code != 200:
                error_msg = f"API 返回错误: {response.status_code} - {response.text}"
                logger.error(f"[VoiceEnrollment] 创建音色失败: {error_msg}")
                raise VoiceEnrollmentError(error_msg)
            
            result = response.json()
            
            # 解析响应
            output = result.get("output", {})
            voice_id = output.get("voice_id")
            
            if not voice_id:
                raise VoiceEnrollmentError("响应中缺少 voice_id")
            
            logger.info(f"[VoiceEnrollment] 音色创建成功: {voice_id}")
            return voice_id
            
        except httpx.HTTPError as e:
            logger.error(f"[VoiceEnrollment] HTTP 错误: {e}")
            raise VoiceEnrollmentError(f"HTTP 错误: {e}")
        except Exception as e:
            logger.error(f"[VoiceEnrollment] 创建音色失败: {e}")
            raise VoiceEnrollmentError(f"创建音色失败: {e}")

    async def get_voice_status(self, voice_id: str) -> Optional[Dict]:
        """获取音色状态
        
        Args:
            voice_id: 音色 ID
        
        Returns:
            音色状态信息
        """
        try:
            client = await self._get_client()
            
            # 构建请求体
            payload = {
                "model": VOICE_ENROLLMENT_MODEL,
                "input": {
                    "action": "query_voice",
                    "voice_id": voice_id,
                }
            }
            
            response = await client.post(
                VOICE_ENROLLMENT_API_URL,
                headers=self._headers,
                json=payload,
            )
            
            if response.status_code != 200:
                logger.error(f"[VoiceEnrollment] 获取音色状态失败: {response.status_code} - {response.text}")
                return None
            
            result = response.json()
            return result.get("output", {})
            
        except Exception as e:
            logger.error(f"[VoiceEnrollment] 获取音色状态失败: {e}")
            return None

    async def delete_voice(self, voice_id: str) -> bool:
        """删除克隆音色
        
        Args:
            voice_id: 要删除的音色 ID
        
        Returns:
            是否删除成功
        
        Raises:
            VoiceEnrollmentError: 删除失败
        """
        try:
            logger.info(f"[VoiceEnrollment] 删除音色: {voice_id}")
            
            client = await self._get_client()
            
            # 构建请求体
            payload = {
                "model": VOICE_ENROLLMENT_MODEL,
                "input": {
                    "action": "delete_voice",
                    "voice_id": voice_id,
                }
            }
            
            response = await client.post(
                VOICE_ENROLLMENT_API_URL,
                headers=self._headers,
                json=payload,
            )
            
            if response.status_code not in (200, 204):
                error_msg = f"API 返回错误: {response.status_code} - {response.text}"
                logger.error(f"[VoiceEnrollment] 删除音色失败: {error_msg}")
                raise VoiceEnrollmentError(error_msg)
            
            logger.info(f"[VoiceEnrollment] 音色删除成功: {voice_id}")
            return True
            
        except httpx.HTTPError as e:
            logger.error(f"[VoiceEnrollment] HTTP 错误: {e}")
            raise VoiceEnrollmentError(f"HTTP 错误: {e}")
        except Exception as e:
            logger.error(f"[VoiceEnrollment] 删除音色失败: {e}")
            raise VoiceEnrollmentError(f"删除音色失败: {e}")


# ============ 便捷函数 ============

def generate_prefix_from_audio(audio_bytes: bytes, custom_name: Optional[str] = None) -> str:
    """从音频内容生成音色前缀
    
    基于音频内容的 MD5 Hash 生成唯一前缀，确保相同音频自动复用。
    
    Args:
        audio_bytes: 音频文件的字节数据
        custom_name: 自定义名称前缀（可选）
    
    Returns:
        生成的前缀字符串（只允许数字和英文字母，不超过10个字符）
    """
    # 计算 MD5 Hash
    audio_hash = hashlib.md5(audio_bytes).hexdigest()[:8]
    
    # 清理自定义名称 - 只保留英文字母和数字，不能有下划线
    if custom_name:
        # 只保留字母数字，移除所有非字母数字字符
        clean_name = re.sub(r"[^a-zA-Z0-9]", "", custom_name)[:8]  # 最多8个字符
        # 组合：名称 + hash
        remaining = 10 - len(clean_name)
        if remaining > 0:
            return f"{clean_name}{audio_hash[:remaining]}"
        return clean_name[:10]
    
    # 无自定义名称，直接使用 hash
    return f"v{audio_hash[:9]}"


def extract_prefix_from_filename(filename: str) -> str:
    """从文件名提取前缀
    
    Args:
        filename: 音频文件名
    
    Returns:
        清理后的前缀（只允许数字和英文字母，不超过10个字符）
    """
    from pathlib import Path
    
    # 去掉扩展名
    name = Path(filename).stem
    
    # 只保留字母数字，并限制长度
    clean_name = re.sub(r"[^a-zA-Z0-9]", "", name)[:10]
    return clean_name.lower()


async def get_or_create_voice(
    api_key: str,
    audio_bytes: bytes,
    model: str = "cosyvoice-v3.5-plus",
    custom_name: Optional[str] = None,
    force_recreate: bool = False,
) -> Dict[str, any]:
    """获取或创建克隆音色
    
    优先查找已存在的匹配音色，若不存在则创建新的。
    基于音频内容 Hash 自动判断是否需要创建新音色。
    
    Args:
        api_key: 阿里云 DashScope API Key
        audio_bytes: 音频文件的字节数据
        model: TTS 模型名称
        custom_name: 自定义名称（用于 prefix）
        force_recreate: 是否强制重新创建（忽略已有音色）
    
    Returns:
        包含以下字段的字典:
        - voice_id: 音色 ID
        - created: 是否新创建 (True/False)
        - prefix: 使用的前缀
    """
    try:
        service = VoiceEnrollmentService(api_key)
        
        # 生成 prefix
        prefix = generate_prefix_from_audio(audio_bytes, custom_name)
        logger.info(f"[VoiceEnrollment] 生成前缀: {prefix}")
        
        if not force_recreate:
            # 查找已有音色
            voice_id = await service.find_voice_by_prefix(model, prefix)
            if voice_id:
                logger.info(f"[VoiceEnrollment] 复用已有音色: {voice_id}")
                await service.close()
                return {
                    "voice_id": voice_id,
                    "created": False,
                    "prefix": prefix,
                }
        
        # 创建新音色
        logger.info(f"[VoiceEnrollment] 创建新音色: prefix={prefix}")
        voice_id = await service.create_voice(
            target_model=model,
            prefix=prefix,
            audio_bytes=audio_bytes,
        )
        
        await service.close()
        return {
            "voice_id": voice_id,
            "created": True,
            "prefix": prefix,
        }
        
    except Exception as e:
        logger.error(f"[VoiceEnrollment] 获取/创建音色失败: {e}")
        raise
