"""统一日志配置。"""

import logging
import sys


def setup_logger(name: str = "sentiment_analysis", level: int = logging.INFO) -> logging.Logger:
    """创建并配置日志器。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    return logger


logger = setup_logger()
