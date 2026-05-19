"""
process_jinse.py — 读取 CSV 中的 URL → Playwright 下载 HTML → 提取正文/评论 → 输出 JSONL

用法:
  python process_jinse.py
"""

import csv
import json
import re
import time
from pathlib import Path

# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

CSV_PATH = Path("dataset/csv/jinse_news.csv")
HTML_DIR = Path("dataset/html/jinse")
OUTPUT_JSONL = Path("dataset/result/jinse_parsed.jsonl")

# 页面正文 CSS 选择器（按优先级尝试）
CONTENT_SELECTORS = [
    "div.article-content",
    "div.detail-content",
    "div.js-live-wrapper",
    "article",
]

# 评论 API URL 关键词
COMMENT_API_KEYWORDS = ["comment/list", "getComment", "commentList", "reply/list"]

# Playwright 配置
HEADLESS = True
USER_DATA_DIR = "data_collection/tmp_chrome_profile_jinse"  # 独立缓存，不和币安混用
TIMEOUT_SECONDS = 30
COMMENT_WAIT_SECONDS = 5
PAUSE_SECONDS = 1.0
LIMIT = 0  # 最多处理条数，0=全部


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════


def read_urls(csv_path: Path, limit: int = 0) -> list[dict]:
    """从 CSV 读取 URL 列表，自动检测列名和编码。"""
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            with open(csv_path, encoding=enc) as f:
                rows = list(csv.DictReader(f))
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    url_col = None
    for col in rows[0]:
        if col.strip().lower() in ("链接", "url", "link"):
            url_col = col
            break
    if not url_col:
        for col in rows[0]:
            if rows[0][col] and "http" in rows[0][col]:
                url_col = col
                break

    results = []
    for row in rows:
        url = (row.get(url_col) or "").strip()
        if not url:
            continue
        results.append({"url": url})
        if limit > 0 and len(results) >= limit:
            break

    print(f"从 CSV 读取 {len(results)} 条 URL")
    return results


def extract_id_from_url(url: str) -> str:
    """从金色财经 URL 提取新闻 ID。

    示例:
      https://www.jinse2.com/lives/511438.html → "511438"
      https://www.jinse2.com/blockchain/3732533.html → "3732533"
    """
    m = re.search(r"/(\d+)\.html", url)
    if m:
        return m.group(1)
    m = re.search(r"/(\d+)$", url)
    if m:
        return m.group(1)
    return re.sub(r"[^\w\-.]", "_", url)[:50]


def extract_content(html: str) -> dict:
    """从 HTML 中提取标题、正文、时间。

    金色财经页面是 Nuxt.js SSR 渲染，核心数据在 window.__NUXT__ 中。
    """
    import datetime

    result = {"title": "", "content": "", "time": "", "author": "金色财经"}

    # 从 __NUXT__ 提取 content 字段（核心正文）
    m = re.search(r'content:"(.+?)",content_prefix:', html)
    if m:
        content = m.group(1)
        content = content.replace("\\u002F", "/").replace("\\n", "\n").replace("\\\\", "\\")
        result["content"] = content

    # content_prefix 是变量引用（如 e），需要从函数调用参数中解析
    m = re.search(r'content_prefix:(\w+),', html)
    if m:
        var_name = m.group(1)
        var_idx = ord(var_name) - ord('a')
        m2 = re.search(r'\}\}\(([^)]+)\)', html)
        if m2:
            args = m2.group(1).split(',')
            if var_idx < len(args):
                prefix = args[var_idx].strip().strip('"').strip("'")
                result["title"] = prefix

    # 时间戳
    m = re.search(r'created_at:(\d+)', html)
    if m:
        ts = int(m.group(1))
        result["time"] = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    return result


def load_comments_from_sidecar(html_path: Path) -> list[dict]:
    """加载 sidecar 评论文件。"""
    stem = html_path.stem
    comments_path = html_path.parent / f"{stem}_comments.json"
    if comments_path.exists():
        with open(comments_path, encoding="utf-8") as f:
            return json.load(f)
    return []


# ═══════════════════════════════════════════════════════════
# Playwright 浏览器
# ═══════════════════════════════════════════════════════════


def launch_browser():
    """启动 Playwright Chromium 浏览器。"""
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    context = p.chromium.launch_persistent_context(
        user_data_dir=USER_DATA_DIR,
        headless=HEADLESS,
        viewport={"width": 1280, "height": 1600},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    )
    return p, context


# ═══════════════════════════════════════════════════════════
# 页面下载
# ═══════════════════════════════════════════════════════════


def download_page(context, url: str, news_id: str, html_dir: Path) -> str | None:
    """下载单个 URL 的 HTML，拦截评论 API 响应。

    返回保存的 HTML 文件路径，失败返回 None。
    """
    html_path = html_dir / f"{news_id}.html"

    if html_path.exists():
        print(f"  [{news_id}] HTML 已存在，跳过下载")
        return str(html_path)

    api_payloads = []

    def on_response(response):
        u = response.url.lower()
        if any(kw.lower() in u for kw in COMMENT_API_KEYWORDS):
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    api_payloads.append(payload)
            except Exception:
                pass

    page = None
    try:
        page = context.new_page()
        page.on("response", on_response)
        page.set_default_timeout(TIMEOUT_SECONDS * 1000)

        print(f"  [{news_id}] 访问: {url[:100]}")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        # 滚动底部触发评论懒加载
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(COMMENT_WAIT_SECONDS * 1000)

        # 保存 HTML
        html = page.content()
        html_path.write_text(html, encoding="utf-8")
        print(f"  [{news_id}] 已保存 HTML ({len(html)} bytes)")

        # 提取评论
        if api_payloads:
            comments = _extract_comments(api_payloads)
            comments_path = html_dir / f"{news_id}_comments.json"
            comments_path.write_text(
                json.dumps(comments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  [{news_id}] 已保存 {len(comments)} 条评论")

        return str(html_path)

    except Exception as e:
        print(f"  [{news_id}] 下载失败: {e}")
        return None
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass


def _extract_comments(payloads: list[dict]) -> list[dict]:
    """从 API 响应中递归提取评论。"""
    results = []

    def _recurse(obj, depth=0):
        if depth > 5:
            return
        if isinstance(obj, list) and depth > 0 and all(isinstance(x, dict) for x in obj):
            for item in obj:
                text = (
                    item.get("content")
                    or item.get("body")
                    or item.get("text")
                    or item.get("message")
                    or ""
                )
                if text and isinstance(text, str) and len(text) > 2:
                    results.append({
                        "author": item.get("username") or item.get("author") or item.get("nickname") or "",
                        "text": text.strip(),
                        "time": item.get("create_time") or item.get("created_at") or item.get("time") or "",
                    })
        elif isinstance(obj, dict):
            for v in obj.values():
                _recurse(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                _recurse(v, depth + 1)

    _recurse({"root": payloads})
    return results


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════


def main():
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)

    # 读取 CSV
    items = read_urls(CSV_PATH, LIMIT)
    if not items:
        print("CSV 中无有效 URL")
        return

    # 启动浏览器
    playwright_obj, context = launch_browser()

    try:
        total = len(items)
        parsed_count = 0

        for i, item in enumerate(items):
            url = item["url"]
            news_id = extract_id_from_url(url)

            print(f"\n[{i+1}/{total}] {news_id}")

            # 下载 HTML
            html_path = download_page(context, url, news_id, HTML_DIR)
            if not html_path:
                continue

            # 解析正文
            html_content = Path(html_path).read_text(encoding="utf-8")
            info = extract_content(html_content)

            # 加载评论
            comments = load_comments_from_sidecar(Path(html_path))

            # 组装结果
            result = {
                "news_id": news_id,
                "url": url,
                "title": info["title"],
                "time": info["time"],
                "content": info["content"],
                "author": info["author"],
                "comment_num": len(comments),
                "comments": comments,
            }

            # 写入 JSONL
            with open(OUTPUT_JSONL, "a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")

            parsed_count += 1
            print(f"  [{news_id}] 解析完成，{len(comments)} 条评论")

            if i < total - 1:
                time.sleep(PAUSE_SECONDS)

        print(f"\n完成！共解析 {parsed_count}/{total} 条，结果保存到 {OUTPUT_JSONL}")

    finally:
        try:
            context.close()
        finally:
            playwright_obj.stop()


if __name__ == "__main__":
    main()
