#!/usr/bin/env python3
"""
Panel 状态管理器 - 统一管理 HTML 面板的显示/隐藏状态

职责：
1. 统一的状态管理：维护当前 panel 状态，防止重复发送
2. 统一的操作接口：show_panel() 和 hide_panel()
3. 状态追踪：记录每次操作的来源，方便排错
4. 防抖机制：避免短时间内重复发送相同消息
5. 自动位置计算：根据面板大小自动选择位置（左中或居中）

设计原则：
- 单一入口：所有 panel 操作必须通过 PanelManager
- 状态锁：防止并发操作导致状态混乱
- 操作历史：保留最近操作记录，方便排错
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from app.realtime.agent_ws_manager import AgentWSManager


@dataclass
class PanelOperation:
    """Panel 操作记录"""
    op: str  # "SHOW", "HIDE", "SKIP", "UPDATE"
    source: str  # 操作来源（用于排错）
    timestamp: float
    reason: str = ""  # 跳过原因等
    panel_info: dict = field(default_factory=dict)  # panel 内容摘要


class PanelManager:
    """
    Panel 状态管理器（多 Panel 版本）
    
    核心功能：
    - show_panel(): 显示 panel（带内容）
    - hide_panel(): 隐藏指定 panel
    - hide_all_panels(): 隐藏所有 panel
    - get_state(): 获取当前状态
    - get_history(): 获取操作历史
    - get_active_panels(): 获取当前活跃的 panel 列表
    
    多 Panel 支持：
    - 支持同时显示多个 panel（通过 id 区分）
    - 每个 panel 有唯一 id（自动生成或手动指定）
    - 自动分配 zIndex（递增）
    - 自动计算位置 x, y, z（根据面板大小）
    
    排错支持：
    - 每次操作记录来源和内容摘要
    - 提供 get_history() 查询最近操作
    - 日志输出包含 source 标记
    """
    
    def __init__(
        self,
        ws_manager: "AgentWSManager",
        max_history: int = 50,
        debounce_interval: float = 0.05,  # 50ms 内相同操作跳过
    ):
        """
        Args:
            ws_manager: WebSocket 管理器，用于广播消息
            max_history: 操作历史最大记录数
            debounce_interval: 防抖间隔（秒）
        """
        self._ws_manager = ws_manager
        self._max_history = max_history
        self._debounce_interval = debounce_interval
        
        # ⭐ 多 Panel 存储：panel_id -> panel_data
        self._panels: dict[str, dict] = {}
        self._panel_counter: int = 0  # 用于生成 zIndex
        self._last_update_time: float = 0.0
        
        # 操作历史
        self._operation_history: deque[PanelOperation] = deque(maxlen=max_history)
        
        # 状态锁（防止并发操作）
        self._lock = asyncio.Lock()
        
        logger.info(
            f"[PanelManager] 初始化完成（多 Panel 版本）, "
            f"max_history={max_history}, debounce={debounce_interval*1000}ms"
        )
    
    # ===== 公共接口 =====
    
    def get_active_panels(self) -> list[dict]:
        """
        获取当前活跃的 panel 列表
        
        Returns:
            活跃 panel 列表，每项包含 id, x, y, z, width, height
        """
        return [
            {
                "id": panel_id,
                "x": p.get("x"),
                "y": p.get("y"),
                "z": p.get("z"),
                "width": p.get("width"),
                "height": p.get("height"),
            }
            for panel_id, p in self._panels.items()
        ]
    
    async def show_panel(
        self,
        panel_html: dict,
        source: str = "unknown",
    ) -> bool:
        """
        显示 panel（多 Panel 版本）
        
        Args:
            panel_html: Panel 内容字典，必须包含 html 字段
            source: 操作来源（用于排错），如 "speak_followup_text"
        
        Returns:
            True 表示成功推送，False 表示跳过
        
        Note:
            - 如果 panel_html 没有 html 字段或 html 为空，会跳过
            - 自动生成 panel_id（如果没有）
            - 自动分配 zIndex（递增）
            - 自动计算位置 x, y, z（根据面板大小）
            - 操作会被记录到历史
        """
        # 检查 panel_html 是否有效
        if not panel_html:
            self._record_operation("SKIP", source, reason="empty_panel_html")
            logger.debug(f"[PanelManager] SKIP show: source={source}, reason=empty_panel_html")
            return False
        
        html_content = panel_html.get("html", "")
        if not html_content:
            self._record_operation("SKIP", source, reason="no_html_content")
            logger.debug(f"[PanelManager] SKIP show: source={source}, reason=no_html_content")
            return False
        
        async with self._lock:
            now = time.monotonic()
            
            # ⭐ 防抖检查：短时间内相同 html 内容跳过
            existing_panel_id = self._find_same_html_content(html_content)
            if existing_panel_id:
                interval = now - self._last_update_time
                if interval < self._debounce_interval:
                    self._record_operation("SKIP", source, reason="debounce_same_content")
                    logger.debug(
                        f"[PanelManager] SKIP show: source={source}, "
                        f"reason=debounce, interval={interval*1000:.1f}ms"
                    )
                    return False
            
            # ⭐ 自动生成 panel_id（如果没有）
            panel_id = panel_html.get("id")
            if not panel_id:
                panel_id = f"panel-{int(time.time() * 1000)}"
                panel_html["id"] = panel_id
            
            # ⭐ 自动分配 zIndex（递增）
            self._panel_counter += 1
            panel_html["zIndex"] = self._panel_counter
            
            # 确保 visible = True
            panel_html["visible"] = True
            
            # ⭐ 自动设置 10 秒延时关闭
            panel_html["duration"] = 10000  # 10秒 = 10000毫秒
            
            # ⭐ 深度计算：新窗口 z=0，老窗口后退 z+2
            await self._calculate_panel_depth(panel_html)
            
            # ⭐ 自动计算位置 x, y（根据面板大小）
            self._calculate_panel_position(panel_html)
            
            # 执行显示
            await self._broadcast_panel(panel_html)
            
            # ⭐ 存储到多 panel 字典
            self._panels[panel_id] = panel_html.copy()
            self._last_update_time = now
            
            # 记录操作
            panel_info = self._extract_panel_info(panel_html)
            self._record_operation("SHOW", source, panel_info=panel_info)
            
            logger.info(
                f"[PanelManager] SHOW panel: id={panel_id}, source={source}, "
                f"zIndex={panel_html['zIndex']}, "
                f"x={panel_html.get('x')}, y={panel_html.get('y')}, z={panel_html.get('z')}, "
                f"width={panel_html.get('width')}, height={panel_html.get('height')}"
            )
            
            return True
    
    async def hide_panel(
        self,
        panel_id: Optional[str] = None,
        source: str = "unknown",
        reason: str = "",
    ) -> bool:
        """
        隐藏指定 panel
        
        Args:
            panel_id: 要隐藏的 panel ID，None 表示隐藏所有
            source: 操作来源（用于排错）
            reason: 隐藏原因
        
        Returns:
            True 表示成功推送，False 表示跳过
        """
        async with self._lock:
            if panel_id:
                # 鱼藏指定 panel
                if panel_id not in self._panels:
                    self._record_operation("SKIP", source, reason=f"panel_not_found: {panel_id}")
                    logger.debug(f"[PanelManager] SKIP hide: panel_id={panel_id} not found")
                    return False
                
                # 发送隐藏消息
                await self._broadcast_panel({"id": panel_id, "visible": False})
                
                # 从存储中移除
                self._panels.pop(panel_id, None)
                self._last_update_time = time.monotonic()
                
                self._record_operation("HIDE", source, reason=reason, panel_info={"id": panel_id})
                logger.info(f"[PanelManager] HIDE panel: id={panel_id}, source={source}, reason={reason}")
                
                return True
            else:
                # 鱼藏所有 panel
                if not self._panels:
                    self._record_operation("SKIP", source, reason="no_active_panels")
                    logger.debug(f"[PanelManager] SKIP hide all: no active panels")
                    return False
                
                # 发送隐藏所有消息
                await self._broadcast_panel({"visible": False})
                
                # 清空所有 panel
                panel_count = len(self._panels)
                self._panels.clear()
                self._panel_counter = 0
                self._last_update_time = time.monotonic()
                
                self._record_operation("HIDE", source, reason=f"hide_all ({reason})", panel_info={"count": panel_count})
                logger.info(f"[PanelManager] HIDE all panels: count={panel_count}, source={source}, reason={reason}")
                
                return True
    
    async def force_hide_panel(
        self,
        source: str = "unknown",
        reason: str = "forced",
    ) -> bool:
        """
        强制隐藏所有 panel（不检查当前状态）
        
        用于打断等需要立即关闭的场景。
        
        Args:
            source: 操作来源
            reason: 鱼藏原因
        
        Returns:
            True 表示成功推送
        """
        async with self._lock:
            # 直接发送隐藏消息
            await self._broadcast_panel({"visible": False})
            
            # 清空所有 panel
            panel_count = len(self._panels)
            self._panels.clear()
            self._panel_counter = 0
            self._last_update_time = time.monotonic()
            
            # 记录操作
            self._record_operation("HIDE", source, reason=f"forced ({reason})", panel_info={"count": panel_count})
            
            logger.info(
                f"[PanelManager] FORCE HIDE all panels: count={panel_count}, source={source}, reason={reason}"
            )
            
            return True
    
    # ===== 深度计算 =====
    
    async def _calculate_panel_depth(self, panel_html: dict) -> None:
        """
        计算面板深度 z
        
        新坐标系深度规则：
        - z 范围：0.5 ~ 2（越大越靠前）
        - 新窗口 z = 2（最靠前）
        - 老窗口后退 z - 0.3（变小/后退）
        - 最小深度限制 z = 0.5（避免超出镜头范围）
        
        Args:
            panel_html: Panel 内容字典，会直接修改其 z 字段
        """
        if self._panels:
            # 有旧窗口，让旧窗口后退
            for old_panel_id, old_panel in self._panels.items():
                old_z = old_panel.get("z", 2.0)
                new_z = old_z - 0.3
                
                # 限制最小深度 z = 0.5
                if new_z < 0.5:
                    new_z = 0.5
                
                # 更新老窗口的 z 值
                old_panel["z"] = new_z
                
                # 发送更新消息让老窗口后退
                await self._broadcast_panel({
                    "id": old_panel_id,
                    "visible": True,
                    "z": new_z,
                })
                
                logger.info(
                    f"[PanelManager] 老窗口后退: id={old_panel_id}, "
                    f"z={old_z} → {new_z}"
                )
            
            # 新窗口 z = 2（最靠前）
            panel_html["z"] = 2.0
            logger.info("[PanelManager] 新窗口深度: z=2.0（最靠前）")
        else:
            # 无旧窗口，z = 2（最靠前）
            panel_html["z"] = 2.0
            logger.info("[PanelManager] 首个窗口深度: z=2.0（最靠前）")
    
    # ===== 位置计算 =====
    
    def _calculate_panel_position(self, panel_html: dict) -> None:
        """
        设置面板位置 x, y
        
        新坐标系规则：
        - 所有面板统一放置在屏幕中心点 (0, 0)
        - x = 0 表示水平中心
        - y = 0 表示垂直中心
        - 不再根据面板大小计算不同位置
        
        Args:
            panel_html: Panel 内容字典，会直接修改其 x, y 字段
        """
        # 所有面板统一设置为中心点 (0, 0)
        panel_html["x"] = 0
        panel_html["y"] = 0
        
        logger.debug(
            f"[PanelManager] 面板位置：中心点, "
            f"position=(x=0, y=0)"
        )
    
    # ===== 状态查询 =====
    
    @property
    def is_visible(self) -> bool:
        """当前是否有活跃 panel"""
        return len(self._panels) > 0
    
    @property
    def current_panel_html(self) -> Optional[dict]:
        """当前最新的 panel 内容（兼容旧接口）"""
        if not self._panels:
            return None
        # 返回最后一个添加的 panel
        last_id = list(self._panels.keys())[-1] if self._panels else None
        return self._panels.get(last_id)
    
    @property
    def panel_count(self) -> int:
        """当前活跃 panel 数量"""
        return len(self._panels)
    
    def get_state(self) -> dict:
        """
        获取当前状态（用于排错）
        
        Returns:
            状态字典，包含 panel_count, panels, last_time 等
        """
        last_op = self._operation_history[-1] if self._operation_history else None
        
        return {
            "panel_count": len(self._panels),
            "panel_ids": list(self._panels.keys()),
            "last_update_time": self._last_update_time,
            "last_operation": last_op.op if last_op else None,
            "last_source": last_op.source if last_op else None,
            "last_reason": last_op.reason if last_op else None,
            "history_count": len(self._operation_history),
        }
    
    def get_history(self, limit: int = 20) -> list[dict]:
        """
        获取操作历史（用于排错）
        
        Args:
            limit: 返回的最大记录数
        
        Returns:
            操作历史列表，每项包含 op, source, timestamp, reason, panel_info
        """
        operations = list(self._operation_history)[-limit:]
        return [
            {
                "op": op.op,
                "source": op.source,
                "timestamp": op.timestamp,
                "time_str": time.strftime("%H:%M:%S", time.localtime(op.timestamp)),
                "reason": op.reason,
                "panel_info": op.panel_info,
            }
            for op in operations
        ]
    
    # ===== 内部方法 =====
    
    async def _broadcast_panel(self, data: dict):
        """广播 panel 消息到 WebSocket"""
        await self._ws_manager.broadcast_panel_html(data)
    
    def _find_same_html_content(self, html_content: str) -> Optional[str]:
        """查找相同 html 内容的 panel（用于防抖）
        
        Args:
            html_content: 要检查的 html 内容
            
        Returns:
            如果找到相同内容的 panel_id，否则返回 None
        """
        for panel_id, panel_data in self._panels.items():
            if panel_data.get("html", "") == html_content:
                return panel_id
        return None
    
    def _extract_panel_info(self, panel_html: dict) -> dict:
        """提取 panel 内容摘要（用于历史记录）"""
        return {
            "id": panel_html.get("id"),
            "has_html": bool(panel_html.get("html")),
            "html_len": len(panel_html.get("html", "")),
            "x": panel_html.get("x"),
            "y": panel_html.get("y"),
            "z": panel_html.get("z"),
            "width": panel_html.get("width"),
            "height": panel_html.get("height"),
            "zIndex": panel_html.get("zIndex"),
            "visible": panel_html.get("visible", True),
        }
    
    def _record_operation(
        self,
        op: str,
        source: str,
        reason: str = "",
        panel_info: dict = None,
    ):
        """记录操作到历史"""
        operation = PanelOperation(
            op=op,
            source=source,
            timestamp=time.time(),
            reason=reason,
            panel_info=panel_info or {},
        )
        self._operation_history.append(operation)
    
    # ===== 工具方法 =====
    
    def reset(self):
        """重置状态（用于错误恢复）"""
        self._panels.clear()
        self._panel_counter = 0
        self._last_update_time = 0.0
        self._operation_history.clear()
        logger.info("[PanelManager] 状态已重置")