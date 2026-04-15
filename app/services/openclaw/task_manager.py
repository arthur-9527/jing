"""
OpenClaw 任务管理器 - 事件驱动版本

核心设计：
1. 事件驱动：WebSocket 监听 final 事件，立即更新任务状态
2. 超时保护：60 秒内没收到 final，标记为超时
3. 主动轮询：每 60 秒查询一次 RUNNING 任务的状态（容错）
4. 并发控制：3 个 session 独立处理，互不阻塞
5. 二次处理：OpenClaw 结果到达后，触发 LLM 二次重写

特点：
- 收到 final 立即完成，响应快
- 不需要轮询，效率高
- 充分利用 WebSocket 的推送特性
- 支持 LLM 二次处理生成最终结果
"""

import asyncio
import time
import json
from typing import Optional, Dict, Any, List
from loguru import logger

from .config import get_openclaw_config
from .models import Task, TaskStatus, SessionState, SessionStatus
from .redis_repo import OpenClawTaskRepository
from .ws_client import OpenClawWSClient


class LLMPostProcessor:
    """LLM 二次处理器

    负责在 OpenClaw 返回结果后，调用独立的 LLM 进行二次重写，
    生成最终的对用户回复内容。

    支持两种 Provider：
    - litellm: 使用 HTTP 调用 OpenAI 兼容 API
    - cerebras: 使用 Cerebras SDK 直连
    """

    def __init__(self, config=None):
        self._config = get_openclaw_config()
        self._character_config = config  # 角色配置（用于动作规则）
        self._http_client = None
        self._cerebras_client = None
        self._initialized = False

    async def _get_http_client(self):
        """获取 HTTP 客户端（懒加载，用于 litellm）"""
        if self._http_client is None:
            import httpx
            timeout_config = httpx.Timeout(
                connect=5.0,
                read=self._config.llm_post_process_timeout,
                write=5.0,
                pool=10.0
            )
            self._http_client = httpx.AsyncClient(
                timeout=timeout_config,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
            )
        return self._http_client

    async def _get_cerebras_client(self):
        """获取 Cerebras 客户端（懒加载）"""
        if self._cerebras_client is None:
            from cerebras.cloud.sdk import Cerebras
            self._cerebras_client = Cerebras(
                api_key=self._config.llm_post_process_api_key
            )
        return self._cerebras_client

    async def process_openclaw_result(
        self,
        task: Task,
        openclaw_result: Dict[str, Any],
        active_panels: list[dict] | None = None,
        screen_width: int = 1920,
        screen_height: int = 1080,
    ) -> Dict[str, Any]:
        """处理 OpenClaw 结果，生成最终回复

        参考 HTTP 流程的处理方式（main.py::_run_second_pass_with_tool_result）

        Args:
            task: 任务对象（包含二次处理所需的上下文）
            openclaw_result: OpenClaw 返回的结果
            active_panels: 当前已显示的 panel 列表（用于智能布局）
            screen_width: 屏幕宽度（像素）
            screen_height: 屏幕高度（像素）

        Returns:
            final_result: 最终结果字典，包含 {
                "content": "最终台词",
                "panel_html": {...},  # 可选
                "action": {...},  # 可选，解析出的动作
            }
        """
        import re

        try:
            # 提取 OpenClaw 结果
            tool_result_content = openclaw_result.get("content", "")
            panel_html_from_openclaw = openclaw_result.get("panel_html")

            # 构建二次重写 Prompt（简化版）
            from app.agent.prompt.tool_rewrite_prompt import build_tool_rewrite_prompt

            # 提取 panel 的 html 内容（让 AI 理解展示信息）
            panel_html_content = None
            if panel_html_from_openclaw:
                panel_html_content = panel_html_from_openclaw.get("html", "")

            prompt = build_tool_rewrite_prompt(
                user_input=task.user_input or "",
                tool_result=tool_result_content,
                panel_html_content=panel_html_content,
                config=self._character_config,
            )

            provider = self._config.llm_post_process_provider
            model = self._config.llm_post_process_model

            logger.info(f"[LLMPostProcessor] 开始二次处理：task={task.id[:8]}, provider={provider}, model={model}")

            # 根据 provider 选择调用方式（返回字符串）
            if provider == "cerebras":
                response_text = await self._call_cerebras(prompt, model)
            else:
                # 默认使用 litellm (OpenAI 兼容 API)
                response_text = await self._call_litellm(prompt, model)

            # 参考 main.py::_run_second_pass_with_tool_result 的处理方式
            response_text = response_text.strip()

            # ⭐ 解析 <panel> 标签（在解析 <panel> 之前）
            action_json = None
            action_match = re.search(r'<a>(.*?)</a>', response_text, re.DOTALL)
            if action_match:
                action_content = action_match.group(1).strip()
                action_json = self._parse_action_json(action_content)
                if action_json:
                    logger.info(f"[LLMPostProcessor] 解析到动作标签: action={action_json.get('action')}, emotion={action_json.get('emotion')}")
                # 从响应中移除 <a> 标签
                response_text = re.sub(r'<a>.*?</a>', '', response_text, flags=re.DOTALL).strip()

            # 解析 <panel> 标签
            panel_html_from_llm = None
            panel_match = re.search(r'<panel>(.*?)</panel>', response_text, re.DOTALL)
            if panel_match:
                panel_json_str = panel_match.group(1).strip()
                try:
                    panel_html_from_llm = json.loads(panel_json_str)
                    logger.info(f"[LLMPostProcessor] LLM 返回了 panel_html: x={panel_html_from_llm.get('x')}, y={panel_html_from_llm.get('y')}, width={panel_html_from_llm.get('width')}, height={panel_html_from_llm.get('height')}, has_html={bool(panel_html_from_llm.get('html'))}")
                except json.JSONDecodeError:
                    logger.warning(f"[LLMPostProcessor] panel JSON 解析失败: {panel_json_str[:100]}")
                # 从响应中移除 <panel> 标签
                response_text = re.sub(r'<panel>.*?</panel>', '', response_text, flags=re.DOTALL).strip()

            # 最终台词（已过滤 <a> 和 <panel> 标签）
            final_content = response_text

            # ⭐ 关键修改：确保 panel_html 完整
            final_panel_html = None
            if panel_html_from_llm:
                # LLM 返回了 panel，检查是否完整
                if panel_html_from_llm.get("html"):
                    # LLM 完整复制了（包括 html），直接使用
                    final_panel_html = panel_html_from_llm
                    logger.info(f"[LLMPostProcessor] 使用 LLM 返回的完整 panel")
                else:
                    # LLM 只返回了配置（缺少 html），合并原始数据
                    if panel_html_from_openclaw:
                        final_panel_html = panel_html_from_openclaw.copy()
                        # 只更新配置字段，保留原始 html
                        config_fields = ["type", "visible", "x", "y", "width", "height"]
                        for field in config_fields:
                            if field in panel_html_from_llm:
                                final_panel_html[field] = panel_html_from_llm[field]
                        logger.info(f"[LLMPostProcessor] 合并 panel: 使用 LLM 配置 + OpenClaw html")
                    else:
                        # 没有原始数据，使用 LLM 返回的（不完整）
                        final_panel_html = panel_html_from_llm
                        logger.warning(f"[LLMPostProcessor] LLM 返回的 panel 不完整且无原始数据，html 字段缺失")
            else:
                # LLM 没有返回 panel，使用 OpenClaw 原始的
                final_panel_html = panel_html_from_openclaw
                logger.debug(f"[LLMPostProcessor] 使用 OpenClaw 原始 panel")

            logger.info(f"[LLMPostProcessor] 二次处理成功：task={task.id[:8]}, content={final_content[:50]}...")

            # 返回最终结果
            result = {
                "content": final_content,
                "panel_html": final_panel_html,
            }
            if action_json:
                result["action"] = action_json

            return result

        except Exception as e:
            logger.error(f"[LLMPostProcessor] 二次处理失败：task={task.id[:8]}, error={e}")
            # 失败时返回 OpenClaw 原始结果
            return openclaw_result

    def _parse_action_json(self, text: str) -> dict | None:
        """
        解析动作标签内的 JSON，参考 main.py::_parse_action_json。

        支持多种解析策略：
        1. 直接 json.loads
        2. raw_decode 只解析第一个 JSON
        3. 正则提取 JSON 块
        4. 尝试修复被截断的 JSON（缺少结尾的 }>）
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

        # 策略3：正则提取 JSON 块（处理嵌套情况）
        # 匹配最外层的 { ... }
        try:
            # 处理标准 JSON 格式
            match = re.search(r'\{(?:[^{}]|\{[^{}]*\})*\}', text)
            if match:
                json_str = match.group()
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # 策略4：尝试修复被截断的 JSON
        # 常见情况：JSON 被截断在 "}>我" 这种模式
        try:
            # 找到最后一个完整的字段
            truncated_match = re.search(r'(\{"[^"]*":\s*"[^"]*"[^}]*)$', text)
            if truncated_match:
                # 尝试补全
                partial = truncated_match.group(1)
                # 尝试解析（虽然可能不完整，但至少能获取 action）
                result = json.loads(partial + '"}')
                return result
        except (json.JSONDecodeError, ValueError):
            pass

        # 策略5：尝试提取 action 字段（即使 JSON 不完整）
        try:
            action_match = re.search(r'"action"\s*:\s*"([^"]+)"', text)
            if action_match:
                result = {"action": action_match.group(1)}
                # 尝试提取 emotion
                emotion_match = re.search(r'"emotion"\s*:\s*"([^"]+)"', text)
                if emotion_match:
                    result["emotion"] = emotion_match.group(1)
                return result
        except Exception:
            pass

        logger.warning(f"[LLMPostProcessor] 动作 JSON 解析失败: {text[:100]}")
        return None

    async def _call_litellm(self, prompt: str, model: str) -> str:
        """使用 LiteLLM (OpenAI 兼容 API) 调用

        Args:
            prompt: 提示词
            model: 模型名称

        Returns:
            LLM 返回的文本（不是 JSON，是包含 <panel> 标签的字符串）
        """
        client = await self._get_http_client()

        headers = {
            "Content-Type": "application/json",
        }
        if self._config.llm_post_process_api_key:
            headers["Authorization"] = f"Bearer {self._config.llm_post_process_api_key}"

        # 构建 API URL（避免重复 /v1）
        base_url = self._config.llm_post_process_base_url.rstrip('/')
        api_url = f"{base_url}/chat/completions"

        # 不要求 JSON 格式，接收自然文本（包含 <panel> 标签）
        response = await client.post(
            api_url,
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
            },
        )

        response.raise_for_status()
        data = response.json()

        # 返回文本内容
        content = data["choices"][0]["message"]["content"]
        logger.debug(f"[LLMPostProcessor._call_litellm] 原始返回: {content[:200]}...")
        return content if isinstance(content, str) else str(content)

    async def _call_cerebras(self, prompt: str, model: str) -> str:
        """使用 Cerebras SDK 调用

        Args:
            prompt: 提示词
            model: 模型名称

        Returns:
            LLM 返回的文本（不是 JSON，是包含 <panel> 标签的字符串）
        """
        client = await self._get_cerebras_client()

        # 使用 Cerebras SDK 调用（支持自定义模型名称）
        # 不要求 JSON 格式，接收自然文本（包含 <panel> 标签）
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )

        # 返回文本内容
        content = response.choices[0].message.content
        logger.debug(f"[LLMPostProcessor._call_cerebras] 原始返回: {content[:200]}...")
        return content if isinstance(content, str) else str(content)

    async def close(self):
        """关闭客户端"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        self._cerebras_client = None  # Cerebras SDK 客户端不需要异步关闭


class OpenClawTaskManager:
    """OpenClaw 任务管理器（事件驱动版本 + LLM 二次处理）

    核心特点：
    - 事件驱动：收到 final 立即完成
    - 超时保护：60 秒超时
    - 真并发：每个任务独立协程
    - 轮询容错：每 60 秒主动检查一次
    - LLM 二次处理：OpenClaw 结果到达后，自动触发 LLM 二次重写
    """

    def __init__(self, config=None):
        self._config = get_openclaw_config()
        self._character_config = config  # 角色配置
        self._ws_client = OpenClawWSClient()
        self._redis_repo = OpenClawTaskRepository()
        self._post_processor = LLMPostProcessor(config=config)  # 传入 config 到二次处理器

        # 3 个 session 状态
        self._sessions: Dict[str, SessionState] = {}
        for session_key in self._config.session.session_keys:
            self._sessions[session_key] = SessionState(session_key=session_key)

        # 后台任务
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running = False

    # ==================== 生命周期 ====================

    async def start(self) -> None:
        """启动服务"""
        if self._running:
            logger.warning("[TaskManager] 已在运行中")
            return

        logger.info("[TaskManager] 启动中...")

        # 连接 Redis
        await self._redis_repo.connect()
        logger.info("[TaskManager] Redis 已连接")

        # ⭐ 重启时无条件清理所有任务（不管状态）
        logger.warning("[TaskManager] 项目启动，清理所有遗留任务...")
        cleared_count = await self._redis_repo.clear_all_on_restart()
        logger.warning(f"[TaskManager] 已清空 {cleared_count} 个遗留任务")

        # ⭐ 注册回调（final 事件和重连事件）
        self._ws_client.register_final_callback(self._on_final_event)
        self._ws_client.register_reconnect_callback(self._on_ws_reconnect)

        await self._ws_client.connect()
        logger.info("[TaskManager] WebSocket 已连接")

        # 初始化所有 Session 状态
        for session in self._sessions.values():
            session.release()

        self._running = True

        # 启动调度器（分配 pending 任务）
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

        logger.info("[TaskManager] 已启动")

    async def stop(self) -> None:
        """停止服务"""
        if not self._running:
            return

        logger.info("[TaskManager] 停止中...")

        self._running = False

        # 取消后台任务
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        # ⭐ 取消注册回调
        self._ws_client.unregister_final_callback(self._on_final_event)

        # 断开连接
        await self._ws_client.disconnect()
        await self._redis_repo.disconnect()

        # ⭐ 关闭 LLM 二次处理器的客户端
        await self._post_processor.close()

        logger.info("[TaskManager] 已停止")

    @property
    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running

    # ==================== 对外接口 ====================

    async def submit_task(
        self,
        tool_prompt: str,
        user_input: Optional[str] = None,
        memory_context: Optional[str] = None,
        conversation_history: Optional[str] = None,
        inner_monologue: Optional[str] = None,
        emotion_delta: Optional[Dict[str, float]] = None,
    ) -> str:
        """提交任务

        Args:
            tool_prompt: LLM 的工具调用提示
            user_input: 用户输入（用于二次处理）
            memory_context: 记忆上下文（用于二次处理）
            conversation_history: 对话历史（用于二次处理）
            inner_monologue: 第一阶段内心独白（用于二次处理）
            emotion_delta: 情绪变化（用于二次处理）

        Returns:
            任务 ID
        """
        task_id = await self._redis_repo.create_task(
            tool_prompt=tool_prompt,
            user_input=user_input,
            memory_context=memory_context,
            conversation_history=conversation_history,
            inner_monologue=inner_monologue,
            emotion_delta=emotion_delta,
        )
        logger.info(f"[TaskManager] 任务已提交：{task_id}")
        return task_id

    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态字典
        """
        task = await self._redis_repo.get_task(task_id)
        if not task:
            return {"error": "任务不存在", "task_id": task_id}

        return task.to_public_dict()

    async def wait_for_result(
        self,
        task_id: str,
        timeout: float = None,
    ) -> Dict[str, Any]:
        """等待任务完成（阻塞）

        Args:
            task_id: 任务 ID
            timeout: 超时时间（默认使用配置）

        Returns:
            任务结果字典

        Raises:
            TimeoutError: 超时
        """
        if timeout is None:
            timeout = self._config.timeout.task_timeout

        start_time = time.time()

        while time.time() - start_time < timeout:
            task = await self._redis_repo.get_task(task_id)
            if not task:
                raise ValueError(f"任务不存在：{task_id}")

            # 检查是否已完成
            if task.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.TIMEOUT,
                TaskStatus.CANCELLED,
            ):
                return task.to_result_dict()

            # 等待一段时间再查询（事件驱动会主动更新状态）
            await asyncio.sleep(0.5)

        # 超时
        await self._redis_repo.update_status(task_id, TaskStatus.TIMEOUT)
        raise TimeoutError(f"任务超时：{task_id}")

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务（打断机制）

        Args:
            task_id: 任务 ID

        Returns:
            是否取消成功
        """
        task = await self._redis_repo.get_task(task_id)
        if not task:
            logger.warning(f"[TaskManager] 取消任务失败：任务不存在 {task_id[:8]}...")
            return False

        logger.info(f"[TaskManager] 取消任务：{task_id[:8]}..., status={task.status.value}")

        # 如果任务正在运行，发送 abort
        if task.status == TaskStatus.RUNNING and task.session_key and task.run_id:
            try:
                # 发送 chat.abort 到 OpenClaw
                await self._ws_client.abort_chat(
                    session_key=task.session_key,
                    run_id=task.run_id,
                )
                logger.info(
                    f"[TaskManager] abort 已发送：session={task.session_key}, "
                    f"runId={task.run_id[:8]}..."
                )
            except Exception as e:
                logger.error(f"[TaskManager] 发送 abort 失败：{e}")

        # 如果任务已分配 session，释放 session
        if task.session_key:
            session = self._sessions.get(task.session_key)
            if session and session.is_busy() and session.current_task_id == task_id:
                session.release()
                logger.info(f"[TaskManager] Session 已释放：{task.session_key}")

        # 清理 runId waiter（如果存在）
        if task.run_id:
            self._ws_client._remove_run_waiter(task.run_id)

        # 更新任务状态为 CANCELLED
        await self._redis_repo.update_status(
            task_id,
            TaskStatus.CANCELLED,
            error="任务已被用户取消",
        )

        logger.info(f"[TaskManager] 任务已取消：{task_id[:8]}...")
        return True

    # ==================== 事件回调（核心）====================

    async def _on_final_event(self, run_id: str, payload: dict) -> None:
        """收到 final/error/aborted 事件的回调（由 WSClient 调用）

        这是新的事件驱动架构的核心：
        - WSClient 收到 final 事件后立即调用此回调
        - 不需要轮询、不需要等待
        - 真正的异步非阻塞

        Args:
            run_id: OpenClaw 的 runId
            payload: chat 事件 payload
        """
        try:
            # 查找对应的任务
            task = await self._redis_repo.get_task_by_run_id(run_id)
            if not task:
                logger.debug(f"[TaskManager] 未找到 runId 对应的任务：{run_id[:8]}...")
                return

            # 处理 final 事件
            await self._handle_final_event(task, payload)

        except Exception as e:
            logger.error(f"[TaskManager] 处理 final 事件异常：{e}")

    async def _handle_final_event(self, task: Task, payload: dict) -> None:
        """处理 final/error/aborted 事件

        Args:
            task: 任务对象
            payload: chat 事件 payload

        Note:
            收到 OpenClaw 结果后，会自动触发 LLM 二次处理（异步）
        """
        state = payload.get("state")
        run_id = payload.get("runId")

        logger.info(f"[TaskManager] 收到{state}事件：task={task.id[:8]}..., runId={run_id[:8]}...")

        # ⭐ 立即清理 runId waiter，防止重复处理
        if task.run_id:
            self._ws_client._remove_run_waiter(task.run_id)

        if state == "final":
            # 提取 OpenClaw 结果
            message = payload.get("message", {})
            logger.info(f"[TaskManager] 收到的 message 类型: {type(message)}, 内容前100字符: {str(message)[:100]}")
            openclaw_result = self._parse_message(message)

            # ⭐ 更新状态为 OPENCLAW_DONE（第一阶段完成）
            await self._redis_repo.update_result(task.id, result=openclaw_result)

            logger.info(f"[TaskManager] OpenClaw 完成：{task.id[:8]}...")

            # ⭐ 释放 session（不阻塞后续任务）
            if task.session_key:
                session = self._sessions.get(task.session_key)
                if session:
                    session.release()
                    logger.debug(f"[TaskManager] Session 已释放：{task.session_key}")

            # ⭐ 启动 LLM 二次处理（异步协程，不阻塞）
            asyncio.create_task(self._run_llm_post_processing(task, openclaw_result))

        elif state == "error":
            error = payload.get("error", "未知错误")
            await self._redis_repo.update_status(
                task.id,
                TaskStatus.FAILED,
                error=error,
            )

            # 释放 session
            if task.session_key:
                session = self._sessions.get(task.session_key)
                if session:
                    session.release()

            logger.error(f"[TaskManager] 任务失败：{task.id[:8]}..., {error}")

        elif state == "aborted":
            # ⭐ 处理中止事件（由 cancel_task 触发）
            await self._redis_repo.update_status(
                task.id,
                TaskStatus.CANCELLED,
                error="任务已被中止",
            )

            # 释放 session
            if task.session_key:
                session = self._sessions.get(task.session_key)
                if session:
                    session.release()

            logger.info(f"[TaskManager] 任务已中止：{task.id[:8]}...")

    async def _run_llm_post_processing(self, task: Task, openclaw_result: Dict[str, Any]) -> None:
        """运行 LLM 二次处理（异步）

        Args:
            task: 任务对象
            openclaw_result: OpenClaw 返回的结果

        Note:
            此方法在独立协程中运行，不阻塞 session 释放
        """
        try:
            logger.info(f"[TaskManager] LLM 二次处理开始：task={task.id[:8]}...")

            # 更新状态为 POST_PROCESSING
            await self._redis_repo.update_status(task.id, TaskStatus.POST_PROCESSING)

            # ⭐ 获取当前活跃 panel 和屏幕配置
            active_panels = None
            screen_width = 1920
            screen_height = 1080
            try:
                from app.services.agent_service import get_agent_service
                from app.config import settings
                
                agent_service = get_agent_service()
                if agent_service._panel_manager:
                    active_panels = agent_service._panel_manager.get_active_panels()
                
                screen_width = settings.SCREEN_WIDTH
                screen_height = settings.SCREEN_HEIGHT
                
                logger.debug(
                    f"[TaskManager] 智能布局参数：screen={screen_width}x{screen_height}, "
                    f"active_panels={len(active_panels) if active_panels else 0}"
                )
            except Exception as e:
                logger.warning(f"[TaskManager] 获取 panel 信息失败：{e}")

            # 调用 LLM 二次处理（传入 panel 信息）
            final_result = await self._post_processor.process_openclaw_result(
                task=task,
                openclaw_result=openclaw_result,
                active_panels=active_panels,
                screen_width=screen_width,
                screen_height=screen_height,
            )

            # 保存最终结果
            await self._redis_repo.update_final_result(
                task.id,
                final_result=final_result,
            )

            logger.info(f"[TaskManager] LLM 二次处理完成：task={task.id[:8]}...")

            # ⭐ 新增：入播报队列（触发事件1）
            await self._enqueue_to_playback_queue(task, final_result)

        except Exception as e:
            logger.error(f"[TaskManager] LLM 二次处理失败：task={task.id[:8]}, error={e}")

            # 二次处理失败，将 OpenClaw 原始结果作为最终结果
            await self._redis_repo.update_final_result(
                task.id,
                final_result=openclaw_result,
            )

            # ⭐ 即使失败也入播报队列（使用原始结果）
            await self._enqueue_to_playback_queue(task, openclaw_result)

            logger.warning(f"[TaskManager] 使用 OpenClaw 原始结果作为 fallback: task={task.id[:8]}...")

    async def _enqueue_to_playback_queue(self, task: Task, final_result: Dict[str, Any]) -> None:
        """将完成的任务加入播报队列（Redis）

        Args:
            task: 任务对象
            final_result: 最终结果（包含content、panel_html、action等）
        
        Note:
            使用 Redis 播报队列替代内存队列，实现解耦。
            PlaybackScheduler 每 1s 检查队列，IDLE 时自动执行播报。
        """
        try:
            from app.services.playback.redis_repo import get_playback_repository
            from app.services.playback.models import PlaybackTask

            # 获取 Redis 仓库
            playback_repo = await get_playback_repository()

            # 提取结果字段
            content = final_result.get("content", "")
            panel_html = final_result.get("panel_html")
            action = final_result.get("action")

            # 创建播报任务
            playback_task = PlaybackTask(
                id=task.id,
                content=content,
                panel_html=panel_html,
                action=action,
            )

            # 入 Redis 播报队列
            await playback_repo.enqueue(playback_task)

            logger.info(
                f"[TaskManager] 任务已入 Redis 播报队列：task={task.id[:8]}, "
                f"content={content[:50]}..."
            )

        except Exception as e:
            logger.error(f"[TaskManager] 入播报队列失败：task={task.id[:8]}, error={e}")

    def _parse_message(self, message: Any) -> Dict[str, Any]:
        """解析 OpenClaw 消息

        支持多种格式：
        1. WebSocket 旧格式：{"content": [{"type": "text", "text": "..."}]}
        2. WebSocket 新格式：{"content": [{"type": "text", "text": "{...JSON字符串...}"}]} (嵌套 JSON)
        3. HTTP 格式：{"content": "...", "panel_html": {...}}

        Args:
            message: OpenClaw 返回的 message 字段

        Returns:
            解析后的结果字典，包含 {"content": "...", "panel_html": {...}}
        """
        if isinstance(message, str):
            return {"content": message, "panel_html": None}

        if isinstance(message, dict):
            # 提取 content 字段
            content = message.get("content", "")
            panel_html = message.get("panel_html")  # 顶层的 panel_html

            # 情况1: content 是字符串（可能是 JSON 字符串）
            if isinstance(content, str):
                logger.debug(f"[TaskManager._parse_message] content 是字符串，长度={len(content)}, 前100字符={content[:100]}")
                try:
                    # 尝试解析嵌套的 JSON 字符串
                    parsed = json.loads(content)
                    logger.debug(f"[TaskManager._parse_message] JSON 解析成功，parsed type={type(parsed)}")
                    if isinstance(parsed, dict):
                        # 解析成功，提取 content 和 panel_html
                        result = {
                            "content": parsed.get("content", content),
                            "panel_html": parsed.get("panel_html") or panel_html
                        }
                        logger.debug(f"[TaskManager._parse_message] 最终结果: content={result['content'][:50]}..., panel_html={result['panel_html'] is not None}")
                        return result
                except json.JSONDecodeError as e:
                    # 不是 JSON，直接使用原始字符串
                    logger.debug(f"[TaskManager._parse_message] JSON 解析失败: {e}，使用原始字符串")
                    return {"content": content, "panel_html": panel_html}

            # 情况2: content 是列表（WebSocket 格式：[{"type": "text", "text": "..."}]）
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))

                # 拼接所有文本
                combined_text = "".join(text_parts)
                logger.debug(f"[TaskManager._parse_message] 从列表提取文本，长度={len(combined_text)}, 前100字符={combined_text[:100]}")

                # ⭐ 关键：检查拼接后的文本是否是 JSON 字符串
                try:
                    parsed = json.loads(combined_text)
                    logger.debug(f"[TaskManager._parse_message] 列表文本 JSON 解析成功，parsed type={type(parsed)}")
                    if isinstance(parsed, dict):
                        # 解析成功，提取 content 和 panel_html
                        result = {
                            "content": parsed.get("content", combined_text),
                            "panel_html": parsed.get("panel_html") or panel_html
                        }
                        logger.debug(f"[TaskManager._parse_message] 最终结果: content={result['content'][:50]}..., panel_html={result['panel_html'] is not None}")
                        return result
                except json.JSONDecodeError as e:
                    # 不是 JSON，直接使用拼接的文本
                    logger.debug(f"[TaskManager._parse_message] 列表文本 JSON 解析失败: {e}，使用原始文本")
                    return {"content": combined_text, "panel_html": panel_html}

            # 情况3: content 是字典（直接对象格式）
            if isinstance(content, dict):
                return {
                    "content": content.get("content", str(content)),
                    "panel_html": content.get("panel_html") or panel_html
                }

            # 默认情况
            return {"content": str(content), "panel_html": panel_html}

        return {"content": str(message), "panel_html": None}

    # ==================== 调度器 ====================

    async def _scheduler_loop(self) -> None:
        """调度器循环：分配 pending 任务给空闲 session"""
        logger.info("[TaskManager] 调度器启动")

        while self._running:
            try:
                # 找空闲 session
                idle_session = self._get_idle_session()
                if not idle_session:
                    await asyncio.sleep(0.1)
                    continue

                # 从 Redis 取 pending 任务
                task = await self._redis_repo.pop_pending_task()
                if not task:
                    await asyncio.sleep(0.1)
                    continue

                # ⭐ 关键：为每个任务创建独立的执行协程
                asyncio.create_task(self._execute_task(idle_session, task))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TaskManager] 调度器异常：{e}")
                await asyncio.sleep(1.0)

        logger.info("[TaskManager] 调度器停止")

    async def _execute_task(self, session: SessionState, task: Task) -> None:
        """执行单个任务（独立协程，不阻塞调度器）

        这是关键！每个任务在独立协程中执行：
        1. 原子检查并抢占 session（防止竞态）
        2. 发送 chat.send
        3. 不等待响应（事件驱动会处理）
        4. session 保持 BUSY 直到收到 final

        Args:
            session: 分配的 session
            task: 任务对象
        """
        # ⭐ 关键修复：原子检查并抢占 session
        # 防止竞态条件：多个任务同时认为同一个 session 空闲
        if not session.is_idle():
            logger.warning(f"[TaskManager] Session {session.session_key} 已被占用，跳过任务 {task.id[:8]}...")
            # 推回队列
            await self._redis_repo.push_pending_task(task.id)
            return

        # 立即标记 session 为 BUSY（抢占）
        session.assign_task(task.id)

        # 更新 Redis 状态为 ASSIGNED
        await self._redis_repo.update_status(
            task.id,
            TaskStatus.ASSIGNED,
            session_key=session.session_key,
        )

        try:
            # 发送到 OpenClaw
            logger.info(f"[TaskManager] 发送任务：{task.id[:8]}... → {session.session_key}")
            # ⭐ 添加 "panel 模式:" 前缀，让 OpenClaw 按照标准格式输出
            message_with_prefix = f"panel 模式:{task.tool_prompt}"
            run_id = await self._ws_client.send_chat_message(
                session_key=session.session_key,
                message=message_with_prefix,
            )

            # 更新状态为 RUNNING
            await self._redis_repo.update_status(
                task.id,
                TaskStatus.RUNNING,
                run_id=run_id,
            )

            session.run_id = run_id

            # ⭐ 关键修复：立即为 runId 创建 waiter
            # 这样 final 事件到达时就能被正确路由（兼容旧的 wait_for_run_id 逻辑）
            self._ws_client._create_run_waiter(run_id)

            logger.info(f"[TaskManager] 任务已发送：{task.id[:8]}..., runId={run_id[:8]}...")

            # ⭐ 注意：不在这里等待响应！
            # 响应由回调 _on_final_event 处理（事件驱动）
            # 如果 60 秒没有 final 事件，会被标记为超时（由 wait_for_result 处理）

        except Exception as e:
            logger.error(f"[TaskManager] 任务执行失败：{task.id[:8]}..., {e}")
            await self._redis_repo.update_status(
                task.id,
                TaskStatus.FAILED,
                error=str(e),
            )
            session.release()

    # ==================== 工具方法 ====================

    def _get_idle_session(self) -> Optional[SessionState]:
        """获取空闲 session

        Returns:
            空闲的 SessionState，没有则返回 None
        """
        for session in self._sessions.values():
            if session.is_idle():
                return session
        return None

    async def _on_ws_reconnect(self) -> None:
        """WebSocket 重连/断开时的处理（检测 OpenClaw 重启）

        当 WebSocket 断开时触发，可能是：
        1. OpenClaw 服务重启
        2. 网络中断
        3. 其他连接问题

        无论何种原因，断开期间的任务都无法继续，标记为失败
        """
        logger.warning("[TaskManager] 检测到 WebSocket 断开，OpenClaw 可能已重启")

        try:
            # 获取所有 RUNNING 状态的任务
            running_tasks = await self._redis_repo.get_all_tasks(
                status=TaskStatus.RUNNING,
                limit=100
            )

            if not running_tasks:
                logger.info("[TaskManager] 没有 RUNNING 任务需要处理")
                return

            logger.warning(f"[TaskManager] 发现 {len(running_tasks)} 个中断的任务，将标记为失败")

            # 将所有 RUNNING 任务标记为失败
            for task in running_tasks:
                # 释放 session
                if task.session_key:
                    session = self._sessions.get(task.session_key)
                    if session and session.is_busy():
                        session.release()
                        logger.debug(f"[TaskManager] Session 已释放：{task.session_key}")

                # 清理 runId waiter
                if task.run_id:
                    self._ws_client._remove_run_waiter(task.run_id)

                # 标记为失败
                await self._redis_repo.update_status(
                    task.id,
                    TaskStatus.FAILED,
                    error="OpenClaw 服务中断或重启，任务已终止"
                )

                logger.info(f"[TaskManager] 任务已标记为失败：{task.id[:8]}...")

            logger.warning(f"[TaskManager] 已处理 {len(running_tasks)} 个中断的任务")

        except Exception as e:
            logger.error(f"[TaskManager] 处理 WebSocket 重连失败：{e}")

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息

        Returns:
            统计数据字典
        """
        stats = {
            "running": self._running,
            "ws_connected": self._ws_client.is_connected,
            "redis_connected": self._redis_repo.is_connected,
            "sessions": {},
            "tasks": await self._redis_repo.get_stats(),
        }

        # Session 统计
        for key, session in self._sessions.items():
            stats["sessions"][key] = session.to_dict()

        return stats


# ==================== 全局实例（懒加载） ====================

_manager: Optional[OpenClawTaskManager] = None


def get_openclaw_manager() -> OpenClawTaskManager:
    """获取任务管理器实例（单例）

    注意：需要调用 await manager.start() 启动服务
    """
    global _manager
    if _manager is None:
        _manager = OpenClawTaskManager()
    return _manager


def set_openclaw_manager(manager: OpenClawTaskManager) -> None:
    """手动设置任务管理器实例（用于测试）"""
    global _manager
    _manager = manager