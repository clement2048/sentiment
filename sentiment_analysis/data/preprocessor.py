"""文本预处理 — jieba 分词 + 停用词过滤。"""

import re
import sys
from pathlib import Path

# 确保能找到项目根目录下的 utils
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from utils.crawler_util import clean_text


# ── 中文停用词表（哈工大停用词表精简 + 加密领域补充） ──
_STOP_WORDS: set[str] = set()

# 基础中文停用词
_BASE_STOP_WORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "什么", "怎么", "如何", "为什么", "因为", "所以", "但是", "而且", "虽然",
    "如果", "可以", "还是", "只是", "这个", "那个", "哪个", "这样", "那样",
    "还", "被", "把", "从", "让", "对", "与", "之", "而", "以", "及",
    "并", "或", "但", "却", "且", "为", "其", "啊", "吧", "呢", "哦",
    "嗯", "哈", "呀", "嘛", "咯", "啦", "哦", "噢", "哎", "嗯嗯", "哈哈",
    "的", "地", "得", "着", "了", "过",
    "太", "更", "最", "很", "极", "挺", "真", "非常",
    "已经", "正在", "将要", "能", "能够", "可能", "会", "可以",
    # 加密货币特定无意义词
    "bnb", "btc", "eth", "usdt", "sol", "币", "涨", "跌",
    "一个", "一些", "一下", "一点", "很多", "很少",
    "今天", "明天", "昨天", "现在", "以前", "以后", "时候",
}

# 英文常见停用词
_EN_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "it", "its", "they", "them", "their", "we", "us", "our", "you", "your",
    "he", "she", "his", "her", "and", "but", "or", "not", "no", "so",
    "if", "then", "than", "that", "this", "these", "those", "just", "about",
}

_STOP_WORDS = _BASE_STOP_WORDS | _EN_STOP_WORDS


def _ensure_jieba() -> None:
    """确保 jieba 已导入并加载默认词典。"""
    try:
        import jieba
    except ImportError:
        raise ImportError("请先安装 jieba: pip install jieba")
    return jieba


def preprocess(text: str, remove_stopwords: bool = True) -> str:
    """清洗 → jieba 分词 → 去停用词 → 空格连接。

    参数:
        text: 原始中文文本
        remove_stopwords: 是否去除停用词

    返回:
        空格分隔的 token 字符串，用于 TF-IDF 向量化
    """
    jieba = _ensure_jieba()

    # 步骤1：基础清洗
    text = clean_text(text)
    if not text:
        return ""

    # 步骤2：移除 URL
    text = re.sub(r"https?://\S+", "", text)

    # 步骤3：jieba 精确模式分词
    tokens = jieba.lcut(text)

    # 步骤4：过滤
    result: list[str] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        # 跳过纯数字/标点
        _punct_chars = set("0123456789.,+-%!?;:()（）[]【】\"'""''，。！？、；：…— \t")
        if all(c in _punct_chars for c in token):
            continue
        # 去停用词
        if remove_stopwords and token.lower() in _STOP_WORDS:
            continue
        result.append(token)

    return " ".join(result)


def preprocess_batch(texts: list[str], remove_stopwords: bool = True) -> list[str]:
    """批量预处理文本列表。"""
    return [preprocess(t, remove_stopwords=remove_stopwords) for t in texts]


def get_stop_words() -> set[str]:
    """返回当前停用词表（只读）。"""
    return _STOP_WORDS.copy()
