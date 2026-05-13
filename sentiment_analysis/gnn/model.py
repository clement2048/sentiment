"""GNN 模型 — 3层 GCN + GlobalMeanMaxPool → MLP 分类器。"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GCNConv, global_mean_pool, global_max_pool

from sentiment_analysis.config import (
    HIDDEN_DIM,
    HIDDEN_DIM2,
    HIDDEN_DIM3,
    MLP_HIDDEN,
    DROPOUT,
)


class SentimentGCN(nn.Module):
    """三层 GCN + 全局池化 + MLP 图分类器。

    输入: 图 batch (x: node features, edge_index: edges)
    输出: [0, 1] 概率，>0.5=看涨，<0.5=看跌
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int = HIDDEN_DIM,
        hidden_dim2: int = HIDDEN_DIM2,
        hidden_dim3: int = HIDDEN_DIM3,
        mlp_hidden: int = MLP_HIDDEN,
        dropout: float = DROPOUT,
    ):
        super().__init__()
        self.dropout_rate = dropout

        # GCN 层（自带 self-loop）
        self.conv1 = GCNConv(input_dim, hidden_dim)
        self.conv2 = GCNConv(hidden_dim, hidden_dim2)
        self.conv3 = GCNConv(hidden_dim2, hidden_dim3)

        # BatchNorm
        self.bn1 = nn.BatchNorm1d(hidden_dim)
        self.bn2 = nn.BatchNorm1d(hidden_dim2)
        self.bn3 = nn.BatchNorm1d(hidden_dim3)

        # 全局池化后 concat: hidden_dim3 * 2
        pooled_dim = hidden_dim3 * 2

        # MLP 分类器
        self.classifier = nn.Sequential(
            nn.Linear(pooled_dim, mlp_hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(mlp_hidden, 1),
            nn.Sigmoid(),
        )

    def forward(self, data) -> torch.Tensor:
        """前向传播。

        参数:
            data: PyG Batch 对象，包含 x, edge_index, batch

        返回:
            (batch_size, 1) 概率张量
        """
        x, edge_index, batch = data.x, data.edge_index, data.batch

        # GCN 层 1
        x = self.conv1(x, edge_index)
        x = self.bn1(x)
        x = F.relu(x)
        x = F.dropout(x, p=self.dropout_rate, training=self.training)

        # GCN 层 2
        x = self.conv2(x, edge_index)
        x = self.bn2(x)
        x = F.relu(x)
        x = F.dropout(x, p=self.dropout_rate, training=self.training)

        # GCN 层 3
        x = self.conv3(x, edge_index)
        x = self.bn3(x)
        x = F.relu(x)

        # 全局池化
        x_mean = global_mean_pool(x, batch)
        x_max = global_max_pool(x, batch)
        x_pooled = torch.cat([x_mean, x_max], dim=1)

        # MLP 分类
        out = self.classifier(x_pooled)
        return out

    def predict(self, data) -> tuple[torch.Tensor, torch.Tensor]:
        """预测，返回 (概率, 类别标签 0=看跌 1=看涨)。"""
        self.eval()
        with torch.no_grad():
            prob = self.forward(data)
            pred_label = (prob >= 0.5).long()
        return prob, pred_label
