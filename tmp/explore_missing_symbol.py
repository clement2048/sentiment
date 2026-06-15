"""临时探查脚本：读 pending_missing_symbol.jsonl 涉及的 HTML，扫描币种线索。

不做任何数据修改；只输出 CSV 报告与控制台汇总。
"""
from __future__ import annotations

import csv
import json
import re
import sys
import argparse
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
INPUT_JSONL = ROOT / "dataset" / "result" / "pending_missing_symbol.jsonl"
HTML_DIR = ROOT / "dataset" / "html" / "update_news"
OUT_CSV = ROOT / "tmp" / "missing_symbol_coin_scan.csv"
OUT_SUMMARY = ROOT / "tmp" / "missing_symbol_coin_scan_summary.json"

NAME_TO_SYMBOL = {
    "Bitcoin Cash": "BCH",
    "Ethereum Classic": "ETC",
    "Ethereum Name Service": "ENS",
    "NEAR Protocol": "NEAR",
    "Shiba Inu": "SHIB",
    "USD Coin": "USDC",
    "Binance Coin": "BNB",
    "Binance USD": "BUSD",
    "First Digital USD": "FDUSD",
    "Wrapped Bitcoin": "WBTC",
    "Wrapped Ether": "WETH",
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
    "Theta": "THETA",
    "Hedera": "HBAR",
    "Flow": "FLOW",
    "Axie Infinity": "AXS",
    "Decentraland": "MANA",
    "The Sandbox": "SAND",
    "ApeCoin": "APE",
    "Immutable": "IMX",
    "Loopring": "LRC",
    "dYdX": "DYDX",
    "Hyperliquid": "HYPE",
    "Sui": "SUI",
    "Sei": "SEI",
    "Starknet": "STRK",
    "ZetaChain": "ZETA",
    "Manta": "MANTA",
    "Pendle": "PENDLE",
    "Blur": "BLUR",
    "Jupiter": "JUP",
    "Fantom": "FTM",
    "Internet Computer": "ICP",
    "EOS": "EOS",
    "比特币": "BTC",
    "以太坊": "ETH",
    "以太币": "ETH",
    "索拉纳": "SOL",
    "瑞波币": "XRP",
    "狗狗币": "DOGE",
    "卡尔达诺": "ADA",
    "雪崩": "AVAX",
    "波卡": "DOT",
    "莱特币": "LTC",
    "柴犬币": "SHIB",
    "泰达币": "USDT",
    "币安币": "BNB",
    "枨子币": "EOS",
    "恒星币": "XLM",
    "波场": "TRX",
    "波场币": "TRX",
    "达世币": "DASH",
    "门罗币": "XMR",
    "原子币": "ATOM",
    "互联网计算机": "ICP",
}

SYMBOLS = [
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "DOT", "LTC",
    "BNB", "LINK", "UNI", "USDT", "TRX", "TON", "MATIC", "ARB", "OP",
    "APT", "FIL", "TIA", "INJ", "MKR", "AAVE", "COMP", "SNX", "CRV",
    "ATOM", "ALGO", "XTZ", "XLM", "XMR", "HBAR", "FLOW", "AXS", "MANA",
    "SAND", "APE", "IMX", "LRC", "DYDX", "HYPE", "SUI", "SEI", "STRK",
    "ZETA", "MANTA", "PENDLE", "BLUR", "JUP", "FTM", "ICP", "EOS",
    "BCH", "ETC", "ENS", "NEAR", "SHIB", "USDC", "BUSD", "FDUSD",
    "WBTC", "WETH", "TAO", "RNDR", "VVV", "IO", "ZK", "BANANA",
    "PEPE", "WIF", "BONK",
]

# (pattern, symbol) 列表，USDT 组合放最前以获得更强证据
SYMBOL_PATTERN = []
for sym in SYMBOLS:
    SYMBOL_PATTERN.append((re.compile(r"\b" + re.escape(sym) + r"USDT\b", re.I), sym))
for sym in SYMBOLS:
    SYMBOL_PATTERN.append((re.compile(r"\b" + re.escape(sym) + r"\b"), sym))

TEMPLATE_TAG = re.compile(r"\{(future|coin|token|stock|etf)\}\(([A-Z0-9]{2,12})\)")
TRADE_LINK = re.compile(
    r"https?://(?:www\.)?binance\.com/(?:[a-zA-Z-]+/)?(trade|futures|spot|earn|convert|simple-earn)/([A-Z0-9]{2,12})(?:USDT|USDC|BUSD|FDUSD)?",
    re.I,
)
URL_SYMBOL = re.compile(r"symbol=([A-Z0-9]{2,12})USDT", re.I)


def detect_encoding(raw: bytes) -> str:
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    m = re.search(rb'<meta[^>]+charset=["\']?([a-zA-Z0-9_-]+)', raw[:65536], re.I)
    if m:
        return m.group(1).decode("ascii", "ignore").lower()
    try:
        raw.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        return "gbk"


def read_html_text(html_path: Path) -> tuple:
    raw = html_path.read_bytes()
    enc = detect_encoding(raw)
    try:
        return raw.decode(enc, errors="replace"), enc
    except LookupError:
        return raw.decode("utf-8", errors="replace"), enc


def scan_html(html_path: Path) -> dict:
    text, enc = read_html_text(html_path)
    m_app = re.search(r'<script[^>]*id="__APP_DATA"[^>]*>([\s\S]*?)</script>', text)
    m_state = re.search(r'<script[^>]*id="__INITIAL_STATE__"[^>]*>([\s\S]*?)</script>', text)
    template_hits = TEMPLATE_TAG.findall(text)
    trade_hits = TRADE_LINK.findall(text)
    url_symbol_hits = URL_SYMBOL.findall(text)
    name_hits = []
    for name, sym in NAME_TO_SYMBOL.items():
        if not sym:
            continue
        if name in text:
            name_hits.append((name, sym))
    symbol_hits_count = 0
    candidates = set()
    evidence = []
    for kind, sym in template_hits:
        candidates.add(sym.upper())
        evidence.append(f"template:{{{kind}}}({sym})")
    for kind, sym in trade_hits:
        candidates.add(sym.upper())
        evidence.append(f"link:{kind}/{sym}")
    for sym in url_symbol_hits:
        candidates.add(sym.upper())
        evidence.append(f"url-symbol:{sym}")
    for name, sym in name_hits:
        candidates.add(sym)
        evidence.append(f"name:{name}")
    for pat, sym in SYMBOL_PATTERN:
        for m in pat.finditer(text):
            symbol_hits_count += 1
            candidates.add(sym)
            if len(evidence) < 30:
                evidence.append(f"symbol:{m.group(0)}")
            if symbol_hits_count > 5000:
                break
        if symbol_hits_count > 5000:
            break
    product_urls = []
    for kind, sym in trade_hits[:5]:
        kind_l = kind.lower()
        if kind_l == "futures":
            product_urls.append(f"https://www.binance.com/zh-CN/futures/{sym}USDT")
        else:
            product_urls.append(f"https://www.binance.com/zh-CN/trade/{sym}_USDT")
    return {
        "size": html_path.stat().st_size,
        "encoding": enc,
        "has_app_data": bool(m_app),
        "has_initial_state": bool(m_state),
        "candidates": sorted(candidates),
        "evidence_sample": evidence[:15],
        "product_urls": product_urls[:5],
        "template_hits": template_hits[:10],
        "trade_link_hits": list({(k, s) for k, s in trade_hits})[:10],
        "url_symbol_hits": url_symbol_hits[:10],
        "name_hits": name_hits[:10],
        "symbol_hits_count": symbol_hits_count,
    }


def iter_posts():
    with INPUT_JSONL.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--offset", type=int, default=0)
    return p.parse_args()


def main() -> int:
    if not INPUT_JSONL.exists():
        print(f"[!] missing input: {INPUT_JSONL}")
        return 1
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    args = parse_args()
    posts = list(iter_posts())
    if args.offset:
        posts = posts[args.offset:]
    if args.limit:
        posts = posts[:args.limit]
    print(f"[scan] total posts in pending_missing_symbol: {len(posts)}")
    rows = []
    summary = {
        "total": len(posts),
        "with_html": 0,
        "missing_html": 0,
        "with_candidates": 0,
        "no_candidates": 0,
        "by_app_data_present": 0,
        "by_initial_state_present": 0,
        "candidate_distribution": {},
        "evidence_kind_distribution": {},
    }
    for post in posts:
        post_id = post.get("post_id", "")
        html_path = HTML_DIR / f"{post_id}.html"
        rec = {
            "post_id": post_id,
            "html_exists": html_path.exists(),
            "html_size": 0,
            "html_encoding": "",
            "has_app_data": False,
            "has_initial_state": False,
            "candidates": "",
            "candidates_count": 0,
            "symbol_hits_count": 0,
            "evidence_sample": "",
            "product_url_candidate": "",
        }
        if not html_path.exists():
            summary["missing_html"] += 1
            rows.append(rec)
            continue
        summary["with_html"] += 1
        info = scan_html(html_path)
        rec["html_size"] = info["size"]
        rec["html_encoding"] = info["encoding"]
        rec["has_app_data"] = info["has_app_data"]
        rec["has_initial_state"] = info["has_initial_state"]
        rec["candidates"] = ",".join(info["candidates"])
        rec["candidates_count"] = len(info["candidates"])
        rec["symbol_hits_count"] = info["symbol_hits_count"]
        rec["evidence_sample"] = " | ".join(info["evidence_sample"])
        rec["product_url_candidate"] = ",".join(info["product_urls"])
        if info["has_app_data"]:
            summary["by_app_data_present"] += 1
        if info["has_initial_state"]:
            summary["by_initial_state_present"] += 1
        if info["candidates"]:
            summary["with_candidates"] += 1
            for c in info["candidates"]:
                summary["candidate_distribution"][c] = summary["candidate_distribution"].get(c, 0) + 1
        else:
            summary["no_candidates"] += 1
        for ev in info["evidence_sample"]:
            kind = ev.split(":", 1)[0] if ":" in ev else ev
            summary["evidence_kind_distribution"][kind] = summary["evidence_kind_distribution"].get(kind, 0) + 1
        rows.append(rec)
    fieldnames = ["post_id", "html_exists", "html_size", "html_encoding", "has_app_data", "has_initial_state", "candidates", "candidates_count", "symbol_hits_count", "evidence_sample", "product_url_candidate"]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[scan] csv   -> {OUT_CSV}")
    print(f"[scan] summary -> {OUT_SUMMARY}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
