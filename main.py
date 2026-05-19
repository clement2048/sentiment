"""主入口 — 训练和推理。

用法:
    python -m main --mode train --input dataset/result/parsed_28.jsonl
    python -m main --mode analyze --input dataset/result/parsed_28.jsonl --post-id 317353268392961
"""

import argparse
import json
import sys
from pathlib import Path

# 确保项目根在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import torch

from agent.agent_orchestrator import run_agents
from agent.user_profile import UserProfileManager
from config import (
    DEFAULT_INPUT,
    RANDOM_SEED,
    AGENT_SENTIMENT_DIM,
    USE_DEPTH_FEATURE,
    MODEL_DIR,
)
from data_loader.graph_builder import build_graphs
from data_loader.loader import load_conversations
from data_loader.preprocessor import preprocess
from features.feature_pipeline import FeaturePipeline
from gnn.dataset import split_graphs
from gnn.model import SentimentGCN
from gnn.predictor import SentimentPredictor
from gnn.trainer import train_model
from logger import logger


def _set_seed(seed: int = RANDOM_SEED) -> None:
    """固定随机种子。"""
    import random
    import numpy as np
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def cmd_train(jsonl_path: Path) -> dict:
    """训练模式：完整管线。

    1. 加载数据 → 对话列表
    2. 特征提取 (TF-IDF fit + transform)
    3. Agent 分析 → 情绪矩阵
    4. 构建图
    5. GNN 训练 + 评估
    """
    _set_seed()

    # ── 步骤 1: 加载数据 ──
    logger.info("=" * 60)
    logger.info("步骤 1/5: 加载数据")
    conversations = load_conversations(jsonl_path)
    labeled_conv = [c for c in conversations if c.label is not None]
    logger.info("有效对话(有标签): %d / %d", len(labeled_conv), len(conversations))

    if len(labeled_conv) < 10:
        logger.error("有标签的对话不足 10 条，无法训练。请检查数据。")
        sys.exit(1)

    # ── 步骤 2: 特征提取 ──
    logger.info("=" * 60)
    logger.info("步骤 2/5: 拟合 TF-IDF 特征")
    pipeline = FeaturePipeline()
    pipeline.fit(conversations)  # 在所有对话上拟合（含无标签），增加词汇覆盖
    text_features_list = pipeline.extract_per_conversation(conversations)
    tfidf_dim = pipeline.feature_dim
    logger.info("TF-IDF 特征维度: %d", tfidf_dim)

    # ── 步骤 3: Agent 分析 ──
    logger.info("=" * 60)
    logger.info("步骤 3/5: 构建用户画像 + Agent 分析")
    profile_manager = UserProfileManager().build_from_conversations(conversations)
    _, sentiment_list = run_agents(conversations, profile_manager)

    # ── 步骤 4: 构建图 ──
    logger.info("=" * 60)
    logger.info("步骤 4/5: 构建图")
    graphs = build_graphs(
        conversations,
        text_features_list,
        sentiment_list,
        use_depth=USE_DEPTH_FEATURE,
        filter_null_label=True,
    )

    if len(graphs) < 10:
        logger.error("有效图不足 10 张，无法训练。")
        sys.exit(1)

    # ── 步骤 5: GNN 训练 ──
    logger.info("=" * 60)
    logger.info("步骤 5/5: GNN 训练")
    input_dim = tfidf_dim + AGENT_SENTIMENT_DIM + (1 if USE_DEPTH_FEATURE else 0)
    logger.info("输入特征维度: %d (TF-IDF=%d + Agent=%d + depth=%d)",
                input_dim, tfidf_dim, AGENT_SENTIMENT_DIM, 1 if USE_DEPTH_FEATURE else 0)

    train_graphs, val_graphs, test_graphs = split_graphs(graphs)
    model = SentimentGCN(input_dim=input_dim)
    logger.info("模型参数量: %s", sum(p.numel() for p in model.parameters()))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    metrics = train_model(model, train_graphs, val_graphs, test_graphs, device=device)

    logger.info("=" * 60)
    logger.info("训练完成!")
    logger.info("测试集: Acc=%.4f  F1_macro=%.4f  F1_weighted=%.4f",
                metrics["accuracy"], metrics["f1_macro"], metrics["f1_weighted"])

    return metrics


def cmd_analyze(jsonl_path: Path, post_id: str | None = None, use_llm: bool = False) -> None:
    """分析模式：加载已训练模型，预测指定帖子的情绪。"""
    _set_seed()

    # 加载 checkpoint
    checkpoint_path = MODEL_DIR / "sentiment_gcn.pt"
    if not checkpoint_path.exists():
        logger.error("模型文件不存在: %s\n请先运行训练: python -m main --mode train", checkpoint_path)
        sys.exit(1)

    # 加载数据
    conversations = load_conversations(jsonl_path)

    # 准备特征管线（需要先拟合以确定维度）
    pipeline = FeaturePipeline().fit(conversations)
    text_features_list = pipeline.extract_per_conversation(conversations)
    tfidf_dim = pipeline.feature_dim

    # Agent 分析
    profile_manager = UserProfileManager().build_from_conversations(conversations)
    _, sentiment_list = run_agents(conversations, profile_manager)

    # 构建图
    graphs = build_graphs(
        conversations, text_features_list, sentiment_list,
        use_depth=USE_DEPTH_FEATURE, filter_null_label=False,
    )

    # 找出对应的对话索引
    if post_id:
        conv_indices = [
            i for i, c in enumerate(conversations) if c.post_id == post_id
        ]
        if not conv_indices:
            logger.error("未找到帖子: %s", post_id)
            sys.exit(1)
        post_graphs = [graphs[i] for i in conv_indices]
        logger.info("帖子 %s: %d 段对话", post_id, len(post_graphs))
    else:
        # 分析所有有标签的对话
        labeled_indices = [
            i for i, g in enumerate(graphs) if g.y.item() != -100
        ]
        post_graphs = [graphs[i] for i in labeled_indices]
        logger.info("分析所有有标签对话: %d 段", len(post_graphs))

    # 加载模型
    input_dim = tfidf_dim + AGENT_SENTIMENT_DIM + (1 if USE_DEPTH_FEATURE else 0)
    predictor = SentimentPredictor.from_checkpoint(checkpoint_path, input_dim)

    # 预测
    post_id_str = post_id or "all"
    results = predictor.predict_post(post_graphs, post_id_str)

    # 打印结果
    print("\n" + "=" * 60)
    print("预测结果:")
    for i, r in enumerate(results):
        arrow = "[UP]" if r["prediction"] == "bullish" else "[DOWN]"
        print(f"  对话{i + 1}: {arrow} {r['prediction']} (prob={r['probability']:.3f}, conf={r['confidence']:.3f})")
        conv_idx = conv_indices[i] if post_id else labeled_indices[i]
        conv = conversations[conv_idx]
        print(f"    用户: {conv.nodes[0].author}")
        print(f"    评论: {conv.nodes[0].text[:80]}{'...' if len(conv.nodes[0].text) > 80 else ''}")
        print(f"    真实标签: {'看涨' if conv.label == 1 else '看跌' if conv.label == -1 else '无'}")

    # 如果有标签，计算准确率
    labeled_results = [
        (r, conversations[conv_indices[i] if post_id else labeled_indices[i]])
        for i, r in enumerate(results)
    ]
    labeled_results = [(r, c) for r, c in labeled_results if c.label is not None]
    if labeled_results:
        correct = sum(
            1 for r, c in labeled_results
            if (r["prediction"] == "bullish" and c.label == 1) or
               (r["prediction"] == "bearish" and c.label == -1)
        )
        acc = correct / len(labeled_results)
        print(f"\n准确率: {correct}/{len(labeled_results)} = {acc:.2%}")


def main():
    parser = argparse.ArgumentParser(
        description="币安广场评论情绪分析 — 多 Agent + GNN",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m main --mode train
  python -m main --mode train --input dataset/result/parsed_28.jsonl
  python -m main --mode analyze --post-id 317353268392961
        """,
    )
    parser.add_argument(
        "--mode",
        type=str,
        required=True,
        choices=["train", "analyze"],
        help="运行模式: train=训练, analyze=推理",
    )
    parser.add_argument(
        "--input",
        type=str,
        default=str(DEFAULT_INPUT),
        help=f"输入 JSONL 路径 (默认: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--post-id",
        type=str,
        default=None,
        help="analyze 模式下指定帖子 ID，不指定则分析所有",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        default=False,
        help="使用 LLM Agent (DeepSeek)，需设置 DEEPSEEK_API_KEY 环境变量",
    )
    args = parser.parse_args()

    jsonl_path = Path(args.input)

    if args.mode == "train":
        cmd_train(jsonl_path, use_llm=args.use_llm)
    elif args.mode == "analyze":
        cmd_analyze(jsonl_path, args.post_id, use_llm=args.use_llm)


if __name__ == "__main__":
    main()
