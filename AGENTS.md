# AGENTS.md

## 语言要求

- 所有对话使用中文。
- 所有文档使用中文。
- 所有代码注释使用中文。

## 执行要求

- 在生成说明、总结、计划、提交说明时，统一使用中文。
- 在新增或修改 Markdown 文档时，统一使用中文。
- 在新增或修改代码注释时，统一使用中文。
- 当更新代码后，请遵循如下步骤：
  1. 确保已激活 conda 环境 `sentiment`（位于 `D:\anaconda\envs\sentiment`）。
  2. 先自行运行相关测试以验证正确性。
  3. 告知用户用于验证代码更新正确性的命令。
- 对于新增代码（如获取 API 等），可在编写完成后直接运行以获取数据。
- 如果用户表达有误，请严格指出。

## 项目真实主线

本项目是币安 Square 新闻与评论采集、解析系统，输出结构化 JSONL 数据，供下游情绪分析使用。

当前最有效、最推荐的主流程不是旧的 SQLite/滚动点击方案，而是：

```text
update_news.py
  通过 Binance BAPI 增量获取新闻列表，更新 CSV

process_csv.py
  从 CSV 读取 Binance Square 文章链接，下载 HTML，拦截评论 API，保存 sidecar 评论

parsers/parse_article.py
  解析 HTML + sidecar 评论，输出 JSONL
```

严格说明：`update_news.py` 通过 BAPI 获取新闻列表和链接，并不直接获取完整 HTML。完整 HTML 和评论信息由 `process_csv.py` 获取。

## 推荐工作流

### 1. 激活环境

```bash
conda activate sentiment
```

Windows 终端如遇中文输出或 `conda run` 编码问题，可先设置：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
```

### 2. BAPI 增量更新新闻 CSV

```bash
python update_news.py
```

默认主数据集：

```text
dataset/csv/master_news_dataset_0505.csv
```

当日增量文件：

```text
dataset/csv/binance_news_YYYYMMDD.csv
```

### 3. 从 CSV 下载 HTML、抓评论并解析

```bash
python process_csv.py --csv dataset/csv/master_news_dataset_0505.csv --html-dir dataset/html/update_news --output dataset/result/parsed.jsonl
```

小规模验证：

```bash
python process_csv.py --csv dataset/csv/master_news_dataset_0505.csv --html-dir dataset/html/update_news --output dataset/result/test.jsonl --limit 3
```

只解析已有 HTML：

```bash
python process_csv.py --csv dataset/csv/master_news_dataset_0505.csv --html-dir dataset/html/update_news --output dataset/result/test.jsonl --skip-existing --limit 3
```

## 当前主流程产物

- `dataset/csv/master_news_dataset_0505.csv`：主新闻 CSV。
- `dataset/csv/binance_news_YYYYMMDD.csv`：每日增量 CSV。
- `dataset/html/update_news/<post_id>.html`：下载后的页面 HTML。
- `dataset/html/update_news/<post_id>_api_raw.json`：页面加载期间拦截到的原始 API 响应。
- `dataset/html/update_news/<post_id>_comments.json`：从评论 API 中提取的评论 sidecar。
- `dataset/result/*.jsonl`：结构化解析结果。

## 解析器选择

- `parsers/parse_article.py`：当前主流程解析器，用于 Binance News 官方文章，支持 sidecar 评论和价格标注。
- `parsers/parse_binance_square_html_final.py`：旧用户帖子解析器，仅在明确处理用户帖子 HTML 时使用。

## 旧流程定位

以下方案容易受币安反爬、模拟点击、标签页切换、登录态和 SQLite 中转影响，不再作为默认主线：

```text
crawlers/crawler_v2.py
  → downloaders/fetch_pages_from_db.py
  → parsers/parse_binance_square_html_final.py
```

以及：

```text
crawlers/crawler_profile.py
crawlers/crawler_coin.py
downloaders/fetch_coin_pages.py
```

除非用户明确要求使用这些旧脚本，否则后续 agent 不要默认建议“滚动采集 URL → SQLite → 下载 HTML → 离线解析”的流程。

## 项目目录

```text
├── update_news.py                 # 当前主流程：BAPI 增量获取新闻列表 CSV
├── process_csv.py                 # 当前主流程：CSV → HTML/API 评论 → JSONL
├── config.py                      # 配置项，部分旧脚本仍会引用
├── parsers/
│   ├── parse_article.py           # 当前主解析器
│   └── parse_binance_square_html_final.py  # 旧用户帖子解析器
├── dataset/
│   ├── csv/                       # CSV 数据
│   ├── html/update_news/          # HTML、api_raw、comments sidecar
│   └── result/                    # JSONL 输出
├── cleaner/                       # 数据清洗
├── repair/                        # 评论标签修复与数据分流
├── jinse/                         # 金色财经
├── crawlers/                      # 旧采集器或备用采集器
├── downloaders/                   # 旧下载器或备用下载器
├── utils/                         # 工具函数
├── pending_review/                # 根目录移出的待检查历史/调试脚本
└── README.md
```

根目录只保留当前主流程入口和必要配置。`pending_review/` 不是推荐工作流的一部分，其中脚本不要被默认当作当前主流程入口。

## 已知问题和判断标准

- 当前流程能跑通，但历史 CSV 或 sidecar 评论中可能存在中文乱码；不要把“流程可运行”误判为“所有历史数据编码干净”。
- `process_csv.py` 的默认 CSV 是 `dataset/csv/master_news_dataset.csv`，但当前推荐显式传入 `dataset/csv/master_news_dataset_0505.csv`。
- 若 `update_news.py` 报 `127.0.0.1:9` 代理连接失败，优先检查代理环境变量或网络权限。
- 若 `process_csv.py` 启动 Playwright 报 Windows 权限错误，通常是浏览器子进程受限，需要外部执行权限。
- 判断主流程是否可用时，至少验证：
  1. `update_news.py` 能调用 BAPI 并更新 CSV。
  2. `process_csv.py --skip-existing` 能解析已有 HTML。
  3. 有 sidecar 评论的 HTML 能被 `parse_article.py` 解析出 `comment_total_num > 0`。
