# -*- coding: utf-8 -*-
"""
视频分析服务 - 使用多模态LLM分析视频并生成标签
"""
import base64
import json
import httpx
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from openai import AsyncOpenAI

from app.config import settings


@dataclass
class VideoAnalysisResult:
    """视频分析结果"""
    suggested_name: str
    description: str
    tags: List[Dict[str, Any]]
    confidence: float


# 视频分析Prompt模板 - 优化版
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
- 示例：活泼舞蹈、优雅转身、热情挥手、向前行走

### 2. 描述生成（description）
- 专注描述动作本身（动作类型、节奏、力度、风格、情绪）
- 20-50字以内，简洁准确
- **禁止描述人物外貌、衣着、发型等**
- **不要出现"与文本描述一致/相似"等表述**
- 示例：人物站立，双手置于胸前微动，节奏缓慢，表现出思考的状态

### 3. 置信度（confidence）
- 根据标签生成的准确性评估：
  - 动作识别准确且标签全面: 0.9-1.0
  - 动作识别基本准确: 0.7-0.9
  - 动作识别部分准确: 0.5-0.7
  - 动作识别不够准确: 0.3-0.5

### 4. 标签维度（tags）

**【必须输出的标签 - 共3个】**
1. **system** - 系统分类（固定选项，根据文本判断）:
   - `default` → 文本同时包含"系统"和"默认"（如"系统默认动作"）
   - `thinking` → 文本同时包含"系统"和"思考"或"倾听"（如"系统思考动作"）
   - `idle` → 文本包含"系统"但不包含上述修饰词（如"系统动作"）
   - `others` → 文本不包含"系统"二字

2. **emotion** - 情绪表达（固定选项，选择最合适的）:
   - happy(开心), sad(悲伤), angry(生气), excited(兴奋), calm(平静)
   - surprised(惊讶), scared(害怕), neutral(中性), shy(害羞), confident(自信), dreamy(梦幻)

3. **action** - 动作类型（AI自由生成，**必须输出**）:
   - 根据视频中人物实际做的动作命名
   - 使用简洁英文标识符（小写+下划线），同时提供中文显示名
   - 参考示例：walk(行走), run(跑步), dance(舞蹈), jump(跳跃), wave(挥手)
   - 参考示例：point(指向), call(招手), bow(鞠躬), clap(鼓掌), sit(坐下), stand(站立)
   - 参考示例：turn(转身), spin(旋转), sway(摇摆), stretch(伸展), raise_hand(举手)
   - 不限于示例，可根据实际动作灵活命名，如 salute(敬礼), kiss(飞吻), hug(拥抱)

**【建议输出的标签 - 至少2个】**
从以下维度中选择**至少2个**最相关的输出：
- **style** - 动作风格：cute(可爱), cool(酷帅), elegant(优雅), energetic(活力), graceful(柔美), powerful(有力), gentle(温柔), playful(俏皮), sexy(性感), mysterious(神秘), formal(正式), casual(休闲)
- **speed** - 动作速度：slow(慢), normal(正常), fast(快), very_fast(极速)
- **intensity** - 动作强度：low(低), medium(中), high(高), extreme(极限)
- **scene** - 场景类型：indoor(室内), outdoor(室外), stage(舞台), urban(城市), nature(自然), fantasy(幻想), studio(工作室)
- **rhythm** - 节奏类型：steady(平稳), dynamic(动感), syncopated(切分), flowing(流畅), sharp(急促)
- **complexity** - 动作复杂度：simple(简单), moderate(中等), complex(复杂)

## 输出格式

请返回JSON格式:
{{
  "suggested_name": "综合动作理解生成的名称（10字以内中文）",
  "description": "专注描述动作本身（20-50字）",
  "confidence": 0.85,
  "tags": [
    {{"type": "system", "name": "others", "display_name": "其他动作"}},
    {{"type": "emotion", "name": "happy", "display_name": "开心"}},
    {{"type": "action", "name": "walk", "display_name": "行走"}},
    {{"type": "speed", "name": "normal", "display_name": "正常"}},
    {{"type": "style", "name": "casual", "display_name": "休闲"}},
    {{"type": "intensity", "name": "low", "display_name": "低"}}
  ]
}}

要求:
1. **总标签数量不少于5个**（3个必须 + 至少2个建议）
2. **system、emotion、action 三个标签必须输出**
3. 每个维度最多输出1个标签
4. 只返回JSON，不要有其他文字
"""


class VideoAnalysisService:
    """视频+文本分析服务，生成标签和描述"""
    
    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None
    
    async def _get_client(self) -> AsyncOpenAI:
        """获取OpenAI兼容客户端"""
        if self._client is None:
            api_key = settings.VISION_LLM_API_KEY or settings.LLM_API_KEY or "dummy"
            base_url = settings.VISION_LLM_API_BASE_URL or settings.LLM_API_BASE_URL
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url
            )
        return self._client
    
    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.close()
            self._client = None
    
    async def analyze_video(
        self,
        video_path: str,
        text_prompt: str,
        duration: float
    ) -> VideoAnalysisResult:
        """
        分析视频并生成标签

        Args:
            video_path: 视频文件路径
            text_prompt: 文本描述
            duration: 视频时长（秒）

        Returns:
            VideoAnalysisResult: 分析结果

        Raises:
            Exception: 分析失败时抛出异常
        """
        # 提取关键帧（保留首尾帧 + 中间每500ms一帧）
        keyframes = self._extract_keyframes_by_time(video_path, duration)

        if not keyframes:
            raise ValueError("无法提取视频关键帧")

        # 调用多模态LLM分析图片
        return await self._analyze_with_images(keyframes, text_prompt, duration)
    
    async def analyze_video_from_bytes(
        self,
        video_data: bytes,
        text_prompt: str,
        duration: float
    ) -> VideoAnalysisResult:
        """
        从字节数据分析视频

        Args:
            video_data: 视频文件字节数据
            text_prompt: 文本描述
            duration: 视频时长（秒）

        Returns:
            VideoAnalysisResult: 分析结果

        Raises:
            Exception: 分析失败时抛出异常
        """
        import tempfile
        import os

        # 保存到临时文件
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f:
            f.write(video_data)
            temp_path = f.name

        try:
            return await self.analyze_video(temp_path, text_prompt, duration)
        finally:
            # 清理临时文件
            if os.path.exists(temp_path):
                os.remove(temp_path)
    
    async def _analyze_with_images(
        self,
        keyframes: List[str],  # base64编码的图片
        text_prompt: str,
        duration: float
    ) -> VideoAnalysisResult:
        """
        使用视觉模型分析关键帧图片

        Args:
            keyframes: base64编码的图片列表
            text_prompt: 文本描述
            duration: 视频时长

        Returns:
            VideoAnalysisResult: 分析结果

        Raises:
            Exception: 分析失败时抛出异常
        """
        client = await self._get_client()

        # 构建消息内容
        content = [
            {"type": "text", "text": VIDEO_ANALYSIS_PROMPT.format(
                text_prompt=text_prompt,
                duration=duration
            )}
        ]

        # 添加关键帧图片
        for i, frame_b64 in enumerate(keyframes):
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{frame_b64}",
                    "detail": "low"  # 使用低细节以节省token
                }
            })

        response = await client.chat.completions.create(
            model=settings.VISION_LLM_MODEL,
            messages=[
                {"role": "user", "content": content}
            ],
            max_tokens=1000,
            temperature=0.7
        )

        result_text = response.choices[0].message.content.strip()
        return self._parse_result(result_text, text_prompt)
    
    def _parse_result(self, result_text: str, original_prompt: str) -> VideoAnalysisResult:
        """解析LLM返回的JSON结果"""
        try:
            # 尝试提取JSON
            json_str = self._extract_json(result_text)
            data = json.loads(json_str)
            
            # 构建标签列表 - 支持动态维度
            tags = []
            tags_data = data.get("tags", [])
            
            # 标签权重配置 - 不同维度权重不同
            weight_config = {
                "emotion": 1.0,
                "action": 1.0,
                "scene": 0.8,
                "intensity": 0.7,
                "style": 0.6,
                "speed": 0.5,
                "energy": 0.5,
                "mood": 0.5,
            }
            
            # 处理新的tags数组格式
            if isinstance(tags_data, list):
                for tag in tags_data:
                    if isinstance(tag, dict):
                        tag_type = tag.get("type", "")
                        tag_name = tag.get("name", "")
                        if tag_type and tag_name:
                            tags.append({
                                "type": tag_type,
                                "name": tag_name.lower(),
                                "display_name": self._get_display_name(tag_type, tag_name),
                                "weight": weight_config.get(tag_type, 0.5)
                            })
            # 兼容旧的单个字段格式
            elif isinstance(tags_data, dict):
                for tag_type, tag_name in tags_data.items():
                    if tag_name:
                        name = tag_name.lower() if isinstance(tag_name, str) else ""
                        tags.append({
                            "type": tag_type,
                            "name": name,
                            "display_name": self._get_display_name(tag_type, tag_name),
                            "weight": weight_config.get(tag_type, 0.5)
                        })
            
            # 确保至少有emotion和action两个基础标签
            tag_types = {t["type"] for t in tags}
            if "emotion" not in tag_types:
                tags.insert(0, {
                    "type": "emotion",
                    "name": "neutral",
                    "display_name": "中性",
                    "weight": 1.0
                })
            if "action" not in tag_types:
                # 从原始描述中推断动作类型，而不是默认使用idle
                action_name, action_display = self._infer_action_from_text(original_prompt)
                tags.insert(1, {
                    "type": "action",
                    "name": action_name,
                    "display_name": action_display,
                    "weight": 1.0
                })
            
            # 确保 system 标签存在（根据输入文本判断）
            if "system" not in tag_types:
                if "系统" in original_prompt and "默认" in original_prompt:
                    system_name = "default"
                elif "系统" in original_prompt and ("思考" in original_prompt or "倾听" in original_prompt):
                    system_name = "thinking"
                elif "系统" in original_prompt:
                    system_name = "idle"
                else:
                    system_name = "others"
                tags.append({
                    "type": "system",
                    "name": system_name,
                    "display_name": self._get_display_name("system", system_name),
                    "weight": 1.0
                })
            
            # 生成名称：如果LLM返回的名称是直接copy文本，则重新处理
            suggested_name = data.get("suggested_name", "")
            if self._is_copy_of_prompt(suggested_name, original_prompt):
                suggested_name = self._generate_name_from_tags(tags, original_prompt)
            
            # 处理描述：如果是直接copy，则重新生成
            description = data.get("description", "")
            if self._is_copy_of_prompt(description, original_prompt):
                description = self._generate_description_from_tags(tags, original_prompt)
            
            return VideoAnalysisResult(
                suggested_name=suggested_name or self._extract_name_from_prompt(original_prompt),
                description=description or original_prompt[:50] if original_prompt else "动作描述",
                tags=tags,
                confidence=data.get("confidence", 0.8)
            )
            
        except Exception as e:
            print(f"Parse error: {e}, result: {result_text}")
            # 返回默认结果 - 尝试从文本描述中推断action
            action_name, action_display = self._infer_action_from_text(original_prompt)
            return VideoAnalysisResult(
                suggested_name=self._extract_name_from_prompt(original_prompt),
                description=original_prompt[:50] if original_prompt else "动作描述",
                tags=[
                    {"type": "emotion", "name": "neutral", "display_name": "中性", "weight": 1.0},
                    {"type": "action", "name": action_name, "display_name": action_display, "weight": 1.0}
                ],
                confidence=0.5
            )
    
    def _is_copy_of_prompt(self, text: str, prompt: str) -> bool:
        """检测文本是否直接复制自prompt"""
        if not text or not prompt:
            return False
        
        # 去除空格后比较
        text_clean = text.strip().replace(" ", "").replace("　", "")
        prompt_clean = prompt.strip().replace(" ", "").replace("　", "")
        
        # 如果完全相同或text是prompt的子串（超过50%重合度），认为是copy
        if text_clean == prompt_clean:
            return True
        
        # 检查包含关系
        if prompt_clean in text_clean or text_clean in prompt_clean:
            return True
        
        return False
    
    def _generate_name_from_tags(self, tags: List[Dict[str, Any]], original_prompt: str) -> str:
        """根据标签生成名称"""
        action_tag = next((t for t in tags if t["type"] == "action"), None)
        style_tag = next((t for t in tags if t["type"] == "style"), None)
        emotion_tag = next((t for t in tags if t["type"] == "emotion"), None)
        
        parts = []
        
        # 添加风格前缀
        if style_tag:
            parts.append(style_tag["display_name"])
        
        # 添加动作
        if action_tag:
            action = action_tag["display_name"]
            # 去除重复的动作词汇
            if parts and action in parts[0]:
                parts.append(action.replace(action, ""))
            else:
                parts.append(action)
        
        # 如果有情绪且名称较短，可以加入
        if emotion_tag and len("".join(parts)) < 6:
            parts.append(emotion_tag["display_name"])
        
        result = "".join(parts)
        return result[:10] if result else original_prompt[:10]
    
    def _generate_description_from_tags(self, tags: List[Dict[str, Any]], original_prompt: str) -> str:
        """根据标签生成动作描述"""
        parts = []
        
        for tag in tags:
            tag_type = tag["type"]
            display = tag["display_name"]
            
            if tag_type == "action":
                parts.append(f"进行{display}动作")
            elif tag_type == "emotion":
                parts.append(f"呈现{display}情绪")
            elif tag_type == "style":
                parts.append(f"展现{display}风格")
            elif tag_type == "intensity":
                parts.append(f"强度为{display}")
            elif tag_type == "speed":
                parts.append(f"速度偏{display}")
            elif tag_type == "rhythm":
                parts.append(f"节奏为{display}")
            elif tag_type == "complexity":
                parts.append(f"复杂度为{display}")
        
        if parts:
            desc = "，".join(parts[:3])  # 最多3个维度
            return desc[:50]
        
        return original_prompt[:50] if original_prompt else "动作描述"
    
    def _extract_json(self, text: str) -> str:
        """从文本中提取JSON"""
        # 尝试找 ```json ... ```
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()
        
        # 尝试找 ``` ... ```
        if "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()
        
        # 尝试找 { ... }
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return text[start:end]
        
        return text.strip()
    
    def _infer_action_from_text(self, prompt: str) -> tuple:
        """从文本描述中推断动作类型
        
        Returns:
            tuple: (action_name, action_display_name)
        """
        if not prompt:
            return ("unknown", "未知动作")
        
        prompt_lower = prompt.lower()
        
        # 动作关键词映射表
        action_keywords = {
            "走": ("walk", "行走"),
            "行走": ("walk", "行走"),
            "walk": ("walk", "行走"),
            "跑步": ("run", "跑步"),
            "run": ("run", "跑步"),
            "跳舞": ("dance", "舞蹈"),
            "dance": ("dance", "舞蹈"),
            "跳跃": ("jump", "跳跃"),
            "jump": ("jump", "跳跃"),
            "挥手": ("wave", "挥手"),
            "wave": ("wave", "挥手"),
            "鞠躬": ("bow", "鞠躬"),
            "bow": ("bow", "鞠躬"),
            "坐下": ("sit", "坐下"),
            "sit": ("sit", "坐下"),
            "站立": ("stand", "站立"),
            "stand": ("stand", "站立"),
            "鼓掌": ("clap", "鼓掌"),
            "clap": ("clap", "鼓掌"),
            "转身": ("turn", "转身"),
            "turn": ("turn", "转身"),
            "旋转": ("spin", "旋转"),
            "spin": ("spin", "旋转"),
            "伸展": ("stretch", "伸展"),
            "stretch": ("stretch", "伸展"),
            "摇摆": ("sway", "摇摆"),
            "sway": ("sway", "摇摆"),
            "举手": ("raise_hand", "举手"),
            "raise_hand": ("raise_hand", "举手"),
            "低头": ("lower_head", "低头"),
            "lower_head": ("lower_head", "低头"),
            "踏步": ("step", "踏步"),
            "step": ("step", "踏步"),
            "下蹲": ("squat", "下蹲"),
            "squat": ("squat", "下蹲"),
            "单脚跳": ("hop", "单脚跳"),
            "hop": ("hop", "单脚跳"),
            "滑步": ("slide", "滑步"),
            "slide": ("slide", "滑步"),
            "扭动": ("twist", "扭动"),
            "twist": ("twist", "扭动"),
            "晃动": ("shake", "晃动"),
            "shake": ("shake", "晃动"),
            "指向": ("point", "指向"),
            "point": ("point", "指向"),
            "招手": ("call", "招手"),
            "call": ("call", "招手"),
            "敬礼": ("salute", "敬礼"),
            "salute": ("salute", "敬礼"),
            "飞吻": ("kiss", "飞吻"),
            "kiss": ("kiss", "飞吻"),
            "拥抱": ("hug", "拥抱"),
            "hug": ("hug", "拥抱"),
            "下跪": ("kneel", "下跪"),
            "kneel": ("kneel", "下跪"),
            "眨眼": ("wink", "眨眼"),
            "wink": ("wink", "眨眼"),
            "说话": ("talk", "说话"),
            "talk": ("talk", "说话"),
            "倾斜": ("lean", "倾斜"),
            "lean": ("lean", "倾斜"),
            "说话": ("talk", "说话"),
            "talk": ("talk", "说话"),
            "待机": ("idle", "待机"),
            "idle": ("idle", "待机"),
        }
        
        # 遍历关键词映射表，查找匹配的动作
        for keyword, (action_name, display_name) in action_keywords.items():
            if keyword in prompt_lower:
                return (action_name, display_name)
        
        # 如果没有匹配，返回unknown而不是idle
        return ("unknown", "未知动作")
    
    def _extract_name_from_prompt(self, prompt: str) -> str:
        """从提示中提取名称"""
        if not prompt:
            return "未命名动作"
        
        # 尝试提取动作关键词
        keywords = ["跳舞", "行走", "跑步", "挥手", "鞠躬", "坐下", "站立", 
                    "跳跃", "旋转", "待机", "idle", "dance", "walk", "run"]
        for kw in keywords:
            if kw.lower() in prompt.lower():
                return kw[:10]
        
        # 取前10个字符
        return prompt[:10] if len(prompt) > 10 else prompt
    
    def _get_display_name(self, tag_type: str, name: str) -> str:
        """获取标签的中文显示名"""
        display_names = {
            "emotion": {
                "happy": "开心", "sad": "悲伤", "angry": "生气", 
                "excited": "兴奋", "calm": "平静", "surprised": "惊讶",
                "scared": "害怕", "neutral": "中性", "shy": "害羞",
                "confident": "自信", "dreamy": "梦幻"
            },
            "action": {
                "idle": "待机", "walk": "行走", "run": "跑步", 
                "dance": "舞蹈", "jump": "跳跃", "sit": "坐下", 
                "stand": "站立", "wave": "挥手", "bow": "鞠躬", 
                "clap": "鼓掌", "turn": "转身", "kneel": "下跪",
                "spin": "旋转", "stretch": "伸展", "wink": "眨眼",
                "talk": "说话", "sway": "摇摆", "lean": "倾斜",
                "raise_hand": "举手", "lower_head": "低头",
                "step": "踏步", "squat": "下蹲", "hop": "单脚跳",
                "slide": "滑步", "twist": "扭动", "shake": "晃动",
                "point": "指向", "call": "招手", "salute": "敬礼",
                "kiss": "飞吻", "hug": "拥抱", "unknown": "未知动作"
            },
            "scene": {
                "indoor": "室内", "outdoor": "室外", "stage": "舞台",
                "urban": "城市", "nature": "自然", "fantasy": "幻想",
                "studio": "工作室", "beach": "海滩", "forest": "森林",
                "city_night": "夜景"
            },
            "intensity": {
                "low": "低强度", "medium": "中强度", 
                "high": "高强度", "extreme": "极限"
            },
            "style": {
                "cute": "可爱", "cool": "酷帅", "elegant": "优雅",
                "energetic": "活力", "graceful": "柔美",
                "powerful": "有力", "gentle": "温柔", "playful": "俏皮",
                "sexy": "性感", "mysterious": "神秘", "formal": "正式",
                "casual": "休闲"
            },
            "speed": {
                "slow": "慢速", "normal": "正常", "fast": "快速", "very_fast": "极速"
            },
            "rhythm": {
                "steady": "平稳", "dynamic": "动感", "syncopated": "切分",
                "flowing": "流畅", "sharp": "急促"
            },
            "complexity": {
                "simple": "简单", "moderate": "中等", "complex": "复杂"
            },
            "system": {
                "default": "系统默认", "idle": "系统待机", "thinking": "系统思考", "others": "其他动作"
            }
        }
        
        return display_names.get(tag_type, {}).get(name.lower(), name)
    
    def _convert_to_mp4(self, video_path: str) -> str:
        """
        将视频转换为 mp4 格式（使用 ffmpeg）

        Args:
            video_path: 原视频路径

        Returns:
            转换后的 mp4 文件路径（临时文件）
        """
        import tempfile
        import subprocess
        import os

        # 创建临时文件
        fd, mp4_path = tempfile.mkstemp(suffix='.mp4')
        os.close(fd)

        try:
            # 使用 ffmpeg 转换，参数优化：
            # -c:v libx264: 使用 H.264 编码
            # -preset ultrafast: 最快编码速度
            # -crf 23: 平衡质量和文件大小
            # -vf "scale=iw:-2": 调整宽度为偶数（libx264 要求）
            # -an: 去掉音频（我们只需要提取关键帧）
            # -y: 覆盖输出文件
            result = subprocess.run([
                'ffmpeg',
                '-i', video_path,
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-crf', '23',
                '-vf', 'scale=iw:-2',  # 宽度保持，高度自动调整为偶数
                '-an',
                '-y',
                mp4_path
            ], check=True, capture_output=True, timeout=30)

            return mp4_path

        except subprocess.TimeoutExpired:
            print(f"[_convert_to_mp4] 转换超时")
            if os.path.exists(mp4_path):
                os.remove(mp4_path)
            raise
        except subprocess.CalledProcessError as e:
            print(f"[_convert_to_mp4] ffmpeg 转换失败: {e}")
            if e.stderr:
                print(f"stderr: {e.stderr.decode()}")
            if os.path.exists(mp4_path):
                os.remove(mp4_path)
            raise
        except Exception as e:
            print(f"[_convert_to_mp4] 转换异常: {e}")
            if os.path.exists(mp4_path):
                os.remove(mp4_path)
            raise

    def _extract_keyframes_by_time(self, video_path: str, duration: float) -> List[str]:
        """
        基于时间提取关键帧

        策略：
        1. 保留第一帧（0ms）
        2. 保留最后一帧（duration * 1000 ms）
        3. 中间每 500ms 一帧

        例如：
        - 3秒视频: 0-500-1000-1500-2000-2500-3000ms
        - 2.3秒视频: 0-500-1000-1500-2000-2300ms

        Args:
            video_path: 视频文件路径
            duration: 视频时长（秒）

        Returns:
            base64编码的图片列表
        """
        import os
        import tempfile

        original_path = video_path
        temp_mp4 = None
        converted = False

        try:
            import cv2

            # 检查是否需要转换（非 mp4 格式或帧数异常）
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                print(f"[_extract_keyframes_by_time] 无法打开视频: {video_path}")
                raise ValueError("无法打开视频文件")

            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count_raw = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            cap.release()

            # 如果不是 mp4 或者帧数异常，转换为 mp4
            needs_conversion = (
                not video_path.lower().endswith('.mp4') or
                frame_count_raw <= 0 or
                frame_count_raw > 1e9  # 异常大的值
            )

            if needs_conversion:
                print(f"[_extract_keyframes_by_time] 需要转换: {video_path} (帧数: {frame_count_raw})")
                temp_mp4 = self._convert_to_mp4(video_path)
                video_path = temp_mp4
                converted = True

            # 重新打开转换后的视频（或原视频）
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                print(f"[_extract_keyframes_by_time] 转换后无法打开视频: {video_path}")
                raise ValueError("转换后无法打开视频")

            fps = cap.get(cv2.CAP_PROP_FPS)

            # 计算需要提取的时间点（毫秒）
            # 将时长向下取整到最近的100ms，避免最后一帧无法读取
            duration_ms = (int(duration * 1000) // 100) * 100
            time_points = [0]  # 第一帧

            # 中间每 500ms 一帧
            current_time = 500
            while current_time < duration_ms:
                time_points.append(current_time)
                current_time += 500

            # 最后一帧（如果不在列表中）
            if duration_ms not in time_points:
                time_points.append(duration_ms)

            print(f"[_extract_keyframes_by_time] 提取时间点: {time_points} ms, 共 {len(time_points)} 帧")

            # 提取关键帧
            keyframes = []
            for time_ms in time_points:
                # 将毫秒转换为帧号
                frame_idx = int((time_ms / 1000) * fps)

                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()

                if ret:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
                    _, buffer = cv2.imencode('.jpg', frame_rgb, encode_param)
                    keyframes.append(base64.b64encode(buffer).decode('utf-8'))
                else:
                    print(f"[_extract_keyframes_by_time] 无法读取帧 {time_ms}ms (帧号: {frame_idx})")

            cap.release()
            print(f"[_extract_keyframes_by_time] 成功提取 {len(keyframes)}/{len(time_points)} 帧")

            if not keyframes:
                raise ValueError("未能提取任何关键帧")

            return keyframes

        except ImportError:
            print("OpenCV not available")
            raise ValueError("OpenCV 未安装")
        except Exception as e:
            print(f"[_extract_keyframes_by_time] Error: {e}")
            import traceback
            traceback.print_exc()
            raise
        finally:
            # 清理临时文件
            if converted and temp_mp4 and os.path.exists(temp_mp4):
                try:
                    os.remove(temp_mp4)
                    print(f"[_extract_keyframes_by_time] 已清理临时文件: {temp_mp4}")
                except Exception as e:
                    print(f"[_extract_keyframes_by_time] 清理临时文件失败: {e}")

    def _extract_keyframes(self, video_path: str, count: int = 3) -> List[str]:
        """
        提取视频关键帧（使用OpenCV）

        先将视频转换为 mp4 格式以获得更可靠的帧数信息

        Args:
            video_path: 视频文件路径
            count: 提取帧数

        Returns:
            base64编码的图片列表
        """
        import os
        import tempfile

        original_path = video_path
        temp_mp4 = None
        converted = False

        try:
            import cv2

            # 检查是否需要转换（非 mp4 格式或帧数异常）
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                print(f"[_extract_keyframes] 无法打开视频: {video_path}")
                return []

            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count_raw = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            cap.release()

            # 如果不是 mp4 或者帧数异常，转换为 mp4
            needs_conversion = (
                not video_path.lower().endswith('.mp4') or
                frame_count_raw <= 0 or
                frame_count_raw > 1e9  # 异常大的值
            )

            if needs_conversion:
                print(f"[_extract_keyframes] 需要转换: {video_path} (帧数: {frame_count_raw})")
                temp_mp4 = self._convert_to_mp4(video_path)
                video_path = temp_mp4
                converted = True

            # 重新打开转换后的视频（或原视频）
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                print(f"[_extract_keyframes] 转换后无法打开视频: {video_path}")
                return []

            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)

            print(f"[_extract_keyframes] 提取关键帧: {total_frames} 帧, FPS: {fps}")

            if total_frames <= 0:
                cap.release()
                print(f"[_extract_keyframes] 帧数异常，使用备用方案")
                # 降级到逐帧读取
                return self._extract_keyframes_fallback(original_path, count)

            # 正常提取关键帧
            keyframes = []
            interval = max(1, total_frames // (count + 1))

            for i in range(1, count + 1):
                frame_idx = i * interval
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()

                if ret:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
                    _, buffer = cv2.imencode('.jpg', frame_rgb, encode_param)
                    keyframes.append(base64.b64encode(buffer).decode('utf-8'))

            cap.release()
            print(f"[_extract_keyframes] 成功提取 {len(keyframes)}/{count} 帧")
            return keyframes

        except ImportError:
            print("OpenCV not available, skipping keyframe extraction")
            return []
        except Exception as e:
            print(f"Keyframe extraction error: {e}")
            import traceback
            traceback.print_exc()
            # 降级到备用方案
            return self._extract_keyframes_fallback(original_path, count)
        finally:
            # 清理临时文件
            if converted and temp_mp4 and os.path.exists(temp_mp4):
                try:
                    os.remove(temp_mp4)
                    print(f"[_extract_keyframes] 已清理临时文件: {temp_mp4}")
                except Exception as e:
                    print(f"[_extract_keyframes] 清理临时文件失败: {e}")

    def _extract_keyframes_fallback(self, video_path: str, count: int = 3) -> List[str]:
        """
        备用方案：逐帧读取提取关键帧（不依赖总帧数）

        Args:
            video_path: 视频文件路径
            count: 提取帧数

        Returns:
            base64编码的图片列表
        """
        try:
            import cv2

            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                print(f"[_extract_keyframes_fallback] 无法打开视频: {video_path}")
                return []

            # 估算间隔
            estimated_frames = 100
            interval = max(10, estimated_frames // (count + 1))

            target_indices = set(interval * i for i in range(1, count + 1))
            current_idx = 0
            keyframes = []
            extracted = 0

            while extracted < count and current_idx < 10000:
                ret, frame = cap.read()
                if not ret:
                    break

                if current_idx in target_indices:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
                    _, buffer = cv2.imencode('.jpg', frame_rgb, encode_param)
                    keyframes.append(base64.b64encode(buffer).decode('utf-8'))
                    extracted += 1
                    target_indices.discard(current_idx)

                current_idx += 1

            cap.release()
            print(f"[_extract_keyframes_fallback] 提取 {len(keyframes)}/{count} 帧")
            return keyframes

        except Exception as e:
            print(f"[_extract_keyframes_fallback] Error: {e}")
            return []


# 全局单例
video_analysis_service = VideoAnalysisService()