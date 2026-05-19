"""特征提取管线 — 串联预处理 → TF-IDF 向量化。"""

import numpy as np

from data_loader.loader import Conversation
from data_loader.preprocessor import preprocess
from features.text_embedding import (
    TfidfVectorizer,
    fit_vectorizer,
    transform_texts,
)
from logger import logger


class FeaturePipeline:
    """文本特征提取管线。"""

    def __init__(self):
        self.vectorizer: TfidfVectorizer | None = None

    def fit(self, conversations: list[Conversation]) -> "FeaturePipeline":
        """在全量对话文本上拟合 TF-IDF 向量器。

        将每段对话中所有节点的评论连接起来，一起拟合。
        """
        all_texts: list[str] = []
        for conv in conversations:
            for node in conv.nodes:
                text = preprocess(node.text)
                if text:
                    all_texts.append(text)

        logger.info("拟合 TF-IDF 向量器，共 %d 条评论文本", len(all_texts))
        self.vectorizer = fit_vectorizer(all_texts)
        return self

    def extract_per_conversation(
        self,
        conversations: list[Conversation],
    ) -> list[np.ndarray]:
        """为每段对话提取节点级文本特征。

        返回:
            features_list: 列表长度 = len(conversations)
                          每个元素 shape = (num_nodes_in_conv, TFIDF_MAX_FEATURES)
        """
        if self.vectorizer is None:
            raise RuntimeError("请先调用 fit() 拟合向量器")

        features_list: list[np.ndarray] = []
        for conv in conversations:
            node_texts = [preprocess(node.text) for node in conv.nodes]
            # 处理空文本
            node_texts = [t if t else " " for t in node_texts]
            feat = transform_texts(self.vectorizer, node_texts)
            features_list.append(feat)

        logger.info("特征提取完成: %d 段对话", len(features_list))
        return features_list

    @property
    def feature_dim(self) -> int:
        """TF-IDF 特征维度。"""
        if self.vectorizer is None:
            return 500  # 默认
        return len(self.vectorizer.vocabulary_)
