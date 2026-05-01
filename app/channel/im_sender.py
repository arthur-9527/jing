"""公共 IM 发送器

统一的媒体生成 + 条件检查 + 推送接口。
IM 回复和日常事件分享共用此模块。

核心规则：
- 全满足才发，任一不满足就 pass
- text 始终可以发送（不需要额外条件）
- image 需要: IMAGE_VIDEO_GEN_ENABLED + Channel.supports_image + 好感度三维均≥80
- video 需要: IMAGE_VIDEO_GEN_ENABLED + Channel.supports_video + 好感度三维均≥90
"""

import asyncio
import base64
import logging
from pathlib import Path
from typing import Optional

from app.channel.types import OutboundMessage, ContentBlock, MediaType
from app.providers.image_gen import get_image_gen_provider
from app.providers.video_gen import get_video_gen_provider
from app.config import settings

logger = logging.getLogger(__name__)


class IMSender:
    """公共 IM 发送器 — 条件检查 + 媒体生成 + 推送"""

    def __init__(
        self,
        character_id: str,
        reference_image_path: str | None = None,
    ):
        self._character_id = character_id
        self._reference_image_path = reference_image_path

    # ------------------------------------------------------------------
    # 条件检查
    # ------------------------------------------------------------------

    async def check_media_conditions(
        self,
        user_id: str,
        requires_image: bool = False,
        requires_video: bool = False,
    ) -> tuple[bool, str | None]:
        """检查媒体生成条件是否全部满足

        Args:
            user_id: 目标用户 ID
            requires_image: 是否需要生成图片
            requires_video: 是否需要生成视频

        Returns:
            (是否通过, 失败原因) — 通过时原因为 None
        """
        if not requires_image and not requires_video:
            return True, None

        if not settings.IMAGE_VIDEO_GEN_ENABLED:
            return False, "IMAGE_VIDEO_GEN_ENABLED 未开启"

        # 解析用户 → Channel
        channel_id = await self._resolve_channel(user_id)
        if not channel_id:
            return False, f"用户 {user_id} 无可用 Channel"

        from app.channel.manager import get_channel_manager
        channel = get_channel_manager().get_channel(channel_id)
        if not channel:
            return False, f"Channel {channel_id} 未注册"

        if requires_image and not channel.supports_image:
            return False, f"Channel {channel_id} 不支持图片发送"
        if requires_video and not channel.supports_video:
            return False, f"Channel {channel_id} 不支持视频发送"

        # 好感度门槛
        min_score = await self._get_affection_min_score(user_id)
        if min_score is None:
            return False, "无法获取好感度分数"

        if requires_image and min_score < 80:
            return False, f"好感度三维最低分 {min_score:.1f} < 80（图片门槛）"
        if requires_video and min_score < 90:
            return False, f"好感度三维最低分 {min_score:.1f} < 90（视频门槛）"

        return True, None

    # ------------------------------------------------------------------
    # 发送分享（all-or-nothing）
    # ------------------------------------------------------------------

    async def send_share(
        self,
        user_id: str,
        text: str,
        image_prompt: str | None = None,
        video_prompt: str | None = None,
    ) -> bool:
        """发送分享消息 — 全满足才发，任一不满足就不发

        Args:
            user_id: 目标用户 ID
            text: 分享文本（必选）
            image_prompt: 图片生成提示词（可选）
            video_prompt: 视频生成提示词（可选）

        Returns:
            是否成功发送
        """
        requires_image = bool(image_prompt)
        requires_video = bool(video_prompt)

        passed, reason = await self.check_media_conditions(
            user_id,
            requires_image=requires_image,
            requires_video=requires_video,
        )
        if not passed:
            logger.info(f"[IMSender] 分享条件不满足，跳过: {reason}")
            return False

        channel_id = await self._resolve_channel(user_id)
        if not channel_id:
            logger.warning(f"[IMSender] 无法解析用户 {user_id} 的 Channel")
            return False

        outbound = OutboundMessage(channel_id=channel_id, user_id=user_id)
        outbound.add_text(text)

        # 生成图片
        image_url = None
        if image_prompt:
            image_url = await self.generate_image(image_prompt)
            if image_url:
                outbound.contents.append(ContentBlock(
                    type=MediaType.IMAGE, content="",
                    mime_type="image/png", url=image_url,
                ))
            else:
                logger.warning("[IMSender] 图片生成失败，按 all-or-nothing 规则整体跳过")
                return False

        # 生成视频
        video_url = None
        if video_prompt:
            video_url = await self.generate_video(video_prompt)
            if video_url:
                outbound.contents.append(ContentBlock(
                    type=MediaType.VIDEO, content="",
                    mime_type="video/mp4", url=video_url,
                ))
            else:
                logger.warning("[IMSender] 视频生成失败，按 all-or-nothing 规则整体跳过")
                return False

        # 推送
        try:
            from app.channel.manager import get_channel_manager
            success = await get_channel_manager().send_to_user(user_id, outbound)
            if success:
                parts = ["text"]
                if image_url:
                    parts.append("image")
                if video_url:
                    parts.append("video")
                logger.info(f"[IMSender] 分享发送成功: user={user_id}, types={parts}")
            else:
                logger.warning(f"[IMSender] 分享发送失败: user={user_id}")
            return success
        except Exception as e:
            logger.error(f"[IMSender] 推送异常: {e}")
            return False

    # ------------------------------------------------------------------
    # 媒体生成
    # ------------------------------------------------------------------

    async def generate_image(self, prompt: str) -> str | None:
        """生成图片，返回 URL，失败返回 None"""
        try:
            image_gen = get_image_gen_provider()
            if not image_gen:
                logger.error("[IMSender] ImageGen Provider 未配置")
                return None

            reference_image = await self._get_reference_image()
            if not reference_image:
                logger.warning("[IMSender] 无参考图")
                reference_image = ""

            task = await image_gen.submit(prompt=prompt, reference_image=reference_image)
            if task.status.name not in ("PENDING", "RUNNING"):
                logger.error(f"[IMSender] 图片任务提交失败: {task.error}")
                return None

            logger.info(f"[IMSender] 图片任务已提交: {task.task_id}")

            for _ in range(60):
                await asyncio.sleep(2)
                task = await image_gen.poll(task.task_id)
                if task.status.name == "SUCCEEDED":
                    break
                elif task.status.name == "FAILED":
                    logger.error(f"[IMSender] 图片生成失败: {task.error}")
                    return None

            if task.status.name != "SUCCEEDED" or not task.result:
                logger.error("[IMSender] 图片生成超时")
                return None

            image_url = task.result.images[0].url if task.result.images else None
            if not image_url:
                logger.error("[IMSender] 图片结果无 URL")
                return None

            logger.info(f"[IMSender] 图片生成完成: {image_url}")
            return image_url

        except Exception as e:
            logger.error(f"[IMSender] 图片生成异常: {e}")
            return None

    async def generate_video(self, prompt: str) -> str | None:
        """生成视频（首帧图生图 → 图生视频），返回 URL，失败返回 None"""
        try:
            video_gen = get_video_gen_provider()
            if not video_gen:
                logger.error("[IMSender] VideoGen Provider 未配置")
                return None

            image_gen = get_image_gen_provider()
            if not image_gen:
                logger.error("[IMSender] ImageGen Provider 未配置（视频需要首帧）")
                return None

            reference_image = await self._get_reference_image()
            if not reference_image:
                logger.warning("[IMSender] 无参考图")
                reference_image = ""

            # Step 1: 生成首帧图片
            logger.info(f"[IMSender] 生成视频首帧: {prompt}")
            first_frame_task = await image_gen.submit(
                prompt=prompt, reference_image=reference_image,
            )
            if first_frame_task.status.name not in ("PENDING", "RUNNING"):
                logger.error(f"[IMSender] 首帧图片提交失败: {first_frame_task.error}")
                return None

            first_frame_url = None
            for _ in range(60):
                await asyncio.sleep(2)
                first_frame_task = await image_gen.poll(first_frame_task.task_id)
                if first_frame_task.status.name == "SUCCEEDED":
                    if first_frame_task.result and first_frame_task.result.images:
                        first_frame_url = first_frame_task.result.images[0].url
                    break
                elif first_frame_task.status.name == "FAILED":
                    logger.error(f"[IMSender] 首帧图片生成失败: {first_frame_task.error}")
                    return None

            if not first_frame_url:
                logger.error("[IMSender] 首帧图片生成超时")
                return None

            logger.info(f"[IMSender] 首帧图片完成: {first_frame_url}")

            # Step 2: 下载首帧 → 生成视频
            first_frame_data = await self._download_media(first_frame_url)
            if not first_frame_data:
                logger.error("[IMSender] 无法下载首帧图片")
                return None

            first_frame_b64 = base64.b64encode(first_frame_data).decode("utf-8")
            first_frame_uri = f"data:image/jpeg;base64,{first_frame_b64}"

            task = await video_gen.submit(prompt=prompt, reference_image=first_frame_uri)
            if task.status.name not in ("PENDING", "RUNNING"):
                logger.error(f"[IMSender] 视频任务提交失败: {task.error}")
                return None

            logger.info(f"[IMSender] 视频任务已提交: {task.task_id}")

            for _ in range(120):
                await asyncio.sleep(2)
                task = await video_gen.poll(task.task_id)
                if task.status.name == "SUCCEEDED":
                    break
                elif task.status.name == "FAILED":
                    logger.error(f"[IMSender] 视频生成失败: {task.error}")
                    return None

            if task.status.name != "SUCCEEDED" or not task.result:
                logger.error("[IMSender] 视频生成超时")
                return None

            video_url = task.result.videos[0].url if task.result.videos else None
            if not video_url:
                logger.error("[IMSender] 视频结果无 URL")
                return None

            logger.info(f"[IMSender] 视频生成完成: {video_url}")
            return video_url

        except Exception as e:
            logger.error(f"[IMSender] 视频生成异常: {e}")
            return None

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    async def _resolve_channel(self, user_id: str) -> str | None:
        """解析用户 → Channel ID"""
        try:
            from app.channel.user_manager import get_user_manager
            from app.channel.manager import get_channel_manager
            bindings = await get_user_manager().get_user_bindings(user_id)
            channel_manager = get_channel_manager()
            for binding in bindings:
                platform = binding.get("platform", "")
                if platform in channel_manager.get_registered_channels():
                    return platform
        except Exception as e:
            logger.warning(f"[IMSender] 解析 Channel 失败: {e}")
        return None

    async def _get_affection_min_score(self, user_id: str) -> float | None:
        """获取用户对当前角色的好感度三维最低分"""
        try:
            from app.services.affection.models import AffectionDimension
            from app.services.affection.service import AffectionService
            from app.stone import get_affection_repo, get_database

            affection_repo = get_affection_repo()
            db_conn = get_database()
            service = AffectionService(
                affection_repo=affection_repo, db_conn=db_conn,
            )
            state = await service.get_state(self._character_id, user_id)
            return min(
                state.get_dimension(AffectionDimension.TRUST).total,
                state.get_dimension(AffectionDimension.INTIMACY).total,
                state.get_dimension(AffectionDimension.RESPECT).total,
            )
        except Exception as e:
            logger.warning(f"[IMSender] 获取好感度失败: {e}")
            return None

    async def _get_reference_image(self) -> str | None:
        """加载角色参考图，返回 data URI"""
        if not self._reference_image_path:
            return None
        try:
            path = Path(self._reference_image_path)
            if not path.exists():
                logger.warning(f"[IMSender] 参考图不存在: {path}")
                return None
            image_bytes = path.read_bytes()
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            return f"data:image/jpeg;base64,{b64_data}"
        except Exception as e:
            logger.error(f"[IMSender] 读取参考图失败: {e}")
            return None

    async def _download_media(self, url: str) -> bytes | None:
        """下载媒体文件"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
            return None
        except Exception as e:
            logger.error(f"[IMSender] 下载媒体失败: {e}")
            return None
