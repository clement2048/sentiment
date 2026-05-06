# CLAUDE.md

## 语言要求

- 所有对话使用中文。
- 所有文档使用中文。
- 所有代码注释使用中文。

## 执行要求

- 在生成说明、总结、计划、提交说明时，统一使用中文。
- 在新增或修改 Markdown 文档时，统一使用中文。
- 在新增或修改代码注释时，统一使用中文。
- 在更新了代码之后要告诉我使用什么命令去验证代码更新的正确性
- 如果更新代码，最好先自己运行测试来判断是否正确
- 运行命令以前看看有没有开启conda环境"sentiment", conda环境在D盘
- 写了新的代码例如获取API等，可以写好了代码之后直接自己运行去获取

## 项目概览

币安广场内容采集与解析系统。共三条主要流水线：

1. **用户帖子（v2/增量）** — `crawler_v2.py` → `fetch_pages_from_db.py` → `parse_binance_square_html_final.py`
2. **Profile 新闻（Binance News 官方）** — `crawler_profile.py`（采集 + 多线程下载 HTML）→ `parse_article.py`
3. **币种相关新闻** — `crawler_coin.py`（BAPI）→ `fetch_coin_pages.py` → `parse_article.py`

### 解析器选择（重要）

- `parse_binance_square_html_final.py` → **仅用于用户帖子（Posts）**，从 Square的discover界面上爬取的
- `parse_article.py` → **用于官方新闻文章（Articles）**，如 Binance News 发布的新闻，支持相对时间、过滤相关文章

## 命令

### 用户帖子流水线（v2 — 推荐）
```bash
# 步骤 1：增量采集帖子 URL 到 SQLite
python crawler_v2.py --lang en --target-posts 5000 --max-scroll-rounds 20000 --idle-stop-rounds 200 --wait-for-login --output-dir update_news_v2

# 步骤 2：从数据库下载 HTML 页面
python fetch_pages_from_db.py --db-path update_news_v2/square_posts_v2.db --output-dir update_news/binance_square_page_dump --limit 200 --headless

# 步骤 3：解析 HTML 为结构化 JSON
python parse_binance_square_html_final.py --batch --input update_news/binance_square_page_dump --output update_news/parsed_from_html/binance_square_html_parsed.json

# 一步到位：采集 + 下载 HTML
python crawler_v2.py --lang en --target-posts 500 --fetch-html --html-limit 200 --headless
```

### 币种新闻流水线
```bash
# 检查命中率
python crawler_coin.py --check-only --symbols BTC,ETH,SOL --trust-env-proxy

# 按币种采集
python crawler_coin.py --symbols BTC,ETH,SOL --max-posts 200 --trust-env-proxy

# 采集 + 下载 HTML
python crawler_coin.py --symbols BTC --max-posts 100 --fetch-html --headless --trust-env-proxy
```

### Profile 新闻流水线（Binance News 官方文章）
```bash
# 检查连通性
python crawler_profile.py --check-only --headless

# 采集 + 多线程下载 HTML（4 线程）
python crawler_profile.py --target-posts 500 --fetch-html --workers 4 --headless

# 采集 + 下载 + 过滤（要求有评论或产品符号）
python crawler_profile.py --target-posts 500 --fetch-html --workers 4 --require-content --headless

# 采集 + 下载 + 解析（端到端）
python crawler_profile.py --target-posts 500 --fetch-html --workers 4 --parse-html --headless

# 仅下载已有的帖子（跳过采集）
python crawler_profile.py --target-posts 300 --fetch-html --workers 4 --html-limit 100 --headless
```

### 数据清洗
```bash
python clean_labeled_data.py --input <path> --drop-no-products --drop-label-error --min-comment-total 1
```

## 关键架构

### 数据流（v2 流水线）
```
币安广场（无限滚动）
       ↓ Playwright 滚动 + URL 采集
crawler_v2.py → square_posts_v2.db（SQLite，去重帖子索引）
                      ↓ Playwright 页面导航
       fetch_pages_from_db.py → *.html 文件
                      ↓ 离线正则/JSON 提取
       parse_binance_square_html_final.py → 结构化 JSON（帖子ID、作者、内容、时间、产品、评论）
```

### SQLite 表结构
- **posts**: `post_id（TEXT 主键）`、`link（TEXT 唯一）`、`first_seen_at`、`last_seen_at`、`seen_count`
- **runs**: 每次运行的元数据（开始时间、语言、目标帖子数、滚动轮数、新增数量、停止原因）

### 核心脚本

| 脚本 | 功能 |
|---|---|
| `crawler_v2.py` | discover页面帖子 URL 采集器 — 滚动广场首页，将唯一 URL 插入 SQLite |
| `fetch_pages_from_db.py` | 从数据库读取 → 通过 Playwright 下载完整 HTML（支持按时间/评论/产品预过滤） |
| `parse_binance_square_html_final.py` | 从 HTML 中提取帖子元数据和评论（APP_DATA JSON + DOM 回退） |
| `crawler_profile.py` | Profile 页面采集器：采集 URL → 多线程下载 HTML → 委托 `parse_article.py` 解析 |
| `crawler_coin.py` | 基于 BAPI 的新闻采集器，客户端关键词匹配（SYMBOL_ALIASES 字典） |
| `fetch_coin_pages.py` | 币种帖子 HTML 下载器（与 fetch_pages_from_db 功能对应） |
| `parse_article.py` | 解析官方新闻文章 HTML（相对时间处理，不同的 DOM 结构） |
| `config.py` | 集中式命令行参数定义|
| `crawler_util.py` | 共享工具函数：`clean_text()`、`is_meaningful_comment()`、`extract_first_string()`、`ensure_dir()` |
| `crawler_comment.py` | 评论数据提取辅助函数（递归键遍历、`looks_like_comment_node()`） |
| `clean_labeled_data.py` | 后处理过滤器（删除无产品、标签错误、评论数低的帖子） |

### 输出目录布局
- `update_news_v2/` — crawler_v2 输出（数据库、CSV、JSON、last_run.json）
- `update_news/binance_square_page_dump/` — 下载的 HTML 页面 + 采集摘要/失败/过滤 JSON
- `update_news/parsed_from_html/` — 解析后的 JSON 输出
- `crawler_coin_output/` — 币种采集器输出（数据库、CSV、JSON）
- `crawler_profile_output/` — profile 采集器输出（数据库、CSV、JSON、下载的 HTML、解析后的 JSON）
- `tmp_chrome_profile/` — 持久化 Chromium 用户配置（用于登录态复用）

### 关键设计决策
- **增量 + 去重**: URL 存储在 SQLite 中，带 `first_seen_at`/`last_seen_at`/`seen_count` 字段；重复运行只添加新帖子
- **三阶段解耦**: 采集（仅索引）→ 下载 HTML → 离线解析。各阶段可独立运行，支持批量/重试
- **客户端币种匹配**: BAPI 不支持服务端币种过滤，因此获取所有帖子后在本地通过关键词别名匹配
- **双重提取**: `extract_app_data()` 优先解析内嵌 JSON，评论提取回退到 DOM 文本搜索
- **持久化浏览器配置**: Chromium 用户数据目录（`tmp_chrome_profile/`）在多次运行间保留登录 Cookie



### todo
1、根据币安广场爬取代码设计金色财经的爬取