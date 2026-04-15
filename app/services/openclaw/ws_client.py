"""
OpenClaw WebSocket 客户端

负责：
- WebSocket连接管理（单连接）
- Ed25519设备签名认证
- chat.send/chat.abort消息发送
- chat事件接收和runId路由
- 断线重连和心跳保活

参考：raspi_mmd/pipecat/services/llm/openclaw_ws.py
"""

import asyncio
import base64
import json
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Callable, Awaitable
from loguru import logger

import websockets
from websockets.exceptions import ConnectionClosed
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from .config import get_openclaw_config


# 协议版本
PROTOCOL_VERSION = 3


def _base64url_encode(data: bytes) -> str:
    """Base64 URL-safe编码"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_identity(identity_file: str) -> tuple:
    """加载设备身份文件，返回 (device_id, private_key_obj, public_key_b64)

    支持两种文件格式:
      pipecat 格式:   {"device_id": "pipecat-xxx", "private_key": "<hex32bytes>"}
      raspi_node 格式: {"version":1, "deviceId":"...", "publicKeyPem":"...", "privateKeyPem":"..."}
    """
    identity_path = Path(identity_file).expanduser()
    if not identity_path.exists():
        raise FileNotFoundError(f"身份文件不存在: {identity_file}")

    with open(identity_path) as f:
        data = json.load(f)

    if "private_key" in data:
        # pipecat 简化格式: raw hex 私钥
        device_id = data["device_id"]
        raw = bytes.fromhex(data["private_key"])
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
    elif "privateKeyPem" in data:
        # raspi_node PEM 格式
        device_id = data["deviceId"]
        private_key = serialization.load_pem_private_key(
            data["privateKeyPem"].encode(), password=None
        )
    else:
        raise ValueError(f"未知的身份文件格式: {identity_file}")

    public_key = private_key.public_key()
    pub_raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return device_id, private_key, _base64url_encode(pub_raw)


def _sign_connect_payload(
    private_key, device_id: str, client_id: str, client_mode: str, role: str,
    scopes: list, signed_at: int, gateway_token: str, nonce: str
) -> str:
    """构建并签名 connect payload（v2 格式）

    字段顺序与 TypeScript buildDeviceAuthPayload 完全一致:
      v2 | deviceId | clientId | clientMode | role | scopes | signedAtMs | token | nonce
    """
    scopes_str = ",".join(scopes)
    parts = ["v2", device_id, client_id, client_mode, role, scopes_str,
             str(signed_at), gateway_token or "", nonce]
    payload_str = "|".join(parts)
    sig = private_key.sign(payload_str.encode())
    return _base64url_encode(sig)


class OpenClawWSClient:
    """OpenClaw WebSocket客户端

    管理：
    - 单个WebSocket连接到OpenClaw Gateway
    - Ed25519设备签名认证
    - 3个session的消息收发（通过sessionKey区分）
    - runId路由（避免串话）

    Args:
        config: OpenClaw配置（如果为None则使用默认配置）
    """

    def __init__(self):
        self._config = get_openclaw_config()

        # WebSocket连接
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._authenticated = False
        self._auth_future: Optional[asyncio.Future] = None

        # 接收任务
        self._receive_task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue = asyncio.Queue()

        # runId等待器：runId -> Future
        self._run_id_waiters: Dict[str, asyncio.Future] = {}

        # 请求等待器：req_id -> Future（用于RPC响应）
        self._pending: Dict[str, asyncio.Future] = {}

        # ⭐ final事件回调列表（支持多个监听器）
        self._final_callbacks = []

        # ⭐ 重连回调（WebSocket断开重连时触发）
        self._reconnect_callback = None

        # 设备身份（延迟加载）
        self._device_id: Optional[str] = None
        self._private_key = None
        self._public_key_b64: Optional[str] = None

    # ==================== 连接管理 ====================

    async def connect(self) -> None:
        """连接到OpenClaw Gateway并完成认证"""
        if self._connected:
            return

        self._authenticated = False
        self._auth_future = asyncio.get_event_loop().create_future()

        # 加载设备身份
        self._load_identity_lazy()

        logger.info(f"[WSClient] 连接到 {self._config.ws.ws_url}...")

        try:
            # 建立WebSocket连接
            self._ws = await websockets.connect(
                self._config.ws.ws_url,
                ping_interval=self._config.ws.ping_interval,
                ping_timeout=self._config.ws.ping_timeout,
            )

            self._connected = True  # 标记为已连接
            logger.info("[WSClient] WebSocket连接已建立")

            # 启动接收协程
            self._receive_task = asyncio.create_task(self._receive_loop())

            # 如果禁用了设备认证，直接标记为已认证
            # 检查配置或尝试立即连接
            try:
                # 尝试等待认证完成（如果有设备认证）
                await asyncio.wait_for(self._auth_future, timeout=2.0)
            except asyncio.TimeoutError:
                # 超时可能是设备认证被禁用，直接连接成功
                logger.warning("[WSClient] 设备认证超时，可能已禁用设备认证，尝试直接连接")
                self._authenticated = True
                self._connected = True

            logger.info("[WSClient] 已连接并认证成功")

        except asyncio.TimeoutError:
            await self.disconnect()
            raise TimeoutError("[WSClient] 认证超时（未收到 hello-ok）")
        except Exception as e:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """断开WebSocket连接"""
        self._connected = False
        self._authenticated = False

        # 取消接收任务
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        # 关闭连接
        if self._ws:
            await self._ws.close()
            self._ws = None

        # 清空等待器
        for future in self._run_id_waiters.values():
            if not future.done():
                future.set_exception(ConnectionError("连接已断开"))
        self._run_id_waiters.clear()

        logger.info("[WSClient] 已断开连接")

    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected and self._ws is not None

    @property
    def is_authenticated(self) -> bool:
        """检查是否已认证"""
        return self._authenticated

    def _load_identity_lazy(self) -> None:
        """延迟加载设备身份"""
        if self._device_id is not None:
            return

        try:
            self._device_id, self._private_key, self._public_key_b64 = _load_identity(
                self._config.ws.identity_file
            )
            logger.info(f"[WSClient] 身份加载成功: device_id={self._device_id}")
        except Exception as e:
            raise RuntimeError(f"[WSClient] 无法加载身份文件: {e}")

    # ==================== 消息发送 ====================

    async def _send_json(self, obj: dict) -> None:
        """发送JSON消息"""
        if not self._ws or not self._connected:
            raise RuntimeError("WebSocket未连接")

        message = json.dumps(obj, ensure_ascii=False)
        await self._ws.send(message)

    async def _send_connect_frame(self, nonce: str) -> None:
        """发送带签名的connect请求"""
        client_id = "node-host"
        client_mode = "node"
        role = "operator"
        scopes = ["operator.write"]  # 允许 chat.send / chat.abort
        signed_at = int(time.time() * 1000)

        signature = _sign_connect_payload(
            self._private_key,
            self._device_id,
            client_id,
            client_mode=client_mode,
            role=role,
            scopes=scopes,
            signed_at=signed_at,
            gateway_token=self._config.ws.ws_token,
            nonce=nonce,
        )

        frame = {
            "type": "req",
            "id": f"auth-{signed_at}",
            "method": "connect",
            "params": {
                "minProtocol": PROTOCOL_VERSION,
                "maxProtocol": PROTOCOL_VERSION,
                "client": {
                    "id": client_id,
                    "version": "1.0.0",
                    "platform": "linux",
                    "mode": client_mode,
                    "instanceId": self._device_id[:16],
                },
                "role": role,
                "scopes": scopes,
                "caps": ["text"],
                "device": {
                    "id": self._device_id,
                    "publicKey": self._public_key_b64,
                    "signature": signature,
                    "signedAt": signed_at,
                    "nonce": nonce,
                },
                "auth": {"token": self._config.ws.ws_token},
            },
        }
        logger.debug(f"[WSClient] 发送 connect 帧: role={role} scopes={scopes}")
        await self._send_json(frame)

    async def send_chat_message(
        self,
        session_key: str,
        message: str,
        timeout: float = 10.0,
    ) -> str:
        """通过chat.send发送用户消息

        Args:
            session_key: Session key（如 "agent:main:chat1"）
            message: 消息内容
            timeout: 等待ACK超时时间

        Returns:
            runId: 本次对话的唯一ID
        """
        if not self._authenticated:
            raise RuntimeError("WebSocket未认证")

        idempotency_key = uuid.uuid4().hex
        req_id = uuid.uuid4().hex

        # 创建Future等待ACK
        ack_future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = ack_future

        logger.info(f"[WSClient] chat.send session={session_key} text={message[:40]}")

        await self._send_json({
            "type": "req",
            "id": req_id,
            "method": "chat.send",
            "params": {
                "sessionKey": session_key,
                "message": message,
                "idempotencyKey": idempotency_key,
                "thinking": "low",
            },
        })

        try:
            # 等待ACK
            result = await asyncio.wait_for(ack_future, timeout=timeout)
            run_id = result.get("runId", idempotency_key) if isinstance(result, dict) else idempotency_key
            logger.debug(f"[WSClient] chat.send ACK runId={run_id}")
            return run_id
        except asyncio.TimeoutError:
            logger.warning("[WSClient] chat.send ACK超时，使用idempotencyKey")
            return idempotency_key
        finally:
            self._pending.pop(req_id, None)

    async def abort_chat(
        self,
        session_key: str,
        run_id: Optional[str] = None,
    ) -> None:
        """向OpenClaw发送chat.abort中止指定运行

        Args:
            session_key: Session key
            run_id: 要中止的runId（None时中止session所有运行）
        """
        if not self._authenticated:
            return

        req_id = uuid.uuid4().hex
        params: dict = {"sessionKey": session_key}
        if run_id:
            params["runId"] = run_id

        try:
            await self._send_json({
                "type": "req",
                "id": req_id,
                "method": "chat.abort",
                "params": params,
            })
            logger.info(f"[WSClient] chat.abort 已发送 session={session_key} runId={run_id or 'all'}")
        except Exception as e:
            logger.warning(f"[WSClient] chat.abort发送失败: {e}")

    # ==================== 消息接收 ====================

    async def _receive_loop(self) -> None:
        """接收消息循环"""
        try:
            async for raw in self._ws:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(f"[WSClient] JSON解析失败: {raw[:120]}")
                    continue

                frame_type = frame.get("type")
                if frame_type == "event":
                    await self._handle_event(frame)
                elif frame_type == "res":
                    await self._handle_response(frame)

        except ConnectionClosed:
            logger.warning("[WSClient] WebSocket连接已断开（OpenClaw可能重启）")

            # ⭐ 触发重连回调（通知TaskManager处理RUNNING任务）
            if self._reconnect_callback:
                try:
                    await self._reconnect_callback()
                except Exception as e:
                    logger.error(f"[WSClient] 重连回调执行失败: {e}")

        except asyncio.CancelledError:
            logger.debug("[WSClient] 接收任务取消")
        except Exception as e:
            logger.error(f"[WSClient] receive_loop异常: {e}")
        finally:
            self._authenticated = False
            self._connected = False

    async def _handle_event(self, frame: dict) -> None:
        """处理事件消息"""
        event = frame.get("event")
        payload = frame.get("payload", {})

        if event == "connect.challenge":
            nonce = payload.get("nonce", "")
            logger.debug(f"[WSClient] 收到challenge nonce={nonce[:16]}")
            await self._send_connect_frame(nonce)

        elif event == "chat":
            session_key = payload.get("sessionKey", "unknown")
            state = payload.get("state", "?")
            run_id = payload.get("runId", "?")
            # logger.debug(f"[WSClient] chat事件: session={session_key} state={state} runId={run_id}")

            # ⭐ 收到final/error/aborted事件，调用所有回调
            if state in ("final", "error", "aborted"):
                for callback in self._final_callbacks:
                    try:
                        await callback(run_id, payload)
                    except Exception as e:
                        logger.error(f"[WSClient] 回调执行失败: {e}")

            # ⭐ 兼容旧的Future逻辑（用于wait_for_run_id）
            future = self._run_id_waiters.get(run_id)
            if future and not future.done():
                if state in ("delta", "final"):
                    future.set_result(payload)
                elif state == "error":
                    future.set_exception(Exception(payload.get("error", "未知错误")))
                elif state == "aborted":
                    future.set_exception(ConnectionAbortedError("聊天已中止"))
            else:
                # logger.debug(f"[WSClient] 忽略无对应waiter的runId: {run_id}")
                pass

        else:
            # logger.debug(f"[WSClient] 忽略事件: {event}")
            pass

    async def _handle_response(self, frame: dict) -> None:
        """处理响应消息"""
        req_id = frame.get("id", "")

        # 认证响应（hello-ok）
        if not self._authenticated and req_id.startswith("auth-"):
            if frame.get("ok"):
                payload = frame.get("payload", {})
                logger.info(f"[WSClient] hello-ok收到")
                self._authenticated = True
                self._connected = True
                if self._auth_future and not self._auth_future.done():
                    self._auth_future.set_result(payload)
            else:
                error = frame.get("error", {})
                logger.error(f"[WSClient] 认证失败: {error}")
                if self._auth_future and not self._auth_future.done():
                    self._auth_future.set_exception(RuntimeError(f"认证失败: {error}"))
            return

        # 普通请求响应
        future = self._pending.pop(req_id, None)
        if future and not future.done():
            if frame.get("ok"):
                future.set_result(frame.get("payload"))
            else:
                error = frame.get("error", {})
                future.set_exception(Exception(f"{error.get('code')}: {error.get('message')}"))

    # ==================== runId等待器 ====================

    def register_final_callback(self, callback) -> None:
        """注册final事件回调（支持多个监听器）

        Args:
            callback: 回调函数，签名 async def callback(run_id: str, payload: dict)
        """
        if callback not in self._final_callbacks:
            self._final_callbacks.append(callback)
            logger.debug(f"[WSClient] 已注册final回调: {callback.__name__}")

    def unregister_final_callback(self, callback) -> None:
        """取消注册final事件回调

        Args:
            callback: 要移除的回调函数
        """
        if callback in self._final_callbacks:
            self._final_callbacks.remove(callback)
            logger.debug(f"[WSClient] 已移除final回调: {callback.__name__}")

    def register_reconnect_callback(self, callback) -> None:
        """注册重连回调（WebSocket断开时触发）

        Args:
            callback: 回调函数，签名 async def callback()
        """
        self._reconnect_callback = callback
        logger.debug(f"[WSClient] 已注册重连回调: {callback.__name__}")

    def _create_run_waiter(self, run_id: str) -> asyncio.Future:
        """为runId创建等待器"""
        future = asyncio.get_event_loop().create_future()
        self._run_id_waiters[run_id] = future
        return future

    def _remove_run_waiter(self, run_id: str) -> None:
        """移除runId等待器"""
        self._run_id_waiters.pop(run_id, None)

    async def wait_for_run_id(
        self,
        run_id: str,
        timeout: float = 60.0,
    ) -> dict:
        """等待指定runId的响应

        Args:
            run_id: chat.send返回的runId
            timeout: 超时时间

        Returns:
            响应payload

        Raises:
            TimeoutError: 超时未收到响应
            Exception: OpenClaw返回错误
        """
        future = self._create_run_waiter(run_id)
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"[WSClient] 等待runId={run_id}超时")
            raise
        finally:
            self._remove_run_waiter(run_id)
