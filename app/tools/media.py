"""媒体文件上传工具

提供简单的上传函数，将音频、图片、视频上传到 OSS 并返回公开 URL。
"""

import logging
from typing import Optional

from app.config import settings
from app.tools.oss import get_oss_client

logger = logging.getLogger(__name__)


# MIME 类型映射
CONTENT_TYPES = {
    # 音频
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    # 图片
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    # 视频
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}


def _get_content_type(extension: str) -> Optional[str]:
    """根据扩展名获取 MIME 类型"""
    return CONTENT_TYPES.get(extension.lower())


def upload_audio(data: bytes, extension: str = ".mp3") -> str:
    """上传音频文件
    
    Args:
        data: 音频二进制数据
        extension: 文件扩展名，默认 .mp3
        
    Returns:
        str: 公开访问 URL
        
    Raises:
        ValueError: OSS 未启用
        oss2.exceptions.OssError: OSS 操作错误
        
    Example:
        >>> url = upload_audio(tts_audio_bytes)
        >>> print(url)  # https://bucket.oss-cn-hangzhou.aliyuncs.com/cache/0423/abc123.mp3
    """
    if not settings.OSS_ENABLED:
        raise ValueError("OSS is not enabled. Set OSS_ENABLED=true to use this feature.")
    
    client = get_oss_client()
    content_type = _get_content_type(extension)
    
    url = client.upload(data, extension, content_type)
    logger.info(f"[Media] 音频上传成功: {url}")
    return url


def upload_image(data: bytes, extension: str = ".png") -> str:
    """上传图片文件
    
    Args:
        data: 图片二进制数据
        extension: 文件扩展名，默认 .png
        
    Returns:
        str: 公开访问 URL
        
    Raises:
        ValueError: OSS 未启用
        oss2.exceptions.OssError: OSS 操作错误
        
    Example:
        >>> url = upload_image(image_bytes, extension=".jpg")
        >>> print(url)  # https://bucket.oss-cn-hangzhou.aliyuncs.com/cache/0423/def456.jpg
    """
    if not settings.OSS_ENABLED:
        raise ValueError("OSS is not enabled. Set OSS_ENABLED=true to use this feature.")
    
    client = get_oss_client()
    content_type = _get_content_type(extension)
    
    url = client.upload(data, extension, content_type)
    logger.info(f"[Media] 图片上传成功: {url}")
    return url


def upload_video(data: bytes, extension: str = ".mp4") -> str:
    """上传视频文件
    
    Args:
        data: 视频二进制数据
        extension: 文件扩展名，默认 .mp4
        
    Returns:
        str: 公开访问 URL
        
    Raises:
        ValueError: OSS 未启用
        oss2.exceptions.OssError: OSS 操作错误
        
    Example:
        >>> url = upload_video(video_bytes)
        >>> print(url)  # https://bucket.oss-cn-hangzhou.aliyuncs.com/cache/0423/ghi789.mp4
    """
    if not settings.OSS_ENABLED:
        raise ValueError("OSS is not enabled. Set OSS_ENABLED=true to use this feature.")
    
    client = get_oss_client()
    content_type = _get_content_type(extension)
    
    url = client.upload(data, extension, content_type)
    logger.info(f"[Media] 视频上传成功: {url}")
    return url


def upload_file(data: bytes, extension: str, content_type: Optional[str] = None) -> str:
    """通用文件上传
    
    Args:
        data: 文件二进制数据
        extension: 文件扩展名（如 .mp3, .png, .mp4）
        content_type: MIME 类型（可选，自动推断）
        
    Returns:
        str: 公开访问 URL
        
    Raises:
        ValueError: OSS 未启用
        oss2.exceptions.OssError: OSS 操作错误
    """
    if not settings.OSS_ENABLED:
        raise ValueError("OSS is not enabled. Set OSS_ENABLED=true to use this feature.")
    
    client = get_oss_client()
    if content_type is None:
        content_type = _get_content_type(extension)
    
    url = client.upload(data, extension, content_type)
    logger.info(f"[Media] 文件上传成功: {url}")
    return url