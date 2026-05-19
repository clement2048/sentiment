"""JSONL 数据加载器 — 读取帖子，拆分为独立对话。"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from logger import logger


@dataclass
class CommentNode:
    """对话中单个评论节点。"""
    comment_id: str             # 解析后自增ID（如 "c1"）
    original_comment_id: str    # 币安系统原始ID
    author: str                 # 评论者昵称
    text: str                   # 评论正文
    post_time: str              # 发布时间
    depth: int                  # 嵌套深度（0=根评论）
    label: Optional[int]        # 仅根评论有值，嵌套回复为 None
    comment_error: str          # 标注错误原因


@dataclass
class Conversation:
    """一段对话 = 一个根评论 + 其下所有嵌套回复。"""
    post_id: str
    post_author: str
    post_content: str           # 新闻正文（对话背景）
    products: list[str]         # 涉及产品
    nodes: list[CommentNode]    # 扁平节点列表（根评论为 nodes[0]）
    edges: list[tuple[int, int]]  # (source_idx, target_idx)，source 回复了 target
    label: Optional[int]        # 对话标签 = 根评论的 label


def _flatten_comments(
    comment: dict,
    depth: int,
    parent_idx: int,
    nodes: list[CommentNode],
    edges: list[tuple[int, int]],
) -> None:
    """递归展开嵌套评论，填充 nodes 和 edges。"""
    current_idx = len(nodes)
    label = comment.get("label")
    # 嵌套回复的 label 应为 None（数据中可能不存在）
    if depth > 0:
        label = None

    node = CommentNode(
        comment_id=comment.get("comment_id", ""),
        original_comment_id=str(comment.get("original_comment_id", "")),
        author=comment.get("author", ""),
        text=comment.get("text", ""),
        post_time=comment.get("post_time", ""),
        depth=depth,
        label=label,
        comment_error=comment.get("comment_error", ""),
    )
    nodes.append(node)

    if depth > 0:
        edges.append((current_idx, parent_idx))

    # 递归处理嵌套回复
    replies = comment.get("replies", []) or []
    for reply in replies:
        _flatten_comments(reply, depth + 1, current_idx, nodes, edges)


def load_conversations(
    jsonl_path: str | Path,
    min_text_length: int = 2,
) -> list[Conversation]:
    """从 JSONL 文件加载并拆分为独立对话。

    每个根评论 → 一段对话，其 replies 子树扁平化为节点列表。
    """
    path = Path(jsonl_path)
    if not path.exists():
        raise FileNotFoundError(f"JSONL 文件不存在: {path}")

    conversations: list[Conversation] = []
    stats = {"total_posts": 0, "total_conversations": 0, "skipped_no_text": 0}

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            post = json.loads(line)

            # 跳过 label_error 非空的帖子（整个帖子标注有问题）
            # 但仍可提取对话（部分评论可能有 label）

            post_id = post.get("post_id", "")
            post_author = post.get("post_author", "")
            post_content = post.get("post_content", "")
            products = post.get("products", []) or []
            comments = post.get("comments", []) or []

            stats["total_posts"] += 1

            for root_comment in comments:
                nodes: list[CommentNode] = []
                edges: list[tuple[int, int]] = []

                _flatten_comments(root_comment, depth=0, parent_idx=-1, nodes=nodes, edges=edges)

                # 过滤：跳过评论为空的节点
                if not nodes or not nodes[0].text.strip():
                    stats["skipped_no_text"] += 1
                    continue
                if len(nodes[0].text.strip()) < min_text_length:
                    stats["skipped_no_text"] += 1
                    continue

                conv = Conversation(
                    post_id=post_id,
                    post_author=post_author,
                    post_content=post_content,
                    products=products,
                    nodes=nodes,
                    edges=edges,
                    label=nodes[0].label,  # 根评论的 label
                )
                conversations.append(conv)
                stats["total_conversations"] += 1

    # 统计
    labeled = sum(1 for c in conversations if c.label is not None)
    bullish = sum(1 for c in conversations if c.label == 1)
    bearish = sum(1 for c in conversations if c.label == -1)
    has_replies = sum(1 for c in conversations if len(c.nodes) > 1)
    total_nodes = sum(len(c.nodes) for c in conversations)

    logger.info("数据加载完成: %s", path.name)
    logger.info("  帖子数: %d", stats["total_posts"])
    logger.info("  对话数: %d", stats["total_conversations"])
    logger.info("  有标签: %d (看涨=%d, 看跌=%d)", labeled, bullish, bearish)
    logger.info("  无标签: %d", stats["total_conversations"] - labeled)
    logger.info("  有回复: %d (%.1f%%)", has_replies,
                100 * has_replies / max(stats["total_conversations"], 1))
    logger.info("  总评论节点: %d", total_nodes)
    logger.info("  跳过(无文本): %d", stats["skipped_no_text"])

    return conversations
