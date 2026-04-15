#!/usr/bin/env python3
"""
公共文本处理工具 - ASR过滤与投机匹配共用

统一语气词和标点处理逻辑，用于：
1. ASR输入有效性检测（过滤误触发）
2. 投机匹配分数计算（标准化文本）
"""

# 中文语气词集合
MODAL_PARTICLES = frozenset({
    # 常见单字语气词
    '嗯', '哼', '啊', '哦', '呃', '唔', '咦', '唉', '嗨', '喂',
    '哈', '嘿', '噢', '哎', '啵', '啾',
    # 句尾语气词
    '呀', '吧', '呢', '嘛', '啦', '嘞', '喽', '咯',
    # 其他语气词
    '哉', '焉', '呐', '嚯', '哟', '呦',
})

# 标点符号集合
PUNCTUATION = frozenset('。！？…!?.，,、；;：:\"\'\"\'（）()【】[]{}~')


def normalize_text(text: str) -> str:
    """标准化文本：去除句尾的单个语气词和所有标点
    
    ⭐ 只处理句尾的语气词（不去除句首、句中的语气词）
    ⭐ 只去除单个语气词（不去除连续语气词，如"哈哈哈哈"）
    
    Args:
        text: 原始文本
        
    Returns:
        标准化后的文本（可能为空字符串）
    
    Examples:
        >>> normalize_text("嗯")
        ""
        >>> normalize_text("嗯嗯")
        "嗯嗯"  # 连续语气词不处理
        >>> normalize_text("哈哈哈哈")
        "哈哈哈哈"  # 连续语气词保留
        >>> normalize_text("今天天气真好啊！")
        "今天天气真好"  # 去除句尾标点和单个语气词
        >>> normalize_text("娃哈哈")
        "娃哈哈"  # 句中语气词不处理
        >>> normalize_text("啊好的呀")
        "啊好的"  # 只去除句尾的"呀"
        >>> normalize_text("好的呢！")
        "好的"  # 去除句尾标点和语气词
    """
    if not text:
        return ""
    
    # 先去除所有标点
    result = ''.join(char for char in text if char not in PUNCTUATION)
    
    if not result:
        return ""
    
    # 只去除句尾的单个语气词（不去除连续语气词）
    while result and result[-1] in MODAL_PARTICLES:
        # 检查句尾是否有连续相同语气词
        last_char = result[-1]
        count = 0
        for char in reversed(result):
            if char == last_char:
                count += 1
            else:
                break
        
        # 只有单个语气词才去除（count == 1）
        if count == 1:
            result = result[:-1]
        else:
            # 连续语气词（≥2个）保留，如"哈哈哈哈"、"嗯嗯"
            break
    
    return result.strip()


def is_valid_asr_input(text: str) -> bool:
    """检查 ASR 文本是否是有效的用户输入
    
    用于过滤 ASR 误触发（如单字语气词"嗯"、"啊"等），
    防止无效输入进入 Redis 和触发 LLM 请求。
    
    ⭐ 特殊处理：
    - 标准化后为空 → 无效（全是语气词+标点）
    - 标准化后全是语气词 → 无效（如"嗯嗯"、"哈哈哈哈"）
    - 标准化后包含非语气词 → 有效
    
    Args:
        text: ASR 识别的文本
        
    Returns:
        True: 有效输入，应触发正常流程（入 Redis、投机采样等）
        False: 无效输入（纯语气词+标点），应跳过后续流程
    
    Examples:
        >>> is_valid_asr_input("嗯")
        False
        >>> is_valid_asr_input("嗯！")
        False
        >>> is_valid_asr_input("嗯嗯")
        False
        >>> is_valid_asr_input("哈哈哈哈")
        True  # ≥3个相同语气词，视为有效（大笑）
        >>> is_valid_asr_input("娃哈哈")
        True  # 包含非语气词"娃"
        >>> is_valid_asr_input("今天天气真好啊")
        True
        >>> is_valid_asr_input("")
        False
    """
    normalized = normalize_text(text)
    
    # 标准化后为空，说明全是语气词和标点
    if not normalized:
        return False
    
    # 检查是否全是语气词（可能有连续语气词保留）
    # 但≥3个相同语气词视为有效（如"哈哈哈哈"表示大笑）
    all_chars = list(normalized)
    unique_chars = set(all_chars)
    
    # 如果所有字符都是语气词
    if unique_chars.issubset(MODAL_PARTICLES):
        # 检查是否有≥3个相同语气词
        for char in unique_chars:
            count = all_chars.count(char)
            if count >= 3:
                return True  # 视为有效表达（如大笑）
        # 否则全是语气词但不足3个，无效
        return False
    
    # 包含非语气词字符，有效
    return True


def match_score(final_text: str, cached_text: str) -> float:
    """计算投机匹配分数
    
    基于**标准化文本**计算公共前缀长度比例：
    score = 公共前缀长度 / 最终文本标准化长度
    
    Args:
        final_text: ASR 最终结果文本
        cached_text: 投机请求缓存的文本
        
    Returns:
        匹配分数 (0.0 - 1.0)
    
    Examples:
        >>> match_score("今天天气真好啊", "今天天气真好吧")
        1.0
        >>> match_score("好的呢！", "好的哦")
        1.0
        >>> match_score("啊好的呀", "好的")
        1.0
        >>> match_score("你好", "你好世界")
        0.5
    """
    core_final = normalize_text(final_text)
    core_cached = normalize_text(cached_text)
    
    if not core_final:
        return 0.0
    
    # 计算公共前缀长度
    common_len = 0
    for i in range(min(len(core_final), len(core_cached))):
        if core_final[i] == core_cached[i]:
            common_len += 1
        else:
            break
    
    return common_len / len(core_final)


def is_similar(text1: str, text2: str, threshold: float = 0.8) -> bool:
    """相似度检查（用于投机请求去重）
    
    Args:
        text1: 第一个文本
        text2: 第二个文本
        threshold: 匹配阈值，默认 0.8
        
    Returns:
        True: 相似度达到阈值
        False: 相似度未达到阈值
    """
    return match_score(text1, text2) >= threshold