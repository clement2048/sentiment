# Binance Square（币安广场）内容采集系统

币安广场内容采集与解析系统，包含四条独立流水线，覆盖普通用户帖子、官方新闻文章、币种相关新闻和金色财经新闻。输出为结构化 JSONL 文件，可直接用于下游情绪分析。

## 目录

- [项目结构](#项目结构)
- [四条流水线概览](#四条流水线概览)
- [流水线 1：用户帖子](#流水线-1用户帖子v2推荐)
- [流水线 2：Profile 新闻](#流水线-2profile-新闻binance-news-官方文章)
- [流水线 3：币种新闻](#流水线-3币种新闻)
- [流水线 4：金色财经新闻](#流水线-4金色财经新闻)
- [解析器选择](#解析器选择)
- [数据清洗](#数据清洗)
- [标签修复](#标签修复)
- [输出目录布局](#输出目录布局)
- [环境配置](#环境配置)

---

## 项目结构

```
├── crawlers/          # 采集器
│   ├── crawler_v2.py          # 用户帖子采集（Square 首页无限滚动）
│   ├── crawler_profile.py     # Profile 新闻采集 + 多线程下载 + 解析
│   └── crawler_coin.py        # 币种新闻采集（BAPI 接口）
├── downloaders/       # HTML 下载器
│   ├── fetch_pages_from_db.py # 从数据库下载用户帖子 HTML
│   └── fetch_coin_pages.py    # 币种帖子 HTML 下载器
├── parsers/           # 解析器
│   ├── parse_article.py       # 官方新闻文章解析（相对时间、价格标注）
│   └── parse_binance_square_html_final.py  # 用户帖子解析（APP_DATA + DOM 回退）
├── jinse/             # 金色财经
│   ├── sniff_jinse_api.py     # API 嗅探器
│   ├── crawl_jinse.py         # 新闻列表采集
│   └── process_jinse.py       # 页面下载 + 正文提取
├── cleaner/           # 数据清洗
│   └── clean_labeled_data.py  # 后处理过滤器
├── repair/            # 标签修复
│   └── repair_labels.py       # 评论标签修复与数据分流
├── utils/             # 工具模块
│   ├── crawler_util.py        # 共享工具函数
│   └── crawler_comment.py     # 评论数据提取辅助
├── config.py          # 采集配置
├── CLAUDE.md
└── README.md
```

## 四条流水线概览

| 流水线 | 入口 | 数据源 | 输出 |
|--------|------|--------|------|
| 用户帖子 | `crawlers/crawler_v2.py` | Square 首页无限滚动 | SQLite + HTML + JSONL |
| Profile 新闻 | `crawlers/crawler_profile.py` | Binance News 官方 | SQLite + HTML + JSONL |
| 币种新闻 | `crawlers/crawler_coin.py` | BAPI 接口 | SQLite + HTML + JSONL |
| 金色财经 | `jinse/crawl_jinse.py` | jinse2.com API | CSV + HTML + JSONL |

## 流水线 1：用户帖子（v2，推荐）

```bash
# 步骤 1：增量采集帖子 URL 到 SQLite
python crawlers/crawler_v2.py --lang en --target-posts 5000 --max-scroll-rounds 20000 --idle-stop-rounds 200 --wait-for-login --output-dir update_news_v2

# 步骤 2：从数据库下载 HTML 页面
python downloaders/fetch_pages_from_db.py --db-path update_news_v2/square_posts_v2.db --output-dir update_news/binance_square_page_dump --limit 200 --headless

# 步骤 3：解析 HTML 为结构化 JSON
python parsers/parse_binance_square_html_final.py --batch --input update_news/binance_square_page_dump --output update_news/parsed_from_html/binance_square_html_parsed.json

# 一步到位：采集 + 下载 HTML
python crawlers/crawler_v2.py --lang en --target-posts 500 --fetch-html --html-limit 200 --headless
```

## 流水线 2：Profile 新闻（Binance News 官方文章）

```bash
# 检查连通性
python crawlers/crawler_profile.py --check-only --headless

# 采集 + 多线程下载 HTML（4 线程）
python crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --headless

# 采集 + 下载 + 解析（端到端）
python crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --parse-html --headless
```

## 流水线 3：币种新闻

```bash
# 检查命中率
python crawlers/crawler_coin.py --check-only --symbols BTC,ETH,SOL --trust-env-proxy

# 按币种采集
python crawlers/crawler_coin.py --symbols BTC,ETH,SOL --max-posts 200 --trust-env-proxy

# 采集 + 下载 HTML
python crawlers/crawler_coin.py --symbols BTC --max-posts 100 --fetch-html --headless --trust-env-proxy
```

## 流水线 4：金色财经新闻

```bash
# 步骤 1（可选）：嗅探金色财经 API 接口
python jinse/sniff_jinse_api.py

# 步骤 2：调用 API 采集新闻列表 → CSV
#   编辑 jinse/crawl_jinse.py 顶部变量：
#     SOURCE = "lives"     # 快讯（内容在 JSON 中）
#     SOURCE = "articles"  # 产业文章（需下载详情页）
python jinse/crawl_jinse.py

# 步骤 3：下载 HTML + 提取正文/评论 → JSONL
python jinse/process_jinse.py
```

## 解析器选择

- `parsers/parse_binance_square_html_final.py` → **仅用于用户帖子（Posts）**，从 Square 的 discover 界面上爬取
- `parsers/parse_article.py` → **用于官方新闻文章（Articles）**，如 Binance News 发布的新闻，支持相对时间、价格标注

## 数据清洗

```bash
python cleaner/clean_labeled_data.py --input <path> --drop-no-products --drop-label-error --min-comment-total 1
```

### 清洗选项

| 参数 | 说明 |
|------|------|
| `--drop-no-products` | 删除未提取到币种的帖子 |
| `--drop-label-error` | 删除 label_error 非空的帖子 |
| `--min-comment-total N` | 保留评论总数 ≥ N 的帖子 |

## 标签修复

解析器在打标签时可能因网络、币种识别等原因失败。用以下命令修复并分流：

```bash
# 干跑：仅分类不调用 API
python repair/repair_labels.py --dry-run

# 全量修复（需要 VPN）
python repair/repair_labels.py

# 指定文件和参数
python repair/repair_labels.py --input dataset/result/parsed_28.jsonl --delay 1.0
```

## 输出目录布局

- `update_news_v2/` — crawler_v2 输出（数据库、CSV、JSON、last_run.json）
- `update_news/binance_square_page_dump/` — 下载的 HTML 页面
- `update_news/parsed_from_html/` — 解析后的 JSON 输出
- `crawler_coin_output/` — 币种采集器输出（数据库、CSV、JSON）
- `crawler_profile_output/` — profile 采集器输出（数据库、CSV、JSON、HTML、解析后 JSON）
- `dataset/html/update_news/` — HTML 页面存储
- `dataset/result/` — JSONL 解析结果
- `dataset/repair/` — 修复后分类输出
- `tmp_chrome_profile/` — Chromium 用户配置（登录态复用）
- `tmp_chrome_profile_jinse/` — 金色财经专用浏览器缓存

## 环境配置

```bash
conda activate sentiment
pip install playwright requests
playwright install chromium
```
