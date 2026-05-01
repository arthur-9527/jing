# -*- coding: utf-8 -*-
"""
视频分析服务 - 使用多模态LLM分析视频并生成标签

支持独立配置 Vision Provider：
- VISION_PROVIDER: 使用哪个 Provider（litellm/cerebras）
- VISION_MODEL: 使用哪个模型
- VISION_MODEL_TYPE: 视觉能力（none/image/video）
"""
import base64
import json
import logging
from typing import List, Dict, Any
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class VideoAnalysisResult:
    """视频分析结果"""
    suggested_name: str
    description: str
    tags: List[Dict[str, Any]]
    confidence: float


VIDEO_ANALYSIS_PROMPT = """分析这段MMD动作视频的动作内容，结合以下文本描述生成标签。

文本描述: {text_prompt}
视频时长: {duration}秒

## 重要说明
- **只分析动作本身**，不关注人物外貌、衣着、发型等特征
- 人物模型仅作为动作参考，分析结果与具体模型无关
- 关注：动作类型、节奏、力度、情绪表达、动作风格

### 1. 名称生成（suggested_name）
- 基于动作内容**综合理解后总结生成**
- 简洁的中文名称，不超过10个字
- **禁止直接copy文本描述**，必须经过理解提炼

### 2. 描述生成（description）
- 专注描述动作本身（动作类型、节奏、力度、风格、情绪）
- 20-50字以内，简洁准确
- **禁止描述人物外貌、衣着、发型等**

### 3. 标签维度（tags）
- emotion: happy/sad/calm/neutral 等
- action: walk/dance/wave 等
- style: cute/cool/elegant 等

请返回JSON格式:
{{
  "suggested_name": "动作名称",
  "description": "动作描述",
  "confidence": 0.85,
  "tags": [
    {{"type": "emotion", "name": "happy", "display_name": "开心"}},
    {{"type": "action", "name": "walk", "display_name": "行走"}}
  ]
}}
"""


class VideoAnalysisService:
    """视频+文本分析服务，生成标签和描述

    使用独立的 Vision Provider 配置：
    - VISION_PROVIDER: litellm / cerebras
    - VISION_MODEL: 模型名称
    - VISION_MODEL_TYPE: none / image / video
    """

    def __init__(self):
        self._provider = None
        self._provider_type = None

    @property
    def provider(self):
        """懒加载 Vision Provider"""
        if self._provider is None:
            from app.providers.llm.base import VisionCapability

            provider_type = settings.VISION_PROVIDER.lower()

            if provider_type == 'cerebras':
                from app.providers.llm.cerebras import CerebrasProvider
                self._provider = CerebrasProvider()
            else:
                from app.providers.llm.litellm import LiteLLMProvider
                # LiteLLM 可以指定模型
                base_url = settings.VISION_API_BASE_URL or settings.LITELLM_API_BASE_URL
                api_key = settings.VISION_API_KEY or settings.LITELLM_API_KEY
                model = settings.VISION_MODEL or settings.LITELLM_MODEL

                self._provider = LiteLLMProvider(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                )

            self._provider_type = provider_type
            logger.info(
                f"[VideoAnalysis] Vision Provider: {self._provider.NAME}, "
                f"Model: {self._provider.model}, "
                f"URL: {self._provider.base_url}"
            )

        return self._provider

    async def analyze_video(
        self,
        video_path: str,
        text_prompt: str,
        duration: float
    ) -> VideoAnalysisResult:
        """分析视频并生成标签"""
        prompt = VIDEO_ANALYSIS_PROMPT.format(
            text_prompt=text_prompt,
            duration=duration
        )

        # 优先使用 Provider 的 analyze_video 方法
        if self.provider.supports_video:
            result = await self.provider.analyze_video(
                video_path=video_path,
                prompt=prompt,
                duration=duration,
            )
            if result:
                return self._convert_result(result.description, text_prompt)

        # Fallback: 提取关键帧并直接分析
        return await self._analyze_with_keyframes(video_path, text_prompt, duration)

    async def analyze_video_from_bytes(
        self,
        video_data: bytes,
        text_prompt: str,
        duration: float
    ) -> VideoAnalysisResult:
        """从字节数据分析视频"""
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            f.write(video_data)
            temp_path = f.name

        try:
            return await self.analyze_video(temp_path, text_prompt, duration)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _convert_result(self, description: str, original_prompt: str) -> VideoAnalysisResult:
        """转换 VisionResult 为 VideoAnalysisResult"""
        try:
            json_str = self._extract_json(description)
            data = json.loads(json_str)

            tags = self._build_tags(data.get("tags", []), original_prompt)

            return VideoAnalysisResult(
                suggested_name=data.get("suggested_name", self._extract_name_from_prompt(original_prompt)),
                description=data.get("description", description[:50]),
                tags=tags,
                confidence=data.get("confidence", 0.8),
            )
        except Exception:
            return self._build_default_result(description, original_prompt)

    async def _analyze_with_keyframes(
        self,
        video_path: str,
        text_prompt: str,
        duration: float
    ) -> VideoAnalysisResult:
        """提取关键帧并分析（fallback 模式）"""
        keyframes = self._extract_keyframes_by_time(video_path, duration)

        if not keyframes:
            raise ValueError("无法提取视频关键帧")

        # 构建带图片的消息
        content = [
            {"type": "text", "text": VIDEO_ANALYSIS_PROMPT.format(
                text_prompt=text_prompt,
                duration=duration
            )}
        ]

        for frame_b64 in keyframes:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{frame_b64}",
                    "detail": "low"
                }
            })

        # 调用 LLM 分析
        response = await self.provider.chat([
            {"role": "user", "content": content}
        ])

        return self._convert_result(response, text_prompt)

    def _build_tags(self, tags_data: list, original_prompt: str) -> List[Dict[str, Any]]:
        """构建标签列表"""
        tags = []
        tag_types = set()

        for tag in tags_data:
            if isinstance(tag, dict):
                tag_type = tag.get("type", "")
                tag_name = tag.get("name", "")
                if tag_type and tag_name:
                    tags.append({
                        "type": tag_type,
                        "name": tag_name.lower(),
                        "display_name": self._get_display_name(tag_type, tag_name),
                        "weight": 1.0
                    })
                    tag_types.add(tag_type)

        # 确保基础标签
        if "emotion" not in tag_types:
            tags.insert(0, {"type": "emotion", "name": "neutral", "display_name": "中性", "weight": 1.0})
        if "action" not in tag_types:
            action_name, action_display = self._infer_action_from_text(original_prompt)
            tags.insert(1, {"type": "action", "name": action_name, "display_name": action_display, "weight": 1.0})

        return tags

    def _build_default_result(self, description: str, original_prompt: str) -> VideoAnalysisResult:
        """构建默认结果"""
        action_name, action_display = self._infer_action_from_text(original_prompt)
        return VideoAnalysisResult(
            suggested_name=self._extract_name_from_prompt(original_prompt),
            description=description[:50] if description else original_prompt[:50],
            tags=[
                {"type": "emotion", "name": "neutral", "display_name": "中性", "weight": 1.0},
                {"type": "action", "name": action_name, "display_name": action_display, "weight": 1.0}
            ],
            confidence=0.5,
        )

    def _extract_json(self, text: str) -> str:
        """从文本中提取 JSON"""
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return text[start:end]

        return text.strip()

    def _extract_keyframes_by_time(self, video_path: str, duration: float) -> List[str]:
        """基于时间提取关键帧"""
        try:
            import cv2
            import os

            temp_mp4 = None
            converted = False

            try:
                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    return []

                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count_raw = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                cap.release()

                needs_conversion = (
                    not video_path.lower().endswith('.mp4') or
                    frame_count_raw <= 0 or
                    frame_count_raw > 1e9
                )

                if needs_conversion:
                    temp_mp4 = self._convert_to_mp4(video_path)
                    video_path = temp_mp4
                    converted = True

                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    return []

                fps = cap.get(cv2.CAP_PROP_FPS)
                duration_ms = (int(duration * 1000) // 100) * 100
                time_points = [0]

                current_time = 500
                while current_time < duration_ms:
                    time_points.append(current_time)
                    current_time += 500

                if duration_ms not in time_points:
                    time_points.append(duration_ms)

                keyframes = []
                for time_ms in time_points:
                    frame_idx = int((time_ms / 1000) * fps)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                    ret, frame = cap.read()

                    if ret:
                        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        _, buffer = cv2.imencode('.jpg', frame_rgb, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                        keyframes.append(base64.b64encode(buffer).decode('utf-8'))

                cap.release()
                return keyframes

            finally:
                if converted and temp_mp4 and os.path.exists(temp_mp4):
                    os.remove(temp_mp4)

        except ImportError:
            return []
        except Exception as e:
            logger.warning(f"提取关键帧失败: {e}")
            return []

    def _convert_to_mp4(self, video_path: str) -> str:
        """转换为 mp4 格式"""
        import tempfile
        import subprocess
        import os

        fd, mp4_path = tempfile.mkstemp(suffix='.mp4')
        os.close(fd)

        try:
            subprocess.run([
                'ffmpeg', '-i', video_path,
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
                '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                '-an', '-y', mp4_path
            ], check=True, capture_output=True, timeout=30)
            return mp4_path
        except Exception:
            if os.path.exists(mp4_path):
                os.remove(mp4_path)
            raise

    def _infer_action_from_text(self, prompt: str) -> tuple:
        """从文本描述中推断动作类型"""
        if not prompt:
            return ("unknown", "未知动作")

        action_keywords = {
            "走": ("walk", "行走"), "行走": ("walk", "行走"),
            "跑步": ("run", "跑步"), "跳舞": ("dance", "舞蹈"),
            "跳跃": ("jump", "跳跃"), "挥手": ("wave", "挥手"),
            "鞠躬": ("bow", "鞠躬"), "待机": ("idle", "待机"),
        }

        prompt_lower = prompt.lower()
        for keyword, (action_name, display_name) in action_keywords.items():
            if keyword.lower() in prompt_lower:
                return (action_name, display_name)

        return ("unknown", "未知动作")

    def _extract_name_from_prompt(self, prompt: str) -> str:
        """从提示中提取名称"""
        if not prompt:
            return "未命名动作"

        keywords = ["跳舞", "行走", "跑步", "挥手", "鞠躬", "待机"]
        for kw in keywords:
            if kw.lower() in prompt.lower():
                return kw[:10]

        return prompt[:10] if len(prompt) > 10 else prompt

    def _get_display_name(self, tag_type: str, name: str) -> str:
        """获取标签的中文显示名"""
        display_names = {
            "emotion": {
                "happy": "开心", "sad": "悲伤", "calm": "平静", "neutral": "中性"
            },
            "action": {
                "walk": "行走", "run": "跑步", "dance": "舞蹈",
                "jump": "跳跃", "wave": "挥手", "idle": "待机"
            },
            "style": {
                "cute": "可爱", "cool": "酷帅", "elegant": "优雅"
            }
        }

        return display_names.get(tag_type, {}).get(name.lower(), name)


# 全局单例
video_analysis_service = VideoAnalysisService()
