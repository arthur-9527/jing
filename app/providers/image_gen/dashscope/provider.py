"""DashScope 图片生成 Provider（通义万相）

实现阿里云 DashScope 图片生成 API（异步模式）：
- submit: 提交生成任务，返回 task_id
- poll: 查询任务状态，获取生成结果

仅支持图生图模式（reference_image 必选）。

所有 DashScope 特定细节（URL、模型名、API格式）都封装在此文件中。

参考文档：https://help.aliyun.com/zh/dashscope/developer-reference/api-details
"""

import base64
import logging
from typing import Optional

import httpx

from app.providers.image_gen.base import (
    BaseImageGenProvider,
    ImageGenItem,
    ImageGenResult,
    ImageGenStatus,
    ImageGenTask,
)

logger = logging.getLogger(__name__)


# DashScope API URLs（服务商特定）
DASHSCOPE_IMAGE_API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
DASHSCOPE_TASK_API_URL = "https://dashscope.aliyuncs.com/api/v1/tasks"


class DashScopeImageGenProvider(BaseImageGenProvider):
    """DashScope 图片生成 Provider（通义万相）

    使用阿里云 DashScope API 进行图片生成。
    仅支持图生图模式（reference_image 必选）。

    Args:
        api_key: 阿里云 DashScope API Key
        model: 模型名称（要求支持图生图能力 + base64 格式输入）
        default_size: 默认图片尺寸（如 "1024*1024"）
        default_num: 默认生成数量（1-4）
        **kwargs: 其他参数
    """

    NAME = "dashscope"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "wanx2.1-t2i-plus",
        default_size: str = "1024*1024",
        default_num: int = 1,
        **kwargs,
    ):
        self._api_key = api_key
        self._model = model
        self._default_size = default_size
        self._default_num = default_num
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self, timeout: float = 30.0) -> httpx.AsyncClient:
        """获取 HTTP 客户端（懒加载）"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=10.0),
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=30.0,
                ),
            )
        return self._http_client

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def _headers(self) -> dict:
        """构建请求 Headers"""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",  # 启用异步模式
        }

    def _convert_reference_image(self, reference_image: bytes | str) -> str:
        """转换 reference_image 为 DashScope API 格式

        支持格式：
        - URL: https://example.com/image.jpg -> 直接使用
        - Base64 data URI: data:image/jpeg;base64,<data> -> 直接使用
        - Base64 string: <base64_data> -> 转为 data URI
        - Bytes: <bytes> -> 转 base64 再转 data URI

        Returns:
            str: DashScope API 可接受的图片格式（URL 或 data URI）
        """
        if isinstance(reference_image, bytes):
            # bytes -> base64 data URI
            b64_data = base64.b64encode(reference_image).decode("utf-8")
            return f"data:image/jpeg;base64,{b64_data}"
        elif isinstance(reference_image, str):
            if reference_image.startswith(("http://", "https://")):
                # URL -> 直接使用
                return reference_image
            elif reference_image.startswith("data:"):
                # data URI -> 直接使用
                return reference_image
            else:
                # 纯 base64 string -> 转 data URI
                return f"data:image/jpeg;base64,{reference_image}"
        else:
            raise ValueError(f"Unsupported reference_image type: {type(reference_image)}")

    async def submit(
        self,
        prompt: str,
        reference_image: bytes | str,  # 必选：参考图（图生图模式）
        *,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        style: str = "",
        num_images: int = 1,
        seed: Optional[int] = None,
        reference_strength: float = 0.5,
        timeout: float = 30.0,
        **kwargs,
    ) -> ImageGenTask:
        """提交图片生成任务（仅支持图生图模式）

        DashScope API 参数映射：
        - prompt -> input.text
        - reference_image -> input.image_url (URL 或 data URI)
        - negative_prompt -> parameters.negative_prompt
        - width/height -> parameters.size (如 "1024*1024")
        - style -> parameters.style
        - num_images -> parameters.n
        - seed -> parameters.seed
        - reference_strength -> parameters.ref_strength
        """
        client = await self._get_client(timeout)

        # 转换 reference_image 为 API 格式
        image_value = self._convert_reference_image(reference_image)

        # 构建请求体（图生图模式）
        # DashScope API 格式：input.prompt + input.image_url（图生图）
        request_body = {
            "model": self._model,
            "input": {
                "prompt": prompt,
                "image_url": image_value,  # DashScope API 用 image_url 接收 URL 或 data URI
            },
            "parameters": {
                "size": f"{width}*{height}",
                "n": num_images,
            },
        }

        # 可选参数
        if negative_prompt:
            request_body["parameters"]["negative_prompt"] = negative_prompt

        if style:
            request_body["parameters"]["style"] = style

        if seed is not None:
            request_body["parameters"]["seed"] = seed

        # 服务商特有参数
        if kwargs:
            request_body["parameters"].update(kwargs)

        # 发送请求
        try:
            resp = await client.post(
                DASHSCOPE_IMAGE_API_URL,
                headers=self._headers(),
                json=request_body,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            error_body = e.response.text if e.response else "Unknown error"
            # 检查是否是模型不支持图生图的错误
            if "model" in error_body.lower() or "not support" in error_body.lower():
                logger.warning(
                    f"[DashScope ImageGen] 模型 {self._model} 可能不支持图生图能力，"
                    f"请检查模型配置。错误: {error_body}"
                )
            logger.error(f"[DashScope ImageGen] 提交失败: {e.response.status_code} - {error_body}")
            return ImageGenTask(
                task_id="",
                status=ImageGenStatus.FAILED,
                prompt=prompt,
                error=f"HTTP {e.response.status_code}: {error_body}",
            )
        except Exception as e:
            logger.error(f"[DashScope ImageGen] 提交异常: {e}")
            return ImageGenTask(
                task_id="",
                status=ImageGenStatus.FAILED,
                prompt=prompt,
                error=str(e),
            )

        # 解析响应
        output = data.get("output", {})
        task_id = output.get("task_id", "")
        task_status = output.get("task_status", "PENDING")

        if not task_id:
            logger.error(f"[DashScope ImageGen] 响应无 task_id: {data}")
            return ImageGenTask(
                task_id="",
                status=ImageGenStatus.FAILED,
                prompt=prompt,
                error="No task_id in response",
            )

        logger.info(f"[DashScope ImageGen] 任务已提交: task_id={task_id}, status={task_status}")

        return ImageGenTask(
            task_id=task_id,
            status=self._map_status(task_status),
            prompt=prompt,
            progress=0.0,
            metadata={"request_body": request_body},
        )

    async def poll(self, task_id: str) -> ImageGenTask:
        """查询任务状态

        DashScope 返回格式：
        - output.task_status: PENDING / RUNNING / SUCCEEDED / FAILED
        - output.results: 图片列表（成功时）
        - output.message: 错误信息（失败时）
        """
        if not task_id:
            return ImageGenTask(
                task_id=task_id,
                status=ImageGenStatus.FAILED,
                prompt="",
                error="Empty task_id",
            )

        client = await self._get_client()

        try:
            resp = await client.get(
                f"{DASHSCOPE_TASK_API_URL}/{task_id}",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            error_body = e.response.text if e.response else "Unknown error"
            logger.error(f"[DashScope ImageGen] 查询失败: {e.response.status_code} - {error_body}")
            return ImageGenTask(
                task_id=task_id,
                status=ImageGenStatus.FAILED,
                prompt="",
                error=f"HTTP {e.response.status_code}: {error_body}",
            )
        except Exception as e:
            logger.error(f"[DashScope ImageGen] 查询异常: {e}")
            return ImageGenTask(
                task_id=task_id,
                status=ImageGenStatus.FAILED,
                prompt="",
                error=str(e),
            )

        # 解析响应
        output = data.get("output", {})
        task_status = output.get("task_status", "PENDING")
        message = output.get("message", "")
        results = output.get("results", [])
        submit_time = data.get("request", {}).get("text", "")  # 原始 prompt

        status = self._map_status(task_status)

        # 成功时构建结果
        result = None
        if status == ImageGenStatus.SUCCEEDED and results:
            images = []
            for item in results:
                url = item.get("url", "")
                images.append(ImageGenItem(
                    url=url,
                    width=0,
                    height=0,
                    format="png",
                ))

            result = ImageGenResult(
                images=images,
                prompt=submit_time,
                model=self._model,
                metadata={"results": results},
            )
            logger.info(f"[DashScope ImageGen] 任务完成: {len(images)} 张图片")

        # 失败时记录错误
        error = None
        if status == ImageGenStatus.FAILED:
            error = message or task_status
            # 检查是否是模型不支持相关错误
            if "not support" in error.lower() or "invalid" in error.lower():
                logger.warning(
                    f"[DashScope ImageGen] 模型 {self._model} 可能不支持图生图能力，"
                    f"请检查模型配置。错误: {error}"
                )
            logger.error(f"[DashScope ImageGen] 任务失败: {error}")

        # 计算进度
        progress = 0.0
        if status == ImageGenStatus.RUNNING:
            progress = 0.5
        elif status == ImageGenStatus.SUCCEEDED:
            progress = 1.0

        return ImageGenTask(
            task_id=task_id,
            status=status,
            prompt=submit_time,
            result=result,
            error=error,
            progress=progress,
            metadata=data,
        )

    async def cancel(self, task_id: str) -> bool:
        """取消任务

        DashScope 支持通过 DELETE 请求取消任务。
        """
        if not task_id:
            return False

        client = await self._get_client()

        try:
            resp = await client.delete(
                f"{DASHSCOPE_TASK_API_URL}/{task_id}",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            if resp.status_code == 200:
                logger.info(f"[DashScope ImageGen] 任务已取消: task_id={task_id}")
                return True
            else:
                logger.warning(f"[DashScope ImageGen] 取消失败: {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"[DashScope ImageGen] 取消异常: {e}")
            return False

    def _map_status(self, dashscope_status: str) -> ImageGenStatus:
        """映射 DashScope 状态到通用状态"""
        mapping = {
            "PENDING": ImageGenStatus.PENDING,
            "RUNNING": ImageGenStatus.RUNNING,
            "SUCCEEDED": ImageGenStatus.SUCCEEDED,
            "FAILED": ImageGenStatus.FAILED,
            "CANCELED": ImageGenStatus.FAILED,
            "UNKNOWN": ImageGenStatus.FAILED,
        }
        return mapping.get(dashscope_status.upper(), ImageGenStatus.FAILED)