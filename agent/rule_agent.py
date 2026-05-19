"""规则 Agent — 基于关键词词典 + 上下文分析用户立场。

每个参与对话的用户分配一个 Agent。
Agent 输入: 评论 + 对话上下文 + 新闻内容 + 用户画像
Agent 输出: 情绪向量 [bullish, bearish, neutral, confidence]
"""

from dataclasses import dataclass

from agent.user_profile import UserProfile
from data_loader.loader import CommentNode
from features.keyword_sentiment import (
    compute_sentiment,
    sentiment_to_vector,
)
from logger import logger


@dataclass
class AgentResult:
    """单个 Agent 的分析结果。"""
    author: str
    comment_text: str
    sentiment_label: str        # "bullish" | "bearish" | "neutral"
    confidence: float           # 0.0 ~ 1.0
    sentiment_vector: list[float]  # [bullish, bearish, neutral, confidence]
    reason: str                 # 推理简述
    matched_keywords: list[str]


def analyze_comment(
    node: CommentNode,
    post_content: str,
    parent_text: str | None,     # 被回复的评论（如果有）
    user_profile: UserProfile | None = None,
) -> AgentResult:
    """分析单条评论的用户情绪。

    策略:
    1. 关键词词典匹配 → 基础情感
    2. 否定词翻转调整
    3. 结合用户画像偏差微调置信度
    4. 结合新闻内容/父评论上下文

    参数:
        node: 评论节点
        post_content: 新闻正文
        parent_text: 父评论文本（如果是回复）
        user_profile: 用户画像（可选）

    返回:
        AgentResult
    """
    text = node.text

    # 步骤1: 词典匹配
    sentiment_label, confidence, keywords = compute_sentiment(text)

    # 步骤2: 上下文调整
    # 如果评论是问题/反问，降低置信度
    question_words = ["吗", "呢", "？", "?", "什么", "怎么", "如何", "为啥", "究竟", "难道"]
    if any(w in text for w in question_words) and confidence < 0.5:
        confidence *= 0.5

    # 步骤3: 结合用户画像
    profile_adjustment = ""
    if user_profile and user_profile.total_comments > 0:
        bias = user_profile.stance_bias
        if abs(bias) > 0.3:
            # 用户有明确立场倾向
            if (bias > 0 and sentiment_label == "bearish") or \
               (bias < 0 and sentiment_label == "bullish"):
                # 当前判断与历史立场相反，降低置信度
                confidence *= 0.8
                profile_adjustment = f"（与历史立场bias={bias:.2f}相反，降低置信度）"
            else:
                profile_adjustment = f"（与历史立场bias={bias:.2f}一致）"

    # 步骤4: 生成情感向量
    vec = sentiment_to_vector(sentiment_label, confidence)

    # 推理原因
    if keywords:
        reason = f"匹配关键词: {', '.join(keywords[:3])}"
    else:
        reason = "无明确情感词，判断为中性"
    if profile_adjustment:
        reason += profile_adjustment

    return AgentResult(
        author=node.author,
        comment_text=text,
        sentiment_label=sentiment_label,
        confidence=confidence,
        sentiment_vector=vec,
        reason=reason,
        matched_keywords=keywords,
    )
