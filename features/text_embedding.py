"""TF-IDF 文本向量化 — 中文 char_wb 模式。"""

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from config import TFIDF_MAX_FEATURES, TFIDF_NGRAM_RANGE, TFIDF_MIN_DF
from logger import logger


def create_tfidf_vectorizer() -> TfidfVectorizer:
    """创建中文 TF-IDF 向量器（char_wb 模式，字符级 ngram）。"""
    return TfidfVectorizer(
        max_features=TFIDF_MAX_FEATURES,
        ngram_range=TFIDF_NGRAM_RANGE,
        analyzer="char_wb",
        min_df=TFIDF_MIN_DF,
        sublinear_tf=True,          # 1+log(tf)，抑制高频词
        dtype=np.float32,
    )


def fit_vectorizer(texts: list[str]) -> TfidfVectorizer:
    """在全部文本上拟合 TF-IDF 向量器。"""
    vectorizer = create_tfidf_vectorizer()
    vectorizer.fit(texts)
    logger.info("TF-IDF 向量器已拟合: vocab_size=%d, max_features=%d",
                len(vectorizer.vocabulary_), TFIDF_MAX_FEATURES)
    return vectorizer


def transform_texts(
    vectorizer: TfidfVectorizer,
    texts: list[str],
) -> np.ndarray:
    """将文本列表转为 TF-IDF 矩阵（稠密）。"""
    if not texts:
        return np.empty((0, TFIDF_MAX_FEATURES), dtype=np.float32)
    return vectorizer.transform(texts).toarray().astype(np.float32)
