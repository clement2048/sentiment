"""LLM Agent — 调用 DeepSeek Anthropic 兼容 API 分析用户情绪。

失败时抛出异常，由 agent_factory 层 catch 后 fallback 到规则 Agent。
"""

import json
import os

from anthropic import Anthropic

from dataclasses import dataclass

from agent.user_profile import UserProfile


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
from config import (
    LLM_MODEL,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_API_KEY_ENV,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT,
)
from data_loader.loader import CommentNode
from logger import logger

# ── System Prompt ──────────────────────────────────────
SYSTEM_PROMPT = """你是一个加密货币市场情绪分析专家。你需要分析用户在币安新闻下的评论，判断该用户对相关币种是看涨(bullish)还是看跌(bearish)。

分析规则：
1. 结合新闻内容和评论上下文判断用户立场
2. 注意反讽、调侃、疑问等语气（如"这还能涨？"可能是质疑看跌）
3. 如果评论是在提问而非表态（如"利多还是利空"），标记为 neutral，confidence 低于 0.5
4. 如果用户明确表达了对币价方向的预期，标记为 bullish 或 bearish
5. confidence 表示你对判断的把握程度：0.8+ = 非常确定，0.5-0.8 = 有一定把握，<0.5 = 不确定

你必须只输出一行 JSON，不要输出其他内容：
{"sentiment": "bullish"|"bearish", "confidence": 0.0-1.0, "reason": "简短推理"}"""


def _build_client() -> Anthropic:
    """创建 Anthropic 客户端，指向 DeepSeek 兼容接口。"""
    api_key = os.environ.get(LLM_API_KEY_ENV, LLM_API_KEY_ENV)
    if not api_key:
        raise ValueError(f"环境变量 {LLM_API_KEY_ENV} 未设置")

    return Anthropic(
        api_key=api_key,
        base_url=LLM_BASE_URL,
        timeout=LLM_TIMEOUT,
        max_retries=LLM_MAX_RETRIES,
    )


def analyze_comment(
    node: CommentNode,
    post_content: str,
    parent_text: str | None = None,
    user_profile: UserProfile | None = None,
) -> AgentResult:
    """调用 LLM 分析单条评论的用户情绪。

    参数同 rule_agent.analyze_comment()。

    异常:
        ValueError: API key 未设置
        Exception: API 调用失败 / JSON 解析失败
    """
    text = node.text

    # 构建用户消息
    user_parts = [f"新闻内容：{post_content}"]

    if parent_text:
        user_parts.append(f"被回复的评论：{parent_text}")
    user_parts.append(f"用户 {node.author} 的评论：{text}")

    if user_profile and user_profile.total_comments > 0:
        user_parts.append(
            f"用户画像：历史{user_profile.total_comments}条评论，"
            f"立场偏差={user_profile.stance_bias:.2f}"
            f"（-1=极度看跌 +1=极度看涨），"
            f"一致性={user_profile.consistency:.2f}"
        )

    user_message = "\n".join(user_parts)

    # 调用 API
    client = _build_client()
    logger.debug("LLM 请求: model=%s, user=%s, text=%s...", LLM_MODEL, node.author, text[:40])

    response = client.messages.create(
        model=LLM_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        temperature=LLM_TEMPERATURE,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    # 解析响应：thinking 模型会返回多个 content block，取 TextBlock 的 text
    response_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            response_text = block.text.strip()
            break

    if not response_text:
        raise ValueError("LLM 返回空响应")

    # 去掉 markdown 代码块包裹
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    # 尝试直接解析 JSON，失败则用正则提取
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{[^{}]*"sentiment"[^{}]*\}', response_text)
        if not match:
            raise
        result = json.loads(match.group())
    sentiment_label = result.get("sentiment", "neutral")
    confidence = float(result.get("confidence", 0.0))
    reason = result.get("reason", "")

    # 验证 label 合法
    if sentiment_label not in ("bullish", "bearish", "neutral"):
        sentiment_label = "neutral"
        confidence = 0.0

    # 限制 confidence 范围
    confidence = max(0.0, min(1.0, confidence))

    # 转为向量
    if sentiment_label == "bullish":
        sentiment_vector = [confidence, 0.0, 0.0, confidence]
    elif sentiment_label == "bearish":
        sentiment_vector = [0.0, confidence, 0.0, confidence]
    else:
        sentiment_vector = [0.0, 0.0, 1.0, confidence]

    return AgentResult(
        author=node.author,
        comment_text=text,
        sentiment_label=sentiment_label,
        confidence=confidence,
        sentiment_vector=sentiment_vector,
        reason=f"[LLM] {reason}",
        matched_keywords=[],  # LLM 无关键词匹配
    )
