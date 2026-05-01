"""Image Gen Provider 抽象基类

定义统一的图片生成接口，所有 Provider 必须实现这些方法。
采用异步模式（submit + poll），适用于所有服务商。

设计原则：
- 基类只定义接口和通用数据结构
- 不包含任何服务商细节（URL、模型名、API 格式）
- wait_and_get_result 提供通用轮询实现，子类无需重写
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ImageGenStatus(Enum):
    """图片生成任务状态"""
    PENDING = "pending"       # 已提交，等待处理
    RUNNING = "running"       # 正在生成
    SUCCEEDED = "succeeded"   # 成功完成
    FAILED = "failed"         # 失败


@dataclass
class ImageGenItem:
    """单个生成图片"""
    url: Optional[str] = None       # 图片 URL（服务商返回）
    data: Optional[bytes] = None    # 图片二进制数据（二选一）
    width: int = 0
    height: int = 0
    seed: Optional[int] = None      # 随机种子（可复现）
    format: str = "png"             # 图片格式


@dataclass
class ImageGenResult:
    """图片生成结果"""
    images: list[ImageGenItem] = field(default_factory=list)
    prompt: str = ""                # 原始 prompt
    model: str = ""                 # 使用的模型
    elapsed_ms: float = 0.0         # 总耗时（毫秒）
    metadata: dict[str, Any] = field(default_factory=dict)  # 服务商额外信息


@dataclass
class ImageGenTask:
    """图片生成任务"""
    task_id: str                    # 任务 ID（服务商返回）
    status: ImageGenStatus          # 任务状态
    prompt: str                     # 原始 prompt
    result: Optional[ImageGenResult] = None  # 完成后的结果
    error: Optional[str] = None     # 失败时的错误信息
    progress: float = 0.0           # 进度 0.0 ~ 1.0
    metadata: dict[str, Any] = field(default_factory=dict)  # 服务商额外信息


class BaseImageGenProvider(ABC):
    """Image Gen Provider 抽象基类

    所有图片生成 Provider 必须实现以下方法：
    - submit(): 提交生成任务
    - poll(): 查询任务状态

    可选方法：
    - cancel(): 取消任务（默认不支持）

    wait_and_get_result() 提供通用的 submit + poll 循环实现，
    子类无需重写，除非有特殊需求。

    Attributes:
        NAME: Provider 名称（用于 registry 注册）
    """

    NAME: str = "base"

    @abstractmethod
    async def submit(
        self,
        prompt: str,
        reference_image: bytes | str,  # 必选：参考图（图生图模式）
        *,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        style: str = "",               # 风格提示（服务商自由解释）
        num_images: int = 1,           # 生成图片数量
        seed: Optional[int] = None,    # 随机种子（可复现）
        reference_strength: float = 0.5,  # 参考图影响强度
        timeout: float = 30.0,         # 提交超时
        **kwargs,                      # 服务商特有参数
    ) -> ImageGenTask:
        """提交图片生成任务（仅支持图生图模式）

        Args:
            prompt: 生成提示词
            reference_image: 参考图片（必选，支持 URL / base64 string / bytes）
            negative_prompt: 负向提示词
            width: 图片宽度
            height: 图片高度
            style: 风格提示（如 "realistic", "anime"）
            num_images: 生成数量
            seed: 随机种子
            reference_strength: 参考图影响强度 0~1
            timeout: 提交请求超时
            **kwargs: 服务商特有参数

        Returns:
            ImageGenTask: 任务信息（包含 task_id）
        """
        pass

    @abstractmethod
    async def poll(self, task_id: str) -> ImageGenTask:
        """查询任务状态

        Args:
            task_id: 任务 ID（submit 返回的）

        Returns:
            ImageGenTask: 任务当前状态（包含 progress、result、error）
        """
        pass

    async def cancel(self, task_id: str) -> bool:
        """取消任务（默认不支持）

        Args:
            task_id: 任务 ID

        Returns:
            bool: 是否成功取消
        """
        return False  # 默认不支持

    async def wait_and_get_result(
        self,
        prompt: str,
        reference_image: bytes | str,  # 必选：参考图
        *,
        poll_interval: float = 2.0,
        max_wait: float = 300.0,
        **kwargs,
    ) -> ImageGenResult:
        """提交任务并等待完成（通用轮询实现）

        子类无需重写此方法，除非有特殊需求。

        Args:
            prompt: 生成提示词
            reference_image: 参考图片（必选）
            poll_interval: 轮询间隔（秒）
            max_wait: 最大等待时间（秒）
            **kwargs: submit 的其他参数

        Returns:
            ImageGenResult: 生成结果

        Raises:
            TimeoutError: 超过最大等待时间
            RuntimeError: 任务失败
        """
        import asyncio
        import time

        start_time = time.monotonic()

        # 提交任务
        task = await self.submit(prompt, reference_image, **kwargs)

        # 轮询直到完成
        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > max_wait:
                raise TimeoutError(
                    f"Image generation timeout after {elapsed:.1f}s "
                    f"(task_id={task.task_id})"
                )

            task = await self.poll(task.task_id)

            if task.status == ImageGenStatus.SUCCEEDED:
                if task.result:
                    task.result.elapsed_ms = elapsed * 1000
                    return task.result
                raise RuntimeError("Task succeeded but no result")

            if task.status == ImageGenStatus.FAILED:
                raise RuntimeError(
                    f"Image generation failed: {task.error or 'Unknown error'}"
                )

            # 继续等待
            await asyncio.sleep(poll_interval)