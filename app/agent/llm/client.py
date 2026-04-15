"""OpenAI 兼容 API 封装（支持 Cerebras Prompt Caching 监控 + Provider 抽象）

向后兼容的 LLMClient，通过 Provider 代理到具体实现：
- LiteLLMProvider: 使用 httpx 调用 OpenAI 兼容 API（默认）
- CerebrasProvider: 使用 Cerebras SDK 直连官方 API

使用方式保持不变，现有代码无需修改。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator
from typing import Any, Optional

from app.config import settings
from app.agent.llm.providers import create_llm_provider, BaseLLMProvider

logger = logging.getLogger(__name__)


class LLMClient:
    """
    LLM 客户端封装（向后兼容）

    通过 Provider 代理到具体实现，保持现有 API 不变。
    """

    def __init__(self):
        self.base_url = settings.LLM_API_BASE_URL
        self.api_key = settings.LLM_API_KEY or ""
        self.model = settings.LLM_MODEL
        self.fast_model = settings.LLM_FAST_MODEL or self.model

        # 创建 Provider（根据配置自动选择）
        self._provider: BaseLLMProvider | None = None

    @property
    def provider(self) -> BaseLLMProvider:
        """懒加载 Provider"""
        if self._provider is None:
            self._provider = create_llm_provider()
            logger.info(f"LLM Client 已初始化，Provider: {self._provider.NAME}")
        return self._provider

    def _build_payload(
        self,
        messages: list[dict],
        temperature: float,
        json_mode: bool,
        use_fast: bool,
        stream: bool,
        model_override: str | None = None,
    ) -> dict:
        """构建请求 payload（仅用于参考，实际由 Provider 处理）"""
        payload = {
            "model": model_override or (self.fast_model if use_fast else self.model),
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
        """构建请求 Headers（仅用于参考，实际由 Provider 处理）"""
        headers = {
            "Authorization": f"Bearer {api_key_override if api_key_override is not None else self.api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _log_cache_stats(self, usage: dict) -> None:
        """记录缓存统计，委托给 Provider"""
        if hasattr(self._provider, '_log_cache_stats'):
            self._provider._log_cache_stats(usage)

    async def chat(
        self,
        messages: list[dict],
        json_mode: bool = False,
        temperature: float = 0.8,
        use_fast: bool = False,
        timeout: float | None = None,
        base_url_override: str | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
        extra_headers: dict | None = None,
    ) -> str:
        """
        调用 chat completion API
        Args:
            messages: OpenAI 格式的消息列表
            json_mode: 是否启用 JSON 输出模式
            temperature: 采样温度
            use_fast: 是否使用快速小模型
        Returns:
            助手回复内容字符串
        """
        return await self.provider.chat(
            messages,
            json_mode=json_mode,
            temperature=temperature,
            use_fast=use_fast,
            timeout=timeout,
            base_url_override=base_url_override,
            model_override=model_override,
            api_key_override=api_key_override,
            extra_headers=extra_headers,
        )

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        use_fast: bool = False,
        timeout: float | None = None,
        base_url_override: str | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
        extra_headers: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式调用 chat completion API，逐块 yield 文本
        """
        async for chunk in self.provider.chat_stream(
            messages,
            temperature=temperature,
            use_fast=use_fast,
            timeout=timeout,
            base_url_override=base_url_override,
            model_override=model_override,
            api_key_override=api_key_override,
            extra_headers=extra_headers,
        ):
            yield chunk

    async def chat_json(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        use_fast: bool = False,
        timeout: float | None = None,
        base_url_override: str | None = None,
        model_override: str | None = None,
        api_key_override: str | None = None,
        extra_headers: dict | None = None,
    ) -> dict:
        """调用 LLM 并解析 JSON 响应"""
        content = await self.chat(
            messages,
            json_mode=True,
            temperature=temperature,
            use_fast=use_fast,
            timeout=timeout,
            base_url_override=base_url_override,
            model_override=model_override,
            api_key_override=api_key_override,
            extra_headers=extra_headers,
        )
        content = content.strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # LLM 可能在 JSON 后追加了多余文本，用 raw_decode 只解析第一个 JSON 值
            try:
                decoder = json.JSONDecoder()
                result, _ = decoder.raw_decode(content)
                return result
            except json.JSONDecodeError:
                pass
            # 最后尝试提取 JSON 块（对象或数组）
            for pattern in (r'\[[\s\S]*\]', r'\{[\s\S]*\}'):
                match = re.search(pattern, content)
                if match:
                    try:
                        return json.loads(match.group())
                    except json.JSONDecodeError:
                        continue
            raise
