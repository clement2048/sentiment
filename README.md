# Binance Square 新闻与评论采集系统

本项目当前最有效、最推荐的工作流是：

```text
update_news.py 通过 Binance BAPI 增量获取新闻列表 CSV
        ↓
process_csv.py 根据 CSV 中的文章链接下载页面 HTML，并拦截评论 API
        ↓
parsers/parse_article.py 解析 HTML + sidecar 评论数据，输出 JSONL
```

注意：`update_news.py` 获取的是新闻列表、时间、标题摘要和链接，不是直接获取完整 HTML。HTML 下载和评论 API 拦截由 `process_csv.py` 完成。

## 为什么以这个流程为主

旧的 `crawler_v2.py`、`crawler_profile.py`、`fetch_pages_from_db.py` 等流程依赖浏览器滚动、模拟点击、SQLite 中转和大量 HTML 存储，容易被币安反爬、标签切换、登录态异常影响。当前数据规模不大，帖子/新闻数量通常不超过数万条，用 BAPI 先获取列表，再按 CSV 下载和解析，更直接也更稳定。

因此，后续 agent 理解本项目时，应优先按本 README 和 `AGENTS.md` 中的主流程工作。旧流程只作为历史方案或调试参考，不应默认作为主线。

## 推荐命令

先激活 conda 环境：

```bash
conda activate sentiment
```

如果在 Windows 终端中出现中文乱码或 `conda run` 输出编码异常，建议先设置 UTF-8 输出：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
```

### 1. 增量更新新闻 CSV

```bash
python update_news.py
```

默认读取并更新：

- 主数据集：`dataset/csv/master_news_dataset_0505.csv`
- 当日临时 CSV：`dataset/csv/binance_news_YYYYMMDD.csv`

`update_news.py` 会读取主数据集中最新新闻时间，向前回退 1 小时作为缓冲，然后调用 Binance BAPI 抓取较新的新闻，最后按链接去重并合并回主数据集。

### 2. 下载 HTML、抓评论并解析 JSONL

```bash
python process_csv.py --csv dataset/csv/master_news_dataset_0505.csv --html-dir dataset/html/update_news --output dataset/result/parsed.jsonl
```

常用小规模验证：

```bash
python process_csv.py --csv dataset/csv/master_news_dataset_0505.csv --html-dir dataset/html/update_news --output dataset/result/test.jsonl --limit 3
```

只解析已有 HTML，不重新下载：

```bash
python process_csv.py --csv dataset/csv/master_news_dataset_0505.csv --html-dir dataset/html/update_news --output dataset/result/test.jsonl --skip-existing --limit 3
```

`process_csv.py` 的主要产物：

- `dataset/html/update_news/<post_id>.html`：页面 HTML
- `dataset/html/update_news/<post_id>_api_raw.json`：拦截到的原始 API 响应
- `dataset/html/update_news/<post_id>_comments.json`：从评论 API 提取的 sidecar 评论
- `dataset/result/*.jsonl`：解析后的结构化结果

## 解析逻辑

`process_csv.py` 会调用 `parsers/parse_article.py`。解析器优先使用同目录下的 `<post_id>_comments.json`，如果没有 sidecar 评论，再尝试从 DOM 或 APP_DATA 中提取评论。

输出 JSONL 中常见字段包括：

```json
{
  "post_id": "303290959198465",
  "post_url": "https://www.binance.com/zh-CN/square/post/303290959198465",
  "post_author": "Binance News",
  "post_content": "文章正文...",
  "post_time": "2026-04-29 12:00:00",
  "products": ["BTC"],
  "comment_total_num": 1,
  "comments": [
    {
      "comment_id": "c1",
      "author": "用户名",
      "text": "评论内容",
      "post_time": "2026-04-29 13:00:00",
      "label": 1,
      "replies": []
    }
  ]
}
```

## 旧流程定位

以下脚本仍保留，但不再是默认推荐主线：

- `crawlers/crawler_v2.py`：Square 首页无限滚动采集
- `downloaders/fetch_pages_from_db.py`：从 SQLite 下载 HTML
- `parsers/parse_binance_square_html_final.py`：用户帖子 HTML 解析
- `crawlers/crawler_profile.py`：Profile 页面采集和多线程下载
- `crawlers/crawler_coin.py`：按币种关键词匹配的 BAPI 采集

除非用户明确要求恢复旧流程，否则不要默认建议“采集 URL → SQLite → 下载 HTML → 离线解析”的方案。

## 目录结构

```text
├── update_news.py                 # 当前主流程：BAPI 增量获取新闻 CSV
├── process_csv.py                 # 当前主流程：从 CSV 下载 HTML、抓评论、解析 JSONL
├── config.py                      # 配置项，部分旧脚本仍会引用
├── parsers/
│   ├── parse_article.py           # 官方新闻文章解析，支持 sidecar 评论
│   └── parse_binance_square_html_final.py  # 旧用户帖子解析器
├── dataset/
│   ├── csv/                       # 主 CSV 和每日增量 CSV
│   ├── html/update_news/          # HTML、api_raw、comments sidecar
│   └── result/                    # JSONL 结果
├── cleaner/                       # 数据清洗
├── repair/                        # 标签修复与数据分流
├── jinse/                         # 金色财经采集和处理
├── crawlers/                      # 旧采集器或备用采集器
├── downloaders/                   # 旧下载器或备用下载器
├── pending_review/                # 根目录移出的待检查历史/调试脚本
└── tmp_chrome_profile/            # Playwright 持久化浏览器配置
```

根目录只保留当前主流程入口和必要配置。`pending_review/` 不是推荐工作流的一部分，其中脚本仅用于后续人工复查。

## 已知注意事项

- 当前 CSV 里部分历史文本可能存在乱码；流程能跑通不等于历史数据编码全部干净。
- `process_csv.py` 默认 CSV 是 `dataset/csv/master_news_dataset.csv`，实际推荐显式传入 `--csv dataset/csv/master_news_dataset_0505.csv`。
- 币安访问可能需要代理或可用网络；如果 `update_news.py` 报 `127.0.0.1:9` 代理错误，需要检查本机代理环境变量。
- Playwright 完整下载模式需要能启动浏览器；在受限环境中可能需要外部权限。
- 对于新增或修改后的代码，应在 `sentiment` conda 环境中运行相关验证命令，并把命令告知用户。
