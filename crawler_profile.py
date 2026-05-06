#!/usr/bin/env python3
"""
crawler_profile.py —— 从 Binance Square Profile 页面采集文章 URL 并多线程下载 HTML

浏览 Profile 页面的 "All" 列表，滚动采集帖子 URL，然后多线程并行下载 HTML。
支持在下载阶段按评论数和产品符号即时过滤，跳过无价值文章。

Strategy:
  - 单线程滚动采集 URL（受限于页面滚动加载，无法并行）
  - 多线程并行下载 HTML（ThreadPoolExecutor，每线程独立 Playwright context）
  - 下载时即时提取 __APP_DATA 进行质量过滤，不满足条件直接跳过
  - 集成解析阶段，输出结构化 JSON

Usage:
  # 检查连通性
  python crawler_profile.py --check-only

  # 采集 500 条并多线程下载 HTML（4 线程）
  python crawler_profile.py --target-posts 500 --fetch-html --workers 4 --headless

  # 采集 + 下载 + 过滤（要求至少有 1 条评论或有产品符号）
  python crawler_profile.py --target-posts 500 --fetch-html --workers 4 --require-content --headless

  # 采集 + 下载 + 解析（端到端）
  python crawler_profile.py --target-posts 500 --fetch-html --workers 4 --parse-html --headless

  # 采集 + 下载 + 解析 + 过滤（端到端，保留评论数>=5且有产品的文章）
  python crawler_profile.py --target-posts 500 --fetch-html --workers 4 --parse-html --filter-parsed --headless --min-comment-total 5 --drop-no-products

  # 仅对已有解析结果执行过滤
  python crawler_profile.py --filter-parsed --min-comment-total 5 --drop-no-products
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List
from urllib.parse import urlsplit, urlunsplit

from utils.crawler_util import ensure_dir

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = "crawler_profile_output"
DEFAULT_DB_NAME = "profile_posts.db"
DEFAULT_USER_DATA_DIR = "tmp_chrome_profile"
DEFAULT_PROFILE_URL = "https://www.binance.com/en/square/profile/binance_news"
DEFAULT_WORKERS = 4

PROFILE_MAIN_FEED_SELECTORS = [
    "div.feed-layout-main",
    "div[class*='feed-layout-main']",
    "div.feed-profile-ProfilePageRoot__27CBB",
    "div[class*='feed-profile-ProfilePageRoot']",
]


# ---------------------------------------------------------------------------
# 命令行参数
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description=(
            "Profile-based Binance Square crawler. "
            "Collect post URLs from a profile page, then optionally fetch HTML and parse JSON."
        )
    )

    parser.add_argument(
        "--profile-url",
        default=DEFAULT_PROFILE_URL,
        help=f"Target profile URL, default: {DEFAULT_PROFILE_URL}",
    )
    parser.add_argument(
        "--profile-slug",
        default="",
        help="Optional profile slug label for DB/run logs; auto-derived from profile URL when omitted",
    )
    parser.add_argument("--target-posts", type=int, default=50, help="Stop when unique post count reaches this number")
    parser.add_argument("--max-scroll-rounds", type=int, default=3000, help="Max scroll rounds for this run")
    parser.add_argument("--idle-stop-rounds", type=int, default=120, help="Stop if no new post appears for N rounds")
    parser.add_argument("--pause-seconds", type=float, default=1.0, help="Pause between rounds")
    parser.add_argument("--scroll-pixels", type=int, default=2600, help="Wheel scroll pixels per round")
    parser.add_argument("--max-runtime-minutes", type=float, default=0.0, help="0 means unlimited runtime")

    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--wait-for-login", action="store_true", help="Pause for manual login before collecting")
    parser.add_argument(
        "--user-data-dir",
        default=DEFAULT_USER_DATA_DIR,
        help=f"Persistent Chromium profile dir, default: {DEFAULT_USER_DATA_DIR}",
    )

    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Output directory, default: {DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--db-path", default="", help="SQLite path; defaults to <output-dir>/profile_posts.db")
    parser.add_argument("--export-limit", type=int, default=0, help="Export first N rows to CSV/JSON; 0 means all")
    parser.add_argument("--checkpoint-every", type=int, default=20, help="Print progress every N rounds")
    parser.add_argument("--check-only", action="store_true", help="Only open profile page and verify browser flow")
    parser.add_argument(
        "--pause-after-switch-all",
        action="store_true",
        help="Pause after switching to All so you can manually verify before crawling continues",
    )
    parser.add_argument(
        "--require-all-selected",
        action="store_true",
        help="Exit with error if feed filter is not switched to All before crawling",
    )

    # HTML 下载参数
    parser.add_argument("--fetch-html", action="store_true", help="After indexing, download HTML from collected URLs")
    parser.add_argument("--html-output-dir", default="", help="HTML output directory, default: <output-dir>/html_pages")
    parser.add_argument("--html-limit", type=int, default=0, help="Max number of posts for HTML fetch stage, 0 means all")
    parser.add_argument("--html-offset", type=int, default=0, help="Offset when scanning DB rows for HTML fetch stage")
    parser.add_argument("--html-overwrite", action="store_true", help="Overwrite existing HTML files")
    parser.add_argument("--min-post-age-days", type=int, default=0, help="Only fetch HTML for posts at least N days old")

    # 多线程参数
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of parallel HTML download threads, default: {DEFAULT_WORKERS}",
    )

    # 质量过滤参数（下载时即时过滤）
    parser.add_argument(
        "--require-content",
        action="store_true",
        help="Only save posts with at least 1 comment or 1 product symbol",
    )
    parser.add_argument(
        "--min-comment-total",
        type=int,
        default=0,
        help="Only save posts with comment_total_num >= this threshold, 0 means disabled",
    )
    parser.add_argument(
        "--require-products",
        action="store_true",
        help="Only save posts with at least one extracted product symbol",
    )

    # 解析参数
    parser.add_argument("--parse-html", action="store_true", help="After HTML fetch, run parser and write JSON output")
    parser.add_argument("--parsed-output", default="", help="Parsed JSON path, default: <output-dir>/profile_parsed.json")
    parser.add_argument("--t-window-hours", type=int, default=24, help="Parser comment window hours, default 24")
    parser.add_argument("--price-interval", default="1h", help="Parser Kline interval, default 1h")

    # 过滤参数（解析后自动调用 clean_labeled_data.py）
    parser.add_argument("--filter-parsed", action="store_true",
                        help="After parsing, run clean_labeled_data.py to filter records")
    parser.add_argument("--drop-no-products", action="store_true",
                        help="Drop posts whose products list is empty")
    parser.add_argument("--drop-label-error", action="store_true",
                        help="Drop posts with non-empty label_error")
    parser.add_argument("--keep-comment-error-posts", action="store_true",
                        help="Do not drop posts just because comments have comment_error")
    parser.add_argument("--filtered-output", default="",
                        help="Filtered kept records JSON path, default: <output-dir>/<stem>_clean.json")
    parser.add_argument("--filtered-dropped-output", default="",
                        help="Filtered dropped records JSON path, default: <output-dir>/<stem>_dropped.json")
    parser.add_argument("--filtered-report-output", default="",
                        help="Filter report JSON path, default: <output-dir>/<stem>_clean_report.json")

    parser.add_argument(
        "--verify-author-text",
        default="",
        help="Optional substring for parsed author spot-check, e.g. 'Binance News'",
    )
    parser.add_argument("--verify-sample-size", type=int, default=20, help="Sample size for author spot-check, default 20")

    return parser.parse_args()


# ---------------------------------------------------------------------------
# URL 工具函数
# ---------------------------------------------------------------------------

def now_text() -> str:
    """返回当前时间字符串，格式 YYYY-MM-DD HH:MM:SS。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_post_url(url: str) -> str:
    """将帖子 URL 规范化为 /square/post/<id> 格式，去重去噪。"""
    try:
        parts = urlsplit((url or "").strip())
    except Exception:
        return ""

    path = (parts.path or "").rstrip("/")
    match = re.search(r"/square/post/(\d+)", path)
    if not match:
        return ""

    canonical_path = f"/square/post/{match.group(1)}"
    return urlunsplit((parts.scheme, parts.netloc, canonical_path, "", ""))


def post_id_from_url(url: str) -> str:
    """从 URL 路径中提取帖子 ID。"""
    path = urlsplit(url).path.rstrip("/")
    match = re.search(r"/square/post/(\d+)", path)
    return match.group(1) if match else ""


def profile_slug_from_url(url: str) -> str:
    """从 Profile URL 路径末尾提取 slug。"""
    path = urlsplit(url).path.rstrip("/")
    if not path:
        return ""
    return path.split("/")[-1]


# ---------------------------------------------------------------------------
# 浏览器上下文
# ---------------------------------------------------------------------------

def create_browser_context(headless: bool, user_data_dir: str) -> tuple[Any, Any]:
    if sync_playwright is None:
        raise RuntimeError(
            "playwright is not installed. Run: pip install playwright && playwright install chromium"
        )

    browser_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
    ]

    playwright = sync_playwright().start()
    if user_data_dir:
        print(f"[browser] using persistent profile: {user_data_dir}")
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={"width": 1366, "height": 900},
            args=browser_args,
        )
    else:
        print("[browser] using ephemeral profile")
        browser = playwright.chromium.launch(headless=headless, args=browser_args)
        context = browser.new_context(viewport={"width": 1366, "height": 900})

    # 在页面所有脚本之前注入导航拦截，阻止 SPA 跳转到 /trends 或 /following
    # add_init_script 会在每次页面加载（包括 goto）之前自动执行，不会丢失
    context.add_init_script("""
        (() => {
            const _pushState = History.prototype.pushState;
            const _replaceState = History.prototype.replaceState;
            History.prototype.pushState = function(state, title, url) {
                if (url && (String(url).toLowerCase().includes('/trends')
                         || String(url).toLowerCase().includes('/following'))) return;
                return _pushState.call(this, state, title, url);
            };
            History.prototype.replaceState = function(state, title, url) {
                if (url && (String(url).toLowerCase().includes('/trends')
                         || String(url).toLowerCase().includes('/following'))) return;
                return _replaceState.call(this, state, title, url);
            };
        })();
    """)

    return playwright, context


def safe_close_browser(playwright_obj: Any, context: Any) -> None:
    """安全关闭浏览器上下文和 playwright 实例。"""
    try:
        context.close()
    finally:
        playwright_obj.stop()


# ---------------------------------------------------------------------------
# 数据库操作
# ---------------------------------------------------------------------------

def init_db(conn: sqlite3.Connection) -> None:
    """初始化 SQLite 表结构：posts 表和 runs 表。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            post_id TEXT PRIMARY KEY,
            link TEXT NOT NULL UNIQUE,
            profile_slug TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            seen_count INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            profile_url TEXT NOT NULL,
            profile_slug TEXT NOT NULL,
            target_posts INTEGER NOT NULL,
            max_scroll_rounds INTEGER NOT NULL,
            idle_stop_rounds INTEGER NOT NULL,
            rounds_done INTEGER NOT NULL DEFAULT 0,
            new_added INTEGER NOT NULL DEFAULT 0,
            total_posts_after INTEGER NOT NULL DEFAULT 0,
            stop_reason TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_profile_slug ON posts(profile_slug)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_first_seen ON posts(first_seen_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_last_seen ON posts(last_seen_at)")
    conn.commit()


def count_posts(conn: sqlite3.Connection, profile_slug: str) -> int:
    """统计指定 profile_slug 的帖子总数。"""
    row = conn.execute("SELECT COUNT(1) FROM posts WHERE profile_slug=?", (profile_slug,)).fetchone()
    return int(row[0]) if row else 0


def insert_or_touch_posts(conn: sqlite3.Connection, urls: list[str], seen_at: str, profile_slug: str) -> int:
    """插入新帖子 URL，或更新已有帖子的 last_seen_at 和 seen_count。返回新增数量。"""
    new_count = 0
    for url in urls:
        post_id = post_id_from_url(url)
        if not post_id:
            continue

        existing = conn.execute("SELECT post_id FROM posts WHERE post_id=?", (post_id,)).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO posts(post_id, link, profile_slug, first_seen_at, last_seen_at, seen_count)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (post_id, url, profile_slug, seen_at, seen_at),
            )
            new_count += 1
        else:
            conn.execute(
                """
                UPDATE posts
                SET last_seen_at=?, seen_count=seen_count+1
                WHERE post_id=?
                """,
                (seen_at, post_id),
            )
    conn.commit()
    return new_count


def load_posts_from_db(db_path: Path, limit: int, offset: int, profile_slug: str) -> list[dict[str, str]]:
    """从 SQLite 加载指定 profile_slug 的帖子列表，用于 HTML 下载阶段。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        query = "SELECT post_id, link FROM posts WHERE profile_slug = ? ORDER BY first_seen_at ASC"
        params: list[Any] = [profile_slug]
        if limit > 0:
            query += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        elif offset > 0:
            query += " LIMIT -1 OFFSET ?"
            params.append(int(offset))

        rows = conn.execute(query, params).fetchall()
        return [{"post_id": str(r["post_id"]), "link": str(r["link"])} for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 滚动与采集
# ---------------------------------------------------------------------------

def fetch_visible_post_urls(page: Any) -> list[str]:
    """从 Profile 主页 Feed 中提取当前可见的帖子 URL，仅限主内容区避免侧栏污染。"""
    hrefs: list[str] = []
    for selector in PROFILE_MAIN_FEED_SELECTORS:
        try:
            locator = page.locator(selector)
            if locator.count() == 0:
                continue
            hrefs = locator.first.locator("a[href*='/square/post/']").evaluate_all(
                "(els) => els.map(el => el.href).filter(Boolean)"
            )
            if hrefs:
                break
        except Exception:
            continue

    result: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        normalized = normalize_post_url(str(href))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def keep_profile_in_view(page: Any, profile_url: str) -> bool:
    """检测页面是否偏离 profile 页，如是则重新导航回去。返回是否发生了恢复导航。"""
    current = ""
    try:
        current = str(page.url or "")
    except Exception:
        current = ""

    target = profile_url.rstrip("/")
    if current.rstrip("/").startswith(target):
        return False

    print(f"[profile] navigated away: current='{current[:120]}' -> recovering to {profile_url}")
    page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1800)
    return True


_SCROLL_DIAG_COUNTER = 0
_SCROLL_DIAG_MAX = 5  # 只打印前几次的诊断信息


def progressive_scroll(page: Any, scroll_pixels: int, pause_seconds: float) -> None:
    """分步渐进式滚动，自适应选择最有效的滚动策略。"""
    global _SCROLL_DIAG_COUNTER

    step_count = 3
    step_pixels = max(400, int(scroll_pixels / step_count))
    step_wait_ms = int(max(0.25, pause_seconds / step_count) * 1000)

    # 诊断：记录滚动前的状态
    do_diag = _SCROLL_DIAG_COUNTER < _SCROLL_DIAG_MAX
    _SCROLL_DIAG_COUNTER += 1

    if do_diag:
        before_scroll_y = 0
        before_post_count = 0
        before_scroll_max = 0
        try:
            before_scroll_y = page.evaluate("() => window.scrollY || window.pageYOffset || 0")
            before_post_count = page.evaluate(
                "() => document.querySelectorAll('a[href*=\"/square/post/\"]').length"
            )
            before_scroll_max = page.evaluate(
                "() => Math.max(document.body.scrollHeight || 0, document.documentElement.scrollHeight || 0)"
            )
        except Exception:
            pass

    # 每轮先尝试点击 "View More" / "Load More" 按钮（如果存在）
    click_view_more_if_present(page)

    # 将鼠标移到视口中央偏下的位置（模拟用户滚动时的鼠标位置）
    viewport = page.viewport_size
    if viewport:
        try:
            page.mouse.move(viewport["width"] // 2, int(viewport["height"] * 0.7))
        except Exception:
            pass

    for _ in range(step_count):
        # 方法 1：mouse.wheel 模拟真实滚动
        try:
            page.mouse.wheel(0, step_pixels)
        except Exception:
            pass

        # 方法 2：window.scrollBy 确保 window 级别也滚动
        try:
            page.evaluate("(y) => window.scrollBy(0, y)", int(step_pixels))
        except Exception:
            pass

        # 方法 3：寻找并滚动所有可滚动的容器
        try:
            page.evaluate(
                """
                (args) => {
                    const delta = Number(args.delta || 0);
                    const allElements = document.querySelectorAll('*');
                    for (const el of allElements) {
                        try {
                            const style = window.getComputedStyle(el);
                            if (!style) continue;
                            const overflowY = style.overflowY || '';
                            const canScroll = (overflowY.includes('auto') || overflowY.includes('scroll'))
                                && (el.scrollHeight - el.clientHeight > 20);
                            if (canScroll) {
                                el.scrollBy(0, delta);
                            }
                        } catch (e) {}
                    }
                }
                """,
                {"delta": int(step_pixels)},
            )
        except Exception:
            pass

        # 方法 4：按 PageDown 键（有时能触发键盘导航的懒加载）
        if step_pixels > 500:
            try:
                page.keyboard.press("End")
            except Exception:
                pass

        page.wait_for_timeout(step_wait_ms)

    # 滚动完成后，轮询等待新内容加载（最多 3 秒）
    try:
        initial_count = page.evaluate(
            "() => document.querySelectorAll('a[href*=\"/square/post/\"]').length"
        )
        for _ in range(12):  # 最多等 3 秒 (12 * 250ms)
            page.wait_for_timeout(250)
            try:
                current_count = page.evaluate(
                    "() => document.querySelectorAll('a[href*=\"/square/post/\"]').length"
                )
                if isinstance(current_count, (int, float)) and current_count > initial_count:
                    if do_diag:
                        print(f"[scroll-diag] new posts loaded after scroll, count: {initial_count} → {current_count}")
                    break
            except Exception:
                pass
    except Exception:
        pass

    # 诊断：滚动后的状态
    if do_diag:
        try:
            after_scroll_y = page.evaluate("() => window.scrollY || window.pageYOffset || 0")
            after_post_count = page.evaluate(
                "() => document.querySelectorAll('a[href*=\"/square/post/\"]').length"
            )
            after_scroll_max = page.evaluate(
                "() => Math.max(document.body.scrollHeight || 0, document.documentElement.scrollHeight || 0)"
            )
            print(
                f"[scroll-diag] scrollY: {before_scroll_y} → {after_scroll_y} "
                f"(delta={after_scroll_y - before_scroll_y}) | "
                f"post_links: {before_post_count} → {after_post_count} "
                f"(delta={after_post_count - before_post_count}) | "
                f"scrollHeight: {before_scroll_max} → {after_scroll_max} "
                f"(delta={after_scroll_max - before_scroll_max})"
            )
        except Exception:
            pass


def click_view_more_if_present(page: Any) -> bool:
    """仅在 Feed 区域内点击 'View More' / '查看更多' 按钮，触发加载更多帖子。"""
    selectors = [
        "button:has-text('View More')",
        "a:has-text('View More')",
        "button:has-text('Load More')",
        "a:has-text('Load More')",
        "button:has-text('查看更多')",
        "a:has-text('查看更多')",
        "button:has-text('加载更多')",
        "a:has-text('加载更多')",
    ]
    # 仅在 Feed 区域范围内搜索（绝对不做页面级回退，会误触侧边栏导航）
    for root in PROFILE_MAIN_FEED_SELECTORS:
        try:
            area = page.locator(root)
            if area.count() == 0:
                continue
            scoped = area.first
            for selector in selectors:
                locator = scoped.locator(selector)
                if locator.count() > 0:
                    locator.first.click(timeout=1500)
                    page.wait_for_timeout(600)
                    return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Feed 切换（Highlights → All）
# ---------------------------------------------------------------------------

def click_first_visible(page: Any, selectors: list[str], timeout_ms: int = 3000) -> bool:
    """在全局页面中点击第一个匹配的可见元素。"""
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                locator.first.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def click_first_visible_in_profile_main(page: Any, selectors: list[str], timeout_ms: int = 3000) -> bool:
    """在 Profile 主内容区中点击第一个匹配的可见元素。"""
    for root in PROFILE_MAIN_FEED_SELECTORS:
        try:
            root_locator = page.locator(root)
            if root_locator.count() == 0:
                continue
            area = root_locator.first
            for selector in selectors:
                locator = area.locator(selector)
                if locator.count() > 0:
                    locator.first.click(timeout=timeout_ms)
                    return True
        except Exception:
            continue
    return False


def click_exact_text_in_profile_main(page: Any, texts: list[str]) -> bool:
    """在 Profile 主内容区中点击文本精确匹配的可见元素。"""
    try:
        return bool(
            page.evaluate(
                r"""
                (args) => {
                    const roots = args.roots || [];
                    const textSet = new Set((args.texts || []).map(t => String(t || '').trim()));
                    if (!textSet.size) return false;

                    const isVisible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        if (!rect || rect.width <= 0 || rect.height <= 0) return false;
                        const style = window.getComputedStyle(el);
                        if (!style) return false;
                        return style.visibility !== 'hidden' && style.display !== 'none';
                    };

                    for (const rootSel of roots) {
                        const root = document.querySelector(rootSel);
                        if (!root) continue;

                        const nodes = Array.from(root.querySelectorAll('div,button,span'));
                        for (const el of nodes) {
                            if (!isVisible(el)) continue;
                            const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
                            if (!textSet.has(txt)) continue;
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
                """,
                {"roots": PROFILE_MAIN_FEED_SELECTORS, "texts": texts},
            )
        )
    except Exception:
        return False


def click_exact_text_global(page: Any, texts: list[str]) -> bool:
    """在全局页面中点击文本精确匹配的可见元素。"""
    try:
        return bool(
            page.evaluate(
                r"""
                (args) => {
                    const textSet = new Set((args.texts || []).map(t => String(t || '').trim()));
                    if (!textSet.size) return false;

                    const isVisible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        if (!rect || rect.width <= 0 || rect.height <= 0) return false;
                        const style = window.getComputedStyle(el);
                        if (!style) return false;
                        return style.visibility !== 'hidden' && style.display !== 'none';
                    };

                    const nodes = Array.from(document.querySelectorAll('div,li,button,span'));
                    for (const el of nodes) {
                        if (!isVisible(el)) continue;
                        const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
                        if (!textSet.has(txt)) continue;
                        el.click();
                        return true;
                    }
                    return false;
                }
                """,
                {"texts": texts},
            )
        )
    except Exception:
        return False


def js_click_category_trigger(page: Any, texts: list[str]) -> bool:
    """用 JS 事件在 Profile 主内容区点击分类触发元素（更底层的点击方式）。"""
    try:
        return bool(
            page.evaluate(
                r"""
                (args) => {
                    const roots = args.roots || [];
                    const textSet = new Set((args.texts || []).map(t => String(t || '').trim()));
                    if (!textSet.size) return false;

                    const isVisible = (el) => {
                        const r = el.getBoundingClientRect();
                        if (!r || r.width <= 0 || r.height <= 0) return false;
                        const s = getComputedStyle(el);
                        return s.display !== 'none' && s.visibility !== 'hidden';
                    };

                    const emitClick = (el) => {
                        const opts = { bubbles: true, cancelable: true, view: window };
                        el.dispatchEvent(new MouseEvent('mousedown', opts));
                        el.dispatchEvent(new MouseEvent('mouseup', opts));
                        el.dispatchEvent(new MouseEvent('click', opts));
                    };

                    for (const rootSel of roots) {
                        const root = document.querySelector(rootSel);
                        if (!root) continue;
                        const candidates = Array.from(root.querySelectorAll('div.category, div[role="tab"], div'));
                        for (const el of candidates) {
                            if (!isVisible(el)) continue;
                            const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
                            if (!txt) continue;
                            for (const t of textSet) {
                                if (txt === t || txt.startsWith(t + ' ')) {
                                    emitClick(el);
                                    return true;
                                }
                            }
                        }
                    }
                    return false;
                }
                """,
                {"roots": PROFILE_MAIN_FEED_SELECTORS, "texts": texts},
            )
        )
    except Exception:
        return False


def get_current_feed_filter_label(page: Any) -> str:
    """获取当前 Feed 筛选器的标签文本（如 'Highlights' 或 'All'）。"""
    selectors = [
        "div.hover-bg-input.text-PrimaryText.flex.cursor-pointer.items-center.justify-between",
        "div[class*='hover-bg-input'][class*='cursor-pointer'][class*='justify-between']",
        "div.category[class*='text-PrimaryText']",
        "div.category.text-PrimaryText",
    ]

    # 先在 Feed 区域内搜索
    for root in PROFILE_MAIN_FEED_SELECTORS:
        try:
            area = page.locator(root)
            if area.count() == 0:
                continue
            scoped = area.first
            for selector in selectors:
                trigger = scoped.locator(selector)
                if trigger.count() == 0:
                    continue
                text = str(trigger.first.inner_text() or "")
                text = " ".join(text.split()).strip()
                if text:
                    return text
        except Exception:
            continue

    # 回退：page-wide 搜索（标签可能在 Feed 区域之外）
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                text = str(locator.first.inner_text() or "")
                text = " ".join(text.split()).strip()
                if text:
                    return text
        except Exception:
            continue

    return ""


def is_login_page(page: Any) -> bool:
    """检测当前页面是否为登录页。"""
    try:
        current = str(page.url or "").lower()
    except Exception:
        return False
    return "accounts.binance.com" in current or "/login" in current


def _is_logged_in_to_binance(page: Any) -> bool:
    """检测用户是否已登录 Binance（通过检查页面头部的用户相关元素）。"""
    logged_in_indicators = [
        "#header_avatar",
        "[class*='avatar']",
        "a[href*='/my/wallet']",
        "a[href*='/my/orders']",
        "div[id*='avatar']",
    ]
    for selector in logged_in_indicators:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue

    # 反向检查：没有 "Log In" 按钮也可能说明已登录
    try:
        login_btn = page.locator("a:has-text('Log In'), button:has-text('Log In')")
        if login_btn.count() == 0:
            # 既没有登录按钮，也没有被重定向到登录页 → 可能已登录
            if not is_login_page(page):
                return True
    except Exception:
        pass

    return False


def _wait_for_feed_filter_tabs(page: Any, timeout_seconds: float = 15.0) -> bool:
    """等待 Profile 页面的 Feed 筛选标签页（Highlights/All/Latest）渲染出来。"""
    tab_selectors = [
        "div[role='tab']",
        "div.category",
        "div[class*='category']",
    ]
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        for selector in tab_selectors:
            try:
                el = page.locator(selector)
                if el.count() > 0:
                    text = " ".join((el.first.inner_text() or "").split()).strip()
                    if text and len(text) < 40:
                        return True
            except Exception:
                continue
        page.wait_for_timeout(800)
    return False


def ensure_profile_page_ready(page: Any, profile_url: str, wait_for_login: bool, headless: bool) -> None:
    """确保 Profile 页面已加载且非登录页；如检测到登录页且允许手动登录则等待。"""
    if not is_login_page(page):
        return

    if wait_for_login and (not headless):
        input("[login] Login page detected. Complete login in browser, then press Enter to continue... ")
        page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1800)
        return

    raise RuntimeError(
        "Login page detected before switching feed filter. "
        "Please run with --wait-for-login in non-headless mode and complete login first."
    )


def dump_feed_tabs(page: Any) -> List[str]:
    """打印 Profile 页面 Feed 区域所有可用的标签页/分类选项，用于诊断 UI 结构。"""
    tabs_found: List[str] = []
    print("[profile] === Feed filter tabs found on page ===")

    # 1. role='tab' 元素
    try:
        for el in page.locator("div[role='tab']").all():
            text = " ".join((el.inner_text() or "").split()).strip()
            selected = el.get_attribute("aria-selected")
            cls = el.get_attribute("class") or ""
            if text and text not in tabs_found:
                tabs_found.append(text)
                print(f"  [role=tab] aria-selected={selected} cls='{cls[:80]}': '{text}'")
    except Exception:
        pass

    # 2. div.category 元素（打印 class 和父元素信息）
    try:
        for el in page.locator("div.category").all():
            text = " ".join((el.inner_text() or "").split()).strip()
            cls = el.get_attribute("class") or ""
            if text and text not in tabs_found:
                tabs_found.append(text)
            # 获取父元素的标签和 class
            parent_info = ""
            try:
                parent_js = el.evaluate("el => { const p = el.parentElement; return p ? p.tagName + ' .' + (p.className || '').split(' ').slice(0,3).join('.') : ''; }")
                parent_info = f" parent={parent_js}"
            except Exception:
                pass
            print(f"  [div.category] cls='{cls[:80]}'{parent_info}: '{text}'")
    except Exception:
        pass

    # 3. 在 PROFILE_MAIN_FEED_SELECTORS 范围内查找任何类似标签的元素
    for root_sel in PROFILE_MAIN_FEED_SELECTORS:
        try:
            area = page.locator(root_sel)
            if area.count() == 0:
                continue
            scoped = area.first
            for el in scoped.locator("div[class*='category'], div[role='tab'], div[class*='tab']").all():
                text = " ".join((el.inner_text() or "").split()).strip()
                if text and len(text) < 40 and text not in tabs_found:
                    tabs_found.append(text)
                    cls = el.get_attribute("class") or ""
                    print(f"  [{root_sel[:30]}...] cls='{cls[:60]}': '{text}'")
        except Exception:
            continue

    # 4. 诊断：查找任何包含 Latest/最新/All/全部 文本的元素（包括隐藏的）
    print("[profile] --- Elements containing 'Latest'/'最新'/'All'/'全部' (visible only) ---")
    try:
        diagnostic = page.evaluate("""
            () => {
                const targets = ['Latest', '最新', 'All', '全部', 'Highlights', '精选', '精選'];
                const results = [];
                const allEls = document.querySelectorAll('div, span, li, button, a');
                for (const el of allEls) {
                    const rect = el.getBoundingClientRect();
                    if (!rect || rect.width <= 0 || rect.height <= 0) continue;
                    const style = getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const txt = (el.textContent || '').replace(/\\s+/g, ' ').trim();
                    for (const t of targets) {
                        if (txt === t || txt.startsWith(t + ' ')) {
                            const cls = (el.className || '').toString();
                            const parent = el.parentElement;
                            const pCls = parent ? (parent.className || '').toString() : '';
                            results.push({
                                tag: el.tagName,
                                text: txt.substring(0, 40),
                                cls: cls.substring(0, 80),
                                parentTag: parent ? parent.tagName : '',
                                parentCls: pCls.substring(0, 80),
                                rect: JSON.stringify({x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)})
                            });
                            break;
                        }
                    }
                }
                return results;
            }
        """)
        if diagnostic:
            for item in diagnostic[:15]:
                print(f"  <{item['tag']}> cls='{item['cls']}' rect={item['rect']} text='{item['text']}' parent=<{item['parentTag']}> cls='{item['parentCls']}'")
        else:
            print("  (none found)")
    except Exception:
        print("  (diagnostic failed)")

    if not tabs_found:
        print("  (none found)")
    print("[profile] ================================")
    return tabs_found


def ensure_logged_in_and_feed_ready(
    page: Any, profile_url: str, args: argparse.Namespace
) -> None:
    """确保用户已登录且 Feed 筛选标签页已渲染，然后才允许切换 'All' 标签。

    在页面加载后、调用 prepare_profile_feed() 之前调用。
    如果用户未登录，根据 --wait-for-login 决定等待或报错。
    """
    page.wait_for_timeout(2000)

    if _is_logged_in_to_binance(page):
        if _wait_for_feed_filter_tabs(page, timeout_seconds=12.0):
            return
        print("[profile] WARNING: feed filter tabs not found after timeout; continuing anyway...")
        return

    # 未检测到登录状态
    print("[profile] WARNING: not logged in — feed filter tabs may differ from expected layout")

    if bool(getattr(args, "wait_for_login", False)) and not bool(getattr(args, "headless", False)):
        input("[login] Please complete login in browser, then press Enter to continue... ")
        page.wait_for_timeout(2000)
        if _is_logged_in_to_binance(page):
            if _wait_for_feed_filter_tabs(page, timeout_seconds=12.0):
                print("[profile] logged in and feed tabs ready")
                return
        else:
            print("[profile] WARNING: still not logged in after prompt")

    # 尝试等待更长时间，让未登录状态下的页面渲染完成
    if _wait_for_feed_filter_tabs(page, timeout_seconds=12.0):
        print("[profile] feed filter tabs found (login state uncertain)")
        return

    print("[profile] WARNING: feed filter tabs not found; All-switching may be unreliable")


def is_all_selected_in_profile_main(page: Any) -> bool:
    """检查 Profile 主内容区的 Feed 筛选器是否已切换到目标 feed（All/Latest）。"""
    label = get_current_feed_filter_label(page).lower()
    if not label:
        return False
    # 旧版目标标签（All + Latest）
    if label.startswith("all") or label.startswith("全部") or label.startswith("latest") or label.startswith("最新"):
        return True
    return False


def switch_to_all_listing(page: Any) -> bool:
    """将 Profile Feed 切换到 'All' 或 'Latest' 列表。

    策略：
      1. 如果当前标签已经是目标标签（All/Latest），直接返回 True。
      2. 尝试直接点击 "Latest" / "All" 标签页。
      3. 点击 Highlights/精选 打开下拉菜单 → 在下拉菜单中点击 Latest/最新。
    """
    target_tab_texts = ["Latest", "最新", "All", "全部"]
    target_tab_texts_safe = ["Latest", "最新"]  # 仅用于 get_by_text（全文无范围搜索，必须精确安全）
    source_tab_texts = ["Highlights", "精选", "精選"]

    # 如果已经在目标标签上，不需要切换
    if is_all_selected_in_profile_main(page):
        return True

    # ---- 策略 A：直接点击 "Latest" / "All" 标签页 ----
    # 方法 1: Playwright get_by_text — 仅搜索 Latest/最新（不会误匹配 Trending Topics）
    for text in target_tab_texts_safe:
        try:
            el = page.get_by_text(text, exact=True).first
            if el:
                el.click(timeout=1500)
                page.wait_for_timeout(600)
                if is_all_selected_in_profile_main(page):
                    print(f"[switch-tab] strategy=A get_by_text clicked '{text}'")
                    return True
        except Exception:
            pass

    # 方法 2: CSS 选择器 — 包含 All/全部，但有 div[role='tab']/div.category 范围限制
    for text in target_tab_texts:
        direct_clicked = click_first_visible_in_profile_main(
            page,
            [
                f"div[role='tab']:has-text('{text}')",
                f"div.category:has-text('{text}')",
            ],
            timeout_ms=1500,
        )
        if direct_clicked:
            page.wait_for_timeout(600)
            if is_all_selected_in_profile_main(page):
                print(f"[switch-tab] strategy=A clicked '{text}' tab")
                return True

    # ---- 策略 B：点击 Highlights/精选 → 下拉菜单 → Latest/最新 ----
    print("[switch-tab] strategy=B: trying dropdown approach...")

    # Step 1: 点击当前标签（Highlights/精选）打开下拉菜单
    dropdown_opened = False
    for text in source_tab_texts:
        # 方法 a: Playwright get_by_text（原生点击，模拟真实鼠标）
        try:
            el = page.get_by_text(text, exact=True).first
            if el:
                el.click(timeout=2000)
                dropdown_opened = True
                print(f"[switch-tab] strategy=B step1 get_by_text('{text}') clicked")
                break
        except Exception:
            pass

    if not dropdown_opened:
        page.wait_for_timeout(random.randint(200, 500))  # 反爬：随机延时
        # 方法 b: CSS 选择器
        for text in source_tab_texts:
            if click_first_visible_in_profile_main(
                page,
                [f"div.category:has-text('{text}')", f"div[role='tab']:has-text('{text}')"],
                timeout_ms=2000,
            ):
                dropdown_opened = True
                print(f"[switch-tab] strategy=B step1 scoped click '{text}'")
                break
            if click_first_visible(
                page,
                [f"div.category:has-text('{text}')", f"div[role='tab']:has-text('{text}')"],
                timeout_ms=2000,
            ):
                dropdown_opened = True
                print(f"[switch-tab] strategy=B step1 global click '{text}'")
                break

    if not dropdown_opened:
        page.wait_for_timeout(random.randint(200, 500))  # 反爬：随机延时
        # 方法 c: JS 全局精确文本点击
        for text in source_tab_texts:
            if click_exact_text_global(page, [text]):
                dropdown_opened = True
                print(f"[switch-tab] strategy=B step1 JS click '{text}'")
                break

    if not dropdown_opened:
        print("[switch-tab] strategy=B: could not click Highlights/精选 to open dropdown")
        return is_all_selected_in_profile_main(page)

    # 等待下拉菜单出现
    page.wait_for_timeout(800)

    # Step 2: 诊断 — 打印弹窗中所有候选元素
    try:
        diagnostic_result = page.evaluate(
            """
            () => {
                const targets = ['Latest', '最新', 'All', '全部'];
                const results = [];
                const containers = document.querySelectorAll(
                    'div[role="tooltip"], [class*="bn-tooltips-wrap"], [class*="popup"], [class*="dropdown"], [class*="tooltip"], [class*="menu"], [class*="overlay"], div.shadow-2, [class*="portal"], [class*="select"], [class*="option-list"]'
                );
                for (const container of containers) {
                    const rect = container.getBoundingClientRect();
                    if (!rect || rect.width <= 0 || rect.height <= 0) continue;
                    const style = getComputedStyle(container);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const items = container.querySelectorAll('div, li, span, button, [role="menuitem"], [role="option"]');
                    for (const item of items) {
                        const ir = item.getBoundingClientRect();
                        if (!ir || ir.width <= 0 || ir.height <= 0) continue;
                        const istyle = getComputedStyle(item);
                        if (istyle.display === 'none' || istyle.visibility === 'hidden') continue;
                        const txt = (item.textContent || '').replace(/\\s+/g, ' ').trim();
                        for (const t of targets) {
                            if (txt === t || txt.startsWith(t + ' ') || txt.includes(t)) {
                                const cls = (item.className || '').toString();
                                results.push({
                                    tag: item.tagName,
                                    text: txt.substring(0, 60),
                                    cls: cls.substring(0, 80),
                                    role: item.getAttribute('role') || '',
                                    rect: JSON.stringify({x: Math.round(ir.x), y: Math.round(ir.y), w: Math.round(ir.width), h: Math.round(ir.height)})
                                });
                                break;
                            }
                        }
                    }
                }
                return results;
            }
            """
        )
        if diagnostic_result:
            print("[profile] === Popup/dropdown candidates containing All/Latest ===")
            for item in diagnostic_result[:10]:
                print(f"  <{item['tag']}> role='{item['role']}' cls='{item['cls']}' rect={item['rect']} text='{item['text']}'")
            print("[profile] ==============================================")
    except Exception:
        pass

    # Step 3: 在下拉菜单中查找并点击 "Latest" / "最新" / "All" / "全部"
    option_clicked = False

    # 方法 a: Playwright get_by_text — 仅搜索 Latest/最新（全文搜索，不能搜 All/全部 避免误触 Trending Topics）
    for text in target_tab_texts_safe:
        try:
            el = page.get_by_text(text, exact=True).last
            if el:
                el.click(timeout=2000)
                page.wait_for_timeout(random.randint(400, 700))  # 反爬：随机等待
                if is_all_selected_in_profile_main(page):
                    option_clicked = True
                    print(f"[switch-tab] strategy=B step3 get_by_text('{text}') clicked")
                    break
        except Exception:
            pass

    # 方法 b: JS 在弹出容器中查找并点击
    if not option_clicked:
        page.wait_for_timeout(random.randint(200, 500))  # 反爬：随机延时
        option_clicked = page.evaluate(
            """
            (args) => {
                const targetTexts = args.texts || [];
                const isVisible = (el) => {
                    const r = el.getBoundingClientRect();
                    if (!r || r.width <= 0 || r.height <= 0) return false;
                    const s = getComputedStyle(el);
                    return s.display !== 'none' && s.visibility !== 'hidden';
                };
                // 扩展容器选择器列表
                const containers = document.querySelectorAll(
                    'div[role="tooltip"], [class*="bn-tooltips-wrap"], [class*="popup"], [class*="dropdown"], [class*="tooltip"], [class*="menu"], [class*="overlay"], div.shadow-2, [class*="portal"], [class*="select"], [class*="option-list"], [class*="bn-flex"], [class*="drawer"]'
                );
                for (const container of containers) {
                    if (!isVisible(container)) continue;
                    const items = container.querySelectorAll('div, li, span, button, [role="menuitem"], [role="option"]');
                    for (const item of items) {
                        if (!isVisible(item)) continue;
                        const txt = (item.textContent || '').replace(/\\s+/g, ' ').trim();
                        for (const target of targetTexts) {
                            if (txt === target || txt.startsWith(target + ' ')) {
                                item.click();
                                return true;
                            }
                        }
                    }
                }
                return false;
            }
            """,
            {"texts": ["Latest", "最新", "All", "全部"]},
        )
        if option_clicked:
            print("[switch-tab] strategy=B step3 JS popup click succeeded")
            page.wait_for_timeout(random.randint(400, 700))

    # 方法 c: Playwright CSS 选择器回退
    if not option_clicked:
        page.wait_for_timeout(random.randint(200, 500))  # 反爬：随机延时
        option_selectors = []
        for container in [
            "div[role='tooltip']",
            "div[class*='bn-tooltips-wrap']",
            "div.shadow-2",
            "div[class*='popup']",
            "div[class*='dropdown']",
            "div[class*='menu']",
            "div[class*='overlay']",
            "div[class*='portal']",
        ]:
            for text in ["All", "全部", "Latest", "最新"]:
                option_selectors.append(f"{container} div:has-text('{text}')")
                option_selectors.append(f"{container} span:has-text('{text}')")
                option_selectors.append(f"{container} li:has-text('{text}')")
        for role in ["menuitem", "option"]:
            for text in ["All", "全部", "Latest", "最新"]:
                option_selectors.append(f"[role='{role}']:has-text('{text}')")

        option_clicked = click_first_visible(page, option_selectors, timeout_ms=3000)
        if option_clicked:
            print("[switch-tab] strategy=B step3 CSS fallback clicked")
    page.wait_for_timeout(random.randint(400, 700))
    final_result = is_all_selected_in_profile_main(page)
    print(f"[switch-tab] final result={final_result} label='{get_current_feed_filter_label(page)}'")
    return final_result


def prepare_profile_feed(page: Any) -> str:
    """准备 Profile Feed：确保已切换到 'All' 模式。返回使用的切换策略名称。"""
    current_label = get_current_feed_filter_label(page)
    if is_all_selected_in_profile_main(page):
        return f"all_already_selected({current_label})"

    for attempt in range(1, 4):
        try:
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(random.randint(200, 500))  # 反爬：随机延时
        except Exception:
            pass

        ok = switch_to_all_listing(page)
        if ok:
            new_label = get_current_feed_filter_label(page)
            return f"dropdown_all_attempt_{attempt}({new_label})"

        # 重试前随机等待，避免触发反爬
        if attempt < 3:
            jitter = random.randint(500, 1500)
            print(f"[switch-tab] attempt {attempt} failed, retrying after {jitter}ms...")
            page.wait_for_timeout(jitter)

    # 回退：如果切换失败但仍在 Highlights 上，接受 Highlights 作为后备
    fallback_label = get_current_feed_filter_label(page).lower()
    if fallback_label.startswith("highlights") or fallback_label.startswith("精选") or fallback_label.startswith("精選"):
        print("[profile] 警告: 无法切换到 Latest，回退到 Highlights")
        return "fallback_highlights(attempts_failed)"

    return "none"


def maybe_pause_after_switch_all(page: Any, args: argparse.Namespace) -> None:
    """如果启用了 --pause-after-switch-all，暂停让用户手动确认 UI 状态。"""
    if not bool(getattr(args, "pause_after_switch_all", False)):
        return

    if bool(getattr(args, "headless", False)):
        print("[profile] pause-after-switch-all is enabled but headless mode is on; skipping interactive pause")
        return

    all_selected = is_all_selected_in_profile_main(page)
    current_label = get_current_feed_filter_label(page)
    visible_links = len(fetch_visible_post_urls(page))
    print(
        f"[profile] pause checkpoint: all_selected={all_selected} current_filter_label='{current_label}' visible_profile_links={visible_links}"
    )
    input("[profile] Please verify UI is switched to All, then press Enter to continue... ")


def ensure_all_selected_or_raise(page: Any, args: argparse.Namespace) -> None:
    """如果启用了 --require-all-selected 且 Feed 未切换到 All，则抛出异常。"""
    if not bool(getattr(args, "require_all_selected", False)):
        return

    all_selected = is_all_selected_in_profile_main(page)
    current_label = get_current_feed_filter_label(page)
    if all_selected:
        return

    raise RuntimeError(
        "Feed filter is not switched to All. "
        f"current_filter_label='{current_label}'. "
        "Please verify the UI manually or retry with --pause-after-switch-all."
    )


# ---------------------------------------------------------------------------
# APP_DATA 提取与质量过滤（内联自 fetch_pages_from_db.py）
# ---------------------------------------------------------------------------

def extract_app_data(html_content: str) -> dict[str, Any] | None:
    """从 HTML 中提取 __APP_DATA 内嵌 JSON 数据。"""
    patterns = [
        r'<script id="__APP_DATA" type="application/json">(.*?)</script>',
        r"window\.__APP_DATA__\s*=\s*(\{.*?\});",
    ]
    for pattern in patterns:
        match = re.search(pattern, html_content, re.DOTALL)
        if not match:
            continue
        try:
            raw = match.group(1).strip()
            if not raw:
                continue
            # 处理 HTML 实体
            raw = re.sub(r"&lt;", "<", raw)
            raw = re.sub(r"&gt;", ">", raw)
            raw = re.sub(r"&amp;", "&", raw)
            raw = re.sub(r"&quot;", '"', raw)
            raw = re.sub(r"&#39;", "'", raw)
            return json.loads(raw)
        except Exception:
            continue
    return None


def find_post_data_in_app_data(app_data: dict[str, Any], post_id: str) -> dict[str, Any]:
    """在 APP_DATA JSON 树中递归定位指定 post_id 的帖子数据。"""
    result: dict[str, Any] = {}

    def walk(node: Any, depth: int = 0) -> bool:
        nonlocal result
        if depth > 16:
            return False
        if isinstance(node, dict):
            node_post_id = str(node.get("postId") or "")
            node_content_id = str(node.get("contentId") or "")
            node_id = str(node.get("id") or "")
            if node_post_id == post_id or node_content_id == post_id:
                result = node
                return True
            if node_id == post_id:
                result = node
                return True

            for value in node.values():
                if walk(value, depth + 1):
                    return True

        elif isinstance(node, list):
            for item in node:
                if walk(item, depth + 1):
                    return True
        return False

    walk(app_data)
    return result


def extract_comment_total_num(post_data: dict[str, Any]) -> int:
    """从帖子数据中提取评论总数，遍历直接键名和嵌套结构。"""
    direct_keys = ["commentCount", "commentNum", "commentTotal", "commentsCount"]
    for key in direct_keys:
        value = post_data.get(key)
        if value is None:
            continue
        try:
            num = int(value)
            if num >= 0:
                return num
        except Exception:
            continue

    max_count = 0

    def walk(node: Any, depth: int = 0) -> None:
        nonlocal max_count
        if depth > 10:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if key in direct_keys:
                    try:
                        max_count = max(max_count, int(value))
                    except Exception:
                        pass
                walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(post_data)
    return max_count


def extract_products(post_data: dict[str, Any], html_content: str) -> list[str]:
    """从帖子数据和 HTML 中提取产品符号（如 BTC、ETH）。

    从 post_data 中递归搜索已知键名和 $SYMBOL 模式；
    如果未找到则回退到 HTML 全文搜索 $SYMBOL 模式。
    """
    symbols: set[str] = set()
    key_whitelist = {"symbol", "baseAsset", "pair", "coinSymbol", "ticker", "asset"}

    def normalize_symbol(raw: Any) -> str:
        token = re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()
        if 2 <= len(token) <= 15 and any(c.isalpha() for c in token):
            return token
        return ""

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 10:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if key in key_whitelist and isinstance(value, str):
                    symbol = normalize_symbol(value)
                    if symbol:
                        symbols.add(symbol)
                walk(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
        elif isinstance(node, str):
            for match in re.findall(r"\$([A-Z][A-Z0-9]{1,9})\b", node.upper()):
                symbol = normalize_symbol(match)
                if symbol:
                    symbols.add(symbol)

    walk(post_data)
    if symbols:
        return sorted(symbols)

    # 回退到 HTML 全文搜索
    for match in re.findall(r"\$([A-Z][A-Z0-9]{1,9})\b", html_content.upper()):
        symbol = normalize_symbol(match)
        if symbol:
            symbols.add(symbol)
    return sorted(symbols)


def normalize_timestamp_to_ms(value: Any) -> int:
    """将时间戳值（Unix 秒/毫秒、ISO 8601、常见日期格式）统一转为毫秒。"""
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0

    # Unix 时间戳（秒或毫秒）
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        num = int(text)
        return num if num > 10**12 else num * 1000

    # ISO 8601 和常见日期格式
    for parser in (
        lambda s: datetime.fromisoformat(s.replace("Z", "").replace("T", " ")).timestamp(),
        lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp(),
        lambda s: datetime.strptime(s, "%Y-%m-%d").timestamp(),
    ):
        try:
            return int(parser(re.sub(r"\.\d+", "", text)) * 1000)
        except Exception:
            continue
    return 0


def extract_post_time_ms(post_data: dict[str, Any], html_content: str) -> int:
    """从帖子数据或 JSON-LD 中提取发布时间（毫秒）。"""
    time_keys = [
        "createTime", "publishTime", "postTime", "publishedDate",
        "createdTime", "publishDate", "publishAt", "time", "date",
    ]

    # 从 post_data 中查找时间字段
    for key in time_keys:
        value = post_data.get(key)
        if value is not None and str(value).strip():
            ms = normalize_timestamp_to_ms(value)
            if ms > 0:
                return ms

    def walk(node: Any, depth: int = 0) -> int:
        if depth > 10:
            return 0
        if isinstance(node, dict):
            for key in time_keys:
                value = node.get(key)
                if value is not None and str(value).strip():
                    ms = normalize_timestamp_to_ms(value)
                    if ms > 0:
                        return ms
            for value in node.values():
                result = walk(value, depth + 1)
                if result > 0:
                    return result
        elif isinstance(node, list):
            for item in node:
                result = walk(item, depth + 1)
                if result > 0:
                    return result
        return 0

    result = walk(post_data)
    if result > 0:
        return result

    # JSON-LD 回退
    pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    matches = re.findall(pattern, html_content, re.DOTALL | re.IGNORECASE)
    for json_ld in matches:
        text = re.sub(r"&lt;", "<", json_ld)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&amp;", "&", text)
        text = text.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue

        candidates: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            candidates.append(payload)
            graph = payload.get("@graph")
            if isinstance(graph, list):
                candidates.extend([item for item in graph if isinstance(item, dict)])
        elif isinstance(payload, list):
            candidates.extend([item for item in payload if isinstance(item, dict)])

        for node in candidates:
            if not isinstance(node, dict):
                continue
            val = node.get("datePublished") or node.get("dateCreated")
            if val is not None and str(val).strip():
                ms = normalize_timestamp_to_ms(val)
                if ms > 0:
                    return ms

    return 0


def quick_filter_from_html(
    html_content: str, post_id: str, args: argparse.Namespace
) -> tuple[bool, dict[str, Any]]:
    """下载时即时过滤：从 HTML 中提取元数据，判断是否应保存。

    返回 (passed, metadata)。
    passed=False 表示该帖子应被过滤掉（不保存 HTML）。
    metadata 包含 comment_total_num、products、post_time_ms、filter_reasons 等字段。
    """
    metadata: dict[str, Any] = {
        "comment_total_num": 0,
        "products": [],
        "post_time_ms": 0,
        "filter_reasons": [],
    }

    # 提取 APP_DATA 并定位帖子数据
    app_data = extract_app_data(html_content)
    post_data: dict[str, Any] = {}
    if app_data:
        post_data = find_post_data_in_app_data(app_data, post_id)

    # 提取评论总数
    comment_total_num = extract_comment_total_num(post_data)
    metadata["comment_total_num"] = comment_total_num

    # 提取产品符号
    products = extract_products(post_data, html_content)
    metadata["products"] = products

    # 提取发布时间
    post_time_ms = extract_post_time_ms(post_data, html_content)
    metadata["post_time_ms"] = post_time_ms

    # 应用过滤规则
    # --require-content：要求至少有 1 条评论或有产品符号
    if bool(getattr(args, "require_content", False)):
        if comment_total_num == 0 and len(products) == 0:
            metadata["filter_reasons"].append("no_content")

    # --min-comment-total：评论数不达标
    min_comment = int(getattr(args, "min_comment_total", 0) or 0)
    if min_comment > 0 and comment_total_num < min_comment:
        metadata["filter_reasons"].append("low_comment_total")

    # --require-products：要求至少有 1 个产品符号
    if bool(getattr(args, "require_products", False)):
        if len(products) == 0:
            metadata["filter_reasons"].append("no_products")

    # --min-post-age-days：帖子不够 N 天
    min_age_days = int(getattr(args, "min_post_age_days", 0) or 0)
    if min_age_days > 0:
        if post_time_ms <= 0:
            metadata["filter_reasons"].append("missing_post_time_for_age_filter")
        else:
            now_ms = int(time.time() * 1000)
            min_age_ms = min_age_days * 86400 * 1000
            if now_ms - post_time_ms < min_age_ms:
                metadata["filter_reasons"].append("too_recent_for_label")

    passed = len(metadata["filter_reasons"]) == 0
    return passed, metadata


# ---------------------------------------------------------------------------
# 多线程 HTML 下载
# ---------------------------------------------------------------------------

def fetch_html_multi_threaded(
    db_path: Path,
    output_dir: Path,
    profile_slug: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """多线程并行下载 HTML：从 DB 加载 URL，分发给工作线程并行处理。

    每个工作线程使用独立的 Playwright 实例以确保线程安全。
    返回统计信息字典，包含 ok/failed/filtered/skipped 计数和详情列表。
    """
    posts = load_posts_from_db(
        db_path=db_path,
        limit=int(args.html_limit),
        offset=int(args.html_offset),
        profile_slug=profile_slug,
    )

    if not posts:
        print("[fetch-html] no posts found in DB for given limit/offset")
        return {
            "total": 0,
            "ok": 0,
            "failed": 0,
            "filtered": 0,
            "skipped": 0,
            "failures": [],
            "filtered_details": [],
            "filtered_reason_counter": {},
        }

    ensure_dir(output_dir)
    total = len(posts)
    workers = max(1, int(getattr(args, "workers", DEFAULT_WORKERS) or DEFAULT_WORKERS))

    # 线程共享数据结构
    stats_lock = threading.Lock()
    ok_count = 0
    failed_count = 0
    filtered_count = 0
    skipped_count = 0
    failures: list[dict[str, str]] = []
    filtered_details: list[dict[str, Any]] = []
    filtered_reason_counter: dict[str, int] = {}

    # 进度跟踪
    processed_count = 0
    progress_lock = threading.Lock()

    def _fetch_worker(worker_id: int) -> None:
        """单个工作线程：从队列取 URL → 打开页面 → 过滤 → 保存 HTML。"""
        nonlocal ok_count, failed_count, filtered_count, skipped_count, processed_count

        playwright_obj = None
        context = None
        try:
            # 每个线程创建独立的 Playwright 实例（sync API 不跨线程共享）
            playwright_obj = sync_playwright().start()
            _browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
            if args.user_data_dir:
                # 所有 worker 都使用持久化 profile 以复用登录态
                context = playwright_obj.chromium.launch_persistent_context(
                    user_data_dir=str(args.user_data_dir),
                    headless=bool(args.headless),
                    viewport={"width": 1366, "height": 900},
                    args=_browser_args,
                )
            else:
                browser = playwright_obj.chromium.launch(headless=bool(args.headless), args=_browser_args)
                context = browser.new_context(viewport={"width": 1366, "height": 900})

            page = context.new_page()

            for idx, post in enumerate(posts):
                # 按 worker_id 分片：worker i 处理索引 i, i+N, i+2N, ...
                if idx % workers != worker_id:
                    continue

                post_id = post["post_id"]
                url = post["link"]
                html_path = output_dir / f"{post_id}.html"

                # 跳过已存在的 HTML（除非 --html-overwrite）
                if html_path.exists() and not bool(args.html_overwrite):
                    with stats_lock:
                        skipped_count += 1
                    continue

                try:
                    # 打开文章页面
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(1500)

                    # 点击 "Replies" 标签页以加载真实评论（默认显示的是 Related Posts）
                    try:
                        replies_tab = page.locator('[role="tab"]:has-text("Replies")')
                        if replies_tab.count() > 0:
                            replies_tab.first.click()
                            page.wait_for_timeout(3000)
                            # 滚动以触发懒加载更多回复
                            for _ in range(3):
                                page.mouse.wheel(0, 500)
                                page.wait_for_timeout(800)
                            # 展开 "View Hidden Replies"（如果有）
                            view_hidden = page.locator('text="View Hidden Replies"')
                            if view_hidden.count() > 0:
                                try:
                                    view_hidden.first.click(force=True)
                                    page.wait_for_timeout(1500)
                                    for _ in range(3):
                                        page.mouse.wheel(0, 500)
                                        page.wait_for_timeout(800)
                                except Exception:
                                    pass
                    except Exception:
                        pass  # 如果点击失败，继续用默认页面内容

                    html_content = page.content()

                    # 即时质量过滤
                    passed, metadata = quick_filter_from_html(html_content, post_id, args)

                    if not passed:
                        with stats_lock:
                            filtered_count += 1
                            for reason in metadata.get("filter_reasons", []):
                                filtered_reason_counter[reason] = (
                                    filtered_reason_counter.get(reason, 0) + 1
                                )
                            filtered_details.append(
                                {
                                    "post_id": post_id,
                                    "url": url,
                                    "filter_reasons": sorted(set(metadata.get("filter_reasons", []))),
                                    "comment_total_num": int(metadata.get("comment_total_num") or 0),
                                    "products": metadata.get("products") or [],
                                    "post_time_ms": int(metadata.get("post_time_ms") or 0),
                                }
                            )
                        continue

                    # 保存 HTML
                    html_path.write_text(html_content, encoding="utf-8")

                    with stats_lock:
                        ok_count += 1

                except Exception as exc:
                    with stats_lock:
                        failed_count += 1
                        failures.append({"post_id": post_id, "url": url, "error": str(exc)})

                # 线程间暂停（比单线程时的 pause_seconds 短）
                pause = float(args.pause_seconds or 0.8)
                if pause > 0:
                    time.sleep(pause * 0.3)

        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if playwright_obj:
                try:
                    playwright_obj.stop()
                except Exception:
                    pass

    print(f"[fetch-html] starting {workers} workers for {total} posts")

    # 启动工作线程
    threads = []
    for i in range(workers):
        t = threading.Thread(target=_fetch_worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)

    # 等待所有线程完成，打印进度
    while any(t.is_alive() for t in threads):
        with stats_lock:
            current_processed = ok_count + failed_count + filtered_count + skipped_count
            _ok = ok_count
            _skipped = skipped_count
            _filtered = filtered_count
            _failed = failed_count
        if current_processed != processed_count:
            with progress_lock:
                processed_count = current_processed
            print(
                f"[fetch-html] progress {processed_count}/{total} "
                f"ok={_ok} skipped={_skipped} "
                f"filtered={_filtered} failed={_failed}"
            )
        time.sleep(2)

    for t in threads:
        t.join(timeout=5)

    # 写入摘要和详情文件
    summary = {
        "db_path": str(db_path),
        "output_dir": str(output_dir),
        "profile_slug": profile_slug,
        "total_requested": total,
        "ok": ok_count,
        "skipped": skipped_count,
        "filtered": filtered_count,
        "failed": failed_count,
        "filtered_reason_counter": filtered_reason_counter,
        "workers": workers,
        "filters": {
            "require_content": bool(getattr(args, "require_content", False)),
            "min_comment_total": int(getattr(args, "min_comment_total", 0) or 0),
            "require_products": bool(getattr(args, "require_products", False)),
            "min_post_age_days": int(args.min_post_age_days),
        },
    }
    summary_path = output_dir / "fetch_pages_from_db_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if failures:
        failure_path = output_dir / "fetch_pages_from_db_failures.json"
        failure_path.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")

    if filtered_details:
        filtered_path = output_dir / "fetch_pages_from_db_filtered.json"
        filtered_path.write_text(json.dumps(filtered_details, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[fetch-html] done requested={total} ok={ok_count} skipped={skipped_count} "
        f"filtered={filtered_count} failed={failed_count}"
    )
    print(f"[fetch-html] summary: {summary_path}")

    return {
        "total": total,
        "ok": ok_count,
        "failed": failed_count,
        "filtered": filtered_count,
        "skipped": skipped_count,
        "failures": failures,
        "filtered_details": filtered_details,
        "filtered_reason_counter": filtered_reason_counter,
    }


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def write_posts_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """将帖子列表写入 CSV 文件（UTF-8 BOM 编码）。"""
    fieldnames = [
        "post_id",
        "time",
        "title",
        "subtitle",
        "content",
        "author",
        "author_username",
        "like_count",
        "comment_count",
        "view_count",
        "share_count",
        "related_symbols",
        "link",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export_posts(conn: sqlite3.Connection, csv_path: Path, json_path: Path, export_limit: int, profile_slug: str) -> int:
    """导出帖子到 CSV 和 JSON 文件。返回导出行数。"""
    query = (
        "SELECT post_id, link, profile_slug, first_seen_at, last_seen_at, seen_count "
        "FROM posts WHERE profile_slug=? ORDER BY first_seen_at ASC"
    )
    params: list[Any] = [profile_slug]
    if export_limit > 0:
        query += " LIMIT ?"
        params.append(int(export_limit))

    records = conn.execute(query, tuple(params)).fetchall()
    rows: list[dict[str, Any]] = []
    raw: list[dict[str, Any]] = []

    for post_id, link, row_profile_slug, first_seen_at, last_seen_at, seen_count in records:
        rows.append(
            {
                "post_id": str(post_id),
                "time": str(first_seen_at),
                "title": "",
                "subtitle": "",
                "content": "",
                "author": "",
                "author_username": "",
                "like_count": 0,
                "comment_count": 0,
                "view_count": 0,
                "share_count": 0,
                "related_symbols": "",
                "link": str(link),
            }
        )
        raw.append(
            {
                "post_id": str(post_id),
                "link": str(link),
                "profile_slug": str(row_profile_slug),
                "first_seen_at": str(first_seen_at),
                "last_seen_at": str(last_seen_at),
                "seen_count": int(seen_count),
            }
        )

    write_posts_csv(csv_path, rows)
    json_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)


# ---------------------------------------------------------------------------
# 运行记录
# ---------------------------------------------------------------------------

def update_run_end(
    conn: sqlite3.Connection,
    run_id: int,
    ended_at: str,
    rounds_done: int,
    new_added: int,
    total_posts_after: int,
    stop_reason: str,
) -> None:
    """更新 runs 表中的运行结束信息。"""
    conn.execute(
        """
        UPDATE runs
        SET ended_at=?, rounds_done=?, new_added=?, total_posts_after=?, stop_reason=?
        WHERE id=?
        """,
        (ended_at, rounds_done, new_added, total_posts_after, stop_reason, run_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 解析阶段衔接
# ---------------------------------------------------------------------------

def run_parse_stage(args: argparse.Namespace, html_output_dir: Path, parsed_output: Path) -> None:
    """调用 parse_article.py 子进程解析 HTML（Binance News 文章专用解析器）。"""
    if not args.parse_html:
        return

    parser_script = Path(__file__).resolve().with_name("parse_article.py")
    if not parser_script.exists():
        raise FileNotFoundError(f"parse_article.py not found: {parser_script}")

    command = [
        sys.executable,
        str(parser_script),
        "--batch",
        "--input",
        str(html_output_dir),
        "--output",
        str(parsed_output),
        "--t-window-hours",
        str(int(args.t_window_hours)),
        "--price-interval",
        str(args.price_interval),
    ]

    print("[profile] parse-stage command:", " ".join(command))
    subprocess.run(command, check=True)


def run_filter_stage(args: argparse.Namespace, parsed_output: Path) -> None:
    """调用 clean_labeled_data.py 子进程过滤解析后的 JSON 记录。"""
    if not args.filter_parsed:
        return

    if not parsed_output.exists():
        print(f"[profile] filter-stage: parsed output not found, skipping: {parsed_output}")
        return

    cleaner_script = Path(__file__).resolve().with_name("clean_labeled_data.py")
    if not cleaner_script.exists():
        raise FileNotFoundError(f"clean_labeled_data.py not found: {cleaner_script}")

    stem = parsed_output.stem
    parent = parsed_output.parent

    filtered_output = (
        Path(args.filtered_output) if args.filtered_output
        else parent / f"{stem}_clean.json"
    )
    filtered_dropped = (
        Path(args.filtered_dropped_output) if args.filtered_dropped_output
        else parent / f"{stem}_dropped.json"
    )
    filtered_report = (
        Path(args.filtered_report_output) if args.filtered_report_output
        else parent / f"{stem}_clean_report.json"
    )

    command = [
        sys.executable,
        str(cleaner_script),
        "--input", str(parsed_output),
        "--output", str(filtered_output),
        "--dropped-output", str(filtered_dropped),
        "--report-output", str(filtered_report),
        "--min-comment-total", str(int(args.min_comment_total)),
    ]

    if args.drop_no_products:
        command.append("--drop-no-products")
    if args.drop_label_error:
        command.append("--drop-label-error")
    if args.keep_comment_error_posts:
        command.append("--keep-comment-error-posts")

    print("[profile] filter-stage command:", " ".join(command))
    subprocess.run(command, check=True)


def verify_author_spot_check(parsed_output: Path, author_text: str, sample_size: int) -> dict[str, Any]:
    """抽查解析结果中的作者名是否与预期匹配。"""
    if not author_text:
        return {
            "enabled": False,
            "message": "verify-author-text not set",
        }

    if not parsed_output.exists():
        return {
            "enabled": True,
            "ok": False,
            "reason": "parsed_output_not_found",
            "path": str(parsed_output),
        }

    try:
        data = json.loads(parsed_output.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "enabled": True,
            "ok": False,
            "reason": "parsed_output_load_error",
            "error": str(exc),
        }

    if not isinstance(data, list) or not data:
        return {
            "enabled": True,
            "ok": False,
            "reason": "parsed_output_empty_or_invalid",
        }

    chosen = data[: max(1, int(sample_size))]
    hits = 0
    details: list[dict[str, str]] = []

    needle = author_text.lower().strip()
    for item in chosen:
        author = str(item.get("post_author") or "")
        if needle and needle in author.lower():
            hits += 1
        details.append(
            {
                "post_id": str(item.get("post_id") or ""),
                "post_author": author,
            }
        )

    checked = len(chosen)
    ratio = hits / checked if checked else 0.0
    return {
        "enabled": True,
        "ok": ratio >= 0.7,
        "checked": checked,
        "hits": hits,
        "ratio": ratio,
        "author_text": author_text,
        "sample": details,
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> None:
    """主函数：解析参数 → 初始化 → URL 采集 → 多线程 HTML 下载 → 解析。"""
    args = parse_args()

    output_dir = Path(args.output_dir)
    ensure_dir(output_dir)

    db_path = Path(args.db_path) if args.db_path else output_dir / DEFAULT_DB_NAME
    html_output_dir = Path(args.html_output_dir) if args.html_output_dir else output_dir / "html_pages"
    parsed_output = Path(args.parsed_output) if args.parsed_output else output_dir / "profile_parsed.json"

    csv_path = output_dir / "binance_profile_posts.csv"
    raw_json_path = output_dir / "binance_profile_posts_raw.json"
    run_summary_path = output_dir / "crawler_profile_last_run.json"

    profile_slug = (args.profile_slug or "").strip() or profile_slug_from_url(args.profile_url)
    if not profile_slug:
        raise ValueError("Cannot derive profile slug. Please pass --profile-slug explicitly.")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)

    started_at = now_text()
    run_row = conn.execute(
        """
        INSERT INTO runs(started_at, profile_url, profile_slug, target_posts, max_scroll_rounds, idle_stop_rounds)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            started_at,
            str(args.profile_url),
            profile_slug,
            int(args.target_posts),
            int(args.max_scroll_rounds),
            int(args.idle_stop_rounds),
        ),
    )
    conn.commit()
    run_id = int(run_row.lastrowid)

    existing_before = count_posts(conn, profile_slug)
    print(f"[profile] existing unique posts in db({profile_slug})={existing_before}")

    # === --check-only 模式 ===
    if args.check_only:
        playwright_obj, context = create_browser_context(
            headless=args.headless,
            user_data_dir=args.user_data_dir,
        )
        try:
            page = context.new_page()
            print(f"[check] open {args.profile_url}")
            page.goto(args.profile_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)

            if args.wait_for_login:
                input("[login] Complete login in browser, then press Enter to continue check... ")
                page.wait_for_timeout(1500)

            ensure_profile_page_ready(
                page=page,
                profile_url=str(args.profile_url),
                wait_for_login=bool(args.wait_for_login),
                headless=bool(args.headless),
            )

            ensure_logged_in_and_feed_ready(page, str(args.profile_url), args)

            dump_feed_tabs(page)

            strategy = prepare_profile_feed(page)
            print(f"[check] feed preparation strategy={strategy}")
            print(f"[check] current_filter_label='{get_current_feed_filter_label(page)}'")
            maybe_pause_after_switch_all(page, args)
            ensure_all_selected_or_raise(page, args)
            sample_urls = fetch_visible_post_urls(page)
            print(f"[check] visible profile post links={len(sample_urls)}")
            print("[check] crawler_profile browser flow is ready")

            summary = {
                "started_at": started_at,
                "ended_at": now_text(),
                "profile_url": str(args.profile_url),
                "profile_slug": profile_slug,
                "stop_reason": "check_only",
                "feed_strategy": strategy,
                "db_path": str(db_path),
            }
            run_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

            update_run_end(
                conn=conn,
                run_id=run_id,
                ended_at=summary["ended_at"],
                rounds_done=0,
                new_added=0,
                total_posts_after=existing_before,
                stop_reason="check_only",
            )
            return
        finally:
            safe_close_browser(playwright_obj, context)
            conn.close()

    # === 已达目标，跳过采集 ===
    if existing_before >= int(args.target_posts):
        exported = export_posts(conn, csv_path, raw_json_path, int(args.export_limit), profile_slug)
        summary = {
            "started_at": started_at,
            "ended_at": now_text(),
            "profile_url": str(args.profile_url),
            "profile_slug": profile_slug,
            "target_posts": int(args.target_posts),
            "existing_before": int(existing_before),
            "new_added": 0,
            "total_posts_after": int(existing_before),
            "rounds_done": 0,
            "stop_reason": "target_already_reached",
            "db_path": str(db_path),
            "exported_rows": int(exported),
            "csv": str(csv_path),
            "raw_json": str(raw_json_path),
        }
        run_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        update_run_end(
            conn=conn,
            run_id=run_id,
            ended_at=summary["ended_at"],
            rounds_done=0,
            new_added=0,
            total_posts_after=existing_before,
            stop_reason="target_already_reached",
        )
        conn.close()

        # 多线程下载 HTML（替代原来的 subprocess 调用）
        if args.fetch_html:
            fetch_html_multi_threaded(
                db_path=db_path,
                output_dir=html_output_dir,
                profile_slug=profile_slug,
                args=args,
            )
        run_parse_stage(args, html_output_dir, parsed_output)

        if args.filter_parsed:
            run_filter_stage(args, parsed_output)

        verify_result = verify_author_spot_check(parsed_output, args.verify_author_text, args.verify_sample_size)
        print("[profile] verify:", json.dumps(verify_result, ensure_ascii=False))
        return

    # === 正式采集 ===
    playwright_obj, context = create_browser_context(
        headless=args.headless,
        user_data_dir=args.user_data_dir,
    )

    rounds_done = 0
    new_added_total = 0
    idle_rounds = 0
    stop_reason = "max_scroll_rounds_reached"
    start_epoch = time.time()
    feed_strategy = "none"

    try:
        page = context.new_page()
        print(f"[profile] open {args.profile_url}")
        page.goto(args.profile_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)

        if args.wait_for_login:
            input("[login] Profile opened. Complete login, then press Enter to start collection... ")
            page.wait_for_timeout(1500)

        ensure_profile_page_ready(
            page=page,
            profile_url=str(args.profile_url),
            wait_for_login=bool(args.wait_for_login),
            headless=bool(args.headless),
        )

        ensure_logged_in_and_feed_ready(page, str(args.profile_url), args)

        dump_feed_tabs(page)

        feed_strategy = prepare_profile_feed(page)
        feed_switched_ok = feed_strategy != "none"  # 记录切换是否成功
        print(f"[profile] feed preparation strategy={feed_strategy}")
        print(f"[profile] current_filter_label='{get_current_feed_filter_label(page)}'")
        maybe_pause_after_switch_all(page, args)
        ensure_all_selected_or_raise(page, args)

        for round_idx in range(1, int(args.max_scroll_rounds) + 1):
            rounds_done = round_idx

            recovered = keep_profile_in_view(page, args.profile_url)
            if recovered:
                # add_init_script 确保拦截在 goto 后依然生效，无需重新注入
                if not is_all_selected_in_profile_main(page):
                    feed_strategy = prepare_profile_feed(page)

            round_urls = fetch_visible_post_urls(page)
            new_added = insert_or_touch_posts(conn, round_urls, now_text(), profile_slug)
            total_now = count_posts(conn, profile_slug)

            new_added_total += new_added
            if new_added == 0:
                idle_rounds += 1
            else:
                idle_rounds = 0

            if (
                round_idx == 1
                or round_idx % max(1, int(args.checkpoint_every)) == 0
                or new_added > 0
            ):
                print(
                    f"[profile] round={round_idx}/{args.max_scroll_rounds} "
                    f"visible={len(round_urls)} new_added={new_added} "
                    f"idle_rounds={idle_rounds}/{args.idle_stop_rounds} total_unique={total_now}/{args.target_posts}"
                )

            if new_added == 0 and idle_rounds > 0 and idle_rounds % 10 == 0:
                # 如果已经成功切换过，跳过空闲恢复中的重切换（避免误触其他 UI 元素）
                if not feed_switched_ok:
                    refreshed_strategy = prepare_profile_feed(page)
                    if refreshed_strategy != "none":
                        feed_strategy = refreshed_strategy
                        feed_switched_ok = True
                        print(f"[profile] idle recovery: reapplied feed strategy={refreshed_strategy}")
                if click_view_more_if_present(page):
                    print("[profile] idle recovery: clicked view-more style control")

            if total_now >= int(args.target_posts):
                stop_reason = "target_reached"
                break

            if idle_rounds >= int(args.idle_stop_rounds):
                stop_reason = "idle_stop_reached"
                break

            if float(args.max_runtime_minutes) > 0:
                elapsed_minutes = (time.time() - start_epoch) / 60.0
                if elapsed_minutes >= float(args.max_runtime_minutes):
                    stop_reason = "runtime_limit_reached"
                    break

            progressive_scroll(page, int(args.scroll_pixels), float(args.pause_seconds))

    finally:
        safe_close_browser(playwright_obj, context)

    total_after = count_posts(conn, profile_slug)
    exported = export_posts(conn, csv_path, raw_json_path, int(args.export_limit), profile_slug)
    ended_at = now_text()

    summary = {
        "started_at": started_at,
        "ended_at": ended_at,
        "profile_url": str(args.profile_url),
        "profile_slug": profile_slug,
        "target_posts": int(args.target_posts),
        "existing_before": int(existing_before),
        "new_added": int(new_added_total),
        "total_posts_after": int(total_after),
        "rounds_done": int(rounds_done),
        "stop_reason": stop_reason,
        "feed_strategy": feed_strategy,
        "db_path": str(db_path),
        "exported_rows": int(exported),
        "csv": str(csv_path),
        "raw_json": str(raw_json_path),
    }
    run_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    update_run_end(
        conn=conn,
        run_id=run_id,
        ended_at=ended_at,
        rounds_done=rounds_done,
        new_added=new_added_total,
        total_posts_after=total_after,
        stop_reason=stop_reason,
    )
    conn.close()

    # 多线程下载 HTML（替代原来的 subprocess 调用 fetch_pages_from_db.py）
    if args.fetch_html:
        fetch_html_multi_threaded(
            db_path=db_path,
            output_dir=html_output_dir,
            profile_slug=profile_slug,
            args=args,
        )
    run_parse_stage(args, html_output_dir, parsed_output)

    if args.filter_parsed:
        run_filter_stage(args, parsed_output)

    verify_result = verify_author_spot_check(parsed_output, args.verify_author_text, args.verify_sample_size)
    print("[profile] verify:", json.dumps(verify_result, ensure_ascii=False))

    print(
        f"[profile] done: stop_reason={stop_reason} new_added={new_added_total} "
        f"total_unique={total_after} exported={exported}"
    )


if __name__ == "__main__":
    main()
