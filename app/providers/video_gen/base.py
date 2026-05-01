"""Video Gen Provider 抽象基类

定义统一的视频生成接口，所有 Provider 必须实现这些方法。
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


class VideoGenStatus(Enum):
    """视频生成任务状态"""
    PENDING = "pending"       # 已提交，等待处理
    RUNNING = "running"       # 正在生成
    SUCCEEDED = "succeeded"   # 成功完成
    FAILED = "failed"         # 失败


@dataclass
class VideoGenItem:
    """单个生成视频"""
    url: Optional[str] = None       # 视频 URL（服务商返回）
    data: Optional[bytes] = None    # 视频二进制数据（二选一）
    cover_url: Optional[str] = None # 封面图 URL
    duration: float = 0.0           # 视频时长（秒）
    width: int = 0
    height: int = 0
    fps: int = 0
    format: str = "mp4"             # 视频格式


@dataclass
class VideoGenResult:
    """视频生成结果"""
    videos: list[VideoGenItem] = field(default_factory=list)
    prompt: str = ""                # 原始 prompt
    model: str = ""                 # 使用的模型
    elapsed_ms: float = 0.0         # 总耗时（毫秒）
    metadata: dict[str, Any] = field(default_factory=dict)  # 服务商额外信息


@dataclass
class VideoGenTask:
    """视频生成任务"""
    task_id: str                    # 任务 ID（服务商返回）
    status: VideoGenStatus          # 任务状态
    prompt: str                     # 原始 prompt
    result: Optional[VideoGenResult] = None  # 完成后的结果
    error: Optional[str] = None     # 失败时的错误信息
    progress: float = 0.0           # 进度 0.0 ~ 1.0
    metadata: dict[str, Any] = field(default_factory=dict)  # 服务商额外信息


class BaseVideoGenProvider(ABC):
    """Video Gen Provider 抽象基类

    所有视频生成 Provider 必须实现以下方法：
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
        reference_image: bytes | str,  # 必选：首帧参考图（图生视频模式）
        *,
        negative_prompt: str = "",
        duration: float = 5.0,           # 视频时长（秒）
        resolution: str = "720p",        # 分辨率（服务商自由解释）
        fps: int = 24,                   # 帧率
        style: str = "",                 # 风格提示
        seed: Optional[int] = None,      # 随机种子
        timeout: float = 30.0,           # 提交超时
        **kwargs,                        # 服务商特有参数
    ) -> VideoGenTask:
        """提交视频生成任务（仅支持图生视频模式）

        Args:
            prompt: 生成提示词
            reference_image: 首帧参考图（必选，支持 URL / base64 string / bytes）
            negative_prompt: 负向提示词
            duration: 视频时长（秒）
            resolution: 分辨率（如 "720p", "1080p"）
            fps: 帧率
            style: 风格提示
            seed: 随机种子
            timeout: 提交请求超时
            **kwargs: 服务商特有参数

        Returns:
            VideoGenTask: 任务信息（包含 task_id）
        """
        pass

    @abstractmethod
    async def poll(self, task_id: str) -> VideoGenTask:
        """查询任务状态

        Args:
            task_id: 任务 ID（submit 返回的）

        Returns:
            VideoGenTask: 任务当前状态（包含 progress、result、error）
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
        reference_image: bytes | str,  # 必选：首帧参考图
        *,
        poll_interval: float = 5.0,
        max_wait: float = 600.0,
        **kwargs,
    ) -> VideoGenResult:
        """提交任务并等待完成（通用轮询实现）

        视频生成通常需要较长时间（分钟级），子类无需重写此方法，
        除非有特殊需求。

        Args:
            prompt: 生成提示词
            reference_image: 首帧参考图（必选）
            poll_interval: 轮询间隔（秒），默认 5s
            max_wait: 最大等待时间（秒），默认 10 分钟
            **kwargs: submit 的其他参数

        Returns:
            VideoGenResult: 生成结果

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
                    f"Video generation timeout after {elapsed:.1f}s "
                    f"(task_id={task.task_id})"
                )

            task = await self.poll(task.task_id)

            if task.status == VideoGenStatus.SUCCEEDED:
                if task.result:
                    task.result.elapsed_ms = elapsed * 1000
                    return task.result
                raise RuntimeError("Task succeeded but no result")

            if task.status == VideoGenStatus.FAILED:
                raise RuntimeError(
                    f"Video generation failed: {task.error or 'Unknown error'}"
                )

            # 继续等待
            await asyncio.sleep(poll_interval)