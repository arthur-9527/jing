"""LLM Provider 抽象基类

定义统一的 LLM 调用接口，所有 Provider 必须实现这些方法。
支持可选的视觉能力（image/video）。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import Flag, auto
from typing import Any, Optional


class VisionCapability(Flag):
    """视觉能力标记

    - NONE: 不支持视觉
    - IMAGE: 支持图片分析
    - VIDEO: 支持视频分析（同时支持图片）
    """
    NONE = 0
    IMAGE = auto()  # 1
    VIDEO = auto()  # 2


@dataclass
class VisionResult:
    """视觉分析结果"""

    description: str
    tags: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    objects: list[dict[str, Any]] = field(default_factory=list)
    text_content: str | None = None
    bounding_boxes: list[dict[str, Any]] = field(default_factory=list)


class BaseLLMProvider(ABC):
    """LLM Provider 抽象基类

    所有 LLM Provider 必须实现以下方法：
    - chat(): 非流式对话
    - chat_stream(): 流式对话
    - chat_json(): JSON 模式对话

    可选方法（子类可实现）：
    - analyze_image(): 图片分析
    - analyze_video(): 视频分析
    """

    NAME: str = "base"

    @property
    def vision_capability(self) -> VisionCapability:
        """该 Provider 支持的视觉能力，默认不支持"""
        return VisionCapability.NONE

    @property
    def supports_image(self) -> bool:
        return bool(self.vision_capability & VisionCapability.IMAGE)

    @property
    def supports_video(self) -> bool:
        return bool(self.vision_capability & VisionCapability.VIDEO)

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.8,
        json_mode: bool = False,
        use_fast: bool = False,
        timeout: Optional[float] = None,
        model_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> str:
        """非流式对话"""
        pass

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.8,
        use_fast: bool = False,
        timeout: Optional[float] = None,
        model_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> AsyncGenerator[str, None]:
        """流式对话"""
        pass

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.8,
        use_fast: bool = False,
        timeout: Optional[float] = None,
        model_override: Optional[str] = None,
        api_key_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """JSON 模式对话

        默认实现通过 chat() 获取文本后解析 JSON。
        子类可以覆盖以提供更高效的 JSON 模式实现。
        """
        import json
        import re

        content = await self.chat(
            messages,
            json_mode=True,
            temperature=temperature,
            use_fast=use_fast,
            timeout=timeout,
            model_override=model_override,
            api_key_override=api_key_override,
            base_url_override=base_url_override,
            extra_headers=extra_headers,
        )

        content = content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            try:
                decoder = json.JSONDecoder()
                result, _ = decoder.raw_decode(content)
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                pass

            for pattern in (r'\[[\s\S]*\]', r'\{[\s\S]*\}'):
                match = re.search(pattern, content)
                if match:
                    try:
                        return json.loads(match.group())
                    except json.JSONDecodeError:
                        continue

            raise

    async def analyze_image(
        self,
        image_data: bytes | str,
        prompt: str = "描述这张图片的内容",
        *,
        detail_level: str = "auto",
    ) -> VisionResult | None:
        """分析图片，默认不支持

        子类实现此方法以提供图片分析能力。
        """
        return None

    async def analyze_video(
        self,
        video_path: str,
        prompt: str = "描述视频的主要内容",
        *,
        duration: float | None = None,
        frame_interval: float = 0.5,
        max_frames: int = 10,
    ) -> VisionResult | None:
        """分析视频（提取关键帧），默认不支持

        子类实现此方法以提供视频分析能力。
        """
        return None

    def _log_cache_stats(self, usage: dict) -> None:
        """记录 Cerebras Prompt Caching 缓存命中统计

        子类可以覆盖此方法以自定义日志行为。
        """
        pass

    def _get_model_name(
        self,
        model_override: Optional[str],
        use_fast: bool,
        default_model: str,
        fast_model: Optional[str],
    ) -> str:
        """获取实际使用的模型名称"""
        if model_override:
            return model_override
        if use_fast and fast_model:
            return fast_model
        return default_model
