"""LLM Provider 抽象基类

定义统一的 LLM 调用接口，所有 Provider 必须实现这些方法。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any, Optional


class BaseLLMProvider(ABC):
    """LLM Provider 抽象基类

    所有 LLM Provider 必须实现以下方法：
    - chat(): 非流式对话
    - chat_stream(): 流式对话
    - chat_json(): JSON 模式对话
    """

    # Provider 名称，用于日志和配置
    NAME: str = "base"

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
        """
        非流式对话

        Args:
            messages: OpenAI 格式的消息列表
            temperature: 采样温度
            json_mode: 是否启用 JSON 输出模式
            use_fast: 是否使用快速小模型
            timeout: 超时时间（秒）
            model_override: 模型覆盖
            api_key_override: API Key 覆盖
            base_url_override: Base URL 覆盖
            extra_headers: 额外 Headers

        Returns:
            助手回复内容字符串
        """
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
        """
        流式对话

        Args:
            messages: OpenAI 格式的消息列表
            temperature: 采样温度
            use_fast: 是否使用快速小模型
            timeout: 超时时间（秒）
            model_override: 模型覆盖
            api_key_override: API Key 覆盖
            base_url_override: Base URL 覆盖
            extra_headers: 额外 Headers

        Yields:
            文本片段字符串
        """
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
        """
        JSON 模式对话

        默认实现通过 chat() 获取文本后解析 JSON。
        子类可以覆盖以提供更高效的 JSON 模式实现。

        Args:
            messages: OpenAI 格式的消息列表
            temperature: 采样温度
            use_fast: 是否使用快速小模型
            timeout: 超时时间（秒）
            model_override: 模型覆盖
            api_key_override: API Key 覆盖
            base_url_override: Base URL 覆盖
            extra_headers: 额外 Headers

        Returns:
            解析后的 JSON 字典
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
            # 尝试 raw_decode
            try:
                decoder = json.JSONDecoder()
                result, _ = decoder.raw_decode(content)
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                pass

            # 尝试提取 JSON 块
            for pattern in (r'\[[\s\S]*\]', r'\{[\s\S]*\}'):
                match = re.search(pattern, content)
                if match:
                    try:
                        return json.loads(match.group())
                    except json.JSONDecodeError:
                        continue

            raise

    def _log_cache_stats(self, usage: dict) -> None:
        """
        记录 Cerebras Prompt Caching 缓存命中统计

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
