"""
评论标签修复与数据分流工具。

读取解析后的 JSONL 文件，按 comment_error 类型将帖子分为 5 类，
并尝试修复各类错误（重新提取币种、时间戳、重试价格 API）。

用法:
    # 干跑：仅分类，不调用 API
    python data_collection/repair/repair_labels.py --dry-run

    # 全量修复
    python data_collection/repair/repair_labels.py

    # 指定文件和参数
    python data_collection/repair/repair_labels.py --input dataset/result/parsed_28.jsonl --delay 1.0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from data_collection.parsers.parse_article import (
    annotate_comment_blocks,
    extract_app_data,
    extract_comment_timestamp_map_from_app_data,
    extract_first_product_url_and_market,
    extract_products_from_html,
    extract_symbol_from_product_url,
    fetch_price_at,
    find_post_data_in_app_data,
    load_sidecar_comments,
    normalize_product_symbol,
    to_base_symbol,
)

# ═══════════════════════════════════════════════════════════════
# 加密币名称 → 符号字典（文本兜底提取用）
# ═══════════════════════════════════════════════════════════════
CRYPTO_NAME_TO_SYMBOL: Dict[str, str] = {
    # 英文名（按长度降序，避免子串冲突）
    "Bitcoin Cash": "BCH",
    "Ethereum Classic": "ETC",
    "Ethereum Name Service": "ENS",
    "NEAR Protocol": "NEAR",
    "Shiba Inu": "SHIB",
    "USD Coin": "USDC",
    "Binance Coin": "BNB",
    "Bitcoin": "BTC",
    "Ethereum": "ETH",
    "Solana": "SOL",
    "Ripple": "XRP",
    "Dogecoin": "DOGE",
    "Cardano": "ADA",
    "Avalanche": "AVAX",
    "Polkadot": "DOT",
    "Litecoin": "LTC",
    "Chainlink": "LINK",
    "Uniswap": "UNI",
    "Tether": "USDT",
    "TRON": "TRX",
    "Toncoin": "TON",
    "Polygon": "MATIC",
    "Arbitrum": "ARB",
    "Optimism": "OP",
    "Aptos": "APT",
    "Render": "RNDR",
    "Bittensor": "TAO",
    "Filecoin": "FIL",
    "Celestia": "TIA",
    "Injective": "INJ",
    "Maker": "MKR",
    "Aave": "AAVE",
    "Compound": "COMP",
    "Synthetix": "SNX",
    "Curve DAO": "CRV",
    "Cosmos": "ATOM",
    "Algorand": "ALGO",
    "Tezos": "XTZ",
    "Stellar": "XLM",
    "Monero": "XMR",
    "EOS": "EOS",
    "Theta": "THETA",
    "Hedera": "HBAR",
    "Flow": "FLOW",
    "Axie Infinity": "AXS",
    "Decentraland": "MANA",
    "The Sandbox": "SAND",
    "ApeCoin": "APE",
    "Chiliz": "CHZ",
    "Enjin": "ENJ",
    "Gala": "GALA",
    "Immutable": "IMX",
    "Loopring": "LRC",
    "dYdX": "DYDX",
    "1inch": "1INCH",
    "PancakeSwap": "CAKE",
    "Yearn Finance": "YFI",
    "Convex": "CVX",
    "Lido": "LDO",
    "Rocket Pool": "RPL",
    "Frax": "FRAX",
    "GMX": "GMX",
    "Balancer": "BAL",
    "SushiSwap": "SUSHI",
    "Pendle": "PENDLE",
    "Sei": "SEI",
    "Sui": "SUI",
    "Aptos": "APT",
    "Manta": "MANTA",
    "Starknet": "STRK",
    "zkSync": "ZK",
    "LayerZero": "ZRO",
    "EigenLayer": "EIGEN",
    "Hyperliquid": "HYPE",
    "EtherFi": "ETHFI",
    "Jito": "JTO",
    "Pyth": "PYTH",
    "Wormhole": "W",
    "Ethena": "ENA",
    "AltLayer": "ALT",
    "Dymension": "DYM",
    "Xai": "XAI",
    "ZetaChain": "ZETA",
    "Blast": "BLAST",
    "Mode": "MODE",
    "Cyber": "CYBER",
    # 中文名
    "比特币": "BTC",
    "以太坊": "ETH",
    "以太": "ETH",
    "索拉纳": "SOL",
    "瑞波": "XRP",
    "瑞波币": "XRP",
    "狗狗币": "DOGE",
    "莱特币": "LTC",
    "泰达币": "USDT",
    "币安币": "BNB",
    "波场": "TRX",
    "艾达币": "ADA",
    "雪崩": "AVAX",
    "波卡": "DOT",
    "链接": "LINK",
    "宇宙": "ATOM",
    "马蹄": "MATIC",
    "文件币": "FIL",
    "柴犬币": "SHIB",
    "门罗币": "XMR",
    "恒星币": "XLM",
    "柚子": "EOS",
    "大零币": "ZEC",
    "达世币": "DASH",
    # 常见 ticker（在文中可能出现）
    "BTC": "BTC",
    "ETH": "ETH",
    "SOL": "SOL",
    "XRP": "XRP",
    "BNB": "BNB",
    "DOGE": "DOGE",
    "ADA": "ADA",
    "DOT": "DOT",
    "LINK": "LINK",
    "UNI": "UNI",
    "AVAX": "AVAX",
    "MATIC": "MATIC",
    "ATOM": "ATOM",
    "LTC": "LTC",
    "TRX": "TRX",
    "TON": "TON",
    "ARB": "ARB",
    "OP": "OP",
    "APT": "APT",
    "FIL": "FIL",
    "NEAR": "NEAR",
    "AAVE": "AAVE",
    "MKR": "MKR",
    "SNX": "SNX",
    "CRV": "CRV",
    "COMP": "COMP",
    "ALGO": "ALGO",
    "XTZ": "XTZ",
    "XLM": "XLM",
    "XMR": "XMR",
    "EOS": "EOS",
    "THETA": "THETA",
    "HBAR": "HBAR",
    "FLOW": "FLOW",
    "AXS": "AXS",
    "MANA": "MANA",
    "SAND": "SAND",
    "APE": "APE",
    "CHZ": "CHZ",
    "GALA": "GALA",
    "SUI": "SUI",
    "SEI": "SEI",
    "INJ": "INJ",
    "TIA": "TIA",
    "RNDR": "RNDR",
    "TAO": "TAO",
    "PENDLE": "PENDLE",
    "GMX": "GMX",
    "LDO": "LDO",
    "DYDX": "DYDX",
    "CAKE": "CAKE",
    "SHIB": "SHIB",
    "FRAX": "FRAX",
    "BAL": "BAL",
    "YFI": "YFI",
    "1INCH": "1INCH",
    "ENJ": "ENJ",
    "IMX": "IMX",
    "LRC": "LRC",
    "CVX": "CVX",
    "RPL": "RPL",
    "SUSHI": "SUSHI",
    "MANTA": "MANTA",
    "STRK": "STRK",
    "ZRO": "ZRO",
    "HYPE": "HYPE",
    "ENA": "ENA",
    "JTO": "JTO",
    "PYTH": "PYTH",
    "ETHFI": "ETHFI",
    "EIGEN": "EIGEN",
    "ALT": "ALT",
    "DYM": "DYM",
    "XAI": "XAI",
    "ZETA": "ZETA",
}

# 按长度降序排列，防止 "Bitcoin Cash" 被 "Bitcoin" 先匹配
_NAME_MATCH_ORDER = sorted(CRYPTO_NAME_TO_SYMBOL.keys(), key=len, reverse=True)


# ═══════════════════════════════════════════════════════════════
# 数据加载与分流
# ═══════════════════════════════════════════════════════════════

def load_jsonl_files(file_paths: List[Path]) -> List[Dict[str, Any]]:
    """读取所有 JSONL 文件，返回帖子列表。"""
    posts: List[Dict[str, Any]] = []
    for fp in file_paths:
        if not fp.exists():
            print(f"  [警告] 文件不存在，跳过: {fp}")
            continue
        count = 0
        with open(fp, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    posts.append(json.loads(line))
                    count += 1
                except json.JSONDecodeError as e:
                    print(f"  [警告] JSON 解析失败 ({fp.name}): {e}")
        print(f"  从 {fp.name} 加载 {count} 条帖子")
    return posts


def iter_comments(comments: List[Dict[str, Any]]):
    """递归遍历所有评论节点。"""
    for c in comments:
        yield c
        yield from iter_comments(c.get("replies", []))


def categorize_post(post: Dict[str, Any]) -> str:
    """返回帖子分类: normal | missing_symbol | missing_comment_timestamp | price_unavailable | fallback_post_time"""
    # 1) 帖子级 missing_symbol
    if post.get("label_error") == "missing_symbol":
        return "missing_symbol"

    # 遍历所有评论
    has_price_err = False
    has_ts_err = False
    has_fallback = False

    for c in iter_comments(post.get("comments", [])):
        err = c.get("comment_error", "")
        if not err:
            continue
        if err.startswith("price_api_error:") or err == "no_kline_data" or err.startswith("invalid_"):
            has_price_err = True
        elif err == "missing_comment_timestamp":
            has_ts_err = True
        elif err == "fallback_post_time":
            has_fallback = True

    if has_price_err:
        return "price_unavailable"
    if has_ts_err:
        return "missing_comment_timestamp"
    if has_fallback:
        return "fallback_post_time"
    return "normal"


def resolve_source_path(source_file: str) -> Optional[Path]:
    """将 JSONL 中的 source_file 解析为绝对路径。"""
    p = Path(source_file)
    if p.is_absolute() and p.exists():
        return p
    # 相对路径，拼接项目根目录
    absolute = _PROJECT_ROOT / p
    if absolute.exists():
        return absolute
    return None


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _parse_t0_to_ms(t0_str: str) -> int:
    """将可读 t0 字符串转回毫秒时间戳。"""
    if not t0_str:
        return 0
    try:
        dt = datetime.strptime(t0_str, "%Y-%m-%d %H:%M:%S")
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


def _extract_symbol_from_url(url: str) -> str:
    """从交易 URL 中提取交易对（如 ETHUSDT）。"""
    if not url:
        return ""
    from urllib.parse import urlsplit
    path = urlsplit(url).path
    parts = [p for p in path.split("/") if p]
    if parts:
        raw = parts[-1]
        return re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return ""


def _build_product_url(symbol: str, post_id: str = "", market_type: str = "spot") -> str:
    """根据基础币种构造 Binance 交易 URL。"""
    normalized = normalize_product_symbol(symbol)
    if not normalized:
        return ""
    if not normalized.endswith("USDT"):
        pair = f"{normalized}_USDT"
    else:
        pair = f"{normalized[:-4]}_{normalized[-4:]}"
    base = "https://www.binance.com/zh-CN"
    if market_type == "futures":
        url = f"{base}/futures/{pair}"
    else:
        url = f"{base}/trade/{pair}"
    if post_id:
        url += f"?contentId={post_id}"
    return url


def _build_symbol_for_label(first_product: str) -> str:
    """从基础币种构造交易对符号（如 BTC -> BTCUSDT）。"""
    fallback = normalize_product_symbol(first_product)
    if fallback:
        if fallback.endswith("USDT"):
            return fallback
        return f"{fallback}USDT"
    return ""


# ═══════════════════════════════════════════════════════════════
# 文本兜底：从 post_content 提取币种
# ═══════════════════════════════════════════════════════════════

# 既是币种 ticker 又是常见英文词，需要上下文才能确认
_TICKER_COMMON_WORDS = {
    "LINK", "OP", "SAND", "FLOW", "MODE", "ALT", "BLAST", "W",
    "ZRO", "ENA", "JTO", "PYTH",
}


def extract_symbols_from_text(text: str) -> List[str]:
    """从纯文本中提取币种（不依赖 HTML），返回去重的 base symbol 列表。"""
    found: List[str] = []
    seen: set[str] = set()

    # 策略 1：$SYMBOL 格式（最可靠）
    for m in re.finditer(r"\$([A-Za-z][A-Za-z0-9]{1,14})\b", text):
        sym = m.group(1).upper()
        base = to_base_symbol(sym)
        if base and base not in seen:
            seen.add(base)
            found.append(base)

    # 策略 2：已知币名匹配（按长度降序，避免子串冲突）
    for name in _NAME_MATCH_ORDER:
        if name in text:
            sym = CRYPTO_NAME_TO_SYMBOL[name]
            base = to_base_symbol(sym)
            if base and base not in seen:
                seen.add(base)
                found.append(base)

    # 策略 3：独立大写 ticker，仅当有加密上下文时才采信
    crypto_context_words = {
        "币", "币种", "代币", "加密货币", "数字货币", "虚拟货币",
        "token", "Token", "crypto", "Crypto", "coin", "Coin",
        "空投", "挖矿", "质押", "链", "主网", "合约", "交易对",
        "BTC", "ETH", "USDT",  # 文中已有明确币种时说明在讨论加密
    }
    has_crypto_context = any(w in text for w in crypto_context_words)

    for m in re.finditer(r"\b([A-Z]{2,10})\b", text):
        ticker = m.group(1)
        # 常见英文词且无加密上下文时跳过
        if ticker in _TICKER_COMMON_WORDS and not has_crypto_context:
            continue
        if ticker in CRYPTO_NAME_TO_SYMBOL:
            base = to_base_symbol(ticker)
            if base and base not in seen:
                seen.add(base)
                found.append(base)

    return found


def select_primary_symbol(symbols: List[str]) -> Optional[str]:
    """从多个币种中选主币种，优先 BTC/ETH。"""
    if not symbols:
        return None
    for priority in ("BTC", "ETH"):
        if priority in symbols:
            return priority
    return symbols[0]


# ═══════════════════════════════════════════════════════════════
# 价格获取：重试 + 时间偏移
# ═══════════════════════════════════════════════════════════════

def _is_transient_error(err: str) -> bool:
    """判断是否为瞬时网络错误（可重试）。
    ReadTimeout: 服务端响应慢，重试可能成功
    SSLError: TLS 握手偶发失败，重试可能成功
    ConnectTimeout: 网络不通（没挂 VPN），重试无意义
    """
    if not err:
        return False
    err_lower = err.lower()
    # 连接超时不重试（说明网络不通，不是瞬时错误）
    if "connecttimeout" in err_lower or "connect timed out" in err_lower:
        return False
    # 读取超时可重试
    if "read timed out" in err_lower or "readtimeout" in err_lower:
        return True
    # SSL 偶发失败可重试
    if "ssl" in err_lower or "ssleof" in err_lower:
        return True
    return False


def fetch_price_with_retry(
    symbol: str,
    market_type: str,
    ts_ms: int,
    interval: str,
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> Tuple[Optional[float], str]:
    """带指数退避重试的价格获取。"""
    price, err = fetch_price_at(symbol, market_type, ts_ms, interval)

    if price is not None:
        return price, err

    if not _is_transient_error(err):
        return price, err

    for attempt in range(1, max_retries + 1):
        delay = base_delay * (2 ** (attempt - 1))
        print(f"    重试 {attempt}/{max_retries}（{delay:.0f}s 后）: {err[:80]}")
        time.sleep(delay)
        price, err = fetch_price_at(symbol, market_type, ts_ms, interval)
        if price is not None:
            return price, err

    return None, err


def fetch_price_with_time_adjustment(
    symbol: str,
    market_type: str,
    original_ts_ms: int,
    interval: str,
) -> Tuple[Optional[float], str]:
    """对 no_kline_data 尝试 ±1h、±2h 时间偏移。"""
    for offset_h in (1, -1, 2, -2, 4, -4):
        shifted = original_ts_ms + offset_h * 3600 * 1000
        price, err = fetch_price_at(symbol, market_type, shifted, interval)
        if price is not None:
            return price, ""
    return None, "no_kline_data"


# ═══════════════════════════════════════════════════════════════
# 修复函数
# ═══════════════════════════════════════════════════════════════

def _re_extract_from_html(
    post: Dict[str, Any],
) -> Optional[Tuple[str, List[str], str, str, str, Dict[str, int], int]]:
    """
    从源 HTML 重新提取数据。
    返回 (html_content, products, first_product_url, market_type, symbol_for_label, timestamp_map, fallback_t0_ms)
    或 None（源文件不存在或解析失败）。
    """
    source_file = post.get("source_file", "")
    html_path = resolve_source_path(source_file)
    if not html_path:
        return None

    try:
        html_content = html_path.read_text(encoding="utf-8")
    except Exception:
        return None

    post_id = str(post.get("post_id", ""))
    post_content = str(post.get("post_content", ""))

    # 提取 APP_DATA
    app_data = extract_app_data(html_content)
    post_data = find_post_data_in_app_data(app_data, post_id) if app_data else {}

    # 重新提取币种
    products = extract_products_from_html(
        html_content=html_content,
        post_id=post_id,
        post_content=post_content,
        post_data=post_data,
    )
    first_product = products[0] if products else ""

    # 重新提取产品 URL
    first_product_url, market_type = extract_first_product_url_and_market(html_content, post_id)

    # 构建交易对
    symbol_for_label = extract_symbol_from_product_url(first_product_url, first_product)

    # 重新提取时间戳（sidecar 优先）
    comment_timestamp_map: Dict[str, int] = {}
    sidecar_comments, sidecar_ts_map = load_sidecar_comments(html_path)
    if sidecar_ts_map:
        comment_timestamp_map = sidecar_ts_map
    elif app_data:
        comment_timestamp_map = extract_comment_timestamp_map_from_app_data(app_data)

    # fallback_t0_ms
    post_time_ms = int(post.get("post_time_ms", 0) or 0)
    fallback_t0_ms = post_time_ms if post_time_ms > 0 else 0

    return (
        html_content,
        products,
        first_product_url,
        market_type,
        symbol_for_label,
        comment_timestamp_map,
        fallback_t0_ms,
    )


def repair_post_symbol(
    post: Dict[str, Any],
    t_window_hours: int,
    price_interval: str,
    delay: float,
) -> Dict[str, Any]:
    """修复 missing_symbol：重读 HTML 提取币种，文本提取兜底。"""
    post_id = str(post.get("post_id", ""))
    comments = post.get("comments", [])
    if not comments:
        return post

    # 策略 1：从源 HTML 重新提取
    extracted = _re_extract_from_html(post)
    if extracted:
        (_, products, first_product_url, market_type, symbol_for_label, ts_map, fallback) = extracted
        if symbol_for_label:
            result = dict(post)
            result["products"] = products
            result["first_product"] = products[0] if products else ""
            result["first_product_url"] = first_product_url
            result["market_type"] = market_type or "spot"
            label_error = annotate_comment_blocks(
                comments=result["comments"],
                timestamp_map=ts_map,
                symbol=symbol_for_label,
                market_type=result["market_type"],
                t_window_hours=t_window_hours,
                price_interval=price_interval,
                fallback_t0_ms=fallback,
            )
            result["label_error"] = label_error
            if not label_error:
                print(f"  [missing_symbol] 帖子 {post_id}: 从 HTML 修复成功 → {symbol_for_label}")
                return result

    # 策略 2：从 post_content 文本提取
    text = str(post.get("post_content", ""))
    symbols = extract_symbols_from_text(text)
    primary = select_primary_symbol(symbols)
    if not primary:
        print(f"  [missing_symbol] 帖子 {post_id}: 无法提取币种（文本中也找不到）")
        return post

    symbol_for_label = _build_symbol_for_label(primary)
    if not symbol_for_label:
        return post

    result = dict(post)
    result["products"] = [primary]
    result["first_product"] = primary
    result["first_product_url"] = _build_product_url(primary, post_id)
    result["market_type"] = "spot"

    # 构建时间戳映射（从现有评论中提取 t0）
    ts_map: Dict[str, int] = {}
    for c in iter_comments(result["comments"]):
        cid = str(c.get("original_comment_id") or c.get("comment_id") or "")
        t0_str = c.get("t0") or c.get("post_time") or ""
        t0_ms = _parse_t0_to_ms(t0_str)
        if cid and t0_ms > 0:
            ts_map[cid] = t0_ms

    label_error = annotate_comment_blocks(
        comments=result["comments"],
        timestamp_map=ts_map,
        symbol=symbol_for_label,
        market_type="spot",
        t_window_hours=t_window_hours,
        price_interval=price_interval,
        fallback_t0_ms=int(post.get("post_time_ms", 0) or 0),
    )
    result["label_error"] = label_error
    if not label_error:
        print(f"  [missing_symbol] 帖子 {post_id}: 从文本提取修复成功 → {symbol_for_label}")
    else:
        print(f"  [missing_symbol] 帖子 {post_id}: 文本提取到 {symbol_for_label} 但标注仍失败")
    return result


def repair_post_price(
    post: Dict[str, Any],
    t_window_hours: int,
    price_interval: str,
    max_retries: int,
    delay: float,
) -> Dict[str, Any]:
    """修复 price_unavailable：网络重试 + URL 修正。"""
    post_id = str(post.get("post_id", ""))
    comments = post.get("comments", [])
    if not comments:
        return post

    symbol_for_label = _extract_symbol_from_url(post.get("first_product_url", ""))
    if not symbol_for_label:
        symbol_for_label = _build_symbol_for_label(post.get("first_product", ""))
    market_type = str(post.get("market_type", "spot") or "spot")

    result = dict(post)
    repaired_count = 0
    still_failed = 0

    def fix_comment_block(comment: Dict[str, Any], t_window: int, interval: str):
        nonlocal repaired_count, still_failed
        err = comment.get("comment_error", "")
        if not err:
            return  # 已经是正常的

        # 收集该评论块所有 ID 的时间戳
        ids: List[str] = []

        def collect_ids(node: Dict[str, Any], bucket: List[str]):
            cid = str(node.get("original_comment_id") or node.get("comment_id") or "")
            if cid:
                bucket.append(cid)
            for rep in node.get("replies", []):
                collect_ids(rep, bucket)

        collect_ids(comment, ids)

        # 从现有 t0 字段解析时间戳
        t0_str = comment.get("t0", "")
        t0_ms = _parse_t0_to_ms(t0_str)
        if t0_ms <= 0:
            still_failed += 1
            return

        t1_ms = t0_ms + int(t_window) * 3600 * 1000
        time.sleep(delay)

        # 获取 p0
        if err == "no_kline_data":
            p0, err0 = fetch_price_with_time_adjustment(symbol_for_label, market_type, t0_ms, interval)
        else:
            p0, err0 = fetch_price_with_retry(symbol_for_label, market_type, t0_ms, interval, max_retries)

        time.sleep(delay)

        # 获取 p1
        if err == "no_kline_data":
            p1, err1 = fetch_price_with_time_adjustment(symbol_for_label, market_type, t1_ms, interval)
        else:
            p1, err1 = fetch_price_with_retry(symbol_for_label, market_type, t1_ms, interval, max_retries)

        if p0 is not None and p1 is not None:
            comment["p0"] = p0
            comment["p1"] = p1
            comment["label"] = 1 if p1 > p0 else -1
            comment["comment_error"] = ""
            repaired_count += 1
        else:
            comment["p0"] = p0
            comment["p1"] = p1
            comment["comment_error"] = err0 or err1 or "price_unavailable"
            still_failed += 1

    for c in comments:
        fix_comment_block(c, t_window_hours, price_interval)
        for reply in iter_comments(c.get("replies", [])):
            fix_comment_block(reply, t_window_hours, price_interval)

    if repaired_count > 0:
        print(f"  [price_unavailable] 帖子 {post_id}: 修复 {repaired_count} 条，仍失败 {still_failed} 条")

    # 如果全部修复了，检查是否需要更新 label_error（但 price_unavailable 本来就没有 post-level error）
    return result


def repair_post_timestamp(
    post: Dict[str, Any],
    t_window_hours: int,
    price_interval: str,
    delay: float,
) -> Dict[str, Any]:
    """修复 missing_comment_timestamp / fallback_post_time：从源文件重新提取时间戳。"""
    post_id = str(post.get("post_id", ""))
    comments = post.get("comments", [])
    if not comments:
        return post

    symbol_for_label = _extract_symbol_from_url(post.get("first_product_url", ""))
    if not symbol_for_label:
        symbol_for_label = _build_symbol_for_label(post.get("first_product", ""))
    if not symbol_for_label:
        print(f"  [timestamp] 帖子 {post_id}: 无 symbol，无法修复时间戳错误")
        return post

    market_type = str(post.get("market_type", "spot") or "spot")

    # 尝试从源文件重新提取时间戳
    extracted = _re_extract_from_html(post)
    if extracted and extracted[5]:
        ts_map = extracted[5]
        fallback = extracted[6]
        print(f"  [timestamp] 帖子 {post_id}: 从源文件重新提取到 {len(ts_map)} 个时间戳")
    else:
        # 兜底：用帖子时间
        ts_map = {}
        fallback = post.get("post_time_ms", 0) or 0
        print(f"  [timestamp] 帖子 {post_id}: 无法从源文件提取时间戳，用帖子时间兜底")

    result = dict(post)
    label_error = annotate_comment_blocks(
        comments=result["comments"],
        timestamp_map=ts_map,
        symbol=symbol_for_label,
        market_type=market_type,
        t_window_hours=t_window_hours,
        price_interval=price_interval,
        fallback_t0_ms=fallback,
    )
    result["label_error"] = label_error

    # 统计修复结果
    fixed = sum(1 for c in iter_comments(result["comments"]) if not c.get("comment_error"))
    print(f"  [timestamp] 帖子 {post_id}: 重新标注完成，{fixed} 条评论无错误")

    return result


# ═══════════════════════════════════════════════════════════════
# 写入与报告
# ═══════════════════════════════════════════════════════════════

def write_jsonl(posts: List[Dict[str, Any]], path: Path) -> None:
    """将帖子列表写入 JSONL 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for post in posts:
            fh.write(json.dumps(post, ensure_ascii=False) + "\n")


def count_all_comments(comments: List[Dict[str, Any]]) -> int:
    """递归统计评论总数。"""
    total = 0
    for c in comments:
        total += 1
        total += count_all_comments(c.get("replies", []))
    return total


def count_errors(comments: List[Dict[str, Any]]) -> Counter:
    """统计评论中各种 comment_error 的数量。"""
    c = Counter()
    for comment in iter_comments(comments):
        err = comment.get("comment_error", "")
        if err:
            c[err] += 1
        else:
            c["(ok)"] += 1
    return c


def generate_report(
    categories: Dict[str, List[Dict[str, Any]]],
    output_dir: Path,
) -> Dict[str, Any]:
    """生成修复报告。"""
    report: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "categorization": {},
        "totals": {},
    }

    total_posts = 0
    total_comments = 0
    total_ok_comments = 0
    total_err_comments = 0

    for cat_name, posts in categories.items():
        n_posts = len(posts)
        n_comments = sum(count_all_comments(p.get("comments", [])) for p in posts)
        err_counter = Counter()
        for p in posts:
            err_counter.update(count_errors(p.get("comments", [])))
        n_ok = err_counter.get("(ok)", 0)
        n_err = sum(v for k, v in err_counter.items() if k != "(ok)")

        report["categorization"][cat_name] = {
            "posts": n_posts,
            "comments": n_comments,
            "ok_comments": n_ok,
            "error_comments": n_err,
            "error_breakdown": {k: v for k, v in err_counter.most_common() if k != "(ok)"},
        }
        total_posts += n_posts
        total_comments += n_comments
        total_ok_comments += n_ok
        total_err_comments += n_err

    report["totals"] = {
        "posts": total_posts,
        "comments": total_comments,
        "ok_comments": total_ok_comments,
        "error_comments": total_err_comments,
    }

    return report


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="评论标签修复与数据分流工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        nargs="+",
        default=None,
        help="输入 JSONL 文件（默认: dataset/result/*.jsonl）",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_PROJECT_ROOT / "dataset" / "repair"),
        help="输出目录（默认: dataset/repair）",
    )
    parser.add_argument(
        "--t-window-hours",
        type=int,
        default=24,
        help="价格窗口（小时，默认 24）",
    )
    parser.add_argument(
        "--price-interval",
        default="1h",
        help="K线间隔（默认 1h）",
    )
    parser.add_argument(
        "--retry-count",
        type=int,
        default=3,
        help="API 重试次数（默认 3）",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="API 调用间隔秒数（默认 0.5）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅分类不修复，不调用任何 API",
    )
    args = parser.parse_args()

    # 确定输入文件
    if args.input:
        input_files = [Path(p) for p in args.input]
    else:
        result_dir = _PROJECT_ROOT / "dataset" / "result"
        all_jsonl = sorted(result_dir.glob("*.jsonl"))
        # 排除金色财经数据（无 comment_error 标注系统）
        input_files = [f for f in all_jsonl if "jinse" not in f.name.lower()]
        if not input_files:
            input_files = all_jsonl

    if not input_files:
        print("错误：未找到输入文件")
        sys.exit(1)

    print(f"输入文件 ({len(input_files)} 个):")
    for fp in input_files:
        print(f"  - {fp}")

    # 加载数据
    print("\n加载数据...")
    all_posts = load_jsonl_files(input_files)
    if not all_posts:
        print("错误：未加载到任何帖子")
        sys.exit(1)
    print(f"共加载 {len(all_posts)} 条帖子")

    # 分流
    print("\n分类中...")
    categories: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for post in all_posts:
        cat = categorize_post(post)
        categories[cat].append(post)

    cat_names = ["normal", "missing_symbol", "missing_comment_timestamp", "price_unavailable", "fallback_post_time"]
    print("\n分类结果:")
    for name in cat_names:
        posts = categories.get(name, [])
        n_comments = sum(count_all_comments(p.get("comments", [])) for p in posts)
        print(f"  {name}: {len(posts)} 条帖子, {n_comments} 条评论")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        print("\n[干跑模式] 跳过修复，仅写入分类结果...")
        for name in cat_names:
            posts = categories.get(name, [])
            if posts:
                write_jsonl(posts, output_dir / f"{name}.jsonl")
                print(f"  写入 {name}.jsonl: {len(posts)} 条帖子")
        report = generate_report(categories, output_dir)
        report_path = output_dir / "repair_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入: {report_path}")
        print("干跑完成。请检查分类结果，确认后去掉 --dry-run 运行实际修复。")
        return

    # 修复
    print("\n开始修复...")
    repaired: Dict[str, List[Dict[str, Any]]] = {}

    # normal: 无需修复
    repaired["normal"] = categories.get("normal", [])
    print(f"  normal: {len(repaired['normal'])} 条（无需修复）")

    # missing_symbol: 重读 HTML 提取币种
    missing_symbol_posts = categories.get("missing_symbol", [])
    print(f"\n  修复 missing_symbol ({len(missing_symbol_posts)} 条)...")
    repaired_symbol = []
    for i, post in enumerate(missing_symbol_posts):
        if (i + 1) % 50 == 0:
            print(f"    进度: {i + 1}/{len(missing_symbol_posts)}")
        repaired_symbol.append(
            repair_post_symbol(post, args.t_window_hours, args.price_interval, args.delay)
        )
    repaired["missing_symbol"] = repaired_symbol

    # price_unavailable: 网络重试 / URL 修正
    price_posts = categories.get("price_unavailable", [])
    print(f"\n  修复 price_unavailable ({len(price_posts)} 条)...")
    repaired_price = []
    for i, post in enumerate(price_posts):
        if (i + 1) % 20 == 0:
            print(f"    进度: {i + 1}/{len(price_posts)}")
        repaired_price.append(
            repair_post_price(post, args.t_window_hours, args.price_interval, args.retry_count, args.delay)
        )
    repaired["price_unavailable"] = repaired_price

    # missing_comment_timestamp: 重新提取时间戳
    ts_posts = categories.get("missing_comment_timestamp", [])
    print(f"\n  修复 missing_comment_timestamp ({len(ts_posts)} 条)...")
    repaired_ts = []
    for post in ts_posts:
        repaired_ts.append(
            repair_post_timestamp(post, args.t_window_hours, args.price_interval, args.delay)
        )
    repaired["missing_comment_timestamp"] = repaired_ts

    # fallback_post_time: 重新提取时间戳
    fallback_posts = categories.get("fallback_post_time", [])
    print(f"\n  修复 fallback_post_time ({len(fallback_posts)} 条)...")
    repaired_fallback = []
    for post in fallback_posts:
        repaired_fallback.append(
            repair_post_timestamp(post, args.t_window_hours, args.price_interval, args.delay)
        )
    repaired["fallback_post_time"] = repaired_fallback

    # 写入输出
    print("\n写入输出文件...")
    for name in cat_names:
        posts = repaired.get(name, [])
        if posts:
            write_jsonl(posts, output_dir / f"{name}.jsonl")
            print(f"  {name}.jsonl: {len(posts)} 条帖子")

    # 生成报告
    report = generate_report(repaired, output_dir)
    report_path = output_dir / "repair_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入: {report_path}")

    # 汇总
    original_err = sum(
        count_all_comments(p.get("comments", []))
        for cat in cat_names if cat != "normal"
        for p in categories.get(cat, [])
    )
    remaining_err = sum(
        count_all_comments(p.get("comments", []))
        for cat in cat_names if cat != "normal"
        for p in repaired.get(cat, [])
    )
    print(f"\n完成。修复前 {original_err} 条错误评论，具体变化详见报告。")


if __name__ == "__main__":
    main()
