"""用户画像 — 按作者聚合历史评论，分析立场倾向。"""

from collections import defaultdict
from dataclasses import dataclass, field

from sentiment_analysis.data.loader import Conversation
from sentiment_analysis.utils.logger import logger


@dataclass
class UserProfile:
    """单个用户的画像。"""
    author: str
    total_comments: int = 0
    bullish_count: int = 0       # 历史看涨次数（来自已有 label）
    bearish_count: int = 0       # 历史看跌次数
    neutral_count: int = 0       # 中性/无标签次数
    comment_samples: list[str] = field(default_factory=list)  # 最多保留 10 条

    @property
    def stance_bias(self) -> float:
        """立场偏差: -1.0 (极度看跌) ~ +1.0 (极度看涨), 0 = 中性/未知。"""
        total = self.bullish_count + self.bearish_count
        if total == 0:
            return 0.0
        return (self.bullish_count - self.bearish_count) / total

    @property
    def consistency(self) -> float:
        """立场一致性: 0~1，越高越一致。"""
        total = self.bullish_count + self.bearish_count + self.neutral_count
        if total <= 1:
            return 0.5  # 单条评论，无法判断一致性
        majority = max(self.bullish_count, self.bearish_count, self.neutral_count)
        return majority / total


class UserProfileManager:
    """管理所有用户画像。"""

    def __init__(self):
        self._profiles: dict[str, UserProfile] = defaultdict(
            lambda: UserProfile(author="")
        )

    def build_from_conversations(self, conversations: list[Conversation]) -> "UserProfileManager":
        """从对话列表构建用户画像。

        注意：基于已有 label 统计立场。对于无 label 的评论,暂计为 neutral。
        """
        for conv in conversations:
            for node in conv.nodes:
                author = node.author
                if not author:
                    continue

                profile = self._profiles[author]
                profile.author = author
                profile.total_comments += 1

                if len(profile.comment_samples) < 10:
                    profile.comment_samples.append(node.text)

                if node.label == 1:
                    profile.bullish_count += 1
                elif node.label == -1:
                    profile.bearish_count += 1
                else:
                    profile.neutral_count += 1

        logger.info("用户画像构建完成: %d 个独立用户", len(self._profiles))
        # Top 活跃用户
        top = sorted(self._profiles.values(), key=lambda p: p.total_comments, reverse=True)[:5]
        for p in top:
            logger.info("  %s: %d条评论, bias=%.2f, consistency=%.2f",
                        p.author, p.total_comments, p.stance_bias, p.consistency)

        return self

    def get(self, author: str) -> UserProfile:
        """获取用户画像，不存在时返回默认空画像。"""
        return self._profiles.get(author, UserProfile(author=author))
