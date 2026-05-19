"""Agent 编排器 — 遍历对话，为每个参与用户运行 Agent 分析。"""

import numpy as np

from agent.agent_factory import create_analyze_func
from agent.llm_agent import AgentResult
from agent.user_profile import UserProfileManager
from data_loader.loader import Conversation
from logger import logger


def run_agents(
    conversations: list[Conversation],
    profile_manager: UserProfileManager | None = None,
) -> tuple[list[list[AgentResult]], list[np.ndarray]]:
    """为每段对话的每个评论节点运行 LLM Agent 分析。

    参数:
        conversations: 对话列表
        profile_manager: 用户画像管理器（可选）

    返回:
        agent_results: 每个对话的 Agent 结果列表
        sentiment_matrix_list: 每个对话的情绪矩阵，shape (num_nodes, 4)
    """
    analyze_comment = create_analyze_func()

    all_results: list[list[AgentResult]] = []
    sentiment_list: list[np.ndarray] = []

    for conv in conversations:
        conv_results: list[AgentResult] = []
        conv_sentiments: list[list[float]] = []

        for i, node in enumerate(conv.nodes):
            # 找到父评论文本
            parent_text = None
            for src, tgt in conv.edges:
                if src == i:
                    parent_text = conv.nodes[tgt].text
                    break

            # 获取用户画像
            profile = profile_manager.get(node.author) if profile_manager else None

            # 运行 Agent
            result = analyze_comment(
                node=node,
                post_content=conv.post_content,
                parent_text=parent_text,
                user_profile=profile,
            )
            conv_results.append(result)
            conv_sentiments.append(result.sentiment_vector)

        all_results.append(conv_results)
        sentiment_list.append(np.array(conv_sentiments, dtype=np.float32))

    # 日志统计
    total_analyses = sum(len(r) for r in all_results)
    bullish_count = sum(
        1 for conv_r in all_results for r in conv_r if r.sentiment_label == "bullish"
    )
    bearish_count = sum(
        1 for conv_r in all_results for r in conv_r if r.sentiment_label == "bearish"
    )
    neutral_count = total_analyses - bullish_count - bearish_count

    logger.info("Agent 分析完成: %d 条评论", total_analyses)
    logger.info("  看涨=%d (%.1f%%)  看跌=%d (%.1f%%)  中性=%d (%.1f%%)",
                bullish_count, 100 * bullish_count / max(total_analyses, 1),
                bearish_count, 100 * bearish_count / max(total_analyses, 1),
                neutral_count, 100 * neutral_count / max(total_analyses, 1))

    return all_results, sentiment_list
