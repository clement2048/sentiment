# Binance Square（币安广场）内容采集与情绪分析系统

币安广场内容采集与解析系统，包含四条独立流水线（覆盖普通用户帖子、官方新闻文章、币种相关新闻和金色财经新闻），以及基于多 Agent + 图神经网络 (GNN) 的情绪分析系统。

## 目录

- [项目结构](#项目结构)
- [四条流水线概览](#四条流水线概览)
- [输出统计说明](#输出统计说明)
- [流水线 1：用户帖子](#流水线-1用户帖子v2推荐)
- [流水线 2：Profile 新闻（官方文章）](#流水线-2profile-新闻binance-news-官方文章)
- [流水线 3：币种新闻](#流水线-3币种新闻)
- [流水线 4：金色财经新闻](#流水线-4金色财经新闻)
- [解析器选择](#解析器选择)
- [数据清洗](#数据清洗)
- [情绪分析系统](#情绪分析系统)
- [输出目录布局](#输出目录布局)
- [环境配置](#环境配置)

---

## 项目结构

```
sentiment/
├── data_collection/                    # 数据采集模块
│   ├── crawlers/                       # 采集器
│   │   ├── crawler_v2.py               # 用户帖子采集（Square 首页无限滚动）
│   │   ├── crawler_profile.py          # Profile 新闻采集 + 多线程下载 + 解析
│   │   └── crawler_coin.py             # 币种新闻采集（BAPI 接口）
│   ├── downloaders/                    # HTML 下载器
│   │   ├── fetch_pages_from_db.py      # 从数据库下载用户帖子 HTML
│   │   └── fetch_coin_pages.py         # 币种帖子 HTML 下载器
│   ├── parsers/                        # 解析器
│   │   ├── parse_article.py            # 官方新闻文章解析（相对时间、过滤相关文章）
│   │   └── parse_binance_square_html_final.py  # 用户帖子解析（APP_DATA + DOM 回退）
│   ├── jinse/                          # 金色财经
│   │   ├── sniff_jinse_api.py          # API 嗅探器
│   │   ├── crawl_jinse.py              # 新闻列表采集
│   │   └── process_jinse.py            # 页面下载 + 正文提取
│   ├── cleaner/                        # 数据清洗
│   │   └── clean_labeled_data.py       # 后处理过滤器
│   └── utils/                          # 工具模块
│       ├── crawler_util.py             # 共享工具函数
│       └── crawler_comment.py          # 评论数据提取辅助
│
├── agent/                              # 用户情绪分析 Agent
│   ├── user_profile.py                 # 用户画像
│   ├── rule_agent.py                   # 规则 Agent
│   ├── llm_agent.py                    # LLM Agent（DeepSeek fallback）
│   ├── agent_factory.py               # Agent 工厂
│   └── agent_orchestrator.py          # Agent 编排器
│
├── gnn/                                # 图神经网络
│   ├── model.py                        # 3层 GCN + MeanMax 池化 → MLP 分类器
│   ├── dataset.py                      # PyG 数据集
│   ├── trainer.py                      # 训练器
│   └── predictor.py                    # 预测器
│
├── features/                           # 特征工程
│   ├── keyword_sentiment.py            # TF-IDF 文本特征
│   ├── text_embedding.py               # 加密领域关键词情感词典
│   └── feature_pipeline.py            # 特征流水线
│
├── data_loader/                        # 数据加载
│   ├── loader.py                       # JSONL 加载
│   ├── preprocessor.py                 # jieba 预处理
│   └── graph_builder.py               # 对话 → PyG 图构建
│
├── main.py                             # 情绪分析主入口
├── config.py                           # 全局配置
├── logger.py                           # 日志模块
│
├── CLAUDE.md                           # 项目指引
├── environment.yml                     # conda 环境配置
└── requirements.txt                    # Python 依赖
```

---

## 四条流水线概览

| 流水线 | 数据源 | 采集脚本 | 下载脚本 | 解析脚本 |
|---|---|---|---|---|
| 用户帖子 | Square 首页（无限滚动） | `data_collection/crawlers/crawler_v2.py` | `data_collection/downloaders/fetch_pages_from_db.py` | `data_collection/parsers/parse_binance_square_html_final.py` |
| Profile 新闻 | Binance News 官方 Profile 页 | `data_collection/crawlers/crawler_profile.py`（内置） | `data_collection/crawlers/crawler_profile.py`（内置多线程） | `data_collection/parsers/parse_article.py` |
| 币种新闻 | BAPI 接口 | `data_collection/crawlers/crawler_coin.py` | `data_collection/downloaders/fetch_coin_pages.py` | `data_collection/parsers/parse_article.py` |
| 金色财经新闻 | 金色财经 API + 页面 | `data_collection/jinse/crawl_jinse.py` | `data_collection/jinse/process_jinse.py`（内置） | `data_collection/jinse/process_jinse.py`（内置 Nuxt.js 提取） |
| 情绪分析 | Square 解析结果 JSONL | `agent/`（多 Agent 辩论） | `data_loader/` → `features/` | `gnn/`（GNN 分类器） |

---

## 输出统计说明

所有下载阶段都会输出进度信息，格式如下：

```
[fetch-html] progress 18/302 ok=17 skipped=0 filtered=0 failed=1
```

| 字段 | 含义 |
|---|---|
| `progress 18/302` | 已处理 18 条，共 302 条 |
| `ok` | **成功下载并保存**的 HTML 文件数 |
| `skipped` | **跳过**的数量：HTML 文件已存在，无需重复下载（可用 `--html-overwrite` 覆盖） |
| `filtered` | **被过滤**的数量：帖子不满足质量条件（评论数不足、无产品符号、帖龄不够等），不保存 HTML |
| `failed` | **下载失败**的数量：网络超时、页面无法打开等错误，失败详情记录在 `*_failures.json` 中 |

---

## 流水线 1：用户帖子（v2，推荐）

从 Binance Square 首页（discover 页面）通过无限滚动采集用户发布的帖子。

### 命令

```bash
# 步骤 1：增量采集帖子 URL 到 SQLite
python data_collection/crawlers/crawler_v2.py --lang en --target-posts 5000 --max-scroll-rounds 20000 --idle-stop-rounds 200 --wait-for-login

# 步骤 2：从 DB 下载 HTML 页面
python data_collection/downloaders/fetch_pages_from_db.py --db-path update_news_v2/square_posts_v2.db --output-dir update_news/binance_square_page_dump --limit 200 --headless

# 步骤 3：解析 HTML 为结构化 JSON
python data_collection/parsers/parse_binance_square_html_final.py --batch --input update_news/binance_square_page_dump --output update_news/parsed_from_html/binance_square_html_parsed.json

# 一步到位：采集 + 下载 HTML
python data_collection/crawlers/crawler_v2.py --lang en --target-posts 500 --fetch-html --html-limit 200 --headless
```

### crawler_v2.py 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--lang` | str | `en` | Square 语言版本，可选 `en`、`zh-CN` 等 |
| `--target-posts` | int | 5000 | 目标帖子总数，达到后自动停止 |
| `--max-scroll-rounds` | int | 3000 | 最大滚动轮数，防止无限循环 |
| `--idle-stop-rounds` | int | 50 | 连续 N 轮无新帖时自动停止 |
| `--pause-seconds` | float | 1.0 | 每轮滚动后的等待秒数 |
| `--scroll-pixels` | int | 2600 | 每轮鼠标滚动的像素距离 |
| `--max-runtime-minutes` | float | 0 | 最大运行时间（分钟），0=不限 |
| `--headless` | flag | 关 | 启用无头模式（不显示浏览器窗口） |
| `--wait-for-login` | flag | 关 | 打开页面后暂停，等待手动登录后按 Enter 继续 |
| `--user-data-dir` | str | `data_collection/tmp_chrome_profile` | 持久化 Chromium 用户目录，保留登录态 |
| `--output-dir` | str | `update_news_v2` | 输出目录 |
| `--db-path` | str | 自动 | SQLite 数据库路径，默认 `<output-dir>/square_posts_v2.db` |
| `--export-limit` | int | 0 | 导出 CSV/JSON 的行数限制，0=全部 |
| `--checkpoint-every` | int | 20 | 每 N 轮打印一次进度 |
| `--check-only` | flag | 关 | 仅打开页面检查浏览器链路是否正常 |
| `--fetch-html` | flag | 关 | 采集完成后自动调用 HTML 下载阶段 |
| `--html-output-dir` | str | `update_news/binance_square_page_dump` | HTML 输出目录 |
| `--html-limit` | int | 0 | HTML 下载阶段的最大帖子数，0=全部 |
| `--html-offset` | int | 0 | HTML 下载阶段从第几条开始 |
| `--html-overwrite` | flag | 关 | 覆盖已存在的 HTML 文件 |
| `--min-post-age-days` | int | 0 | 只下载至少 N 天前的帖子，0=不过滤 |

### fetch_pages_from_db.py 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--db-path` | str | 必填 | SQLite 数据库文件路径 |
| `--output-dir` | str | 必填 | HTML 输出目录 |
| `--limit` | int | 0 | 最大下载数量，0=全部 |
| `--offset` | int | 0 | 从第几条开始（配合 `--limit` 分批下载） |
| `--pause-seconds` | float | 0.8 | 每次请求之间的暂停秒数 |
| `--headless` | flag | 关 | 启用无头模式 |
| `--user-data-dir` | str | `data_collection/tmp_chrome_profile` | 持久化浏览器用户目录 |
| `--overwrite` | flag | 关 | 覆盖已存在的 HTML 文件 |
| `--save-screenshot` | flag | 关 | 同时保存 PNG 截图 |
| `--timeout-seconds` | int | 60 | 页面加载超时秒数 |
| `--min-comment-total` | int | 0 | 评论数下限，低于此值不保存，0=不过滤 |
| `--require-products` | flag | 关 | 要求帖子至少包含一个产品符号（如 BTC） |
| `--min-post-age-days` | int | 0 | 只下载至少 N 天前的帖子 |
| `--check-only` | flag | 关 | 仅检查 DB 和浏览器链路 |

---

## 流水线 2：Profile 新闻（Binance News 官方文章）

从 Binance Square 的 Profile 页面（如 `binance_news`）采集官方新闻文章。**采集和 HTML 下载集成在同一个脚本中**，支持多线程并行下载。

### 命令

```bash
# 检查连通性
python data_collection/crawlers/crawler_profile.py --check-only --headless

# 采集 + 多线程下载 HTML（4 线程）
python data_collection/crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --headless

# 采集 + 下载 + 过滤（只要至少有 1 条评论或 1 个产品符号的帖子）
python data_collection/crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --require-content --headless

# 采集 + 下载 + 解析（端到端，一条命令走完）
python data_collection/crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --parse-html --headless

# 采集 + 下载 + 解析 + 过滤（端到端，保留评论>=5且有产品的文章）
python data_collection/crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --parse-html --filter-parsed --headless --min-comment-total 5 --drop-no-products

# 仅对已有解析结果执行过滤
python data_collection/crawlers/crawler_profile.py --filter-parsed --min-comment-total 5 --drop-no-products

# 只下载不采集（数据库已有 URL，跳过采集阶段）
python data_collection/crawlers/crawler_profile.py --target-posts 300 --fetch-html --workers 4 --html-limit 100 --headless

# 覆盖已存在的 HTML
python data_collection/crawlers/crawler_profile.py --fetch-html --workers 4 --html-overwrite --headless
```

### crawler_profile.py 参数说明

#### 采集阶段参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--profile-url` | str | Binance News 主页 | 目标 Profile 页面 URL |
| `--profile-slug` | str | 自动 | Profile 标识名，用于 DB 和日志，默认从 URL 提取 |
| `--target-posts` | int | 500 | 目标帖子总数，达到后停止采集 |
| `--max-scroll-rounds` | int | 3000 | 最大滚动轮数 |
| `--idle-stop-rounds` | int | 120 | 连续 N 轮无新帖时自动停止 |
| `--pause-seconds` | float | 1.0 | 每轮滚动后的等待秒数 |
| `--scroll-pixels` | int | 2600 | 每轮鼠标滚动的像素距离 |
| `--max-runtime-minutes` | float | 0 | 最大运行时间（分钟），0=不限 |
| `--headless` | flag | 关 | 启用无头模式 |
| `--wait-for-login` | flag | 关 | 打开页面后暂停，等待手动登录 |
| `--user-data-dir` | str | `data_collection/tmp_chrome_profile` | 持久化 Chromium 用户目录 |
| `--output-dir` | str | `crawler_profile_output` | 输出目录 |
| `--db-path` | str | 自动 | SQLite 路径，默认 `<output-dir>/profile_posts.db` |
| `--export-limit` | int | 0 | 导出 CSV/JSON 行数限制，0=全部 |
| `--checkpoint-every` | int | 20 | 每 N 轮打印一次进度 |
| `--check-only` | flag | 关 | 仅打开 Profile 页面检查浏览器链路 |

#### HTML 下载阶段参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--fetch-html` | flag | 关 | 采集后自动进入 HTML 下载阶段 |
| `--workers` | int | 4 | 并行下载的线程数 |
| `--html-output-dir` | str | `<output-dir>/html_pages` | HTML 输出目录 |
| `--html-limit` | int | 0 | 最大下载数量，0=全部 |
| `--html-offset` | int | 0 | 从数据库第几条开始下载 |
| `--html-overwrite` | flag | 关 | 覆盖已存在的 HTML 文件 |

#### 质量过滤参数（下载时即时过滤）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--require-content` | flag | 关 | 要求帖子至少有 1 条评论或 1 个产品符号 |
| `--min-comment-total` | int | 0 | 评论数下限，低于此值不保存 HTML |
| `--require-products` | flag | 关 | 要求帖子至少包含一个产品符号 |
| `--min-post-age-days` | int | 0 | 只下载至少 N 天前的帖子 |

#### 解析阶段参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--parse-html` | flag | 关 | 下载完成后自动调用解析器 |
| `--parsed-output` | str | `<output-dir>/profile_parsed.json` | 解析结果 JSON 路径 |
| `--t-window-hours` | int | 24 | 评论价格窗口（小时），用于标注涨跌 |
| `--price-interval` | str | `1h` | K 线图时间间隔，如 `1h`、`15m`、`4h` |

#### 过滤阶段参数（解析后自动清洗）

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--filter-parsed` | flag | 关 | 解析完成后自动调用 `clean_labeled_data.py` 过滤 |
| `--drop-no-products` | flag | 关 | 丢弃 products 为空的帖子 |
| `--drop-label-error` | flag | 关 | 丢弃 label_error 非空的帖子 |
| `--keep-comment-error-posts` | flag | 关 | 不因 comment_error 丢弃帖子 |
| `--min-comment-total` | int | 0 | 评论数下限（同时影响下载和过滤阶段），0=不过滤 |
| `--filtered-output` | str | `<stem>_clean.json` | 保留记录输出路径 |
| `--filtered-dropped-output` | str | `<stem>_dropped.json` | 丢弃记录输出路径 |
| `--filtered-report-output` | str | `<stem>_clean_report.json` | 过滤报告输出路径 |

**注意**：`--min-comment-total` 在下载阶段和过滤阶段共用。如果只想在过滤阶段使用，下载时保持默认值 0，仅在过滤时设置阈值。

端点端示例：

```bash
# 采集 + 下载 + 解析 + 过滤（保留评论>=5且有产品的文章）
python data_collection/crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --parse-html --filter-parsed --headless --min-comment-total 5 --drop-no-products

# 仅对已有解析结果执行过滤
python data_collection/crawlers/crawler_profile.py --filter-parsed --min-comment-total 5 --drop-no-products
```

---

## 流水线 3：币种新闻

通过 Binance BAPI 接口按币种（BTC、ETH、SOL 等）定向采集官方新闻。

### 命令

```bash
# 检查 API 连通性并估算各币种命中率
python data_collection/crawlers/crawler_coin.py --check-only --symbols BTC,ETH,SOL --trust-env-proxy

# 按币种采集
python data_collection/crawlers/crawler_coin.py --symbols BTC,ETH,SOL --max-posts 200 --trust-env-proxy

# 采集 + 自动下载 HTML
python data_collection/crawlers/crawler_coin.py --symbols BTC --max-posts 100 --fetch-html --headless --trust-env-proxy

# 下载指定币种的 HTML
python data_collection/downloaders/fetch_coin_pages.py --db-path crawler_coin_output/coin_posts.db --output-dir crawler_coin_output/html_pages --symbol BTC --limit 100 --headless
```

### crawler_coin.py 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--symbols` | str | 空（所有已知币种） | 目标币种，逗号分隔，如 `BTC,ETH,SOL` |
| `--lang` | str | `en` | 语言版本 |
| `--max-posts` | int | 200 | 每个币种最多采集帖子数 |
| `--max-pages` | int | 1000 | 最大翻页数 |
| `--page-size` | int | 20 | 每页帖子数 |
| `--min-comment-count` | int | 5 | 最低评论数，低于此值的帖子被过滤 |
| `--min-like-count` | int | 0 | 最低点赞数，0=不过滤 |
| `--min-post-age-days` | int | 0 | 只保留至少 N 天前的帖子 |
| `--idle-stop-pages` | int | 10 | 连续 N 页无匹配时停止翻页 |
| `--pause-seconds` | float | 0.3 | 请求间隔秒数 |
| `--retries` | int | 3 | HTTP 请求失败重试次数 |
| `--request-timeout` | int | 30 | HTTP 请求超时秒数 |
| `--output-dir` | str | `crawler_coin_output` | 输出目录 |
| `--check-only` | flag | 关 | 仅检查 API 连通性和命中率 |
| `--fetch-html` | flag | 关 | 采集后自动调用 HTML 下载阶段 |
| `--html-limit` | int | 100 | HTML 下载的最大帖子数 |
| `--headless` | flag | 关 | 下载 HTML 时使用无头模式 |
| `--user-data-dir` | str | `data_collection/tmp_chrome_profile` | 浏览器用户数据目录 |
| `--trust-env-proxy` | flag | 关 | 使用系统代理（国内用户需开启） |

---

## 流水线 4：金色财经新闻

从金色财经（jinse2.com）采集快讯和产业新闻。通过直接调用其公开 API 获取新闻列表，无需浏览器即可完成第一步采集；详情页使用 Playwright 下载并提取 Nuxt.js 服务端渲染的正文内容。

### 命令

```bash
# 步骤 1（可选）：嗅探金色财经 API 接口，用于了解其 API 结构
python data_collection/jinse/sniff_jinse_api.py

# 步骤 2：调用 API 采集新闻列表 → CSV
#   编辑 data_collection/jinse/crawl_jinse.py 顶部变量：
#     SOURCE = "lives"      → 快讯（内容在 JSON 中，无需下载详情页）
#     SOURCE = "articles"   → 产业文章（只有标题+摘要+链接，需步骤 3 获取全文）
python data_collection/jinse/crawl_jinse.py

# 步骤 3：下载 HTML + 提取正文/评论 → JSONL
#   编辑 data_collection/jinse/process_jinse.py 顶部变量：
#     LIMIT = 0             → 处理全部，设为 N 则只处理前 N 条
#     HEADLESS = True       → 无头模式
python data_collection/jinse/process_jinse.py
```

> **注意**：金色财经脚本无命令行参数，所有配置通过编辑 `.py` 文件顶部变量完成。

### crawl_jinse.py 配置说明

编辑文件顶部的 `# 配置` 区域：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SOURCE` | `"lives"` | 数据源：`"lives"`=快讯（内容内嵌在 JSON），`"articles"`=产业文章（需下载详情页） |
| `LIVES_API` | `https://api.jinse2.com/noah/v2/lives` | 快讯 API 地址 |
| `ARTICLES_API` | `https://api.jinse2.com/noah/v2/catalogue/timelines` | 产业文章 API 地址 |
| `OUTPUT_CSV` | `dataset/csv/jinse_news.csv` | 输出 CSV 路径 |
| `PAGE_SIZE` | 20 | 每页条数 |
| `MAX_PAGES` | 500 | 最大翻页数 |
| `PAUSE_SECONDS` | 1.0 | 请求间隔秒数 |

输出 CSV 字段：`新闻id`、`时间`、`内容`、`链接`

### process_jinse.py 配置说明

编辑文件顶部的 `# 配置` 区域：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CSV_PATH` | `dataset/csv/jinse_news.csv` | 输入 CSV 路径 |
| `HTML_DIR` | `dataset/html/jinse` | HTML 下载目录 |
| `OUTPUT_JSONL` | `dataset/result/jinse_parsed.jsonl` | 最终输出 JSONL 路径 |
| `CONTENT_SELECTORS` | `["div.article-content", ...]` | 正文 CSS 选择器（按优先级尝试） |
| `HEADLESS` | `True` | 无头模式 |
| `USER_DATA_DIR` | `data_collection/tmp_chrome_profile_jinse` | 独立浏览器缓存目录 |
| `TIMEOUT_SECONDS` | 30 | 页面加载超时秒数 |
| `LIMIT` | 0 | 最多处理条数，0=全部 |

输出 JSONL 字段：`news_id`、`url`、`title`、`time`、`content`、`author`、`comment_num`、`comments`

### 流水线架构

```
Step 1 (可选): data_collection/jinse/sniff_jinse_api.py  →  dataset/jinse_api_sniff.json
Step 2:        data_collection/jinse/crawl_jinse.py       →  dataset/csv/jinse_news.csv
Step 3:        data_collection/jinse/process_jinse.py     →  dataset/html/jinse/*.html + *_comments.json
                                                          →  dataset/result/jinse_parsed.jsonl
```

---

## 解析器选择

**重要：不同的内容类型必须使用不同的解析器！**

| 解析器 | 适用内容 | 来源 |
|---|---|---|
| `data_collection/parsers/parse_binance_square_html_final.py` | **用户帖子（Posts）** | Square 首页 / discover 页采集的普通用户内容 |
| `data_collection/parsers/parse_article.py` | **官方新闻文章（Articles）** | Binance News 等官方账号发布的新闻，支持相对时间（"1h ago"）、过滤相关文章推荐 |

---

## 数据清洗

```bash
# 过滤掉无产品符号、标签错误、评论数过低的帖子
python data_collection/cleaner/clean_labeled_data.py --input <path> --drop-no-products --drop-label-error --min-comment-total 1
```

---

## 情绪分析系统

项目核心：多 Agent + 图神经网络 (GNN) 情绪分析系统，对币安广场评论进行用户级情绪判断和对话级涨跌分类。

### 系统设计

1. 对于每一个评论的用户，设计一个与用户性格相符的 Agent，根据该用户与其他用户的对话上下文，分析该用户是看涨还是看跌。
2. 根据 Agent 的辩论图，输入图神经网络进行分析，最终输出整个交流网络的情绪分析。
3. 根据之后一段时间币价的涨跌，判断情绪分析是否正确。

### 架构

| 模块 | 目录 | 说明 |
|---|---|---|
| Agent 系统 | `agent/` | 用户情绪分析 Agent（规则 Agent + LLM Agent + DeepSeek fallback） |
| 图神经网络 | `gnn/` | 3 层 GCN + MeanMax 池化 → MLP 分类器 |
| 特征工程 | `features/` | TF-IDF 文本特征 + 加密领域关键词情感词典 |
| 数据加载 | `data_loader/` | JSONL 加载、jieba 预处理、对话 → PyG 图构建 |
| 主入口 | `main.py` | 情绪分析入口 (`python main.py --mode train`) |
| 全局配置 | `config.py` | 集中式全局配置 |
| 日志 | `logger.py` | 日志模块 |

### 命令

```bash
# 训练（规则 Agent）
python main.py --mode train --input dataset/result/parsed_28.jsonl

# 训练（LLM Agent，需 DEEPSEEK_API_KEY）
python main.py --mode train --use-llm
```

---

## 输出目录布局

```
sentiment/
├── update_news_v2/                          # crawler_v2 输出
│   ├── square_posts_v2.db                   # 帖子索引（SQLite）
│   ├── binance_square_posts.csv             # 帖子 CSV 导出
│   ├── binance_square_posts_raw.json        # 帖子 JSON 导出
│   └── crawler_v2_last_run.json             # 最近一次运行摘要
│
├── update_news/
│   ├── binance_square_page_dump/            # 用户帖子 HTML + 摘要
│   │   ├── *.html
│   │   ├── fetch_pages_from_db_summary.json
│   │   ├── fetch_pages_from_db_failures.json
│   │   └── fetch_pages_from_db_filtered.json
│   └── parsed_from_html/                    # 用户帖子解析结果
│       └── binance_square_html_parsed.json
│
├── crawler_profile_output/                  # Profile 新闻输出
│   ├── profile_posts.db                     # 帖子索引（SQLite）
│   ├── binance_square_posts.csv
│   ├── binance_square_posts_raw.json
│   ├── crawler_v2_last_run.json
│   ├── html_pages/                          # 下载的 HTML
│   │   ├── *.html
│   │   ├── fetch_pages_from_db_summary.json
│   │   └── fetch_pages_from_db_failures.json
│   └── profile_parsed.json                  # 解析结果
│
├── crawler_coin_output/                     # 币种新闻输出
│   ├── coin_posts.db
│   ├── *.csv / *.json
│   └── html_pages/
│
├── dataset/
│   ├── csv/
│   │   └── jinse_news.csv                  # 金色财经新闻列表
│   ├── html/
│   │   └── jinse/                          # 金色财经下载的 HTML + 评论 sidecar
│   ├── result/
│   │   ├── jinse_parsed.jsonl             # 金色财经最终解析结果
│   │   └── parsed_28.jsonl                # 情绪分析训练数据
│   └── jinse_api_sniff.json                # 金色财经 API 嗅探结果
│
├── output/                                   # 情绪分析输出
│   ├── models/                              # 训练好的模型
│   ├── predictions/                         # 预测结果
│   └── logs/                                # 训练日志
│
├── data_collection/
│   ├── tmp_chrome_profile/                  # Chromium 持久化用户目录（登录态复用）
│   └── tmp_chrome_profile_jinse/            # 金色财经专用浏览器缓存（独立于币安）
```

---

## 环境配置

建议 Python 3.10+，推荐使用 conda 环境：

```bash
# 创建并激活 conda 环境
conda env create -f environment.yml
conda activate sentiment

# 安装依赖
pip install -r requirements.txt
playwright install chromium
```

---

## TODO

1. ~~拓展金色财经等平台爬取新闻，因为币安平台的新闻太少了~~ ✅ 已完成（见流水线 4）
2. 对没有打标签的数据编写程序进行标签修复
