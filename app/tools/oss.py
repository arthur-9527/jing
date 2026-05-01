"""阿里云 OSS 上传工具

提供简单的 OSS 上传功能：
- 公开读 Bucket，返回永久 URL
- 自动配置生命周期规则（7天清理）
- 文件按日期存放：cache/MMDD/
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

import oss2

from app.config import settings

logger = logging.getLogger(__name__)

# 全局客户端实例
_oss_client: Optional["OSSClient"] = None


class OSSClient:
    """阿里云 OSS 客户端
    
    简单的文件上传工具，支持：
    - 上传 bytes 数据
    - 公开读 Bucket
    - 自动按日期组织路径
    
    Attributes:
        bucket: OSS Bucket 实例
        endpoint: OSS Endpoint
        bucket_name: Bucket 名称
    """
    
    def __init__(self):
        """初始化 OSS 客户端
        
        从 settings 读取配置：
        - OSS_ACCESS_KEY_ID
        - OSS_ACCESS_KEY_SECRET
        - OSS_ENDPOINT
        - OSS_BUCKET_NAME
        """
        if not settings.OSS_ACCESS_KEY_ID or not settings.OSS_ACCESS_KEY_SECRET:
            raise ValueError("OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET are required")
        if not settings.OSS_ENDPOINT:
            raise ValueError("OSS_ENDPOINT is required")
        if not settings.OSS_BUCKET_NAME:
            raise ValueError("OSS_BUCKET_NAME is required")
        
        # 创建 Auth
        self._auth = oss2.Auth(
            settings.OSS_ACCESS_KEY_ID,
            settings.OSS_ACCESS_KEY_SECRET
        )
        
        # 创建 Bucket 实例
        self.bucket = oss2.Bucket(
            self._auth,
            settings.OSS_ENDPOINT,
            settings.OSS_BUCKET_NAME
        )
        
        self.endpoint = settings.OSS_ENDPOINT
        self.bucket_name = settings.OSS_BUCKET_NAME
        self._lifecycle_configured = False
    
    def _get_date_path(self) -> str:
        """获取当前日期路径
        
        Returns:
            str: 格式为 cache/MMDD（如 cache/0423）
        """
        now = datetime.now()
        return f"cache/{now.month:02d}{now.day:02d}"
    
    def _generate_object_key(self, extension: str) -> str:
        """生成唯一的对象键
        
        Args:
            extension: 文件扩展名（如 .mp3, .png）
            
        Returns:
            str: 完整的对象路径（如 cache/0423/uuid.mp3）
        """
        date_path = self._get_date_path()
        unique_id = uuid.uuid4().hex[:12]  # 取前12位
        return f"{date_path}/{unique_id}{extension}"
    
    def get_public_url(self, object_key: str) -> str:
        """获取公开访问 URL
        
        Args:
            object_key: 对象键（如 cache/0423/abc123.mp3）
            
        Returns:
            str: 公开访问 URL
        """
        # 公开读 Bucket，直接拼接 URL
        return f"https://{self.bucket_name}.{self.endpoint}/{object_key}"
    
    def upload(
        self,
        data: bytes,
        extension: str,
        content_type: Optional[str] = None,
    ) -> str:
        """上传文件并返回公开 URL
        
        Args:
            data: 文件二进制数据
            extension: 文件扩展名（如 .mp3, .png, .mp4）
            content_type: MIME 类型（可选）
            
        Returns:
            str: 公开访问 URL
            
        Raises:
            oss2.exceptions.OssError: OSS 操作错误
        """
        # 生成对象键
        object_key = self._generate_object_key(extension)
        
        # 设置 headers
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
            headers["x-oss-object-acl"] = "public-read"
        
        # 上传
        self.bucket.put_object(object_key, data, headers=headers)
        
        logger.info(f"[OSS] 上传成功: {object_key}")
        
        return self.get_public_url(object_key)
    
    def setup_lifecycle(self, days: int = 7) -> bool:
        """配置生命周期规则
        
        自动删除 cache/ 前缀下超过指定天数的文件。
        
        Args:
            days: 保留天数，默认 7 天
            
        Returns:
            bool: 是否配置成功
        """
        try:
            # 创建生命周期规则
            rule = oss2.models.LifecycleRule(
                id="cache-cleanup",
                prefix="cache/",
                status="Enabled",
                expiration=oss2.models.LifecycleExpiration(days=days)
            )
            
            # 设置生命周期配置
            lifecycle = oss2.models.BucketLifecycle([rule])
            self.bucket.put_bucket_lifecycle(lifecycle)
            
            logger.info(f"[OSS] 生命周期规则已配置: cache/ 目录 {days} 天后自动清理")
            self._lifecycle_configured = True
            return True
            
        except oss2.exceptions.OssError as e:
            logger.warning(f"[OSS] 配置生命周期规则失败: {e}")
            return False


def get_oss_client() -> OSSClient:
    """获取全局 OSS 客户端实例
    
    Returns:
        OSSClient: OSS 客户端实例
    """
    global _oss_client
    if _oss_client is None:
        _oss_client = OSSClient()
    return _oss_client


def init_oss_lifecycle() -> bool:
    """初始化 OSS 生命周期规则
    
    在应用启动时调用，确保生命周期规则已配置。
    
    Returns:
        bool: 是否配置成功
    """
    try:
        client = get_oss_client()
        return client.setup_lifecycle(settings.OSS_LIFECYCLE_DAYS)
    except Exception as e:
        logger.error(f"[OSS] 初始化生命周期规则失败: {e}")
        return False