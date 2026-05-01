#!/usr/bin/env python3
"""PAD 情绪模型到 CosyVoice instruct_text 映射

基于 PAD 三维情绪模型映射到中文情绪指令文本：
- P (Pleasure): 愉悦度，正=愉悦/开心，负=不悦/悲伤/生气
- A (Arousal): 激活度，正=兴奋/激动，负=平静/放松
- D (Dominance): 支配度，正=自信/坚定，负=顺从/温和

参考研究：Russell's Circumplex Model of Affect
"""


def pad_to_emotion_instruction(pad: dict) -> str | None:
    """将 PAD 值映射到 CosyVoice instruct_text 情绪指令
    
    Args:
        pad: {"P": float, "A": float, "D": float} 范围 -1.0 到 1.0
        
    Returns:
        情绪指令文本（如 "开心的语气"、"平静自然的语气"）或 None（默认）
    """
    p = pad.get("P", 0.0)
    a = pad.get("A", 0.0)
    d = pad.get("D", 0.0)
    
    # 基于 PA 二维情绪模型 + D 维度微调
    
    # 高愉悦 + 高激活 = 开心/兴奋
    if p > 0.4 and a > 0.3:
        base = "开心兴奋的语气"
    # 高愉悦 + 中等激活 = 愉悦/满足
    elif p > 0.4 and a > 0:
        base = "愉悦满足的语气"
    # 高愉悦 + 低激活 = 平静满足/放松
    elif p > 0.4 and a <= 0:
        base = "平静放松的语气"
    # 中等愉悦 + 高激活 = 激动/热情
    elif p > 0 and a > 0.4:
        base = "激动热情的语气"
    # 中等愉悦 + 中等激活 = 自然/友好
    elif abs(p) <= 0.3 and abs(a) <= 0.3:
        base = "自然友好的语气"
    # 低愉悦 + 高激活 = 生气/愤怒
    elif p < -0.3 and a > 0.3:
        base = "生气愤怒的语气"
    # 低愉悦 + 中等激活 = 不满/抱怨
    elif p < -0.3 and a > 0:
        base = "不满抱怨的语气"
    # 低愉悦 + 低激活 = 悲伤/失落
    elif p < -0.3 and a <= 0:
        base = "悲伤失落的语气"
    # 中等愉悦 + 低激活 = 冷淡/疲惫
    elif p > -0.3 and a < -0.3:
        base = "冷淡疲惫的语气"
    else:
        base = None
    
    # D 维度微调（支配度）
    if base:
        if d > 0.4:
            # 高支配：添加自信/坚定
            return f"自信坚定的{base.replace('语气', '')}语气"
        elif d < -0.4:
            # 低支配：添加温和/谦逊
            return f"温和谦逊的{base.replace('语气', '')}语气"
        else:
            return base
    
    # 默认无情绪指令
    return None