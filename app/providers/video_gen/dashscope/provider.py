"""DashScope 视频生成 Provider

实现阿里云 DashScope 视频生成 API（异步模式）：
- submit: 提交生成任务，返回 task_id
- poll: 查询任务状态，获取生成结果

仅支持图生视频模式（reference_image 必选）。

使用官方 DashScope SDK 实现，更可靠。

参考文档：https://help.aliyun.com/zh/dashscope/developer-reference/video-generation
"""

import asyncio
import base64
import logging
from typing import Optional

from dashscope import VideoSynthesis

from app.providers.video_gen.base import (
    BaseVideoGenProvider,
    VideoGenItem,
    VideoGenResult,
    VideoGenStatus,
    VideoGenTask,
)

logger = logging.getLogger(__name__)


class DashScopeVideoGenProvider(BaseVideoGenProvider):
    """DashScope 视频生成 Provider

    使用阿里云 DashScope API 进行视频生成。
    仅支持图生视频模式（reference_image 必选）。
    """

    NAME = "dashscope"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "wanx2.1-i2v-plus",
        default_resolution: str = "720p",
        default_duration: float = 5.0,
        **kwargs,
    ):
        self._api_key = api_key
        self._model = model
        self._default_resolution = default_resolution
        self._default_duration = default_duration

    def _convert_reference_image(self, reference_image: bytes | str) -> str:
        """转换 reference_image 为 DashScope API 格式"""
        if isinstance(reference_image, bytes):
            b64_data = base64.b64encode(reference_image).decode("utf-8")
            return f"data:image/jpeg;base64,{b64_data}"
        elif isinstance(reference_image, str):
            if reference_image.startswith(("http://", "https://")):
                return reference_image
            elif reference_image.startswith("data:"):
                return reference_image
            else:
                return f"data:image/jpeg;base64,{reference_image}"
        else:
            raise ValueError(f"Unsupported reference_image type: {type(reference_image)}")

    async def submit(
        self,
        prompt: str,
        reference_image: bytes | str,
        *,
        negative_prompt: str = "",
        duration: float = 5.0,
        resolution: str = "720p",
        fps: int = 24,
        style: str = "",
        seed: Optional[int] = None,
        timeout: float = 30.0,
        **kwargs,
    ) -> VideoGenTask:
        """提交视频生成任务（使用官方 SDK）"""
        image_url = self._convert_reference_image(reference_image)

        def _sync_submit():
            return VideoSynthesis.async_call(
                model=self._model,
                prompt=prompt,
                img_url=image_url,
                duration=int(duration),
                negative_prompt=negative_prompt if negative_prompt else None,
                seed=seed,
                api_key=self._api_key,
            )

        try:
            response = await asyncio.get_event_loop().run_in_executor(None, _sync_submit)

            task_id = response.output.task_id if response.output else ""
            task_status = response.output.task_status if response.output else "PENDING"

            if not task_id:
                error_msg = response.message or "No task_id in response"
                logger.error(f"[DashScope VideoGen] 提交失败: {error_msg}")
                return VideoGenTask(
                    task_id="",
                    status=VideoGenStatus.FAILED,
                    prompt=prompt,
                    error=error_msg,
                )

            logger.info(f"[DashScope VideoGen] 任务已提交: task_id={task_id}, status={task_status}")

            return VideoGenTask(
                task_id=task_id,
                status=self._map_status(task_status),
                prompt=prompt,
                progress=0.0,
                metadata={"model": self._model},
            )
        except Exception as e:
            logger.error(f"[DashScope VideoGen] 提交异常: {e}")
            return VideoGenTask(
                task_id="",
                status=VideoGenStatus.FAILED,
                prompt=prompt,
                error=str(e),
            )

    async def poll(self, task_id: str) -> VideoGenTask:
        """查询任务状态（使用官方 SDK）"""
        if not task_id:
            return VideoGenTask(
                task_id=task_id,
                status=VideoGenStatus.FAILED,
                prompt="",
                error="Empty task_id",
            )

        def _sync_poll():
            return VideoSynthesis.fetch(task_id, api_key=self._api_key)

        try:
            response = await asyncio.get_event_loop().run_in_executor(None, _sync_poll)

            output = response.output or {}
            task_status = output.get("task_status", "PENDING")

            status = self._map_status(task_status)

            result = None
            if status == VideoGenStatus.SUCCEEDED:
                video_url = output.get("video_url", "")
                cover_url = output.get("cover_url", "")

                if video_url:
                    video_item = VideoGenItem(
                        url=video_url,
                        cover_url=cover_url,
                        duration=0.0,
                        format="mp4",
                    )
                    result = VideoGenResult(
                        videos=[video_item],
                        prompt="",
                        model=self._model,
                        metadata=output,
                    )
                    logger.info(f"[DashScope VideoGen] 任务完成: video_url={video_url}")

            error = None
            if status == VideoGenStatus.FAILED:
                error = output.get("message", "") or task_status
                logger.error(f"[DashScope VideoGen] 任务失败: {error}")

            progress = 0.0
            if status == VideoGenStatus.RUNNING:
                progress = 0.5
            elif status == VideoGenStatus.SUCCEEDED:
                progress = 1.0

            return VideoGenTask(
                task_id=task_id,
                status=status,
                prompt="",
                result=result,
                error=error,
                progress=progress,
                metadata={"output": output},
            )
        except Exception as e:
            logger.error(f"[DashScope VideoGen] 查询异常: {e}")
            return VideoGenTask(
                task_id=task_id,
                status=VideoGenStatus.FAILED,
                prompt="",
                error=str(e),
            )

    async def cancel(self, task_id: str) -> bool:
        """取消任务（使用官方 SDK）"""
        if not task_id:
            return False

        def _sync_cancel():
            return VideoSynthesis.cancel(task_id, api_key=self._api_key)

        try:
            response = await asyncio.get_event_loop().run_in_executor(None, _sync_cancel)
            if response.status_code == 200:
                logger.info(f"[DashScope VideoGen] 任务已取消: task_id={task_id}")
                return True
            else:
                logger.warning(f"[DashScope VideoGen] 取消失败: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"[DashScope VideoGen] 取消异常: {e}")
            return False

    async def close(self) -> None:
        """关闭资源（SDK 无需关闭）"""
        pass

    def _map_status(self, dashscope_status: str) -> VideoGenStatus:
        """映射 DashScope 状态到通用状态"""
        mapping = {
            "PENDING": VideoGenStatus.PENDING,
            "RUNNING": VideoGenStatus.RUNNING,
            "SUCCEEDED": VideoGenStatus.SUCCEEDED,
            "FAILED": VideoGenStatus.FAILED,
            "CANCELED": VideoGenStatus.FAILED,
            "UNKNOWN": VideoGenStatus.FAILED,
        }
        return mapping.get(dashscope_status.upper(), VideoGenStatus.FAILED)