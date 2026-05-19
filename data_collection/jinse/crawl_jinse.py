"""
crawl_jinse.py — 调用金色财经 API 采集新闻列表，输出 CSV

用法:
  python crawl_jinse.py
"""

import csv
import datetime
import time
from pathlib import Path

import requests

# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

# 快讯 API：内容直接在 JSON 中，适合快速获取
LIVES_API = "https://api.jinse2.com/noah/v2/lives"

# 产业文章 API：标题+摘要+链接，需要下载详情页才能拿到全文
ARTICLES_API = "https://api.jinse2.com/noah/v2/catalogue/timelines"

# 切换数据源："lives" 或 "articles"
SOURCE = "lives"

OUTPUT_CSV = Path("dataset/csv/jinse_news.csv")
PAGE_SIZE = 20
MAX_PAGES = 500
PAUSE_SECONDS = 1.0


# ═══════════════════════════════════════════════════════════
# API 请求
# ═══════════════════════════════════════════════════════════


def fetch_lives(session: requests.Session, cursor_id: int) -> tuple[list[dict], int]:
    """获取快讯列表。cursor_id 为上一页的 bottom_id，首次传 0。

    返回 (条目列表, 下一页的 cursor_id)。
    条目格式: {title, content, url, time, comment_count}
    """
    params = {
        "limit": PAGE_SIZE,
        "reading": "false",
        "source": "web",
        "flag": "down",
        "id": cursor_id,
        "category": 0,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://jinse2.com/lives",
    }

    r = session.get(LIVES_API, params=params, headers=headers)
    data = r.json()

    results = []
    next_cursor = 0

    for day_block in data.get("list", []):
        for item in day_block.get("lives", []):
            content = item.get("content", "")
            prefix = item.get("content_prefix", "")
            news_id = item.get("id")
            ts = item.get("created_at", 0)

            results.append({
                "title": prefix,
                "content": content,
                "url": f"https://www.jinse2.com/lives/{news_id}.html",
                "time": datetime.datetime.fromtimestamp(ts),
                "comment_count": item.get("comment_count", 0),
            })

    bottom_id = data.get("bottom_id", 0)
    if bottom_id:
        next_cursor = bottom_id

    return results, next_cursor


def fetch_articles(session: requests.Session, cursor_id: int) -> tuple[list[dict], int]:
    """获取产业文章列表。cursor_id 为上一页的 bottom_id，首次传 0。

    返回 (条目列表, 下一页的 cursor_id)。
    条目格式: {title, content, url, time}
    """
    params = {
        "catelogue_key": "industry",
        "limit": PAGE_SIZE,
        "information_id": cursor_id,
        "flag": "down",
        "version": "9.9.9",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://jinse2.com/industry",
    }

    r = session.get(ARTICLES_API, params=params, headers=headers)
    data = r.json()

    results = []
    next_cursor = 0

    for item in data.get("data", {}).get("list", []):
        extra = item.get("extra", {})
        title = item.get("title", "")
        summary = extra.get("summary", "") or item.get("short_title", "")
        topic_url = extra.get("topic_url", "")
        ts = extra.get("published_at", 0)

        results.append({
            "title": title,
            "content": summary,
            "url": topic_url,
            "time": datetime.datetime.fromtimestamp(ts),
            "comment_count": 0,
        })

    bottom_id = data.get("data", {}).get("bottom_id", 0)
    if bottom_id:
        next_cursor = bottom_id

    return results, next_cursor


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════


def main():
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    s = requests.Session()
    all_news: list[dict] = []
    cursor = 0

    fetch_fn = fetch_lives if SOURCE == "lives" else fetch_articles

    for page in range(1, MAX_PAGES + 1):
        print(f"请求第 {page} 页 (cursor={cursor})...")
        items, cursor = fetch_fn(s, cursor)

        if not items:
            print("没有更多数据，停止。")
            break

        all_news.extend(items)
        print(f"  → 获取 {len(items)} 条，累计 {len(all_news)} 条")

        if cursor == 0:
            print("cursor 归零，停止。")
            break

        time.sleep(PAUSE_SECONDS)

    # 按链接去重
    seen_urls = set()
    unique_news = []
    for item in all_news:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique_news.append(item)

    print(f"\n去重后共 {len(unique_news)} 条新闻")

    # 写入 CSV
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["新闻id", "时间", "内容", "链接"])
        for i, item in enumerate(unique_news, 1):
            writer.writerow([
                i,
                item["time"].strftime("%Y-%m-%d %H:%M:%S"),
                item["title"] + " " + item["content"],
                item["url"],
            ])

    print(f"已保存到 {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
