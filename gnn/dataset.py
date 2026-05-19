"""PyG 数据集 — 图列表封装 + 训练/验证/测试划分。"""

import random

import torch
from torch_geometric.data import Data

from config import RANDOM_SEED, TRAIN_RATIO, VAL_RATIO, TEST_RATIO
from logger import logger


def split_graphs(
    graphs: list[Data],
    train_ratio: float = TRAIN_RATIO,
    val_ratio: float = VAL_RATIO,
    test_ratio: float = TEST_RATIO,
    seed: int = RANDOM_SEED,
) -> tuple[list[Data], list[Data], list[Data]]:
    """按比例随机划分图列表。

    返回:
        (train_graphs, val_graphs, test_graphs)
    """
    assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6, \
        "划分比例之和必须为 1.0"

    random.seed(seed)
    indices = list(range(len(graphs)))
    random.shuffle(indices)

    n = len(graphs)
    train_end = int(n * train_ratio)
    val_end = train_end + int(n * val_ratio)

    train_graphs = [graphs[i] for i in indices[:train_end]]
    val_graphs = [graphs[i] for i in indices[train_end:val_end]]
    test_graphs = [graphs[i] for i in indices[val_end:]]

    # 标签映射: 1 → 1.0, -1 → 0.0 (看跌为 0)
    for g in train_graphs + val_graphs + test_graphs:
        label = g.y.item()
        g.y = torch.tensor([1.0 if label == 1 else 0.0], dtype=torch.float32)

    logger.info("数据集划分: train=%d val=%d test=%d (seed=%d)",
                len(train_graphs), len(val_graphs), len(test_graphs), seed)

    # 标签分布
    for name, graphs_split in [("train", train_graphs), ("val", val_graphs), ("test", test_graphs)]:
        bull = sum(1 for g in graphs_split if g.y.item() >= 0.5)
        bear = len(graphs_split) - bull
        logger.info("  %s: 看涨=%d 看跌=%d (%.1f%% / %.1f%%)",
                    name, bull, bear,
                    100 * bull / max(len(graphs_split), 1),
                    100 * bear / max(len(graphs_split), 1))

    return train_graphs, val_graphs, test_graphs
