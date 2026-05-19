"""Agent 工厂 — 创建 LLM Agent 分析函数。"""

import os

from config import LLM_API_KEY_ENV
from logger import logger


def create_analyze_func():
    """返回 LLM analyze_comment 函数。

    返回:
        analyze_comment(node, post_content, parent_text, user_profile) -> AgentResult
    """
    from agent.llm_agent import analyze_comment as llm_analyze

    api_key = os.environ.get(LLM_API_KEY_ENV, LLM_API_KEY_ENV)
    if not api_key:
        raise ValueError(f"LLM API key 未设置，请检查 config.py 中的 LLM_API_KEY_ENV")

    logger.info("Agent 模式: LLM (DeepSeek)")
    return llm_analyze
