"""图构建器 — 对话 → PyG Data 对象。

节点特征 = TF-IDF 向量 ⊕ Agent 情绪向量 ⊕ [深度特征]
"""

import numpy as np
import torch
from torch_geometric.data import Data

from sentiment_analysis.data.loader import Conversation
from sentiment_analysis.utils.logger import logger


def build_graph(
    conversation: Conversation,
    text_features: np.ndarray,       # (num_nodes, tfidf_dim)
    agent_sentiments: np.ndarray,    # (num_nodes, 4) [bullish, bearish, neutral, confidence]
    use_depth: bool = True,
) -> Data:
    """将一段对话转为 PyG Data 对象。

    参数:
        conversation: 对话数据
        text_features: 每个节点的 TF-IDF 特征，shape (N, D_text)
        agent_sentiments: 每个节点的 Agent 情绪向量，shape (N, 4)
        use_depth: 是否添加深度特征

    返回:
        PyG Data 对象: x=节点特征, edge_index=边, y=图标签
    """
    num_nodes = len(conversation.nodes)
    assert text_features.shape[0] == num_nodes, \
        f"text_features shape {text_features.shape} != num_nodes {num_nodes}"
    assert agent_sentiments.shape[0] == num_nodes, \
        f"agent_sentiments shape {agent_sentiments.shape} != num_nodes {num_nodes}"

    # 拼接节点特征
    features = [text_features, agent_sentiments]

    if use_depth:
        depth_feat = np.array([[node.depth] for node in conversation.nodes], dtype=np.float32)
        features.append(depth_feat)

    x = np.concatenate(features, axis=1).astype(np.float32)

    # 边索引
    if conversation.edges:
        edge_index = torch.tensor(conversation.edges, dtype=torch.long).t().contiguous()
    else:
        # 单节点图：空边 (2, 0)
        edge_index = torch.empty((2, 0), dtype=torch.long)

    # 图标签（根评论 label）
    label = conversation.label
    y = torch.tensor([label], dtype=torch.long) if label is not None else torch.tensor([-100], dtype=torch.long)

    return Data(
        x=torch.from_numpy(x),
        edge_index=edge_index,
        y=y,
        num_nodes=num_nodes,
        post_id=conversation.post_id,
    )


def build_graphs(
    conversations: list[Conversation],
    text_features_list: list[np.ndarray],
    agent_sentiments_list: list[np.ndarray],
    use_depth: bool = True,
    filter_null_label: bool = True,
) -> list[Data]:
    """批量构建图列表。

    参数:
        conversations: 对话列表
        text_features_list: 每个对话的文本特征
        agent_sentiments_list: 每个对话的 Agent 情绪特征
        use_depth: 是否添加深度特征
        filter_null_label: 是否过滤无标签对话

    返回:
        PyG Data 对象列表（仅包含有标签的图）
    """
    graphs: list[Data] = []
    null_count = 0

    for i, conv in enumerate(conversations):
        if filter_null_label and conv.label is None:
            null_count += 1
            continue

        data = build_graph(conv, text_features_list[i], agent_sentiments_list[i], use_depth)
        graphs.append(data)

    logger.info("图构建完成: %d 张图 (过滤 %d 条无标签)", len(graphs), null_count)

    # 统计
    num_nodes = [g.num_nodes for g in graphs]
    num_edges = [g.edge_index.size(1) for g in graphs]
    logger.info("  节点/图: min=%d max=%d avg=%.1f", min(num_nodes), max(num_nodes),
                np.mean(num_nodes))
    logger.info("  边/图:   min=%d max=%d avg=%.1f", min(num_edges), max(num_edges),
                np.mean(num_edges))

    # 标签分布
    labels = [g.y.item() for g in graphs]
    bull = sum(1 for l in labels if l == 1)
    bear = sum(1 for l in labels if l == -1)
    logger.info("  标签分布: 看涨=%d 看跌=%d (%.1f%% / %.1f%%)", bull, bear,
                100 * bull / max(len(labels), 1), 100 * bear / max(len(labels), 1))

    return graphs
