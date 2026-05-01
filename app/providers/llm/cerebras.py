"""Cerebras Provider - 使用 AsyncCerebras SDK 直连官方 API

使用 cerebras-cloud-sdk 的 AsyncCerebras 异步客户端，支持：
- 原生异步支持 (async/await)
- Prompt Caching（自动利用缓存）
- 高效流式输出
- 完整的 usage 统计

Cerebras 优势：
1. 超低延迟 - 比 OpenAI 快 10-100 倍
2. Prompt Caching - 缓存相同前缀，系统 prompt 只计费一次
3. 成本低 - 比 GPT-4 便宜 100 倍
4. 原生支持长上下文 - 最高 2M tokens
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from typing import Any, Optional

from app.config import settings
from app.providers.llm.base import BaseLLMProvider, VisionCapability

logger = logging.getLogger(__name__)


class CerebrasProvider(BaseLLMProvider):
    """
    Cerebras Provider - 使用 AsyncCerebras SDK 直连官方 API

    适用于 Cerebras 官方 API，支持：
    - llama3.1-8b (最快)
    - llama3.3-70b (最强)
    - qwen-3-32b-a22b (中文优化)
    等模型

    特点：
    - 使用 AsyncCerebras 异步客户端
    - 原生支持 Prompt Caching（缓存系统 prompt）
    - 高效的流式处理
    - 自动管理连接池
    """

    NAME = "cerebras"

    @property
    def vision_capability(self) -> VisionCapability:
        """Cerebras 模型当前不支持视觉能力"""
        return VisionCapability.NONE

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        fast_model: str | None = None,
        base_url: str | None = None,
    ):
        """
        Args:
            api_key: Cerebras API Key，默认从 CEREBRAS_API_KEY 配置读取
            model: 默认模型，默认从 CHAT_MODEL 或 CEREBRAS_MODEL 配置读取
            fast_model: 快速模型，暂不区分
            base_url: API 端点 URL，默认从 CEREBRAS_API_BASE_URL 配置读取（用于中转）
        """
        self.api_key = api_key or settings.CEREBRAS_API_KEY or ""
        if not self.api_key:
            raise ValueError("CEREBRAS_API_KEY is required for CerebrasProvider")

        self.model = model or settings.CHAT_MODEL or settings.CEREBRAS_MODEL
        self.fast_model = fast_model or self.model  # 暂不区分
        self.base_url = base_url or settings.CEREBRAS_API_BASE_URL

        # 懒加载 AsyncCerebras 客户端
        self._client = None

    @property
    def client(self):
        """懒加载 AsyncCerebras 客户端"""
        if self._client is None:
            try:
                from cerebras.cloud.sdk import AsyncCerebras
                # 支持自定义 base_url（用于 API 中转）
                client_kwargs = {"api_key": self.api_key}
                if self.base_url:
                    client_kwargs["base_url"] = self.base_url
                    logger.info(f"AsyncCerebras 客户端初始化，使用中转端点: {self.base_url}，模型: {self.model}")
                else:
                    logger.info(f"AsyncCerebras 客户端初始化，使用官方 API，模型: {self.model}")
                self._client = AsyncCerebras(**client_kwargs)
            except ImportError:
                raise ImportError(
                    "cerebras-cloud-sdk 未安装，请运行: pip install cerebras-cloud-sdk"
                )
        return self._client

    def _get_model_name(
        self,
        model_override: str | None,
        use_fast: bool,
        default_model: str,
        fast_model: str | None,
    ) -> str:
        """获取实际使用的模型名称"""
        if model_override:
            return model_override
        if use_fast and fast_model:
            return fast_model
        return default_model

    def _log_cache_stats(self, usage) -> None:
        """
        记录 Cerebras Prompt Caching 缓存命中统计

        Cerebras SDK 返回的 usage 是 Pydantic 模型对象，
        需要使用 getattr 访问属性而不是字典的 get 方法。
        """
        if not usage:
            return

        # 使用 getattr 访问 Pydantic 模型的属性
        prompt_tokens = getattr(usage, 'prompt_tokens', 0) or 0
        prompt_tokens_details = getattr(usage, 'prompt_tokens_details', None)

        cached_tokens = 0
        if prompt_tokens_details:
            if hasattr(prompt_tokens_details, 'cached_tokens'):
                cached_tokens = getattr(prompt_tokens_details, 'cached_tokens', 0) or 0
            elif isinstance(prompt_tokens_details, dict):
                cached_tokens = prompt_tokens_details.get('cached_tokens', 0) or 0

        if prompt_tokens > 0:
            cache_ratio = (cached_tokens / prompt_tokens) * 100
            if cached_tokens > 0:
                if cache_ratio >= 50:
                    logger.info(
                        "🔒 Cache HIT: %d/%d tokens (%.1f%%) - 高效缓存",
                        cached_tokens, prompt_tokens, cache_ratio
                    )
                elif cache_ratio >= 20:
                    logger.info(
                        "🔒 Cache hit: %d/%d tokens (%.1f%%)",
                        cached_tokens, prompt_tokens, cache_ratio
                    )
                else:
                    logger.debug(
                        "Cache hit: %d/%d tokens (%.1f%%)",
                        cached_tokens, prompt_tokens, cache_ratio
                    )
            else:
                logger.debug(
                    "Cache miss: %d prompt tokens (首次请求或前缀不匹配)",
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
        model_override: str | None = None,
        api_key_override: str | None = None,  # Cerebras 不支持覆盖 API Key
        base_url_override: str | None = None,  # Cerebras 不支持覆盖 URL
        extra_headers: dict[str, str] | None = None,  # Cerebras 不支持自定义 Headers
    ) -> str:
        """
        非流式对话

        注意：
        - api_key_override 和 base_url_override 在 Cerebras Provider 中被忽略
        - json_mode 通过在 system prompt 中添加 JSON 格式要求来实现
        """
        model = self._get_model_name(
            model_override, use_fast, self.model, self.fast_model
        )

        # 如果启用了 JSON 模式，在 system prompt 中添加格式要求
        if json_mode:
            messages = self._add_json_format_hint(messages)

        try:
            # 使用 await 调用异步方法
            response = await self.client.chat.completions.create(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=4096,
            )

            content = response.choices[0].message.content

            # 记录缓存统计
            if hasattr(response, 'usage') and response.usage:
                self._log_cache_stats(response.usage)

            return content or ""

        except Exception as e:
            logger.error(f"Cerebras API 调用失败: {e}")
            raise

    def _add_json_format_hint(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        为 JSON 模式添加格式提示

        Cerebras SDK 不原生支持 response_format，
        通过在 system prompt 中添加要求来引导 JSON 输出。
        """
        hint = "\n\n请以有效的 JSON 格式回复，不要包含其他内容。"
        modified_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                modified_messages.append({
                    **msg,
                    "content": msg["content"] + hint
                })
            else:
                modified_messages.append(msg)
        return modified_messages

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.8,
        use_fast: bool = False,
        timeout: float | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
        base_url_override: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式对话 - 使用 async for 迭代

        AsyncCerebras 支持异步迭代器，可以直接使用 async for。
        """
        model = self._get_model_name(
            model_override, use_fast, self.model, self.fast_model
        )

        try:
            # 获取异步流
            stream = await self.client.chat.completions.create(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=4096,
                stream=True,
            )

            # 使用 async for 迭代流
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content

                # 尝试获取 usage 信息
                if hasattr(chunk, 'usage') and chunk.usage:
                    self._log_cache_stats(chunk.usage)

        except Exception as e:
            logger.error(f"Cerebras 流式 API 调用失败: {e}")
            raise

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.8,
        use_fast: bool = False,
        timeout: float | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
        base_url_override: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        JSON 模式对话

        通过在 system prompt 中添加格式要求来引导 JSON 输出，
        然后解析响应。
        """
        import json
        import re

        # 添加 JSON 格式提示
        messages = self._add_json_format_hint(messages)

        content = await self.chat(
            messages,
            json_mode=False,  # 已经添加了提示，不需要再次添加
            temperature=temperature,
            use_fast=use_fast,
            timeout=timeout,
            model_override=model_override,
            api_key_override=api_key_override,
            base_url_override=base_url_override,
            extra_headers=extra_headers,
        )

        content = content.strip()

        # 尝试多种解析方式
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

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

        raise ValueError(f"无法解析 JSON 响应: {content[:200]}")
