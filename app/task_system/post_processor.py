"""
任务系统二次改写模块 - 结果二次台词改写

功能：
1. 将 Provider 返回的原始结果改写为播报内容
2. 解析 <a> 和 <panel> 标签
3. 合并 Panel 数据
4. 支持多种 LLM Provider (Cerebras / LiteLLM)
"""

import re
import json
from typing import Dict, Any, Optional

from loguru import logger

from .models import ProviderResult, BroadcastContent, Task
from .config import get_task_system_settings


class PostProcessor:
    """二次改写处理器
    
    将 Provider 返回的原始结果改写为最终的播报内容：
    - 调用 LLM 进行台词改写
    - 解析 <a> 动作标签
    - 解析 <panel> 标签并合并数据
    """
    
    def __init__(self, character_config=None):
        """初始化二次改写处理器
        
        Args:
            character_config: 角色配置（用于动作规则）
        """
        self._settings = get_task_system_settings()
        self._character_config = character_config
        self._http_client = None
        self._cerebras_client = None
        self._initialized = False
    
    async def initialize(self) -> None:
        """初始化 LLM 客户端"""
        if self._initialized:
            return
        
        # 根据 Provider 类型初始化客户端
        provider = self._settings.POST_PROCESS_PROVIDER
        if provider == "cerebras":
            await self._init_cerebras_client()
        else:
            await self._init_http_client()
        
        self._initialized = True
        logger.info(f"[PostProcessor] 已初始化，provider={provider}")
    
    async def _init_http_client(self) -> None:
        """初始化 HTTP 客户端（用于 LiteLLM）"""
        import httpx
        
        timeout_config = httpx.Timeout(
            connect=5.0,
            read=self._settings.POST_PROCESS_TIMEOUT,
            write=5.0,
            pool=10.0
        )
        self._http_client = httpx.AsyncClient(
            timeout=timeout_config,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        )
    
    async def _init_cerebras_client(self) -> None:
        """初始化 Cerebras 客户端（支持中转端点）"""
        from cerebras.cloud.sdk import Cerebras
        
        api_key = self._settings.POST_PROCESS_API_KEY
        base_url = self._settings.POST_PROCESS_BASE_URL
        
        if api_key:
            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
                logger.info(f"[PostProcessor] Cerebras 客户端使用中转端点: {base_url}")
            self._cerebras_client = Cerebras(**client_kwargs)
            logger.info("[PostProcessor] Cerebras 客户端已初始化")
        else:
            logger.warning("[PostProcessor] 未配置 Cerebras API Key，将使用默认行为")
    
    async def close(self) -> None:
        """关闭客户端"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        self._cerebras_client = None
        self._initialized = False
    
    async def process(
        self,
        provider_result: ProviderResult,
        task: Task,
    ) -> BroadcastContent:
        """处理 Provider 结果，生成播报内容
        
        ⭐ 无论成功还是失败，都使用同一套提示词让 LLM 改写
        
        Args:
            provider_result: Provider 返回的原始结果（可能是错误）
            task: 任务对象（包含上下文）
        
        Returns:
            BroadcastContent: 播报内容
        """
        if not self._settings.POST_PROCESS_ENABLED:
            # 二次改写禁用，直接返回原始结果/错误
            if provider_result.success:
                return BroadcastContent(
                    task_id=task.id,
                    content=provider_result.content,
                    panel_html=provider_result.panel_html,
                    action=None,
                )
            else:
                return BroadcastContent(
                    task_id=task.id,
                    content=provider_result.error or "工具调用失败",
                    panel_html=None,
                    action=None,
                )
        
        try:
            # ⭐ 调用 LLM 进行二次改写（成功和失败都用同一套流程）
            final_result = await self._call_llm_rewrite(
                task=task,
                provider_result=provider_result,
            )
            
            logger.info(
                f"[PostProcessor] 二次改写完成: task={task.id[:8]}, "
                f"success={provider_result.success}, "
                f"content={final_result.content[:50]}..."
            )
            
            return final_result
            
        except Exception as e:
            logger.error(f"[PostProcessor] 二次改写失败: {e}")
            # ⭐ LLM 改写失败，返回原始内容（成功=content，失败=error）
            fallback_content = provider_result.content if provider_result.success else (provider_result.error or "工具调用失败")
            logger.info(
                f"[PostProcessor] 使用 fallback 内容: task={task.id[:8]}, "
                f"content={fallback_content[:50]}..."
            )
            return BroadcastContent(
                task_id=task.id,
                content=fallback_content,
                panel_html=provider_result.panel_html if provider_result.success else None,
                action=None,
            )
    
    async def _call_llm_rewrite(
        self,
        task: Task,
        provider_result: ProviderResult,
    ) -> BroadcastContent:
        """调用 LLM 进行二次改写
        
        ⭐ 成功和失败都用同一套提示词：
        - 成功：tool_result = provider_result.content
        - 失败：tool_result = provider_result.error
        
        Args:
            task: 任务对象
            provider_result: Provider 返回的原始结果（可能是错误）
        
        Returns:
            BroadcastContent: 改写后的播报内容
        """
        # 构建二次改写 Prompt
        from app.agent.prompt.tool_rewrite_prompt import build_tool_rewrite_prompt
        
        panel_html_content = None
        if provider_result.panel_html:
            panel_html_content = provider_result.panel_html.get("html", "")
        
        # ⭐ 失败时用 error，成功时用 content
        tool_result = provider_result.content if provider_result.success else (provider_result.error or "工具调用失败")
        
        prompt = build_tool_rewrite_prompt(
            user_input=task.context.get("user_input", ""),
            tool_result=tool_result,
            panel_html_content=panel_html_content,
            config=self._character_config,
        )
        
        # 调用 LLM
        provider = self._settings.POST_PROCESS_PROVIDER
        model = self._settings.POST_PROCESS_MODEL
        
        if provider == "cerebras" and self._cerebras_client:
            response_text = await self._call_cerebras(prompt, model)
        else:
            response_text = await self._call_litellm(prompt, model)
        
        # 解析响应
        return self._parse_llm_response(
            task_id=task.id,
            response_text=response_text,
            original_panel_html=provider_result.panel_html,
        )
    
    async def _call_cerebras(self, prompt: str, model: str) -> str:
        """使用 Cerebras SDK 调用"""
        import asyncio
        if not self._cerebras_client:
            raise RuntimeError("Cerebras 客户端未初始化")

        response = await asyncio.to_thread(
            self._cerebras_client.chat.completions.create,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )

        content = response.choices[0].message.content
        return content if isinstance(content, str) else str(content)
    
    async def _call_litellm(self, prompt: str, model: str) -> str:
        """使用 LiteLLM (OpenAI 兼容 API) 调用"""
        if not self._http_client:
            await self._init_http_client()
        
        headers = {"Content-Type": "application/json"}
        if self._settings.POST_PROCESS_API_KEY:
            headers["Authorization"] = f"Bearer {self._settings.POST_PROCESS_API_KEY}"
        
        base_url = self._settings.POST_PROCESS_BASE_URL.rstrip('/')
        api_url = f"{base_url}/chat/completions"
        
        response = await self._http_client.post(
            api_url,
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
            },
        )
        
        response.raise_for_status()
        data = response.json()
        
        content = data["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else str(content)
    
    def _parse_llm_response(
        self,
        task_id: str,
        response_text: str,
        original_panel_html: Optional[Dict[str, Any]],
    ) -> BroadcastContent:
        """解析 LLM 响应
        
        解析 <a> 和 <panel> 标签，合并 Panel 数据
        """
        response_text = response_text.strip()
        
        # ⭐ 解析 <a> 动作标签 - 存储原始字符串
        action_data = None
        action_match = re.search(r'<a>(.*?)</a>', response_text, re.DOTALL)
        if action_match:
            # 存储原始 <a>...</a> 字符串
            action_data = action_match.group(0).strip()
            if action_data:
                logger.info(
                    f"[PostProcessor] 提取动作数据: {action_data[:50]}..."
                )
            # 移除 <a> 标签
            response_text = re.sub(r'<a>.*?</a>', '', response_text, flags=re.DOTALL).strip()
        
        # 解析 <panel> 标签
        panel_html_from_llm = None
        panel_match = re.search(r'<panel>(.*?)</panel>', response_text, re.DOTALL)
        if panel_match:
            panel_json_str = panel_match.group(1).strip()
            try:
                panel_html_from_llm = json.loads(panel_json_str)
                logger.info(
                    f"[PostProcessor] LLM 返回了 panel_html: "
                    f"x={panel_html_from_llm.get('x')}, y={panel_html_from_llm.get('y')}"
                )
            except json.JSONDecodeError:
                logger.warning(f"[PostProcessor] panel JSON 解析失败: {panel_json_str[:100]}")
            # 移除 <panel> 标签
            response_text = re.sub(r'<panel>.*?</panel>', '', response_text, flags=re.DOTALL).strip()
        
        # 最终台词
        final_content = response_text
        
        # 合并 Panel 数据
        final_panel_html = self._merge_panel_html(
            panel_html_from_llm=panel_html_from_llm,
            original_panel_html=original_panel_html,
        )
        
        return BroadcastContent(
            task_id=task_id,
            content=final_content,
            panel_html=final_panel_html,
            action=action_data,  # ⭐ 存储原始 <a>...</a> 字符串
        )
    
    def _merge_panel_html(
        self,
        panel_html_from_llm: Optional[Dict[str, Any]],
        original_panel_html: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """合并 Panel 数据
        
        优先级：
        1. 如果 LLM 返回完整 Panel（包含 html），使用 LLM 的
        2. 如果 LLM 只返回配置（不包含 html），合并原始数据
        3. 如果 LLM 没有返回 Panel，使用原始数据
        """
        if panel_html_from_llm:
            if panel_html_from_llm.get("html"):
                # LLM 完整返回
                return panel_html_from_llm
            else:
                # LLM 只返回配置，合并原始数据
                if original_panel_html:
                    merged = original_panel_html.copy()
                    config_fields = ["type", "visible", "x", "y", "width", "height"]
                    for field in config_fields:
                        if field in panel_html_from_llm:
                            merged[field] = panel_html_from_llm[field]
                    return merged
                else:
                    return panel_html_from_llm
        else:
            # 使用原始数据
            return original_panel_html
    
    def _parse_action_json(self, text: str) -> Optional[Dict[str, Any]]:
        """解析动作标签内的 JSON
        
        支持多种解析策略：
        1. 直接 json.loads
        2. raw_decode 只解析第一个 JSON
        3. 正则提取 JSON 块
        4. 尝试修复被截断的 JSON
        """
        text = text.strip()
        if not text:
            return None
        
        # 策略1：直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # 策略2：raw_decode
        try:
            decoder = json.JSONDecoder()
            result, _ = decoder.raw_decode(text)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass
        
        # 策略3：正则提取 JSON 块
        try:
            match = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', text)
            if match:
                return json.loads(match.group())
        except json.JSONDecodeError:
            pass
        
        # 策略4：尝试提取 action 字段
        try:
            action_match = re.search(r'"action"\s*:\s*"([^"]+)"', text)
            if action_match:
                result = {"action": action_match.group(1)}
                emotion_match = re.search(r'"emotion"\s*:\s*"([^"]+)"', text)
                if emotion_match:
                    result["emotion"] = emotion_match.group(1)
                return result
        except Exception:
            pass
        
        logger.warning(f"[PostProcessor] 动作 JSON 解析失败: {text[:100]}")
        return None


# ===== 全局实例（懒加载）=====
_post_processor: Optional[PostProcessor] = None


def get_post_processor(character_config=None) -> PostProcessor:
    """获取二次改写处理器实例"""
    global _post_processor
    if _post_processor is None:
        _post_processor = PostProcessor(character_config=character_config)
    return _post_processor