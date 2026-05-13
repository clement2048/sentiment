"""加密领域情感关键词词典 + 否定词翻转。

匹配策略:
1. 先扫描否定词（如 "不"、"没有"、"不是"）
2. 在否定词窗口内匹配情感词 → 翻转极性
3. 无否定词时直接匹配情感词
"""

import re
from typing import Optional

# ── 否定词列表 ──────────────────────────────────────────
NEGATION_WORDS = {
    "不", "没", "没有", "不是", "并非", "无", "别", "未",
    "不会", "不可能", "不一定", "不要", "不用", "未必",
    "并非", "绝不", "毫不", "毫不", "不能", "不行",
}

# 否定词窗口大小（字符数）
NEGATION_WINDOW = 6

# ── 看涨关键词（权重越高越确定） ─────────────────────────
BULLISH_KEYWORDS: dict[str, float] = {
    # 明确看涨信号
    "利好": 1.0,
    "利多": 1.0,
    "看涨": 1.0,
    "做多": 0.9,
    "多单": 0.9,
    "抄底": 0.8,
    "起飞": 0.9,
    "暴涨": 0.9,
    "大涨": 0.8,
    "拉升": 0.8,
    "牛市": 0.9,
    "牛回": 0.9,
    "突破": 0.7,
    "冲高": 0.7,
    "上涨": 0.6,
    "涨了": 0.6,
    "要涨": 0.7,
    "会涨": 0.7,
    "反弹": 0.6,
    "回暖": 0.6,
    "企稳": 0.5,
    "稳了": 0.6,
    "好起来了": 0.7,
    "买买买": 0.9,
    "梭哈": 0.8,
    "上车": 0.6,
    "满仓": 0.7,
    "加仓": 0.6,
    "建仓": 0.5,
    "持有": 0.4,
    "囤币": 0.6,
    "定投": 0.5,
    # 正面评价
    "好事情": 0.7,
    "好消息": 0.7,
    "乐观": 0.6,
    "看好": 1.0,
    "很不错": 0.6,
    "厉害了": 0.6,
    "值得": 0.5,
    "机会": 0.4,
    "稳了": 0.6,
    "强": 0.5,
    "强势": 0.6,
    "好": 0.3,  # 弱信号，需要更多证据
}

# ── 看跌关键词 ──────────────────────────────────────────
BEARISH_KEYWORDS: dict[str, float] = {
    # 明确看跌信号
    "利空": 1.0,
    "看跌": 1.0,
    "做空": 0.9,
    "空单": 0.9,
    "空头": 0.8,
    "砸盘": 0.9,
    "瀑布": 0.9,
    "暴跌": 0.9,
    "大跌": 0.8,
    "崩盘": 1.0,
    "归零": 1.0,
    "腰斩": 0.9,
    "熊市": 0.9,
    "下跌": 0.6,
    "跌了": 0.6,
    "要跌": 0.7,
    "会跌": 0.7,
    "回调": 0.5,
    "阴跌": 0.7,
    "破位": 0.7,
    "亏损": 0.7,
    "被套": 0.8,
    "套牢": 0.8,
    "割肉": 0.8,
    "止损": 0.6,
    "亏了": 0.7,
    "爆仓": 0.9,
    "清仓": 0.5,
    "跑路": 0.8,
    "骗局": 0.8,
    "镰刀": 0.7,
    "收割": 0.7,
    "割韭菜": 0.8,
    "泡沫": 0.6,
    # 负面评价
    "坏消息": 0.7,
    "不妙": 0.7,
    "悲观": 0.7,
    "不看好": 1.0,
    "危险": 0.7,
    "风险": 0.5,
    "凉了": 0.8,
    "完蛋": 0.9,
    "垃圾": 0.6,
    "没戏": 0.7,
    "不行": 0.5,
    "难": 0.3,
    "难说": 0.2,
    "不确定": 0.2,
}


def _has_negation(text: str, keyword_pos: int) -> bool:
    """检查关键词前 N 个字符内是否有否定词。"""
    start = max(0, keyword_pos - NEGATION_WINDOW)
    window = text[start:keyword_pos]
    return any(neg in window for neg in NEGATION_WORDS)


def compute_sentiment(text: str) -> tuple[str, float, list[str]]:
    """基于词典计算单条文本的情感分数。

    参数:
        text: 原始评论文本

    返回:
        (sentiment_label, confidence, matched_keywords)
        sentiment_label: "bullish" | "bearish" | "neutral"
        confidence: 0.0 ~ 1.0
        matched_keywords: 匹配到的关键词列表
    """
    bullish_score = 0.0
    bearish_score = 0.0
    bull_matches: list[str] = []
    bear_matches: list[str] = []

    # 先检查"不看好"这类含否定词的看跌短语
    # "不看好" → 看跌，"不是利空" → 可能是看涨
    for phrase, weight in BEARISH_KEYWORDS.items():
        for m in re.finditer(re.escape(phrase), text):
            pos = m.start()
            if _has_negation(text, pos):
                # 否定 + 利空 → 看涨
                bullish_score += weight
                bull_matches.append(f"不{phrase}")
            else:
                bearish_score += weight
                bear_matches.append(phrase)

    for phrase, weight in BULLISH_KEYWORDS.items():
        for m in re.finditer(re.escape(phrase), text):
            pos = m.start()
            if _has_negation(text, pos):
                # 否定 + 利空关键词（已在上面处理）不重复
                # 否定 + 利好 → 看跌
                bearish_score += weight
                bear_matches.append(f"不{phrase}")
            else:
                bullish_score += weight
                bull_matches.append(phrase)

    # 判断最终情感
    if bullish_score > bearish_score and bullish_score > 0:
        # 归一化置信度
        confidence = min(bullish_score / 3.0, 1.0)
        return ("bullish", confidence, bull_matches[:5])
    elif bearish_score > bullish_score and bearish_score > 0:
        confidence = min(bearish_score / 3.0, 1.0)
        return ("bearish", confidence, bear_matches[:5])
    else:
        return ("neutral", 0.0, [])


def sentiment_to_vector(sentiment_label: str, confidence: float) -> list[float]:
    """将情感标签转为 4 维向量 [bullish, bearish, neutral, confidence]。"""
    if sentiment_label == "bullish":
        return [confidence, 0.0, 0.0, confidence]
    elif sentiment_label == "bearish":
        return [0.0, confidence, 0.0, confidence]
    else:
        return [0.0, 0.0, 1.0, 0.0]
