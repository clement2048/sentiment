"""Agent 工厂 — 根据配置选择 LLM Agent 或规则 Agent，LLM 失败时自动 fallback。"""

import os

from sentiment_analysis.agent.rule_agent import AgentResult
from sentiment_analysis.data.loader import CommentNode
from sentiment_analysis.agent.user_profile import UserProfile
from sentiment_analysis.config import LLM_API_KEY_ENV
from sentiment_analysis.utils.logger import logger


def create_analyze_func(use_llm: bool = False):
    """返回 analyze_comment 函数。

    参数:
        use_llm: True=LLM Agent 优先（失败 fallback），False=纯规则 Agent

    返回:
        analyze_comment(node, post_content, parent_text, user_profile) -> AgentResult
    """
    if not use_llm:
        from sentiment_analysis.agent.rule_agent import analyze_comment as rule_analyze
        logger.info("Agent 模式: 规则")
        return rule_analyze

    # LLM 模式：前置检查 API key
    api_key = os.environ.get(LLM_API_KEY_ENV, "")
    if not api_key:
        logger.warning("LLM Agent: %s 未设置，直接使用规则 Agent", LLM_API_KEY_ENV)
        from sentiment_analysis.agent.rule_agent import analyze_comment as rule_analyze
        return rule_analyze

    # 尝试导入 LLM agent
    try:
        from sentiment_analysis.agent.llm_agent import analyze_comment as llm_analyze
    except ImportError as e:
        logger.warning("LLM Agent 导入失败 (%s)，回退到规则 Agent", e)
        from sentiment_analysis.agent.rule_agent import analyze_comment as rule_analyze
        return rule_analyze

    from sentiment_analysis.agent.rule_agent import analyze_comment as rule_analyze
    llm_fail_count = [0]  # 用列表包装以便在闭包中修改

    logger.info("Agent 模式: LLM (DeepSeek) + 规则 fallback")

    def analyze_with_fallback(
        node: CommentNode,
        post_content: str,
        parent_text: str | None = None,
        user_profile: UserProfile | None = None,
    ) -> AgentResult:
        """LLM 优先，失败时 fallback 到规则 Agent。"""
        try:
            return llm_analyze(node, post_content, parent_text, user_profile)
        except Exception as e:
            llm_fail_count[0] += 1
            if llm_fail_count[0] == 1:
                logger.warning("LLM 调用失败 (%s)，后续将静默 fallback", type(e).__name__)
            return rule_analyze(node, post_content, parent_text, user_profile)

    return analyze_with_fallback
