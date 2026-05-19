"""评估指标：准确率、F1、混淆矩阵。"""

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """计算分类指标。"""
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "f1_macro": f1_score(y_true, y_pred, average="macro"),
        "f1_weighted": f1_score(y_true, y_pred, average="weighted"),
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
    }


def format_metrics(metrics: dict, prefix: str = "") -> str:
    """格式化指标为可读字符串。"""
    parts = [
        f"{prefix}Acc={metrics['accuracy']:.4f}",
        f"F1_macro={metrics['f1_macro']:.4f}",
        f"F1_weighted={metrics['f1_weighted']:.4f}",
    ]
    return "  ".join(parts)
