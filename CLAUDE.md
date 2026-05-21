# CLAUDE.md

## 语言要求

- 所有对话使用中文。
- 所有文档使用中文。
- 所有代码注释使用中文。

## 执行要求

- 在生成说明、总结、计划、提交说明时，统一使用中文。
- 在新增或修改 Markdown 文档时，统一使用中文。
- 在新增或修改代码注释时，统一使用中文。
- 当更新代码后，请遵循如下步骤：
  1. 确保已激活 conda 环境 "sentiment"（位于 D 盘）。
  2. 先自行运行相关测试以验证正确性。
  3. 告知我用于验证代码更新正确性的命令。
- 对于新增代码（如获取API等），可在编写完成后直接运行以获取数据。
- 如果我的表达有误，请严格指出。

## 项目概览

币安广场内容采集与解析系统，外加金色财经新闻采集。输出为 JSONL 格式的结构化数据，供下游情绪分析系统使用。

### 四条主要流水线

1. **用户帖子（v2/增量）** — `crawlers/crawler_v2.py` → `downloaders/fetch_pages_from_db.py` → `parsers/parse_binance_square_html_final.py`
2. **Profile 新闻（Binance News 官方）** — `crawlers/crawler_profile.py`（采集 + 多线程下载 HTML）→ `parsers/parse_article.py`
3. **币种相关新闻** — `crawlers/crawler_coin.py`（BAPI）→ `downloaders/fetch_coin_pages.py` → `parsers/parse_article.py`
4. **金色财经新闻** — `jinse/sniff_jinse_api.py`（可选）→ `jinse/crawl_jinse.py` → `jinse/process_jinse.py`

### 目录结构

```
├── crawlers/          # 采集器
│   ├── crawler_v2.py          # Square 首页无限滚动采集
│   ├── crawler_profile.py     # Profile 页面采集（Binance News 官方）
│   └── crawler_coin.py        # 基于 BAPI 的币种新闻采集
├── downloaders/       # HTML 下载器
│   ├── fetch_pages_from_db.py # 从 SQLite 读取 URL → Playwright 下载 HTML
│   └── fetch_coin_pages.py    # 币种帖子 HTML 下载器
├── parsers/           # 解析器
│   ├── parse_binance_square_html_final.py  # 用户帖子 HTML 解析
│   └── parse_article.py                   # 官方新闻文章 HTML 解析（含价格标注）
├── jinse/             # 金色财经
├── cleaner/           # 数据清洗
├── repair/            # 评论标签修复与数据分流
├── utils/             # 工具函数（crawler_util、crawler_comment）
├── config.py          # 采集配置
├── CLAUDE.md
└── README.md
```

### 解析器选择

- `parsers/parse_binance_square_html_final.py` → **仅用于用户帖子（Posts）**
- `parsers/parse_article.py` → **用于官方新闻文章（Articles）**，支持价格标注

### 关键设计决策

- **增量 + 去重**: URL 存储在 SQLite 中，带 `first_seen_at`/`last_seen_at`/`seen_count` 字段
- **三阶段解耦**: 采集（仅索引）→ 下载 HTML → 离线解析
- **客户端币种匹配**: BAPI 不支持服务端过滤，本地通过关键词别名匹配
- **持久化浏览器配置**: `tmp_chrome_profile/` 在多次运行间保留登录 Cookie

### 常用命令

```bash
# 币安帖子采集（v2 — 推荐）
python crawlers/crawler_v2.py --lang en --target-posts 500 --fetch-html --html-limit 200 --headless

# 币种新闻采集
python crawlers/crawler_coin.py --symbols BTC,ETH,SOL --max-posts 100 --fetch-html --headless

# Profile 新闻采集
python crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --headless

# 标签修复与数据分流
python repair/repair_labels.py --dry-run   # 干跑
python repair/repair_labels.py             # 全量修复

# 数据清洗
python cleaner/clean_labeled_data.py --input <path> --drop-no-products --drop-label-error
```

### 输出目录

- `update_news_v2/` — crawler_v2 输出（数据库、CSV、JSON）
- `update_news/binance_square_page_dump/` — 下载的 HTML 页面
- `update_news/parsed_from_html/` — 解析后的 JSON 输出
- `crawler_coin_output/` — 币种采集器输出
- `crawler_profile_output/` — profile 采集器输出
- `dataset/` — HTML 存储、JSONL 解析结果
