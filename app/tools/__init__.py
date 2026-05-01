"""工具模块

提供阿里云 OSS 存储和媒体文件上传工具。

使用示例:
    from app.tools import upload_audio, upload_image, upload_video
    
    # 上传音频
    url = upload_audio(tts_bytes)
    
    # 上传图片
    url = upload_image(image_bytes, extension=".jpg")
    
    # 上传视频
    url = upload_video(video_bytes)
"""

from app.tools.media import upload_audio, upload_image, upload_video, upload_file
from app.tools.oss import OSSClient, get_oss_client, init_oss_lifecycle

__all__ = [
    # 便捷上传函数（推荐使用）
    "upload_audio",
    "upload_image",
    "upload_video",
    "upload_file",
    # OSS 客户端（高级用法）
    "OSSClient",
    "get_oss_client",
    "init_oss_lifecycle",
]
