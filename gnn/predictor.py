"""GNN 预测器 — 单段对话/单帖推断。"""

from pathlib import Path

import numpy as np
import torch
from torch_geometric.data import Data

from gnn.model import SentimentGCN
from logger import logger


class SentimentPredictor:
    """加载已训练模型进行单段对话预测。"""

    def __init__(
        self,
        model: SentimentGCN,
        device: torch.device | None = None,
    ):
        self.model = model
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        self.model.eval()

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str | Path,
        input_dim: int,
        device: torch.device | None = None,
    ) -> "SentimentPredictor":
        """从保存的 checkpoint 加载模型。"""
        device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = SentimentGCN(input_dim=input_dim)
        checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
        model.load_state_dict(checkpoint["model_state_dict"])
        logger.info("模型已加载: %s", checkpoint_path)
        if "metrics" in checkpoint:
            logger.info("  训练时测试指标: Acc=%.4f F1=%.4f",
                        checkpoint["metrics"]["accuracy"],
                        checkpoint["metrics"]["f1_macro"])
        return cls(model, device)

    @torch.no_grad()
    def predict(self, graph: Data) -> dict:
        """预测单段对话的情绪。

        参数:
            graph: PyG Data 对象（单图，非 batch）

        返回:
            {"prediction": "bullish"|"bearish", "probability": float, "confidence": float}
        """
        self.model.eval()
        # 添加 batch 维度
        graph = graph.clone()
        graph.batch = torch.zeros(graph.num_nodes, dtype=torch.long, device=self.device)
        graph = graph.to(self.device)

        prob = self.model(graph).item()
        label = "bullish" if prob >= 0.5 else "bearish"
        confidence = abs(prob - 0.5) * 2  # 映射到 0~1

        return {
            "prediction": label,
            "probability": prob,
            "confidence": confidence,
        }

    def predict_post(
        self,
        graphs: list[Data],
        post_id: str,
    ) -> list[dict]:
        """预测一个帖子的所有对话。

        返回:
            每个对话的预测结果列表
        """
        results: list[dict] = []
        for i, graph in enumerate(graphs):
            result = self.predict(graph)
            result["dialogue_index"] = i
            result["post_id"] = post_id
            results.append(result)

        if results:
            bull = sum(1 for r in results if r["prediction"] == "bullish")
            bear = len(results) - bull
            logger.info("帖子 %s: %d 段对话, 看涨=%d 看跌=%d", post_id, len(results), bull, bear)

        return results
