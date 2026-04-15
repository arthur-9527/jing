"""
OpenClaw Provider - 同步模式实现

核心设计：
1. 复用现有 OpenClaw WSClient 和内部 Redis 仓库
2. 实现 TaskProvider 同步模式接口
3. 内部队列管理（Session 分配 + 调度）
4. 状态同步到主队列（通过 TaskSyncInterface）

迁移自 app/services/openclaw/task_manager.py
"""

import asyncio
import time
import uuid
from typing import Optional, Dict, Any

from loguru import logger

from ..base import TaskProvider, TaskSyncInterface, ProviderInitError
from ..models import ProviderResult
from ..config import get_task_system_settings


class SessionState:
    """Session 状态（内存维护）"""
    
    def __init__(self, session_key: str):
        self.session_key = session_key
        self.status = "idle"  # idle / busy
        self.current_task_id: Optional[str] = None
        self.last_used: float = time.time()
        self.run_id: Optional[str] = None
    
    def is_idle(self) -> bool:
        return self.status == "idle"
    
    def is_busy(self) -> bool:
        return self.status == "busy"
    
    def assign_task(self, task_id: str) -> None:
        self.status = "busy"
        self.current_task_id = task_id
        self.last_used = time.time()
    
    def release(self) -> None:
        self.status = "idle"
        self.current_task_id = None
        self.run_id = None
        self.last_used = time.time()


class OpenClawProvider(TaskProvider):
    """OpenClaw Provider - 同步模式
    
    特性：
    - 复用现有 OpenClaw WSClient（WebSocket 连接管理）
    - 复用现有 OpenClaw RedisRepo（内部队列）
    - Session 管理（3 并发）
    - 事件驱动 + Future 等待
    - 状态同步到主队列
    
    Args:
        ws_client: OpenClaw WebSocket 客户端（可选，默认复用现有）
        redis_repo: OpenClaw 内部 Redis 仓库（可选，默认复用现有）
    """
    
    def __init__(
        self,
        ws_client=None,
        redis_repo=None,
    ):
        super().__init__()
        self._settings = get_task_system_settings()
        
        # 复用现有组件（延迟初始化）
        self._ws_client = ws_client
        self._redis_repo = redis_repo
        
        # Session 管理
        self._sessions: Dict[str, SessionState] = {}
        
        # 运行状态
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        
        # ⭐ 实际启用状态（初始化失败时会设为 False）
        self._enabled: Optional[bool] = None  # None=未初始化, True=已启用, False=初始化失败
        self._start_error: Optional[str] = None
        
        # 任务等待器：task_id → Future
        self._task_waiters: Dict[str, asyncio.Future] = {}
        
        # run_id → task_id 映射
        self._run_id_to_task: Dict[str, str] = {}
    
    # ===== 基础属性 =====
    
    @property
    def name(self) -> str:
        return "openclaw"
    
    @property
    def is_enabled(self) -> bool:
        """⭐ 返回实际启用状态，而非配置值
        
        - None (未初始化): 返回配置值
        - True (已启用): 返回 True
        - False (初始化失败): 返回 False
        """
        if self._enabled is None:
            return self._settings.OPENCLAW_ENABLED
        return self._enabled
    
    @property
    def use_sync_mode(self) -> bool:
        return True  # ⭐ 同步模式
    
    # ===== 生命周期 =====
    
    async def start(self, clear_queue: bool = True) -> None:
        """启动 Provider
        
        ⭐ 阻塞式初始化：
        1. 初始化 WSClient（连接 WebSocket）
        2. 初始化 RedisRepo
        3. 清空内部队列（如果配置）
        4. 初始化 Session
        5. 注册事件回调
        6. 启动调度器
        
        Raises:
            ProviderInitError: 初始化失败
        """
        logger.info("[OpenClawProvider] 启动中...")
        
        try:
            # Step 1: 初始化 RedisRepo
            await self._init_redis_repo()
            
            # Step 2: 清空内部队列（如果配置）
            if clear_queue:
                cleared_count = await self.clear_all_tasks()
                logger.warning(
                    f"[OpenClawProvider] 启动清理：清空 {cleared_count} 个遗留任务"
                )
            
            # Step 3: 初始化 Session
            self._init_sessions()
            
            # Step 4: 初始化 WSClient
            await self._init_ws_client()
            
            # Step 5: 注册事件回调
            self._ws_client.register_final_callback(self._on_final_event)
            self._ws_client.register_reconnect_callback(self._on_ws_reconnect)
            
            # Step 6: 启动调度器
            self._running = True
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
            
            # ⭐ 标记为已启用
            self._enabled = True
            self._start_error = None
            
            logger.info("[OpenClawProvider] 启动完成")
            
        except Exception as e:
            logger.error(f"[OpenClawProvider] 启动失败: {e}")
            # ⭐ 标记为不可用
            self._enabled = False
            self._start_error = str(e)
            raise ProviderInitError(f"OpenClaw Provider 启动失败: {e}")
    
    async def _init_redis_repo(self) -> None:
        """初始化内部 Redis 仓库"""
        if self._redis_repo is None:
            from app.services.openclaw.redis_repo import get_task_repository
            self._redis_repo = await get_task_repository()
        
        if not self._redis_repo.is_connected:
            await self._redis_repo.connect()
        
        logger.info("[OpenClawProvider] RedisRepo 已连接")
    
    async def _init_ws_client(self) -> None:
        """初始化 WebSocket 客户端"""
        if self._ws_client is None:
            from app.services.openclaw.ws_client import OpenClawWSClient
            self._ws_client = OpenClawWSClient()
        
        # ⭐ 阻塞式连接（失败抛异常）
        await self._ws_client.connect()
        
        if not self._ws_client.is_connected:
            raise ProviderInitError("OpenClaw WebSocket 连接失败")
        
        logger.info("[OpenClawProvider] WebSocket 已连接")
    
    def _init_sessions(self) -> None:
        """初始化 Session"""
        session_keys = self._settings.openclaw_session_list
        for session_key in session_keys:
            self._sessions[session_key] = SessionState(session_key)
        
        logger.info(
            f"[OpenClawProvider] Session 已初始化: {len(self._sessions)} 个"
        )
    
    async def stop(self) -> None:
        """停止 Provider"""
        if not self._running:
            return
        
        logger.info("[OpenClawProvider] 停止中...")
        
        self._running = False
        
        # 取消调度器
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        
        # 断开 WebSocket
        if self._ws_client:
            await self._ws_client.disconnect()
        
        # 断开 Redis
        if self._redis_repo:
            await self._redis_repo.disconnect()
        
        # 清空等待器
        for future in self._task_waiters.values():
            if not future.done():
                future.set_exception(RuntimeError("Provider 已停止"))
        self._task_waiters.clear()
        
        logger.info("[OpenClawProvider] 已停止")
    
    async def clear_all_tasks(self) -> int:
        """清空所有任务"""
        if self._redis_repo:
            return await self._redis_repo.clear_all_on_restart()
        return 0
    
    # ===== 同步模式接口 =====
    
    async def on_task_submitted(
        self, 
        task_id: str, 
        tool_prompt: str, 
        context: dict
    ) -> None:
        """接收任务（同步模式）
        
        流程：
        1. 创建内部任务记录
        2. 入内部队列
        3. 调度器自动分配 Session 执行
        """
        logger.info(
            f"[OpenClawProvider] 接收任务: {task_id[:8]}..., "
            f"prompt={tool_prompt[:40]}..."
        )
        
        # 创建内部任务（使用主队列的 task_id）
        await self._redis_repo.create_task(
            tool_prompt=tool_prompt,
            task_id=task_id,  # ⭐ 使用主队列的 task_id
            user_input=context.get("user_input"),
            memory_context=context.get("memory_context"),
            conversation_history=context.get("conversation_history"),
            inner_monologue=context.get("inner_monologue"),
            emotion_delta=context.get("emotion_delta"),
        )
        
        # 创建 Future 等待结果
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._task_waiters[task_id] = future
    
    async def on_task_cancel(self, task_id: str) -> bool:
        """取消任务"""
        # 找到对应的 session 和 run_id
        for session in self._sessions.values():
            if session.current_task_id == task_id:
                # 发送 abort
                await self._ws_client.abort_chat(
                    session_key=session.session_key,
                    run_id=session.run_id,
                )
                session.release()
                
                # 清理等待器
                future = self._task_waiters.pop(task_id, None)
                if future and not future.done():
                    future.set_exception(RuntimeError("任务已取消"))
                
                # 上报取消
                await self._report_error(task_id, "任务已取消")
                
                logger.info(f"[OpenClawProvider] 任务已取消: {task_id[:8]}...")
                return True
        
        # 任务可能在队列中，标记为取消
        from app.services.openclaw.models import TaskStatus
        await self._redis_repo.update_status(task_id, TaskStatus.CANCELLED)
        
        future = self._task_waiters.pop(task_id, None)
        if future and not future.done():
            future.set_exception(RuntimeError("任务已取消"))
        
        return True
    
    # ===== 调度器 =====
    
    async def _scheduler_loop(self) -> None:
        """调度器循环
        
        每秒检查：
        1. 有空闲 Session
        2. 有 Pending 任务
        
        如果两者都满足，分配任务给 Session 执行
        """
        logger.info("[OpenClawProvider] 调度器启动")
        
        while self._running:
            try:
                await asyncio.sleep(1.0)
                
                # 检查是否有空闲 Session
                idle_session = self._find_idle_session()
                if not idle_session:
                    continue
                
                # 检查是否有 Pending 任务
                task = await self._redis_repo.pop_pending_task()
                if not task:
                    continue
                
                # 分配执行
                await self._execute_task(task, idle_session)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[OpenClawProvider] 调度器异常: {e}")
                await asyncio.sleep(5.0)
        
        logger.info("[OpenClawProvider] 调度器停止")
    
    def _find_idle_session(self) -> Optional[SessionState]:
        """查找空闲 Session"""
        for session in self._sessions.values():
            if session.is_idle():
                return session
        return None
    
    async def _execute_task(self, task, session: SessionState) -> None:
        """执行任务
        
        流程：
        1. 分配 Session
        2. 发送 chat.send
        3. 上报 RUNNING
        4. 等待事件回调
        """
        from app.services.openclaw.models import TaskStatus
        
        # 分配 Session
        session.assign_task(task.id)
        await self._redis_repo.update_status(
            task.id,
            TaskStatus.ASSIGNED,
            session_key=session.session_key,
        )
        
        logger.info(
            f"[OpenClawProvider] 任务分配: {task.id[:8]}... → "
            f"session={session.session_key}"
        )
        
        # 上报 RUNNING
        await self._report_running(task.id)
        
        # 发送 chat.send
        try:
            # ⭐ 添加 "panel 模式:" 前缀，让 OpenClaw 按照标准格式输出
            message_with_prefix = f"panel 模式:{task.tool_prompt}"
            run_id = await self._ws_client.send_chat_message(
                session_key=session.session_key,
                message=message_with_prefix,
            )
            
            # 记录 run_id → task_id 映射
            session.run_id = run_id
            self._run_id_to_task[run_id] = task.id
            
            await self._redis_repo.update_status(
                task.id,
                TaskStatus.RUNNING,
                run_id=run_id,
            )
            
            logger.info(
                f"[OpenClawProvider] 已发送: {task.id[:8]}... → "
                f"runId={run_id[:8]}..."
            )
            
        except Exception as e:
            logger.error(f"[OpenClawProvider] 发送失败: {task.id[:8]}..., {e}")
            
            # 释放 Session
            session.release()
            
            # 上报失败
            await self._report_error(task.id, str(e))
            
            # 清理等待器
            future = self._task_waiters.pop(task.id, None)
            if future and not future.done():
                future.set_exception(e)
    
    # ===== 事件回调 =====
    
    async def _on_final_event(self, run_id: str, payload: dict) -> None:
        """处理 final/error/aborted 事件
        
        Args:
            run_id: OpenClaw runId
            payload: 事件 payload
        """
        state = payload.get("state")
        task_id = self._run_id_to_task.get(run_id)
        
        if not task_id:
            logger.warning(
                f"[OpenClawProvider] 未找到 runId 对应的任务: {run_id[:8]}..."
            )
            return
        
        # ⭐ 打印完整 payload 结构，用于调试解析逻辑
        import json
        logger.info(
            f"[OpenClawProvider] 收到事件: {task_id[:8]}... → state={state}"
        )
        logger.info(
            f"[OpenClawProvider] payload 结构:\n"
            f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
        )
        
        # 找到对应的 Session
        session = None
        for s in self._sessions.values():
            if s.run_id == run_id:
                session = s
                break
        
        if state == "final":
            # 处理成功结果
            result = self._parse_openclaw_result(payload)
            
            # 释放 Session
            if session:
                session.release()
            
            # 上报结果
            await self._report_result(task_id, result)
            
            # 完成 Future
            future = self._task_waiters.pop(task_id, None)
            if future and not future.done():
                future.set_result(result)
            
            # 清理映射
            self._run_id_to_task.pop(run_id, None)
            
        elif state == "error":
            error_msg = payload.get("error", "未知错误")
            
            # 释放 Session
            if session:
                session.release()
            
            # 上报失败
            await self._report_error(task_id, error_msg)
            
            # 完成 Future（异常）
            future = self._task_waiters.pop(task_id, None)
            if future and not future.done():
                future.set_exception(RuntimeError(error_msg))
            
            # 清理映射
            self._run_id_to_task.pop(run_id, None)
            
        elif state == "aborted":
            # 释放 Session
            if session:
                session.release()
            
            # 上报取消
            await self._report_error(task_id, "任务已中止")
            
            # 完成 Future（异常）
            future = self._task_waiters.pop(task_id, None)
            if future and not future.done():
                future.set_exception(RuntimeError("任务已中止"))
            
            # 清理映射
            self._run_id_to_task.pop(run_id, None)
    
    async def _on_ws_reconnect(self) -> None:
        """WebSocket 断开/重连回调
        
        处理所有 RUNNING 任务：
        1. 释放 Session
        2. 上报失败
        """
        logger.warning("[OpenClawProvider] WebSocket 断开，处理 RUNNING 任务")
        
        # 释放所有 Session
        for session in self._sessions.values():
            if session.is_busy():
                task_id = session.current_task_id
                session.release()
                
                # 上报失败
                await self._report_error(
                    task_id, 
                    "OpenClaw 服务中断或重启"
                )
                
                # 完成 Future（异常）
                future = self._task_waiters.pop(task_id, None)
                if future and not future.done():
                    future.set_exception(
                        RuntimeError("OpenClaw 服务中断或重启")
                    )
        
        # 清理映射
        self._run_id_to_task.clear()
    
    def _parse_openclaw_result(self, payload: dict) -> dict:
        """解析 OpenClaw 返回结果
        
        参考 task_manager.py::_parse_message 的解析逻辑
        
        Returns:
            ProviderResult.to_dict() 格式
        """
        # 提取 message 字段
        message = payload.get("message", {})
        
        # ⭐ 复用原有的 _parse_message 逻辑
        parsed_result = self._parse_message(message)
        
        # 构建结果
        result = ProviderResult(
            task_id="",  # 由上层填充
            success=True,
            content=parsed_result.get("content", ""),
            panel_html=parsed_result.get("panel_html"),
            error=None,
            metadata={
                "openclaw_run_id": payload.get("runId"),
                "session_key": payload.get("sessionKey"),
            },
        )
        
        return result.to_dict()
    
    def _parse_message(self, message: Any) -> Dict[str, Any]:
        """解析 OpenClaw 消息

        复用 task_manager.py::_parse_message 的逻辑：
        1. WebSocket 格式：{"content": [{"type": "text", "text": "..."}]}
        2. 嵌套 JSON：text 被 ```json 包裹
        3. panel_html 是字符串形式，需要二次解析

        Args:
            message: OpenClaw 返回的 message 字段

        Returns:
            解析后的结果字典，包含 {"content": "...", "panel_html": {...}}
        """
        import json
        
        if isinstance(message, str):
            return {"content": message, "panel_html": None}

        if isinstance(message, dict):
            # 提取 content 字段
            content = message.get("content", "")
            panel_html = message.get("panel_html")  # 顶层的 panel_html

            # 情况1: content 是字符串（可能是 JSON 字符串）
            if isinstance(content, str):
                logger.debug(f"[OpenClawProvider._parse_message] content 是字符串，长度={len(content)}, 前100字符={content[:100]}")
                try:
                    # 尝试解析嵌套的 JSON 字符串
                    parsed = json.loads(content)
                    logger.debug(f"[OpenClawProvider._parse_message] JSON 解析成功，parsed type={type(parsed)}")
                    if isinstance(parsed, dict):
                        # 解析成功，提取 content 和 panel_html
                        result = {
                            "content": parsed.get("content", content),
                            "panel_html": parsed.get("panel_html") or panel_html
                        }
                        # ⭐ panel_html 可能是字符串，需要二次解析
                        result["panel_html"] = self._parse_panel_html(result["panel_html"])
                        logger.debug(f"[OpenClawProvider._parse_message] 最终结果: content={result['content'][:50]}..., panel_html={result['panel_html'] is not None}")
                        return result
                except json.JSONDecodeError as e:
                    # 不是 JSON，直接使用原始字符串
                    logger.debug(f"[OpenClawProvider._parse_message] JSON 解析失败: {e}，使用原始字符串")
                    return {"content": content, "panel_html": self._parse_panel_html(panel_html)}

            # 情况2: content 是列表（WebSocket 格式：[{"type": "text", "text": "..."}]）
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))

                # 拼接所有文本
                combined_text = "".join(text_parts)
                logger.debug(f"[OpenClawProvider._parse_message] 从列表提取文本，长度={len(combined_text)}, 前100字符={combined_text[:100]}")

                # ⭐ 关键：检查拼接后的文本是否被 ```json 包裹
                try:
                    # 去除 ```json 包裹
                    text_to_parse = combined_text.strip()
                    if text_to_parse.startswith("```json"):
                        # 去除 ```json 和 ```
                        text_to_parse = text_to_parse[7:]  # 去除 ```json
                        if text_to_parse.endswith("```"):
                            text_to_parse = text_to_parse[:-3]  # 去除 ```
                        text_to_parse = text_to_parse.strip()
                    
                    parsed = json.loads(text_to_parse)
                    logger.debug(f"[OpenClawProvider._parse_message] 列表文本 JSON 解析成功，parsed type={type(parsed)}")
                    if isinstance(parsed, dict):
                        # 解析成功，提取 content 和 panel_html
                        result = {
                            "content": parsed.get("content", combined_text),
                            "panel_html": parsed.get("panel_html") or panel_html
                        }
                        # ⭐ panel_html 可能是字符串，需要二次解析
                        result["panel_html"] = self._parse_panel_html(result["panel_html"])
                        logger.debug(f"[OpenClawProvider._parse_message] 最终结果: content={result['content'][:50]}..., panel_html={result['panel_html'] is not None}")
                        return result
                except json.JSONDecodeError as e:
                    # 不是 JSON，直接使用拼接的文本
                    logger.debug(f"[OpenClawProvider._parse_message] 列表文本 JSON 解析失败: {e}，使用原始文本")
                    return {"content": combined_text, "panel_html": self._parse_panel_html(panel_html)}

            # 情况3: content 是字典（直接对象格式）
            if isinstance(content, dict):
                result = {
                    "content": content.get("content", str(content)),
                    "panel_html": content.get("panel_html") or panel_html
                }
                result["panel_html"] = self._parse_panel_html(result["panel_html"])
                return result

            # 默认情况
            return {"content": str(content), "panel_html": self._parse_panel_html(panel_html)}

        return {"content": str(message), "panel_html": None}
    
    def _parse_panel_html(self, panel_html: Any) -> Optional[Dict[str, Any]]:
        """解析 panel_html 字段
        
        panel_html 可能是字符串形式的 JSON，需要二次解析
        
        Args:
            panel_html: 可能是 dict、str 或 None
        
        Returns:
            解析后的 dict 或 None
        """
        import json
        
        if panel_html is None:
            return None
        
        if isinstance(panel_html, dict):
            return panel_html
        
        if isinstance(panel_html, str):
            try:
                parsed = json.loads(panel_html)
                if isinstance(parsed, dict):
                    logger.debug(f"[OpenClawProvider._parse_panel_html] panel_html 字符串解析成功")
                    return parsed
            except json.JSONDecodeError as e:
                logger.warning(f"[OpenClawProvider._parse_panel_html] panel_html 解析失败: {e}")
                return None
        
        return None
    
    # ===== 统计 =====
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = {
            "running": self._running,
            "ws_connected": self._ws_client.is_connected if self._ws_client else False,
            "redis_connected": self._redis_repo.is_connected if self._redis_repo else False,
            "sessions": {},
            "pending_tasks": 0,
            "enabled": self._enabled,
            "start_error": self._start_error,
        }
        
        # Session 统计
        for key, session in self._sessions.items():
            stats["sessions"][key] = {
                "status": session.status,
                "current_task_id": session.current_task_id,
                "run_id": session.run_id,
            }
        
        # 队列统计
        if self._redis_repo:
            stats["pending_tasks"] = await self._redis_repo.get_pending_queue_length()
        
        return stats


# ===== 全局实例（可选，用于测试）=====
_provider: Optional[OpenClawProvider] = None


def get_openclaw_provider() -> OpenClawProvider:
    """获取 OpenClaw Provider 实例"""
    global _provider
    if _provider is None:
        _provider = OpenClawProvider()
    return _provider


def reset_openclaw_provider():
    """重置全局实例（用于测试）"""
    global _provider
    if _provider is not None:
        try:
            # 尝试停止
            if _provider._running:
                # 不能在同步上下文中调用 async 方法，跳过
                pass
        except Exception:
            pass
    _provider = None