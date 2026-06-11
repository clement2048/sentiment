# CLAUDE.md

本文件用于提醒后续 agent：不要把旧的滚动点击、SQLite、HTML DB 流程误判为当前主线。

## 当前推荐主流程

```text
update_news.py
  → 通过 Binance BAPI 增量获取新闻列表并更新 CSV

process_csv.py
  → 从 CSV 读取文章链接，下载 HTML，拦截评论 API，保存 sidecar 评论

parsers/parse_article.py
  → 解析 HTML + sidecar 评论，输出 JSONL
```

`update_news.py` 获取的是列表、时间、摘要和链接，不是完整 HTML。完整 HTML 下载和评论获取由 `process_csv.py` 完成。

## 常用命令

```bash
conda activate sentiment
python update_news.py
python process_csv.py --csv dataset/csv/master_news_dataset_0505.csv --html-dir dataset/html/update_news --output dataset/result/parsed.jsonl
```

小规模验证：

```bash
python process_csv.py --csv dataset/csv/master_news_dataset_0505.csv --html-dir dataset/html/update_news --output dataset/result/test.jsonl --limit 3
python process_csv.py --csv dataset/csv/master_news_dataset_0505.csv --html-dir dataset/html/update_news --output dataset/result/test.jsonl --skip-existing --limit 3
```

Windows 终端如遇编码问题：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
```

## 主要产物

- `dataset/csv/master_news_dataset_0505.csv`：当前主 CSV。
- `dataset/csv/binance_news_YYYYMMDD.csv`：每日增量 CSV。
- `dataset/html/update_news/<post_id>.html`：下载后的 HTML。
- `dataset/html/update_news/<post_id>_api_raw.json`：原始 API 响应。
- `dataset/html/update_news/<post_id>_comments.json`：评论 sidecar。
- `dataset/result/*.jsonl`：解析结果。

## 旧流程说明

以下流程存在反爬、模拟点击、标签页切换、登录态异常和 SQLite 中转成本，不应默认推荐：

```text
crawlers/crawler_v2.py
  → downloaders/fetch_pages_from_db.py
  → parsers/parse_binance_square_html_final.py
```

`crawlers/crawler_profile.py`、`crawlers/crawler_coin.py`、`downloaders/fetch_coin_pages.py` 也仅作为备用或历史方案。

如果用户没有明确要求使用旧流程，应优先围绕 `update_news.py` 和 `process_csv.py` 分析、修复和验证。

## 根目录脚本整理

根目录只保留当前主流程入口和必要配置：`update_news.py`、`process_csv.py`、`config.py`。

已从根目录移出的历史脚本、调试脚本和未确认脚本放在 `pending_review/`。该目录不是推荐工作流的一部分，除非用户明确要求复查其中脚本，否则不要默认建议运行。
