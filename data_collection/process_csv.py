#!/usr/bin/env python3
"""
process_csv.py — 从 CSV 读取 URL → 下载 HTML → 筛选有 product 的页面 → 解析 → 流式输出 JSONL

用法:
  # 完整模式：下载前 10 条并解析（自动跳过已解析的帖子）
  python process_csv.py --limit 10 --output tmp/csv_test.jsonl

  # 快速模式：只解析已有 HTML（不重新下载）
  python process_csv.py --skip-existing --output tmp/csv_result.jsonl

  # 有浏览器窗口的调试模式
  python process_csv.py --limit 5 --no-headless --output tmp/csv_debug.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from data_collection.utils.crawler_util import ensure_dir, extract_richtext_text

# Playwright 惰性导入
sync_playwright = None

DEFAULT_USER_DATA_DIR = "data_collection/tmp_chrome_profile"
DEFAULT_CSV = "dataset/csv/master_news_dataset.csv"
DEFAULT_OUTPUT = "dataset/result/parsed.jsonl"
DEFAULT_HTML_DIR = "dataset/html/update_news"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="process_csv — 从 CSV 读取 URL → 下载 → 筛选 → 解析"
    )
    parser.add_argument("--csv", default=DEFAULT_CSV,
                        help=f"CSV 文件路径，默认 {DEFAULT_CSV}")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"输出 JSON 路径，默认 {DEFAULT_OUTPUT}")
    parser.add_argument("--html-dir", default=DEFAULT_HTML_DIR,
                        help=f"HTML 存储目录，默认 {DEFAULT_HTML_DIR}")
    parser.add_argument("--limit", type=int, default=0,
                        help="最多处理条数，0=全部")
    parser.add_argument("--headless", default=True, action="store_true",
                        help="浏览器无头模式（默认）")
    parser.add_argument("--no-headless", dest="headless", action="store_false",
                        help="浏览器可见（调试模式）")
    parser.add_argument("--overwrite", action="store_true",
                        help="覆盖已存在的 HTML 文件")
    parser.add_argument("--skip-existing", action="store_true",
                        help="快速模式：只解析已有 HTML，不下载")
    parser.add_argument("--comment-wait", type=int, default=5,
                        help="点击 Replies tab 后等待评论加载的秒数，默认 5")
    parser.add_argument("--comment-pages", type=int, default=10,
                        help="滚动触发评论分页次数，默认 10")
    parser.add_argument("--timeout-seconds", type=int, default=30,
                        help="页面超时秒数")
    parser.add_argument("--pause-seconds", type=float, default=1.0,
                        help="页面间等待秒数")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# URL / post_id 工具
# ---------------------------------------------------------------------------

def extract_post_id_from_url(url: str) -> str:
    """从 Binance Square URL 提取 post_id。"""
    m = re.search(r'/square/post/(\d+)', url)
    return m.group(1) if m else ""


def sanitize_filename(post_id: str) -> str:
    """清洗文件名，防止不合法字符。"""
    safe = re.sub(r"[^\w\-.]", "_", post_id)
    safe = re.sub(r"_+", "_", safe)
    safe = safe.strip(".")
    return safe or "post"


# ---------------------------------------------------------------------------
# CSV 读取
# ---------------------------------------------------------------------------

def read_urls_from_csv(csv_path: Path, limit: int = 0) -> list[dict[str, str]]:
    """读取 CSV，返回 [{url, post_id, content, time}] 已去重。"""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 文件不存在: {csv_path}")

    # 尝试多种编码
    for enc in ("gbk", "gb2312", "gb18030", "latin-1"):
        try:
            with open(csv_path, encoding=enc) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            print(f"[csv] 编码 {enc} 读取成功，共 {len(rows)} 行")
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        raise ValueError("无法解码 CSV 文件，请检查编码")

    # 找到 URL 列
    url_col = None
    for col in rows[0] if rows else []:
        col_lower = col.strip().lower()
        if col_lower in ("链接", "url", "link", "links", "网址"):
            url_col = col
            break
    if not url_col:
        # 用包含 http 的列
        for col in rows[0] if rows else []:
            if rows[0][col] and "http" in rows[0][col]:
                url_col = col
                break
    if not url_col:
        raise ValueError(f"CSV 中未找到 URL 列，可用列: {list(rows[0].keys()) if rows else 'empty'}")

    # 内容列（用于后续可能的信息展示）
    content_col = None
    for col in rows[0] if rows else []:
        col_lower = col.strip().lower()
        if col_lower in ("内容", "content", "标题", "title", "文本", "text"):
            content_col = col
            break

    # 时间列
    time_col = None
    for col in rows[0] if rows else []:
        col_lower = col.strip().lower()
        if col_lower in ("时间", "time", "日期", "date", "datetime"):
            time_col = col
            break

    seen: set[str] = set()
    results: list[dict[str, str]] = []
    for row in rows:
        url = (row.get(url_col) or "").strip()
        if not url:
            continue
        post_id = extract_post_id_from_url(url)
        if not post_id:
            continue
        if post_id in seen:
            continue
        seen.add(post_id)
        results.append({
            "url": url,
            "post_id": post_id,
            "content": (row.get(content_col) or "").strip() if content_col else "",
            "time": (row.get(time_col) or "").strip() if time_col else "",
        })
        if limit > 0 and len(results) >= limit:
            break

    print(f"[csv] 提取到 {len(results)} 个去重 URL（limit={limit if limit > 0 else '全部'}）")
    return results


# ---------------------------------------------------------------------------
# JSONL 流式输出
# ---------------------------------------------------------------------------

def load_parsed_post_ids(output_path: Path) -> set[str]:
    """读取已有 JSONL 输出文件，返回已解析的 post_id 集合。"""
    parsed_ids: set[str] = set()
    if not output_path.exists():
        return parsed_ids
    try:
        with open(output_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    pid = obj.get("post_id")
                    if pid:
                        parsed_ids.add(str(pid))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return parsed_ids


def append_to_jsonl(output_path: Path, result: dict[str, Any]) -> None:
    """以追加模式将单条解析结果写入 JSONL 文件（一行一个 JSON 对象）。"""
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Playwright 浏览器管理
# ---------------------------------------------------------------------------

def create_browser_context(
    headless: bool = True, user_data_dir: str = DEFAULT_USER_DATA_DIR
) -> tuple[Any, Any]:
    """创建 Playwright Chromium 浏览器上下文。"""
    global sync_playwright
    if sync_playwright is None:
        try:
            from playwright.sync_api import sync_playwright as sp
            sync_playwright = sp
        except ImportError:
            raise RuntimeError(
                "playwright is not installed. Run:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )

    playwright = sync_playwright().start()
    if user_data_dir:
        print(f"[browser] 使用持久化用户数据: {user_data_dir}")
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={"width": 1280, "height": 1600},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        return playwright, context

    print("[browser] 使用临时用户数据")
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context(
        viewport={"width": 1280, "height": 1600},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    )
    return playwright, context


def safe_close_browser(playwright_obj: Any, context: Any) -> None:
    """安全关闭浏览器。"""
    try:
        context.close()
    finally:
        if playwright_obj:
            playwright_obj.stop()


# ---------------------------------------------------------------------------
# 页面下载 + 评论拦截
# ---------------------------------------------------------------------------

def download_page(
    context: Any,
    url: str,
    post_id: str,
    output_dir: Path,
    overwrite: bool = False,
    timeout: int = 30,
    comment_wait: int = 5,
    comment_pages: int = 10,
) -> tuple[bool, str | None]:
    """
    下载单个 URL 的 HTML，拦截评论 API，保存侧边数据。
    返回 (success, html_file_path)。
    """
    safe_id = sanitize_filename(post_id)
    filename = safe_id + ".html"
    file_path = output_dir / filename
    comments_path = output_dir / f"{safe_id}_comments.json"

    if file_path.exists() and not overwrite:
        print(f"  [{post_id}] 文件已存在: {filename}")
        return True, str(file_path)

    if not url or "binance.com" not in url:
        print(f"  [{post_id}] 链接无效或不是币安链接: {url}")
        return False, None

    page = None
    try:
        page = context.new_page()
        page.set_default_timeout(timeout * 1000)

        # 拦截 bapi 响应
        api_raw_payloads: list[dict[str, Any]] = []

        def on_response(response: Any) -> None:
            u = response.url.lower()
            if "/bapi/" not in u and "/gateway-api/" not in u:
                return
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    api_raw_payloads.append({
                        "url": response.url,
                        "status": response.status,
                        "data": payload,
                    })
            except Exception:
                pass

        page.on("response", on_response)

        print(f"  [{post_id}] 访问: {url[:120]}")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        # 点击 Replies tab + 多轮滚动加载评论分页
        try:
            replies_tab = page.locator(
                "div[role='tab']:has-text('Replies'), "
                "div[role='tab']:has-text('回复'), "
                "div[role='tab']:has-text('Réponses'), "
                "div[role='tab']:has-text('Respuestas'), "
                "div[role='tab']:has-text('Antworten')"
            )
            if replies_tab.count() > 0:
                print(f"  [{post_id}] 点击 Replies tab")
                replies_tab.first.click(timeout=5000)
                page.wait_for_timeout(comment_wait * 1000)
                for _ in range(comment_pages):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1500)
            else:
                all_tabs = page.locator("div[role='tab']")
                if all_tabs.count() >= 2:
                    print(f"  [{post_id}] 点击第 2 个 tab (replies fallback)")
                    all_tabs.nth(1).click(timeout=5000)
                    page.wait_for_timeout(comment_wait * 1000)
                    for _ in range(comment_pages):
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(1500)
                else:
                    print(f"  [{post_id}] 未找到 Replies tab")
        except Exception as e:
            print(f"  [{post_id}] 点击 tab 失败: {e}")

        # 保存 HTML
        html = page.content()
        file_path.write_text(html, encoding="utf-8")
        print(f"  [{post_id}] 已保存 HTML: {filename} ({len(html)} bytes)")

        # 保存 API 原始数据 + 提取评论侧边文件
        if api_raw_payloads:
            debug_path = output_dir / f"{safe_id}_api_raw.json"
            debug_path.write_text(
                json.dumps(api_raw_payloads, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # 递归提取评论（含 childReplyPostList）
            def extract_comment_with_replies(item: dict, parent_id: str = "") -> list[dict[str, str]]:
                if not isinstance(item, dict):
                    return []
                if item.get("sourceType") != 5:
                    return []
                body_raw = item.get("body", "")
                text = extract_richtext_text(body_raw) or item.get("bodyTextOnly", "")
                if not text:
                    return []
                author = item.get("displayName") or item.get("username") or ""
                cid = str(item.get("id", ""))
                time_ms = item.get("firstReleaseTime") or item.get("createTime") or 0
                results = [{
                    "comment_id": cid,
                    "comment_author": author,
                    "comment_text": text,
                    "comment_time": str(time_ms),
                    "parent_comment_id": str(parent_id) if parent_id else "",
                }]
                for child in (item.get("childReplyPostList") or []):
                    results.extend(extract_comment_with_replies(child, cid))
                return results

            comments = []
            for api_resp in api_raw_payloads:
                if "/replyPost/list" not in api_resp["url"]:
                    continue
                if api_resp["status"] != 200:
                    continue
                items = api_resp.get("data", {}).get("data")
                if not isinstance(items, list):
                    continue
                for item in items:
                    comments.extend(extract_comment_with_replies(item))

            if comments:
                comments_path.write_text(
                    json.dumps(comments, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"  [{post_id}] 已保存 {len(comments)} 条评论: {comments_path.name}")
        else:
            print(f"  [{post_id}] 未捕获到 bapi 响应")

        return True, str(file_path)

    except Exception as exc:
        print(f"  [{post_id}] 下载失败: {exc}")
        return False, None
    finally:
        # 关闭页面释放浏览器内存，防止内存泄漏
        if page is not None:
            try:
                page.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Product 检查（从已保存的 HTML）
# ---------------------------------------------------------------------------

def check_products_from_html(html_path: Path) -> list[str]:
    """从已保存的 HTML 文件中提取产品列表。"""
    from data_collection.parsers.parse_article import (
        extract_app_data,
        extract_from_meta_tags,
        extract_products_from_html,
        find_post_data_in_app_data,
    )

    content = html_path.read_text(encoding="utf-8")
    post_id = html_path.stem
    meta_info = extract_from_meta_tags(content)
    app_data = extract_app_data(content)
    post_data = find_post_data_in_app_data(app_data, post_id) if app_data else {}
    post_content = meta_info.get("description") or post_data.get("content") or ""
    return extract_products_from_html(content, post_id, post_content, post_data)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    csv_path = Path(args.csv)
    html_dir = Path(args.html_dir)
    output_path = Path(args.output)
    ensure_dir(html_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 读取 CSV
    items = read_urls_from_csv(csv_path, args.limit)
    if not items:
        print("[process] CSV 中无有效 URL")
        return

    # 2. 快速模式：只解析已有 HTML
    if args.skip_existing:
        print(f"[process] 快速模式：扫描 {html_dir} 中已有 HTML...")
        parsed_ids = load_parsed_post_ids(output_path)
        if parsed_ids:
            print(f"[process] 已有输出文件中包含 {len(parsed_ids)} 条已解析记录")
        csv_post_ids = {item["post_id"] for item in items}
        existing_html = sorted(html_dir.glob("*.html"))
        processed = 0
        skipped = 0
        for html_file in existing_html:
            pid = html_file.stem
            if pid not in csv_post_ids:
                continue
            if pid in parsed_ids:
                skipped += 1
                continue
            print(f"  [{pid}] 解析中...")
            try:
                from data_collection.parsers.parse_article import parse_article_file
                result = parse_article_file(html_file)
                if result and result.get("comment_total_num", 0) > 0:
                    append_to_jsonl(output_path, result)
                    processed += 1
                    print(f"  [{pid}] 解析完成，{result['comment_num']} 条评论 → 已写入")
                else:
                    print(f"  [{pid}] 无评论，跳过")
            except Exception as e:
                print(f"  [{pid}] 解析失败: {e}")

        print(f"\n[process] 快速模式完成: 新增 {processed} 条，跳过已解析 {skipped} 条")
        if processed > 0:
            print(f"[process] 结果已追加写入: {output_path}")
        return

    # 3. 完整模式：下载 + 筛选 + 解析
    parsed_ids = load_parsed_post_ids(output_path)
    if parsed_ids:
        print(f"[process] 已有输出文件中包含 {len(parsed_ids)} 条已解析记录")

    playwright_obj, context = create_browser_context(headless=args.headless)

    try:
        total = len(items)
        processed = 0
        skipped_parsed = 0
        skipped_no_comment = 0
        failed = 0

        for i, item in enumerate(items):
            url = item["url"]
            post_id = item["post_id"]

            # 跳过已解析的帖子
            if post_id in parsed_ids:
                skipped_parsed += 1
                continue

            print(f"\n[{i+1}/{total}] {post_id}: {url[:100]}")

            # 下载
            html_path = html_dir / f"{sanitize_filename(post_id)}.html"
            if html_path.exists() and not args.overwrite:
                print(f"  [{post_id}] HTML 已存在，使用已有文件")
            else:
                success, _ = download_page(
                    context=context,
                    url=url,
                    post_id=post_id,
                    output_dir=html_dir,
                    overwrite=args.overwrite,
                    timeout=args.timeout_seconds,
                    comment_wait=args.comment_wait,
                    comment_pages=args.comment_pages,
                )
                if not success:
                    print(f"  [{post_id}] 跳过（下载失败）")
                    failed += 1
                    continue

            # 解析（不再根据 product 预过滤）
            try:
                from data_collection.parsers.parse_article import parse_article_file
                result = parse_article_file(html_path)
                if result and result.get("comment_total_num", 0) > 0:
                    append_to_jsonl(output_path, result)
                    processed += 1
                    print(f"  [{post_id}] 解析完成，{result['comment_num']} 条评论 → 已写入")
                else:
                    skipped_no_comment += 1
                    print(f"  [{post_id}] 无评论，跳过")
            except Exception as e:
                failed += 1
                print(f"  [{post_id}] 解析失败: {e}")
                import traceback
                traceback.print_exc()

            if i < total - 1:
                time.sleep(args.pause_seconds)

        print(
            f"\n[process] 完成: 总 {total} 条, "
            f"新增有评论 {processed} 条, "
            f"无评论 {skipped_no_comment} 条, "
            f"跳过已解析 {skipped_parsed} 条, "
            f"失败 {failed} 条"
        )
        if processed > 0:
            print(f"[process] 结果已追加写入: {output_path}")

    except KeyboardInterrupt:
        print("\n[process] 用户中断（已解析的帖子已保存）")
    except Exception as exc:
        print(f"[process] 发生错误: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        safe_close_browser(playwright_obj, context)


if __name__ == "__main__":
    main()
