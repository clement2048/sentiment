#!/usr/bin/env python3
"""
parse_article.py — 解析 Binance 官方新闻（Article）HTML

专门处理官方新闻 Article 页面，与用户 Post 区分：
- 时间格式为 "1h", "2d" 等相对时间
- 避免误将相关文章当作评论
- 增强币种提取准确度

用法:
  python parse_article.py --input crawler_coin_output/html_pages/315747499824338.html
  python parse_article.py --batch --input crawler_coin_output/html_pages --output update_news/parsed_articles.json
"""

from __future__ import annotations

import json
import re
import html
import argparse
from urllib.parse import urljoin, urlsplit, parse_qs
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import sys
import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRICE_CACHE_DIR = PROJECT_ROOT / "dataset" / "cache" / "price_klines"


# ---------------------------------------------------------------------------
# 时间处理（Article 特有）
# ---------------------------------------------------------------------------

def relative_time_to_absolute(rel: str, base_ts_ms: int) -> int:
    """将相对时间字符串转换为绝对时间戳（毫秒）。

    支持: "1h", "2d", "1d ago", "5 minutes ago", "just now"

    base_ts_ms: 基准时间戳（如文章发布时间）
    """
    if not rel:
        return 0

    rel_lower = rel.lower().strip()

    # "just now" -> 现在
    if rel_lower in {"just now", "刚刚", "now"}:
        return base_ts_ms

    # 移除 "ago" 等后缀
    rel_lower = rel_lower.replace(" ago", "").replace("前", "")

    # 单位映射
    unit_mapping: List[tuple[str, int]] = [
        ("s", 1), ("sec", 1), ("second", 1),
        ("m", 60), ("min", 60), ("minute", 60),
        ("h", 3600), ("hour", 3600),
        ("d", 86400), ("day", 86400),
        ("w", 604800), ("week", 604800),
        ("mon", 2592000), ("month", 2592000),
    ]

    # 匹配数字和单位
    match = re.search(r'(\d+)\s*([a-z]+)', rel_lower)
    if not match:
        # 尝试纯数字（小时）
        if rel_lower.isdigit():
            hours = int(rel_lower)
            return base_ts_ms - hours * 3600 * 1000
        # 无法解析
        return 0

    num = int(match.group(1))
    unit_str = match.group(2)

    # 查找单位
    for unit, multiplier in unit_mapping:
        if unit_str.startswith(unit):
            seconds_ago = num * multiplier
            return base_ts_ms - seconds_ago * 1000

    return base_ts_ms  # 无法解析则用基时间


def parse_datetime_absolute_or_relative(
    datetime_str: Any,
    post_ts_ms: int = 0,
) -> tuple[str, int]:
    """解析时间字符串，支持绝对/相对格式，返回 (可读时间字符串, 毫秒时间戳)。"""
    if not datetime_str:
        return "", 0

    dt_str = str(datetime_str).strip()

    # 尝试解析为绝对时间（时间戳或 ISO 格式）
    try:
        if dt_str.isdigit():
            ts_int = int(dt_str)
            if ts_int == 0:
                return "", 0
            elif ts_int > 10**12:  # 毫秒级
                ms = ts_int
            else:  # 秒级
                ms = ts_int * 1000
            dt = datetime.fromtimestamp(ms / 1000)
            return dt.strftime("%Y-%m-%d %H:%M:%S"), ms
        else:
            # 尝试解析 ISO 8601 格式
            cleaned_dt = re.sub(r'\\.\\d+', '', dt_str)  # 移除毫秒
            cleaned_dt = cleaned_dt.replace('Z', '')  # 移除Z时区标记
            dt = datetime.fromisoformat(cleaned_dt)
            ms = int(dt.timestamp() * 1000)
            return dt.strftime("%Y-%m-%d %H:%M:%S"), ms
    except (ValueError, TypeError):
        # 尝试作为相对时间解析
        if post_ts_ms > 0:
            ts_ms = relative_time_to_absolute(dt_str, post_ts_ms)
            if ts_ms > 0:
                dt = datetime.fromtimestamp(ts_ms / 1000)
                return dt.strftime("%Y-%m-%d %H:%M:%S"), ts_ms

    # 解析失败，返回原始字符串和0时间戳
    return dt_str, 0


# ---------------------------------------------------------------------------
# 基础解析函数（复用原解析器）
# ---------------------------------------------------------------------------

def extract_app_data(html_content: str) -> Optional[Dict[str, Any]]:
    """提取__APP_DATA中的JSON数据"""
    pattern = r'<script[^>]*id="__APP_DATA"[^>]*type="application/json"[^>]*>(.*?)</script>'
    match = re.search(pattern, html_content, re.DOTALL)

    if match:
        json_str = match.group(1).strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # 尝试清理
            json_str = re.split(r'</script>', json_str)[0].strip()
            try:
                return json.loads(json_str)
            except:
                return None
    return None


def extract_json_ld_data(html_content: str) -> Dict[str, Any]:
    """提取JSON-LD结构化数据（schema.org）"""
    ld_data = {}

    # 查找JSON-LD脚本
    pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    matches = re.findall(pattern, html_content, re.DOTALL | re.IGNORECASE)

    for json_ld_str in matches:
        try:
            data = json.loads(json_ld_str.strip())
            if isinstance(data, dict):
                # 检查是否是DiscussionForumPosting或NewsArticle类型
                if data.get("@type") in {"DiscussionForumPosting", "NewsArticle"}:
                    # 提取关键信息
                    if "headline" in data:
                        ld_data["headline"] = html.unescape(data["headline"])
                    if "text" in data:
                        ld_data["full_text"] = html.unescape(data["text"])
                    if "datePublished" in data:
                        ld_data["date_published"] = data["datePublished"]
                    if "url" in data:
                        ld_data["url"] = data["url"]

                    # 提取作者信息
                    if "author" in data and isinstance(data["author"], dict):
                        author = data["author"]
                        if "name" in author:
                            ld_data["author_name"] = html.unescape(author["name"])
                        if "url" in author:
                            ld_data["author_url"] = author["url"]

                    # 提取互动统计
                    if "interactionStatistic" in data and isinstance(data["interactionStatistic"], dict):
                        interaction = data["interactionStatistic"]
                        if "userInteractionCount" in interaction:
                            ld_data["like_count"] = interaction["userInteractionCount"]

        except json.JSONDecodeError:
            continue

    return ld_data


def extract_from_meta_tags(html_content: str) -> Dict[str, str]:
    """从HTML meta标签提取信息"""
    meta_info = {}

    # 首先提取JSON-LD结构化数据（优先级更高）
    ld_data = extract_json_ld_data(html_content)

    # 优先使用JSON-LD数据
    if "headline" in ld_data:
        meta_info["title"] = ld_data["headline"]
    if "full_text" in ld_data:
        meta_info["full_content"] = ld_data["full_text"]
    if "author_name" in ld_data:
        meta_info["author"] = ld_data["author_name"]
    if "date_published" in ld_data:
        meta_info["published_date"] = ld_data["date_published"]
    if "like_count" in ld_data:
        meta_info["likes"] = str(ld_data["like_count"])

    # 提取OG标签
    og_patterns = {
        "title": r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']*)["\'][^>]*>',
        "description": r'<meta[^>]*property=["\']og:description["\'][^>]*content=["\']([^"\']*)["\'][^>]*>',
        "url": r'<meta[^>]*property=["\']og:url["\'][^>]*content=["\']([^"\']*)["\'][^>]*>',
    }

    for key, pattern in og_patterns.items():
        match = re.search(pattern, html_content, re.IGNORECASE)
        if match:
            # 如果JSON-LD中已有数据，则不要覆盖
            if key not in meta_info:
                meta_info[key] = html.unescape(match.group(1)).strip()

    # 从title标签提取标题
    if "title" not in meta_info:
        title_match = re.search(r'<title[^>]*>([^<]*)</title>', html_content, re.IGNORECASE)
        if title_match:
            meta_info["title"] = html.unescape(title_match.group(1)).split("|")[0].strip()

    # 生成完整的帖子内容
    # 优先使用JSON-LD的完整文本，否则使用headline + description的组合
    if "full_content" in meta_info:
        meta_info["description"] = meta_info["full_content"]
    elif "title" in meta_info and "description" in meta_info:
        # 合并标题和描述
        meta_info["description"] = f"{meta_info['title']}\n\n{meta_info['description']}"

    return meta_info


def normalize_product_symbol(raw: str) -> str:
    """规范化币种符号，返回空字符串表示无效。"""
    symbol = (raw or "").strip().upper().replace("$", "")
    symbol = re.sub(r"[^A-Z0-9]", "", symbol)
    if re.fullmatch(r"[A-Z][A-Z0-9]{1,9}", symbol):
        return symbol
    return ""


def to_base_symbol(symbol: str) -> str:
    """将交易对归一化为基础币种（如 RAVEUSDT -> RAVE）。"""
    normalized = normalize_product_symbol(symbol)
    if not normalized:
        return ""

    quote_suffixes = [
        "USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USDP", "DAI",
        "TRY", "EUR", "BRL", "RUB", "UAH", "BIDR",
    ]
    for suffix in quote_suffixes:
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 1:
            candidate = normalized[: -len(suffix)]
            candidate = normalize_product_symbol(candidate)
            if candidate:
                return candidate

    return normalized


def extract_products_from_post_data(post_data: Dict[str, Any]) -> List[str]:
    """仅从当前帖子对象中提取币种候选。"""
    products: List[str] = []
    seen: set[str] = set()

    def add_product(raw: Any) -> None:
        if not isinstance(raw, str):
            return
        symbol = to_base_symbol(raw)
        if symbol and symbol not in seen:
            seen.add(symbol)
            products.append(symbol)

    def walk(data: Any, depth: int = 0) -> None:
        if depth > 8:
            return

        if isinstance(data, dict):
            symbol_keys = {
                "symbol", "symbols", "relatedSymbol", "relatedSymbols",
                "coin", "coins", "tag", "tags", "keyword", "keywords",
                "product", "products",
            }
            for key, value in data.items():
                if key in symbol_keys:
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict):
                                add_product(item.get("symbol") or item.get("code") or item.get("name") or "")
                            else:
                                add_product(str(item))
                    elif isinstance(value, dict):
                        add_product(value.get("symbol") or value.get("code") or value.get("name") or "")
                    elif isinstance(value, str):
                        for token in re.split(r"[,|/\s]+", value):
                            add_product(token)
                walk(value, depth + 1)
        elif isinstance(data, list):
            for item in data:
                walk(item, depth + 1)

    walk(post_data)
    return products


def extract_products_from_html(
    html_content: str,
    post_id: str,
    post_content: str,
    post_data: Dict[str, Any],
) -> List[str]:
    """从当前帖子范围提取币种，避免全页噪音。"""
    products: List[str] = []
    seen: set[str] = set()

    def add_product(raw: Any) -> None:
        if not isinstance(raw, str):
            return
        symbol = to_base_symbol(raw)
        if symbol and symbol not in seen:
            seen.add(symbol)
            products.append(symbol)

    # 1) 当前帖片段中的交易链接（通过 contentId 锚定当前帖子，最可靠）
    link_pattern = rf'href=["\'][^"\']*/(?:futures|trade|price)/([^"\'/?#]+)\?[^"\']*contentId={re.escape(post_id)}[^"\']*["\']'
    for slug in re.findall(link_pattern, html_content, re.IGNORECASE):
        add_product(slug)

    # 2) 与当前帖子关联的 symbol span（内容中的产品卡片）
    span_pattern = rf'contentId={re.escape(post_id)}[\s\S]{{0,400}}?<span[^>]*class="symbol"[^>]*>([^<]+)</span>'
    for symbol_text in re.findall(span_pattern, html_content, re.IGNORECASE):
        add_product(symbol_text)

    # 3) 当前帖对象内的符号字段
    for token in extract_products_from_post_data(post_data):
        add_product(token)

    # 4) 兜底：若通过链接/卡片未提取到任何产品，再尝试正文 $SYMBOL
    if not products:
        for symbol in re.findall(r"\$([A-Za-z][A-Za-z0-9]{1,14})\b", post_content):
            add_product(symbol)

    return products


def extract_post_url(html_content: str, post_id: str, meta_info: Dict[str, Any]) -> str:
    """提取帖子URL，优先结构化数据，其次从HTML链接回退。"""
    url = str(meta_info.get("url") or "").strip()
    if url and "/square/post/" in url:
        return url
    match = re.search(
        rf'href=["\']([^"\']*/square/post/{re.escape(post_id)}[^"\']*)["\']',
        html_content,
        re.IGNORECASE,
    )
    if match:
        return urljoin("https://www.binance.com", html.unescape(match.group(1)))
    return ""


def extract_post_region(post_url: str) -> str:
    """从帖子URL中提取地区/语言段。"""
    if not post_url:
        return ""
    match = re.search(r"binance\.com/([^/]+)/square/post/", post_url, re.IGNORECASE)
    return match.group(1) if match else ""


def extract_first_product_url_and_market(html_content: str, post_id: str) -> tuple[str, str]:
    """提取第一个产品URL及市场类型（spot/futures）。"""
    pattern = re.compile(
        rf'href=["\']([^"\']*(?:/trade/|/futures/|/price/)[^"\']*\?[^"\']*contentId={re.escape(post_id)}[^"\']*)["\']',
        re.IGNORECASE,
    )
    match = pattern.search(html_content)
    if not match:
        return "", ""
    url = urljoin("https://www.binance.com", html.unescape(match.group(1)))
    market_type = ""
    query = parse_qs(urlsplit(url).query)
    if query.get("type"):
        market_type = str(query["type"][0]).lower()
    elif "/futures/" in url.lower():
        market_type = "futures"
    elif "/trade/" in url.lower() or "/price/" in url.lower():
        market_type = "spot"
    return url, market_type


def extract_symbol_from_product_url(first_product_url: str, first_product: str) -> str:
    """从产品URL中推断交易对symbol。"""
    if first_product_url:
        path = urlsplit(first_product_url).path
        parts = [p for p in path.split("/") if p]
        if parts:
            raw = parts[-1]
            symbol = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
            if symbol:
                if symbol.endswith("USDT"):
                    return symbol
                if len(symbol) <= 10:
                    return f"{symbol}USDT"
    fallback = normalize_product_symbol(first_product)
    if fallback:
        if fallback.endswith("USDT"):
            return fallback
        return f"{fallback}USDT"
    return ""


def extract_comment_timestamp_map_from_app_data(app_data: Dict[str, Any]) -> Dict[str, int]:
    """从APP_DATA提取 comment_id -> 绝对时间戳(ms)。"""
    timestamp_map: Dict[str, int] = {}

    def to_ms(value: Any) -> int:
        if value is None:
            return 0
        text = str(value).strip()
        if not text:
            return 0
        if text.isdigit():
            num = int(text)
            return num if num > 10**12 else num * 1000
        try:
            cleaned = re.sub(r"\.\d+", "", text).replace("Z", "")
            return int(datetime.fromisoformat(cleaned).timestamp() * 1000)
        except Exception:
            return 0

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 14:
            return
        if isinstance(node, dict):
            comment_id = node.get("commentId") or node.get("id")
            ts = node.get("createTime") or node.get("commentTime") or node.get("date") or node.get("time")
            if comment_id is not None:
                ts_ms = to_ms(ts)
                if ts_ms > 0:
                    timestamp_map[str(comment_id)] = ts_ms
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(app_data)
    return timestamp_map


def _price_cache_path(symbol: str, market_type: str, interval: str) -> Path:
    """返回某个交易对/市场/K线周期的本地价格缓存文件。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{market_type}_{symbol}_{interval}".lower())
    return PRICE_CACHE_DIR / f"{safe}.json"


def _load_price_cache(symbol: str, market_type: str, interval: str) -> Dict[str, Any]:
    """读取本地价格缓存。"""
    path = _price_cache_path(symbol, market_type, interval)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_price_cache(symbol: str, market_type: str, interval: str, cache: Dict[str, Any]) -> None:
    """写入本地价格缓存。"""
    PRICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _price_cache_path(symbol, market_type, interval).write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def normalize_label_error(err: str) -> tuple[str, str]:
    """把长错误归一为短错误码，返回 (短错误码, 原始错误)。"""
    if not err:
        return "", ""
    if err.startswith("price_api_error:"):
        return "price_api_error", err
    return err, ""


def fetch_price_at(symbol: str, market_type: str, ts_ms: int, interval: str) -> tuple[Optional[float], str]:
    """按时间点获取最邻近K线收盘价。"""
    if not symbol or ts_ms <= 0:
        return None, "invalid_symbol_or_timestamp"

    market = (market_type or "spot").lower()
    cache_key = str(int(ts_ms))
    cache = _load_price_cache(symbol, market, interval)
    cached = cache.get(cache_key)
    if isinstance(cached, dict) and cached.get("price") is not None:
        try:
            return float(cached["price"]), ""
        except Exception:
            pass

    if market == "futures":
        endpoint = "https://fapi.binance.com/fapi/v1/klines"
    else:
        endpoint = "https://api.binance.com/api/v3/klines"

    params = {"symbol": symbol, "interval": interval, "startTime": ts_ms, "limit": 1}
    try:
        resp = requests.get(endpoint, params=params, timeout=12)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        return None, f"price_api_error:{exc}"

    if not isinstance(payload, list) or not payload:
        return None, "no_kline_data"

    try:
        close_price = float(payload[0][4])
        cache[cache_key] = {
            "price": close_price,
            "open_time": payload[0][0] if len(payload[0]) > 0 else ts_ms,
            "close_time": payload[0][6] if len(payload[0]) > 6 else 0,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save_price_cache(symbol, market, interval, cache)
        return close_price, ""
    except Exception:
        return None, "invalid_kline_format"


def annotate_comment_blocks(
    comments: List[Dict[str, Any]],
    timestamp_map: Dict[str, int],
    symbol: str,
    market_type: str,
    t_window_hours: int,
    price_interval: str,
    fallback_t0_ms: int = 0,
) -> str:
    """按讨论块（根评论+回复）打标签。"""
    if not comments:
        return ""

    now_ms = int(datetime.now().timestamp() * 1000)
    price_cache: Dict[int, tuple[Optional[float], str]] = {}

    def iter_block_nodes(node: Dict[str, Any]):
        yield node
        for rep in node.get("replies", []):
            yield from iter_block_nodes(rep)

    def apply_block_fields(
        root: Dict[str, Any],
        *,
        t0: str,
        p0: Optional[float],
        p1: Optional[float],
        label: Optional[int],
        comment_error: str = "",
        label_warning: str = "",
        debug_error: str = "",
    ) -> None:
        for node in iter_block_nodes(root):
            node["t0"] = t0
            node["t_window"] = f"{t_window_hours}h"
            node["p0"] = p0
            node["p1"] = p1
            node["label"] = label
            node["comment_error"] = comment_error
            if label_warning:
                node["label_warning"] = label_warning
            else:
                node.pop("label_warning", None)
            if debug_error:
                node["debug_error"] = debug_error
            else:
                node.pop("debug_error", None)

    if not symbol:
        for comment in comments:
            apply_block_fields(
                comment,
                t0="",
                p0=None,
                p1=None,
                label=None,
                comment_error="missing_symbol",
            )
        return "missing_symbol"

    def collect_ids(node: Dict[str, Any], bucket: List[str]) -> None:
        cid = str(node.get("original_comment_id") or node.get("comment_id") or "")
        if cid:
            bucket.append(cid)
        for rep in node.get("replies", []):
            collect_ids(rep, bucket)

    for comment in comments:
        ids: List[str] = []
        collect_ids(comment, ids)
        ts_values = [timestamp_map.get(cid, 0) for cid in ids]
        ts_values = [v for v in ts_values if v > 0]
        if not ts_values:
            if fallback_t0_ms > 0:
                ts_values = [fallback_t0_ms]
            else:
                apply_block_fields(
                    comment,
                    t0="",
                    p0=None,
                    p1=None,
                    label=None,
                    comment_error="missing_comment_timestamp",
                )
                continue

        t0_ms = max(ts_values)
        t1_ms = t0_ms + int(t_window_hours) * 3600 * 1000
        t0_readable = datetime.fromtimestamp(t0_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")

        if t1_ms > now_ms:
            apply_block_fields(
                comment,
                t0=t0_readable,
                p0=None,
                p1=None,
                label=None,
                comment_error="future_price_unavailable",
            )
            continue

        if t0_ms not in price_cache:
            price_cache[t0_ms] = fetch_price_at(symbol, market_type, t0_ms, price_interval)
        if t1_ms not in price_cache:
            price_cache[t1_ms] = fetch_price_at(symbol, market_type, t1_ms, price_interval)

        p0, err0 = price_cache[t0_ms]
        p1, err1 = price_cache[t1_ms]

        if p0 is None or p1 is None:
            raw_error = err0 or err1 or "price_unavailable"
            short_error, debug_error = normalize_label_error(raw_error)
            apply_block_fields(
                comment,
                t0=t0_readable,
                p0=p0,
                p1=p1,
                label=None,
                comment_error=short_error or "missing_label_reason",
                debug_error=debug_error,
            )
        else:
            used_fallback = not [v for v in [timestamp_map.get(cid, 0) for cid in ids] if v > 0]
            apply_block_fields(
                comment,
                t0=t0_readable,
                p0=p0,
                p1=p1,
                label=1 if p1 > p0 else -1,
                comment_error="",
                label_warning="fallback_post_time" if used_fallback else "",
            )

    return ""


def find_post_data_in_app_data(app_data: Dict[str, Any], post_id: str) -> Dict[str, Any]:
    """在APP_DATA中查找帖子数据"""
    result = {}

    def search_recursive(data: Any, path: str = "") -> bool:
        if isinstance(data, dict):
            data_id = str(data.get("id") or data.get("postId") or data.get("post_id") or "")
            if data_id == post_id:
                result.update({
                    "title": data.get("title") or data.get("subTitle") or "",
                    "content": data.get("content") or data.get("plainText") or data.get("text") or "",
                    "author": data.get("authorName") or data.get("nickName") or data.get("author") or "",
                    "createTime": data.get("createTime") or data.get("publishTime") or "",
                    "authorUsername": data.get("authorUserName") or data.get("userName") or "",
                    "likeCount": data.get("likeCount") or 0,
                    "commentCount": data.get("commentCount") or 0,
                    "viewCount": data.get("viewCount") or 0,
                })
                return True
            for key, value in data.items():
                if search_recursive(value, f"{path}.{key}"):
                    return True
        elif isinstance(data, list):
            for i, item in enumerate(data):
                if search_recursive(item, f"{path}[{i}]"):
                    return True
        return False

    search_recursive(app_data)
    return result


def find_comments_in_app_data(app_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """在APP_DATA中查找评论数据"""
    comments = []

    # 非评论的卡片类型，跳过避免将推荐文章/恐慌贪婪指数误识别为评论
    _NON_COMMENT_CARD_TYPES = frozenset({
        "BUZZ_SHORT", "BUZZ_LONG", "BUZZ_IMAGE", "BUZZ_VIDEO",
        "FEAR_GREED_HIGHEST_SEARCHED", "FEAR_GREED_INDEX",
    })

    def search_recursive(data: Any, parent_id: Optional[str] = None, depth: int = 0) -> None:
        if depth > 10:
            return
        if isinstance(data, dict):
            # 跳过非评论卡片（推荐文章、恐慌贪婪指数等），避免 BUZZ_SHORT 被误判为评论
            if data.get("cardType", "") in _NON_COMMENT_CARD_TYPES:
                return

            is_comment = False
            comment_data = {}
            if "commentContent" in data or "commentText" in data:
                is_comment = True
                comment_data = {
                    "comment_id": data.get("commentId") or data.get("id") or "",
                    "author": data.get("authorName") or data.get("nickName") or "",
                    "text": data.get("commentContent") or data.get("commentText") or "",
                    "time": data.get("createTime") or data.get("commentTime") or data.get("date") or data.get("time") or "",
                    "like_count": data.get("likeCount") or data.get("upCount") or 0,
                    "reply_count": data.get("replyCount") or 0,
                    "parent_comment_id": parent_id,
                }
            elif "content" in data and isinstance(data["content"], str) and len(data["content"]) < 500:
                if data.get("authorName") or data.get("nickName"):
                    is_comment = True
                    comment_data = {
                        "comment_id": data.get("id") or "",
                        "author": data.get("authorName") or data.get("nickName") or "",
                        "text": data.get("content") or "",
                        "time": data.get("createTime") or data.get("commentTime") or data.get("date") or data.get("time") or "",
                        "like_count": data.get("likeCount") or 0,
                        "reply_count": data.get("replyCount") or 0,
                        "parent_comment_id": parent_id,
                    }
            if is_comment and comment_data.get("text"):
                comments.append(comment_data)
                reply_key = "replies" if "replies" in data else "replyList"
                if reply_key in data:
                    search_recursive(data[reply_key], comment_data["comment_id"], depth + 1)
            for key in ["comments", "commentList", "commentInfo"]:
                if key in data:
                    search_recursive(data[key], parent_id, depth + 1)
            for key, value in data.items():
                if key not in ["comments", "commentList", "commentInfo", "replies", "replyList"]:
                    search_recursive(value, parent_id, depth + 1)
        elif isinstance(data, list):
            for item in data:
                search_recursive(item, parent_id, depth + 1)

    search_recursive(app_data)
    return comments


def parse_datetime(datetime_str: Any) -> str:
    """解析时间字符串为可读格式"""
    if not datetime_str:
        return ""
    try:
        dt_str = str(datetime_str).strip()
        if dt_str.isdigit():
            ts_int = int(dt_str)
            if ts_int == 0:
                return ""
            elif ts_int > 10**12:
                dt = datetime.fromtimestamp(ts_int / 1000)
            else:
                dt = datetime.fromtimestamp(ts_int)
        else:
            cleaned_dt = re.sub(r'\.\d+', '', dt_str)
            cleaned_dt = cleaned_dt.replace('Z', '')
            dt = datetime.fromisoformat(cleaned_dt)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(datetime_str)


def build_comment_tree(flat_comments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """构建评论树形结构（优先使用线程标记；无标记时按parent_comment_id）"""
    if not flat_comments:
        return []
    for comment in flat_comments:
        comment["replies"] = []

    has_thread_marker = any("is_thread_card" in c for c in flat_comments)
    if has_thread_marker:
        root_comments: List[Dict[str, Any]] = []
        current_thread_root: Optional[Dict[str, Any]] = None
        for comment in flat_comments:
            is_first = bool(comment.get("is_thread_first"))
            is_last = bool(comment.get("is_thread_last"))
            is_thread = bool(comment.get("is_thread_card"))
            if is_first:
                comment["parent_comment_id"] = None
                root_comments.append(comment)
                current_thread_root = comment
                if is_last:
                    current_thread_root = None
                continue
            if is_thread and current_thread_root is not None:
                comment["parent_comment_id"] = current_thread_root.get("original_comment_id") or current_thread_root.get("comment_id")
                current_thread_root["replies"].append(comment)
                if is_last:
                    current_thread_root = None
                continue
            comment["parent_comment_id"] = None
            root_comments.append(comment)
            if not is_thread:
                current_thread_root = None
        return root_comments

    comment_map: Dict[str, Dict[str, Any]] = {}
    root_comments = []
    for comment in flat_comments:
        node_id = str(comment.get("comment_id") or comment.get("original_comment_id") or "")
        if node_id:
            comment_map[node_id] = comment
    for comment in flat_comments:
        parent_id = str(comment.get("parent_comment_id") or "")
        if parent_id and parent_id in comment_map:
            comment_map[parent_id]["replies"].append(comment)
        else:
            root_comments.append(comment)
    return root_comments


def format_comment_for_output(comment: Dict[str, Any], id_counter: Dict[str, int]) -> Dict[str, Any]:
    """格式化评论为输出格式"""
    comment_id = f"c{id_counter['value']}"
    id_counter["value"] += 1
    author = comment.get("author", "")
    text = comment.get("text", "")
    post_time = parse_datetime(comment.get("time", ""))
    original_comment_id = str(comment.get("original_comment_id") or comment.get("comment_id") or "")
    return {
        "comment_id": comment_id,
        "original_comment_id": original_comment_id,
        "author": author,
        "text": text,
        "post_time": post_time,
        "replies": [format_comment_for_output(reply, id_counter) for reply in comment.get("replies", [])],
    }


def extract_products_from_article_content(content: str) -> List[str]:
    """从 Article 内容中提取币种（增强版，适合规范新闻）。"""
    products: List[str] = []
    seen: set[str] = set()

    if not content:
        return products

    # 1. 提取所有 $SYMBOL 形式
    for symbol in re.findall(r'\$([A-Za-z][A-Za-z0-9]{1,14})\b', content):
        base = to_base_symbol(symbol)
        if base and base not in seen:
            seen.add(base)
            products.append(base)

    # 2. 大写匹配币种关键词
    uppercase_sections = re.findall(r'\b[A-Z]{2,10}\b', content)
    for token in uppercase_sections:
        if 2 <= len(token) <= 8:
            # 常见币种列表（优先）
            common_tokens = {
                "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "DOT",
                "LINK", "MATIC", "UNI", "ATOM", "LTC", "SUI", "APT", "ARB", "OP",
                "NEAR", "PEPE", "LDO", "ETHFI", "ARB", "AAVE", "MKR", "COMP",
                "SNX", "YFI", "CRV", "SUSHI", "1INCH", "ZRX", "BAL", "REN",
            }
            if token in common_tokens:
                base = to_base_symbol(token)
                if base and base not in seen:
                    seen.add(base)
                    products.append(base)
            else:
                # 检查是否为交易对
                base = to_base_symbol(token)
                if base and len(base) >= 2 and base not in seen:
                    seen.add(base)
                    products.append(base)

    return products


# ---------------------------------------------------------------------------
# 评论提取（Article 版 - 过滤相关文章）
# ---------------------------------------------------------------------------

def is_real_comment_text(text: str, author: str = "") -> bool:
    """判断是否为真实评论（放宽过滤条件，适应 Article 页面特点）。"""
    if not text or len(text) < 5:
        return False

    text_lower = text.lower()

    # 过滤新闻摘要 - 仅对较长文本应用，且需要>=2个关键词匹配才拒绝
    if len(text) > 200:
        news_keywords = {
            "key takeaways", "summary", "according to", "report shows",
            "data reveals", "study finds", "research indicates",
            "analysis suggests", "official statistics",
            "market analysis", "trading volume",
            "market cap", "trading at",
        }
        keyword_matches = sum(1 for kw in news_keywords if kw in text_lower)
        if keyword_matches >= 2:
            return False

    # 过滤纯数字/统计文本 - 宽松到50%，且仅对较长文本应用
    num_count = sum(c.isdigit() for c in text)
    if num_count > len(text) * 0.5 and len(text) > 100:
        return False

    # 过滤过长的结构化文本（可能是文章内容）
    if len(text) > 1000 and ("\n" in text or "•" in text or "·" in text):
        return False

    return True


def load_sidecar_comments(html_file: Path) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    """尝试从 sidecar JSON 文件加载评论数据，返回 (comments, timestamp_map)。"""
    sidecar_path = html_file.with_name(html_file.stem + "_comments.json")
    if not sidecar_path.exists():
        return [], {}

    try:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception:
        return [], {}

    if not isinstance(data, list):
        return [], {}

    comments: List[Dict[str, Any]] = []
    timestamp_map: Dict[str, int] = {}

    for row in data:
        if not isinstance(row, dict):
            continue
        text = row.get("comment_text") or ""
        if not text:
            continue

        comment_id = str(row.get("comment_id", ""))
        comment_time_raw = row.get("comment_time", "")

        # 保存毫秒时间戳用于 labeling
        comment_time_ms = 0
        if comment_time_raw:
            try:
                comment_time_ms = int(comment_time_raw)
                if 0 < comment_time_ms < 10**12:
                    comment_time_ms *= 1000
            except (ValueError, TypeError):
                comment_time_ms = 0

        if comment_id and comment_time_ms > 0:
            timestamp_map[comment_id] = comment_time_ms

        # 可读时间
        time_display = comment_time_raw
        if comment_time_ms > 0:
            try:
                dt = datetime.fromtimestamp(comment_time_ms / 1000)
                time_display = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        comment = {
            "comment_id": comment_id,
            "original_comment_id": comment_id,
            "author": str(row.get("comment_author", "")),
            "text": text,
            "time": time_display,
            "parent_comment_id": row.get("parent_comment_id") or None,
            "replies": [],
        }
        comments.append(comment)

    return comments, timestamp_map


def extract_real_comments_from_dom(html_content: str, post_id: str) -> List[Dict[str, Any]]:
    """从 DOM 中提取真实评论（使用 FeedBuzzBaseViewRoot 模式，跳过相关文章）。

    Binance Square 文章页面有两个标签页："Related Posts"（默认激活）和 "Replies"。
    点击 "Replies" 标签页后，React 会用 <div class="fade-enter-done"> 包裹新加载的回复内容，
    而 Related Posts 内容则被 <div class="fade-exit-done"> 包裹。
    因此以 fade-enter-done 为界，只提取该标记之后的 FeedBuzzBaseViewRoot 卡片。
    """
    comments: List[Dict[str, Any]] = []

    # 第一步：检查 Related Posts（相关帖文）标签页是否仍处于激活状态（多语言）。
    # 如果处于激活状态，说明未点击 Replies 标签页，当前可见的卡片全部是推荐内容。
    _related_active = re.search(
        r'<div[^>]*role="tab"[^>]*aria-selected="true"[^>]*>\s*'
        r'(?:Related\s*Posts|相关帖文|Articles\s*similaires|Ähnliche\s*Beiträge|Publicaciones\s*relacionadas)'
        r'\s*</div>',
        html_content,
    )
    if _related_active:
        return comments

    # 第二步：定位 Replies 内容的起始位置（fade-enter-done）。
    _fade_enter = re.search(r'<div\s+class="fade-enter-done"\s*>', html_content)

    # 如果没有 fade-enter-done 标记，需要确认 Replies 标签页确实被激活了。
    # 否则可能是 Related Posts 内容伪装（例如只有单个标签页的页面）。
    if not _fade_enter:
        _replies_active = re.search(
            r'<div[^>]*role="tab"[^>]*aria-selected="true"[^>]*>\s*'
            r'(?:Replies|回复|Réponses|Respuestas|Antworten)'
            r'\s*</div>',
            html_content,
        )
        if not _replies_active:
            return comments

    _replies_start = _fade_enter.start() if _fade_enter else 0

    card_pattern = re.compile(
        r'<div[^>]*data-id="([^"]+)"[^>]*class="([^"]*FeedBuzzBaseViewRoot[^"]*)"[^>]*>',
        re.DOTALL,
    )
    card_matches = list(card_pattern.finditer(html_content))
    if not card_matches:
        return comments

    for i, match in enumerate(card_matches):
        # 跳过 fade-enter-done 之前的卡片（属于 Related Posts）
        if _fade_enter and match.start() < _replies_start:
            continue
        original_comment_id = match.group(1).strip()
        class_attr = match.group(2)

        start = match.end()
        end = card_matches[i + 1].start() if i + 1 < len(card_matches) else len(html_content)
        segment = html_content[start:end]

        # 跳过相关文章卡片
        if re.search(r'article-title-ellipsis', segment):
            continue

        # 跳过 BUZZ_SHORT 类型的推荐文章（含 buzz-title 链接）
        if re.search(r'<a[^>]*class="[^"]*buzz-title[^"]*"', segment):
            continue

        author = ""
        time_text = ""
        text = ""

        author_match = re.search(
            r'<div[^>]*class="nick-username"[^>]*>[\s\S]*?<a[^>]*class="nick[^"]*"[^>]*>([^<]+)</a>',
            segment,
            re.DOTALL,
        )
        if author_match:
            author = html.unescape(author_match.group(1)).strip()
            author = re.sub(r'\s+', ' ', author)

        time_match = re.search(
            r'<div[^>]*class="create-time"[^>]*>([^<]+)</div>',
            segment,
            re.DOTALL,
        )
        if time_match:
            time_text = html.unescape(time_match.group(1)).strip()

        content_match = re.search(
            rf'<div[^>]*class="[^"]*feed-content-text[^"]*"[^>]*data="{re.escape(original_comment_id)}"[^>]*>[\s\S]*?<div[^>]*class="card__description rich-text"[^>]*>([\s\S]*?)</div>',
            segment,
            re.DOTALL,
        )
        if not content_match:
            content_match = re.search(
                r'<div[^>]*class="card__description rich-text"[^>]*>([\s\S]*?)</div>',
                segment,
                re.DOTALL,
            )

        if content_match:
            text = html.unescape(content_match.group(1))
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()

        if not text:
            continue

        # 过滤非真实评论文本（新闻摘要、纯数字统计等）
        if not is_real_comment_text(text, author):
            continue

        comment = {
            "comment_id": original_comment_id,
            "original_comment_id": original_comment_id,
            "author": author,
            "text": text,
            "time": time_text,
            "parent_comment_id": None,
            "is_thread_card": "in-thread-card" in class_attr,
            "is_thread_first": "in-thread-card-first" in class_attr,
            "is_thread_last": "in-thread-card-last" in class_attr,
            "replies": [],
        }
        comments.append(comment)

    return comments


# ---------------------------------------------------------------------------
# 核心解析
# ---------------------------------------------------------------------------

def parse_article_file(
    html_file: Path,
    t_window_hours: int = 24,
    price_interval: str = "1h",
) -> Dict[str, Any]:
    """解析单个 Article HTML 文件（Article 专用）。"""
    print(f"解析 Article 文件: {html_file.name}")

    try:
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
        post_id = html_file.stem
        meta_info = extract_from_meta_tags(content)
        app_data = extract_app_data(content)

        # 从 APP_DATA 获取帖子数据
        post_data = find_post_data_in_app_data(app_data, post_id) if app_data else {}

        # 帖子发布时间
        post_time_str = post_data.get("createTime") or meta_info.get("published_date") or ""
        post_time_readable, post_time_ms = parse_datetime_absolute_or_relative(post_time_str, 0)

        # fallback_t0_ms: 帖子创建时间，用于 labeling 的兜底
        fallback_t0_ms = 0
        if post_time_ms > 0:
            fallback_t0_ms = post_time_ms
        elif post_time_str:
            raw_ts_text = str(post_time_str).strip()
            if raw_ts_text.isdigit():
                num = int(raw_ts_text)
                fallback_t0_ms = num if num > 10**12 else num * 1000
            else:
                try:
                    cleaned = re.sub(r"\.\d+", "", raw_ts_text).replace("Z", "")
                    fallback_t0_ms = int(datetime.fromisoformat(cleaned).timestamp() * 1000)
                except Exception:
                    fallback_t0_ms = 0

        # 评论提取：优先 sidecar JSON（article 页面依赖 API 拦截），然后 DOM，最后 APP_DATA
        comments: List[Dict[str, Any]] = []
        comment_timestamp_map: Dict[str, int] = {}

        sidecar_comments, sidecar_ts_map = load_sidecar_comments(html_file)
        if sidecar_comments:
            comments = sidecar_comments
            comment_timestamp_map = sidecar_ts_map
            print(f"  从 sidecar 加载到 {len(comments)} 条评论")
        else:
            dom_comments = extract_real_comments_from_dom(content, post_id)
            if dom_comments:
                comments = dom_comments
                if app_data:
                    comment_timestamp_map = extract_comment_timestamp_map_from_app_data(app_data)
                print(f"  从 DOM 提取到 {len(comments)} 条评论")
            elif app_data:
                comments = find_comments_in_app_data(app_data)
                if comments:
                    comment_timestamp_map = extract_comment_timestamp_map_from_app_data(app_data)
                    print(f"  从 APP_DATA 提取到 {len(comments)} 条评论")

        print(f"  总评论数: {len(comments)}")

        # 构建评论树 + 格式化输出
        comment_tree = build_comment_tree(comments)
        id_counter = {"value": 1}
        formatted_comments = [format_comment_for_output(c, id_counter) for c in comment_tree]

        def count_all(nodes: List[Dict[str, Any]]) -> int:
            total = 0
            for node in nodes:
                total += 1
                total += count_all(node.get("replies", []))
            return total

        # 构建结果
        post_author = (
            meta_info.get("author") or
            post_data.get("author") or
            "Binance News"
        )
        post_content = (
            meta_info.get("description") or
            post_data.get("content") or
            ""
        )

        # 产品提取（增强版，使用 contentId 锚定的 <span class="symbol">）
        products = extract_products_from_html(
            html_content=content,
            post_id=post_id,
            post_content=post_content,
            post_data=post_data,
        )
        first_product = products[0] if products else ""
        first_product_url, market_type = extract_first_product_url_and_market(content, post_id)
        post_url = extract_post_url(content, post_id, meta_info)
        post_region = extract_post_region(post_url)

        result = {
            "source_file": str(html_file),
            "post_id": post_id,
            "post_url": post_url,
            "post_author": post_author,
            "post_content": post_content,
            "post_region": post_region,
            "post_time": post_time_readable,
            "post_time_ms": post_time_ms,
            "products": products,
            "first_product": first_product,
            "first_product_url": first_product_url,
            "market_type": market_type or "spot",
            "post_type": "article",
            "comment_num": len(formatted_comments),
            "comment_total_num": count_all(formatted_comments),
            "comments": formatted_comments,
            "label_error": "",
        }

        # 价格标注
        symbol_for_label = extract_symbol_from_product_url(first_product_url, first_product)
        label_error = annotate_comment_blocks(
            comments=result["comments"],
            timestamp_map=comment_timestamp_map,
            symbol=symbol_for_label,
            market_type=result["market_type"],
            t_window_hours=t_window_hours,
            price_interval=price_interval,
            fallback_t0_ms=fallback_t0_ms,
        )
        result["label_error"] = label_error

        return result

    except Exception as e:
        print(f"  解析出错: {e}")
        import traceback
        traceback.print_exc()

        # 返回基本结构
        return {
            "source_file": str(html_file),
            "post_id": html_file.stem,
            "post_url": "",
            "post_author": "",
            "post_content": "",
            "post_region": "",
            "post_time": "",
            "post_time_ms": 0,
            "products": [],
            "first_product": "",
            "first_product_url": "",
            "market_type": "",
            "post_type": "article",
            "comment_num": 0,
            "comment_total_num": 0,
            "comments": [],
            "label_error": f"parse_error: {e}",
        }




def parse_article_directory(
    input_dir: Path,
    t_window_hours: int = 24,
    price_interval: str = "1h",
) -> List[Dict[str, Any]]:
    """解析目录中的所有 Article HTML 文件。"""
    html_files = list(input_dir.glob("*.html"))
    if not html_files:
        raise ValueError(f"在目录中未找到 HTML 文件: {input_dir}")

    results = []
    for html_file in html_files:
        result = parse_article_file(html_file, t_window_hours=t_window_hours, price_interval=price_interval)
        if result:
            results.append(result)

    return results


def write_output(output_file: Path, data: List[Dict[str, Any]]):
    """写入输出文件"""
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"结果已写入: {output_file}")


def main():
    # 命令行参数解析
    parser = argparse.ArgumentParser(
        description="Binance Article 解析器 - 处理官方新闻 HTML"
    )

    parser.add_argument(
        "--input",
        default="update_news/binance_square_page_dump",
        help="输入 HTML 文件或目录，默认: update_news/binance_square_page_dump"
    )

    parser.add_argument(
        "--output",
        default="update_news/parsed_articles/binance_square_articles_parsed.json",
        help="输出 JSON 文件路径，默认: update_news/parsed_articles/binance_square_articles_parsed.json"
    )

    parser.add_argument(
        "--batch",
        action="store_true",
        help="批量处理目录中的所有 HTML 文件"
    )

    parser.add_argument(
        "--t-window-hours",
        type=int,
        default=24,
        help="价格窗口（小时），目前 Article 仅作占位，默认 24"
    )

    parser.add_argument(
        "--price-interval",
        default="1h",
        help="K线周期（如 1m/5m/1h），目前 Article 仅作占位，默认 1h"
    )

    args = parser.parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    try:
        # 批量处理目录
        if args.batch or input_path.is_dir():
            print(f"批量解析 Article 目录: {input_path}")
            results = parse_article_directory(
                input_path,
                t_window_hours=args.t_window_hours,
                price_interval=args.price_interval,
            )
            if results:
                write_output(output_path, results)
                print(f"成功解析 {len(results)} 个 Article 文件")
            else:
                print("未解析出任何结果")

        elif input_path.is_file():
            # 解析单个文件
            print(f"解析单个 Article 文件: {input_path}")
            result = parse_article_file(
                input_path,
                t_window_hours=args.t_window_hours,
                price_interval=args.price_interval,
            )
            if result:
                write_output(output_path, [result])
                print("单个文件解析完成")
            else:
                print("解析失败")

        else:
            print(f"路径不存在: {input_path}")
            sys.exit(1)

        print("\nArticle 解析完成!")

    except Exception as e:
        print(f"程序出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
