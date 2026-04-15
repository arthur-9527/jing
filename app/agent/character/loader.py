"""角色配置加载（简化版）

支持两种加载方式：
1. 单文件模式（向后兼容）：直接加载 JSON 文件
2. 目录模式：从目录中加载多个配置文件
   - character.json: 结构化数据
   - personality.md: 叙述性内容

简化版本：
- 删除 traits 字段（冗余，已在 personality.md 中描述）
- 删除 motion_preferences 字段（数据库已有 action 标签约束）
- 支持新的章节名称映射
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field


class SpeakingStyle(BaseModel):
    """说话风格"""
    tone: str = ""
    口头禅: list[str] = Field(default_factory=list, alias="口头禅")
    sentence_patterns: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)


class CharacterConfig(BaseModel):
    """角色完整配置（简化版）"""
    character_id: str
    name: str
    speaking_style: SpeakingStyle | None = None
    emotion_baseline: dict[str, float] = Field(default_factory=lambda: {"P": 0.3, "A": 0.5, "D": 0.6})
    core_rules: list[str] = Field(default_factory=list)
    
    # 从 personality.md 加载的叙述性内容
    personality_text: str = ""  # 角色介绍/基本性格描述
    emotion_traits_text: str = ""  # 情绪特点 + 动作习惯
    emotion_triggers_text: str = ""  # 敏感词汇

    model_config = {"populate_by_name": True}


def _parse_markdown_sections(content: str) -> dict[str, str]:
    """解析 Markdown 文件，按章节提取内容
    
    返回字典：key 是章节标题（去掉 ##），value 是章节内容
    """
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_content: list[str] = []
    
    for line in content.split("\n"):
        # 检测二级标题
        if line.startswith("## "):
            # 保存上一个章节
            if current_section:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = line[3:].strip()
            current_content = []
        elif current_section:
            current_content.append(line)
    
    # 保存最后一个章节
    if current_section:
        sections[current_section] = "\n".join(current_content).strip()
    
    return sections


def _load_from_directory(dir_path: str) -> CharacterConfig:
    """从目录加载角色配置
    
    目录结构：
    - character.json: 必须存在，包含结构化数据
    - personality.md: 可选，包含叙述性内容
    
    章节映射（支持新旧两种格式）：
    - "角色介绍"(新) 或 "基本性格"(旧) -> personality_text
    - "情绪特点" + "动作习惯"(新) 或 "情绪反应习惯"(旧) -> emotion_traits_text
    - "敏感词汇"(新) 或 "情绪触发词敏感度"(旧) -> emotion_triggers_text
    """
    # 加载 character.json（必须存在）
    json_path = os.path.join(dir_path, "character.json")
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"角色目录中缺少 character.json: {dir_path}")
    
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 加载 personality.md（可选）
    md_path = os.path.join(dir_path, "personality.md")
    if os.path.exists(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        
        sections = _parse_markdown_sections(md_content)
        
        # 提取各章节内容（支持新旧两种章节名称）
        # personality_text: 角色介绍(新) 或 基本性格(旧)
        data["personality_text"] = sections.get("角色介绍", "") or sections.get("基本性格", "")
        
        # emotion_traits_text: 情绪特点 + 动作习惯(新) 或 情绪反应习惯(旧)
        emotion_traits = sections.get("情绪特点", "")
        action_habits = sections.get("动作习惯", "") or sections.get("情绪反应习惯", "")
        if emotion_traits and action_habits:
            data["emotion_traits_text"] = f"{emotion_traits}\n\n{action_habits}"
        else:
            data["emotion_traits_text"] = emotion_traits + action_habits
        
        # emotion_triggers_text: 敏感词汇(新) 或 情绪触发词敏感度(旧)
        data["emotion_triggers_text"] = sections.get("敏感词汇", "") or sections.get("情绪触发词敏感度", "")
        
        # 从核心规则章节提取规则列表
        core_rules_text = sections.get("核心规则", "")
        if core_rules_text and "core_rules" not in data:
            # 解析规则列表（每行以 - 开头）
            rules = []
            for line in core_rules_text.split("\n"):
                line = line.strip()
                if line.startswith("- "):
                    rules.append(line[2:])
            data["core_rules"] = rules
    
    return CharacterConfig(**data)


def load_character(config_path: str | None = None) -> CharacterConfig:
    """加载角色配置
    
    支持两种模式：
    1. 目录模式：config_path 是目录路径，从中加载 character.json + personality.md
    2. 文件模式：config_path 是 JSON 文件路径（向后兼容）
    
    Args:
        config_path: 配置路径（目录或文件）
        
    Returns:
        CharacterConfig 实例
    """
    if config_path is None:
        config_path = os.getenv("CHARACTER_CONFIG_PATH", "config/characters/daji")
    
    # 支持相对路径（相对于项目根目录）
    if not os.path.isabs(config_path):
        # app/agent/character/loader.py -> 往上 4 级到项目根目录
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        config_path = os.path.join(project_root, config_path)
    
    # 判断是目录还是文件
    if os.path.isdir(config_path):
        return _load_from_directory(config_path)
    elif os.path.isfile(config_path):
        # 单文件模式（向后兼容旧格式）
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return CharacterConfig(**data)
    else:
        raise FileNotFoundError(f"角色配置路径不存在: {config_path}")