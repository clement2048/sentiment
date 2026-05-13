"""GNN 训练循环 — 加权 BCE + 早停 + 评估。"""

import copy
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch_geometric.data import Data
from torch_geometric.loader import DataLoader

from sentiment_analysis.config import (
    BATCH_SIZE,
    LEARNING_RATE,
    WEIGHT_DECAY,
    MAX_EPOCHS,
    EARLY_STOP_PATIENCE,
    MODEL_DIR,
)
from sentiment_analysis.utils.logger import logger
from sentiment_analysis.utils.metrics import compute_metrics, format_metrics


class EarlyStopping:
    """早停 — 监控验证 loss，patience 轮不下降则停止。"""

    def __init__(self, patience: int = EARLY_STOP_PATIENCE, min_delta: float = 1e-4):
        self.patience = patience
        self.min_delta = min_delta
        self.best_loss = float("inf")
        self.best_state: dict | None = None
        self.counter = 0
        self.stopped_epoch = -1

    def step(self, val_loss: float, model: nn.Module) -> bool:
        """返回 True 表示应停止训练。"""
        if val_loss < self.best_loss - self.min_delta:
            self.best_loss = val_loss
            self.best_state = copy.deepcopy(model.state_dict())
            self.counter = 0
        else:
            self.counter += 1
            if self.counter >= self.patience:
                self.stopped_epoch = self.counter
                return True
        return False


def train_epoch(
    model: nn.Module,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
    device: torch.device,
) -> float:
    """训练一个 epoch，返回平均 loss。"""
    model.train()
    total_loss = 0.0

    for batch in loader:
        batch = batch.to(device)
        optimizer.zero_grad()
        out = model(batch).squeeze(-1)
        loss = criterion(out, batch.y)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * batch.num_graphs

    return total_loss / len(loader.dataset)


@torch.no_grad()
def evaluate(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module | None,
    device: torch.device,
) -> tuple[float, dict]:
    """评估模型，返回 (loss, metrics)。"""
    model.eval()
    total_loss = 0.0
    all_preds: list[int] = []
    all_labels: list[int] = []

    for batch in loader:
        batch = batch.to(device)
        out = model(batch).squeeze(-1)
        if criterion:
            total_loss += criterion(out, batch.y).item() * batch.num_graphs
        preds = (out >= 0.5).long().cpu().numpy().flatten()
        labels = batch.y.cpu().numpy().flatten().astype(int)
        all_preds.extend(preds.tolist())
        all_labels.extend(labels.tolist())

    loss = total_loss / len(loader.dataset) if criterion else 0.0
    metrics = compute_metrics(np.array(all_labels), np.array(all_preds))
    return loss, metrics


def train_model(
    model: nn.Module,
    train_graphs: list[Data],
    val_graphs: list[Data],
    test_graphs: list[Data],
    device: torch.device | None = None,
    save_path: str | Path | None = None,
) -> dict:
    """完整训练流程。

    返回:
        最终在测试集上的指标字典
    """
    device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    logger.info("训练设备: %s", device)

    # DataLoader
    train_loader = DataLoader(train_graphs, batch_size=BATCH_SIZE, shuffle=True)
    val_loader = DataLoader(val_graphs, batch_size=BATCH_SIZE, shuffle=False)
    test_loader = DataLoader(test_graphs, batch_size=BATCH_SIZE, shuffle=False)

    # 加权 BCE：处理类别不平衡
    bull_count = sum(1 for g in train_graphs if g.y.item() >= 0.5)
    bear_count = len(train_graphs) - bull_count
    pos_weight = torch.tensor([bear_count / max(bull_count, 1)], device=device)
    criterion = nn.BCELoss()
    logger.info("BCE pos_weight: %.3f (看涨=%d 看跌=%d)", pos_weight.item(), bull_count, bear_count)

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=LEARNING_RATE,
        weight_decay=WEIGHT_DECAY,
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=10
    )
    early_stopping = EarlyStopping()

    best_val_metrics = None
    train_losses: list[float] = []
    val_losses: list[float] = []

    for epoch in range(1, MAX_EPOCHS + 1):
        train_loss = train_epoch(model, train_loader, optimizer, criterion, device)
        val_loss, val_metrics = evaluate(model, val_loader, criterion, device)

        train_losses.append(train_loss)
        val_losses.append(val_loss)

        scheduler.step(val_loss)

        if epoch % 10 == 0 or epoch == 1:
            logger.info(
                "Epoch %3d | TrainLoss=%.4f ValLoss=%.4f | %s",
                epoch, train_loss, val_loss, format_metrics(val_metrics),
            )

        if early_stopping.step(val_loss, model):
            logger.info("早停 @ epoch %d, best_val_loss=%.4f", epoch, early_stopping.best_loss)
            break

    # 恢复最佳模型
    if early_stopping.best_state:
        model.load_state_dict(early_stopping.best_state)

    # 测试集评估
    test_loss, test_metrics = evaluate(model, test_loader, criterion, device)
    logger.info("=== 测试集结果 ===")
    logger.info("TestLoss=%.4f | %s", test_loss, format_metrics(test_metrics))
    logger.info("混淆矩阵: %s", test_metrics["confusion_matrix"])

    # 保存模型
    save_path = Path(save_path) if save_path else MODEL_DIR / "sentiment_gcn.pt"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {"model_state_dict": model.state_dict(), "metrics": test_metrics},
        save_path,
    )
    logger.info("模型已保存: %s", save_path)

    return test_metrics
