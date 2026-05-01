"""LiteLLM Provider - 使用 httpx 调用 OpenAI 兼容 API

保留现有的 httpx 实现，支持：
- 文本对话 (chat/chat_stream/chat_json)
- 图片分析 (analyze_image)
- 视频分析 (analyze_video)

优化：使用长连接单例，避免每次请求重新创建 TCP 连接 + TLS 握手。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any, Optional

import httpx

from app.config import settings
from app.providers.llm.base import (
    BaseLLMProvider,
    VisionCapability,
    VisionResult,
)

logger = logging.getLogger(__name__)

# 全局 httpx.AsyncClient 单例（长连接池）
_global_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def get_http_client(timeout: float = 60.0) -> httpx.AsyncClient:
    """获取全局 httpx.AsyncClient 单例"""
    global _global_client

    if _global_client is None or _global_client.is_closed:
        async with _client_lock:
            if _global_client is None or _global_client.is_closed:
                _global_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout, connect=10.0),
                    limits=httpx.Limits(
                        max_connections=10,
                        max_keepalive_connections=5,
                        keepalive_expiry=30.0,
                    ),
                )
                logger.info("[LiteLLM] 创建长连接 httpx.AsyncClient 单例")

    return _global_client


async def close_http_client():
    """关闭全局 httpx.AsyncClient 单例（应用关闭时调用）"""
    global _global_client

    if _global_client is not None:
        async with _client_lock:
            if _global_client is not None:
                await _global_client.aclose()
                _global_client = None
                logger.info("[LiteLLM] 关闭长连接 httpx.AsyncClient")


class LiteLLMProvider(BaseLLMProvider):
    """LiteLLM Provider

    使用 httpx 调用 OpenAI 兼容 API，支持：
    - OpenAI 官方 API
    - 兼容 OpenAI API 格式的第三方 API（如 vLLM、Ollama、本地代理等）
    - Cerebras 通过兼容端点访问
    - 多模态模型（如 Qwen-VL）

    视觉能力取决于配置的模型。
    """

    NAME = "litellm"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        fast_model: str | None = None,
    ):
        self.base_url = base_url or settings.LITELLM_API_BASE_URL
        self.api_key = api_key or settings.LITELLM_API_KEY or ""
        self.model = model or settings.CHAT_MODEL or settings.LITELLM_MODEL
        self.fast_model = fast_model or self.model

    @property
    def vision_capability(self) -> VisionCapability:
        """LiteLLM Provider 视觉能力配置

        VISION_MODEL_TYPE:
        - none: 不支持视觉
        - image: 支持图片分析
        - video: 支持视频分析（同时支持图片）
        """
        model_type = getattr(settings, 'VISION_MODEL_TYPE', 'none').lower()

        if model_type == 'video':
            # VIDEO 包含 IMAGE（位运算）
            return VisionCapability.VIDEO | VisionCapability.IMAGE
        elif model_type == 'image':
            return VisionCapability.IMAGE
        else:
            return VisionCapability.NONE

    def _build_payload(
        self,
        messages: list[dict],
        temperature: float,
        json_mode: bool,
        use_fast: bool,
        stream: bool,
        model_override: str | None = None,
    ) -> dict:
        """构建请求 payload"""
        payload = {
            "model": self._get_model_name(
                model_override, use_fast, self.model, self.fast_model
            ),
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _headers(
        self,
        api_key_override: str | None = None,
        extra_headers: dict | None = None,
    ) -> dict:
        """构建请求 Headers"""
        headers = {
            "Authorization": f"Bearer {api_key_override if api_key_override is not None else self.api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _log_cache_stats(self, usage: dict) -> None:
        """记录缓存统计"""
        if not usage:
            return

        prompt_tokens = usage.get("prompt_tokens", 0)
        prompt_details = usage.get("prompt_tokens_details", {})
        cached_tokens = prompt_details.get("cached_tokens", 0) if prompt_details else 0

        if prompt_tokens > 0:
            cache_ratio = (cached_tokens / prompt_tokens) * 100
            if cached_tokens > 0:
                if cache_ratio >= 50:
                    logger.info(
                        "Cache HIT: %d/%d tokens (%.1f%%) - 高效缓存",
                        cached_tokens, prompt_tokens, cache_ratio
                    )
                elif cache_ratio >= 20:
                    logger.info(
                        "Cache hit: %d/%d tokens (%.1f%%)",
                        cached_tokens, prompt_tokens, cache_ratio
                    )
                else:
                    logger.debug(
                        "Cache hit: %d/%d tokens (%.1f%%)",
                        cached_tokens, prompt_tokens, cache_ratio
                    )
            else:
                logger.debug(
                    "Cache miss: %d prompt tokens",
                    prompt_tokens
                )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.8,
        json_mode: bool = False,
        use_fast: bool = False,
        timeout: float | None = None,
        base_url_override: str | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        """非流式对话"""
        payload = self._build_payload(
            messages,
            temperature,
            json_mode,
            use_fast,
            stream=False,
            model_override=model_override,
        )
        base_url = (base_url_override or self.base_url).rstrip("/")

        client = await get_http_client(timeout or 60)
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers=self._headers(
                api_key_override=api_key_override,
                extra_headers=extra_headers,
            ),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        if "usage" in data:
            self._log_cache_stats(data["usage"])

        return data["choices"][0]["message"]["content"]

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.8,
        use_fast: bool = False,
        timeout: float | None = None,
        base_url_override: str | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式对话"""
        payload = self._build_payload(
            messages,
            temperature,
            json_mode=False,
            use_fast=use_fast,
            stream=True,
            model_override=model_override,
        )
        base_url = (base_url_override or self.base_url).rstrip("/")

        client = await get_http_client(timeout or 60)
        async with client.stream(
            "POST",
            f"{base_url}/chat/completions",
            headers=self._headers(
                api_key_override=api_key_override,
                extra_headers=extra_headers,
            ),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                chunk = json.loads(data_str)
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield content

                if chunk.get("usage"):
                    self._log_cache_stats(chunk["usage"])

    async def analyze_image(
        self,
        image_data: bytes | str,
        prompt: str = "描述这张图片的内容",
        *,
        detail_level: str = "auto",
    ) -> VisionResult | None:
        """分析单张图片"""
        if not self.supports_image:
            return None

        content = [{"type": "text", "text": prompt}]

        if isinstance(image_data, str) and image_data.startswith(("http://", "https://")):
            content.append({
                "type": "image_url",
                "image_url": {"url": image_data, "detail": detail_level},
            })
        elif isinstance(image_data, bytes):
            b64_data = base64.b64encode(image_data).decode("utf-8")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_data}", "detail": detail_level},
            })
        else:
            raise ValueError(f"Unsupported image_data type: {type(image_data)}")

        response = await self.chat([
            {"role": "user", "content": content}
        ])

        return self._parse_vision_result(response)

    async def analyze_video(
        self,
        video_path: str,
        prompt: str = "描述视频的主要内容",
        *,
        duration: float | None = None,
        frame_interval: float = 0.5,
        max_frames: int = 10,
    ) -> VisionResult | None:
        """分析视频（提取关键帧）"""
        if not self.supports_video:
            return None

        frames = self._extract_keyframes(video_path, duration or 10.0, frame_interval, max_frames)

        if not frames:
            return VisionResult(
                description="无法提取视频关键帧",
                tags=[],
                confidence=0.0,
            )

        content = [
            {"type": "text", "text": f"{prompt}\n\n共提取了 {len(frames)} 个关键帧，请综合分析。"},
        ]

        for frame_b64 in frames:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}", "detail": "low"},
            })

        response = await self.chat([{"role": "user", "content": content}])

        return self._parse_vision_result(response)

    def _parse_vision_result(self, response_text: str) -> VisionResult:
        """解析视觉分析结果"""
        try:
            json_str = self._extract_json(response_text)
            data = json.loads(json_str)

            return VisionResult(
                description=data.get("description", response_text[:100]),
                tags=data.get("tags", []),
                confidence=data.get("confidence", 0.8),
                objects=data.get("objects", []),
                text_content=data.get("text_content"),
            )
        except (json.JSONDecodeError, ValueError):
            return VisionResult(
                description=response_text[:200] if response_text else "无描述",
                tags=[],
                confidence=0.5,
            )

    def _extract_json(self, text: str) -> str:
        """从文本中提取 JSON"""
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return text[start:end]

        return text.strip()

    def _extract_keyframes(
        self,
        video_path: str,
        duration: float,
        frame_interval: float,
        max_frames: int,
    ) -> list[str]:
        """提取视频关键帧"""
        try:
            import cv2

            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                logger.warning(f"[LiteLLM] 无法打开视频: {video_path}")
                return []

            fps = cap.get(cv2.CAP_PROP_FPS)
            duration_ms = int(duration * 1000)
            interval_ms = int(frame_interval * 1000)

            time_points = [0]
            current_time = interval_ms
            while current_time < duration_ms and len(time_points) < max_frames:
                time_points.append(current_time)
                current_time += interval_ms

            if duration_ms not in time_points and len(time_points) < max_frames:
                time_points.append(duration_ms)

            keyframes = []
            for time_ms in time_points:
                frame_idx = int((time_ms / 1000) * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()

                if ret:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    _, buffer = cv2.imencode('.jpg', frame_rgb, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    keyframes.append(base64.b64encode(buffer).decode('utf-8'))

            cap.release()
            logger.info(f"[LiteLLM] 提取 {len(keyframes)} 个关键帧")
            return keyframes

        except ImportError:
            logger.warning("[LiteLLM] OpenCV 未安装，无法提取关键帧")
            return []
        except Exception as e:
            logger.error(f"[LiteLLM] 提取关键帧失败: {e}")
            return []
