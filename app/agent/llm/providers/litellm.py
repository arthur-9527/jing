"""LiteLLM Provider - 使用 httpx 调用 OpenAI 兼容 API

保留现有的 httpx 实现，迁移自 LLMClient。
支持 Cerebras Prompt Caching 监控。

优化：使用长连接单例，避免每次请求重新创建 TCP 连接 + TLS 握手。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from typing import Any, Optional

import httpx

from app.agent.llm.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)

# ⭐ 全局 httpx.AsyncClient 单例（长连接池）
_global_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def get_http_client(timeout: float = 60.0) -> httpx.AsyncClient:
    """获取全局 httpx.AsyncClient 单例
    
    使用长连接池，避免每次请求重新创建 TCP 连接 + TLS 握手。
    在树莓派上 TLS 握手额外增加 100-300ms 延迟。
    
    Args:
        timeout: 请求超时时间
        
    Returns:
        httpx.AsyncClient 实例
    """
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
    """
    LiteLLM Provider - 使用 httpx 调用 OpenAI 兼容 API

    适用于：
    - OpenAI 官方 API
    - 兼容 OpenAI API 格式的第三方 API（如 vLLM、Ollama、本地代理等）
    - Cerebras 通过兼容端点访问
    
    优化：使用长连接单例，避免每次请求重新创建 TCP 连接。
    """

    NAME = "litellm"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        fast_model: str | None = None,
    ):
        """
        Args:
            base_url: API Base URL，默认从环境变量读取
            api_key: API Key，默认从环境变量读取
            model: 默认模型，默认从环境变量读取
            fast_model: 快速模型，默认从环境变量读取
        """
        self.base_url = base_url or os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o")
        self.fast_model = fast_model or os.getenv("LLM_FAST_MODEL", self.model)

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
        """
        记录 Cerebras Prompt Caching 缓存命中统计

        Cerebras API 返回格式：
        {
            "usage": {
                "prompt_tokens": 100,
                "prompt_tokens_details": {
                    "cached_tokens": 50  // 缓存命中的 tokens
                },
                ...
            }
        }
        """
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
        base_url_override: str | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        """非流式对话
        
        ⭐ 使用长连接单例，避免每次请求重新创建 TCP 连接。
        """
        payload = self._build_payload(
            messages,
            temperature,
            json_mode,
            use_fast,
            stream=False,
            model_override=model_override,
        )
        base_url = (base_url_override or self.base_url).rstrip("/")

        # ⭐ 使用长连接单例
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

        # 记录缓存统计
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
        """流式对话
        
        ⭐ 使用长连接单例，避免每次请求重新创建 TCP 连接。
        """
        payload = self._build_payload(
            messages,
            temperature,
            json_mode=False,
            use_fast=use_fast,
            stream=True,
            model_override=model_override,
        )
        base_url = (base_url_override or self.base_url).rstrip("/")

        # ⭐ 使用长连接单例
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

                # 流式结束时检查是否有 usage 信息
                if chunk.get("usage"):
                    self._log_cache_stats(chunk["usage"])