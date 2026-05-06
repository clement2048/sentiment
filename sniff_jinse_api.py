"""
嗅探金色财经 API — 打开页面，自动记录所有 JSON API 请求，帮你找到真实的接口地址。

用法:
  python sniff_jinse_api.py
"""

import json
import time
from pathlib import Path

OUTPUT_FILE = Path("dataset/jinse_api_sniff.json")


def main():
    from playwright.sync_api import sync_playwright

    captured = []

    p = sync_playwright().start()
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    )

    def on_response(response):
        """拦截所有 JSON 响应"""
        content_type = response.headers.get("content-type", "")
        url = response.url
        if "application/json" not in content_type:
            return
        try:
            body = response.json()
        except Exception:
            return
        captured.append({
            "url": url,
            "status": response.status,
            "body_preview": json.dumps(body, ensure_ascii=False)[:2000],
        })
        print(f"[API] {response.status} {url[:120]}")

    page = context.new_page()
    page.on("response", on_response)

    # 第一步：打开首页，抓初始 API
    print("正在打开金色财经首页...")
    page.goto("https://jinse2.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)

    # 第二步：导航到快讯页面，抓快讯列表 API
    print("\n正在打开快讯页面...")
    page.goto("https://jinse2.com/lives", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    for _ in range(5):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

    # 第三步：导航到产业新闻页面
    print("\n正在打开产业页面...")
    page.goto("https://jinse2.com/industry", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    for _ in range(5):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

    page.close()
    context.close()
    browser.close()
    p.stop()

    # 保存结果
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=2)

    print(f"\n共捕获 {len(captured)} 个 API 请求，已保存到 {OUTPUT_FILE}")
    print("\n--- API 请求列表 ---")
    for c in captured:
        print(f"\n{c['status']} {c['url']}\n  preview: {c['body_preview'][:300]}...")


if __name__ == "__main__":
    main()
