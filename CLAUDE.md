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

币安广场内容采集与解析系统。共三条主要流水线：

1. **用户帖子（v2/增量）** — `crawler_v2.py` → `fetch_pages_from_db.py` → `parse_binance_square_html_final.py`
2. **Profile 新闻（Binance News 官方）** — `crawler_profile.py`（采集 + 多线程下载 HTML）→ `parse_article.py`
3. **币种相关新闻** — `crawler_coin.py`（BAPI）→ `fetch_coin_pages.py` → `parse_article.py`

### 解析器选择（重要）

- `parse_binance_square_html_final.py` → **仅用于用户帖子（Posts）**，从 Square的discover界面上爬取的
- `parse_article.py` → **用于官方新闻文章（Articles）**，如 Binance News 发布的新闻，支持相对时间、过滤相关文章

## 命令

### 币安帖子采集流水线（v2 — 推荐）
```bash
# 步骤 1：增量采集帖子 URL 到 SQLite
python data_collection/crawlers/crawler_v2.py --lang en --target-posts 5000 --max-scroll-rounds 20000 --idle-stop-rounds 200 --wait-for-login --output-dir update_news_v2

# 步骤 2：从数据库下载 HTML 页面
python data_collection/downloaders/fetch_pages_from_db.py --db-path update_news_v2/square_posts_v2.db --output-dir update_news/binance_square_page_dump --limit 200 --headless

# 步骤 3：解析 HTML 为结构化 JSON
python data_collection/parsers/parse_binance_square_html_final.py --batch --input update_news/binance_square_page_dump --output update_news/parsed_from_html/binance_square_html_parsed.json

# 一步到位：采集 + 下载 HTML
python data_collection/crawlers/crawler_v2.py --lang en --target-posts 500 --fetch-html --html-limit 200 --headless
```

### 币种新闻采集流水线
```bash
# 检查命中率
python data_collection/crawlers/crawler_coin.py --check-only --symbols BTC,ETH,SOL --trust-env-proxy

# 按币种采集
python data_collection/crawlers/crawler_coin.py --symbols BTC,ETH,SOL --max-posts 200 --trust-env-proxy

# 采集 + 下载 HTML
python data_collection/crawlers/crawler_coin.py --symbols BTC --max-posts 100 --fetch-html --headless --trust-env-proxy
```

### 币安新闻采集流水线（Binance News 官方文章）
```bash
# 检查连通性
python data_collection/crawlers/crawler_profile.py --check-only --headless

# 采集 + 多线程下载 HTML（4 线程）
python data_collection/crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --headless

# 采集 + 下载 + 过滤（要求有评论或产品符号）
python data_collection/crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --require-content --headless

# 采集 + 下载 + 解析（端到端）
python data_collection/crawlers/crawler_profile.py --target-posts 500 --fetch-html --workers 4 --parse-html --headless

# 仅下载已有的帖子（跳过采集）
python data_collection/crawlers/crawler_profile.py --target-posts 300 --fetch-html --workers 4 --html-limit 100 --headless
```

### 数据清洗
```bash
python data_collection/cleaner/clean_labeled_data.py --input <path> --drop-no-products --drop-label-error --min-comment-total 1
```

### 情绪分析
```bash
# 启动完整情绪分析流水线
python main.py
```

## 关键架构

### 数据采集目录结构
```
data_collection/
├── crawlers/          # 采集器
│   ├── crawler_v2.py          # discover 页面帖子 URL 采集器
│   ├── crawler_profile.py     # Profile 页面采集器（Binance News 官方）
│   └── crawler_coin.py        # 基于 BAPI 的币种新闻采集器
├── downloaders/       # HTML 下载器
│   ├── fetch_pages_from_db.py # 从数据库读取 URL → Playwright 下载 HTML
│   └── fetch_coin_pages.py    # 币种帖子 HTML 下载器
├── parsers/           # 解析器
│   ├── parse_binance_square_html_final.py  # 用户帖子 HTML 解析
│   └── parse_article.py                   # 官方新闻文章 HTML 解析
├── jinse/             # 金色财经
│   ├── sniff_jinse_api.py      # API 嗅探器
│   ├── crawl_jinse.py          # 新闻列表采集器
│   └── process_jinse.py        # 页面处理器（HTML → JSONL）
├── cleaner/           # 数据清洗
│   └── clean_labeled_data.py   # 后处理过滤器
└── utils/             # 工具
    ├── crawler_util.py         # 共享工具函数
    └── crawler_comment.py      # 评论数据提取辅助
```

### 情绪分析目录结构
```
agent/                # 用户 Agent 系统
├── user_profile.py           # 用户画像构建
├── rule_agent.py             # 规则型 Agent
├── llm_agent.py              # LLM 型 Agent
├── agent_factory.py          # Agent 工厂
└── agent_orchestrator.py     # Agent 编排器（辩论图）
gnn/                  # 图神经网络
├── model.py                  # GNN 模型定义
├── dataset.py                # 图数据集
├── trainer.py                # 训练器
└── predictor.py              # 预测器
features/             # 特征工程
├── keyword_sentiment.py      # 关键词情绪分析
├── text_embedding.py         # 文本嵌入
└── feature_pipeline.py       # 特征流水线
data_loader/          # 数据加载
├── loader.py                 # 数据加载器
├── preprocessor.py           # 预处理器
└── graph_builder.py          # 图构建器（将 Agent 辩论图转为 GNN 输入）
config.py             # 统一配置（数据采集 + 模型超参）
main.py               # 主入口
logger.py             # 日志模块
```

### 数据流（v2 流水线）
```
币安广场（无限滚动）
       ↓ Playwright 滚动 + URL 采集
data_collection/crawlers/crawler_v2.py → square_posts_v2.db（SQLite，去重帖子索引）
                      ↓ Playwright 页面导航
       data_collection/downloaders/fetch_pages_from_db.py → *.html 文件
                      ↓ 离线正则/JSON 提取
       data_collection/parsers/parse_binance_square_html_final.py → 结构化 JSON（帖子ID、作者、内容、时间、产品、评论）
```

### 情绪分析数据流
```
解析后的 JSONL 文件
       ↓ data_loader/loader.py
用户帖子与评论数据
       ↓ data_loader/preprocessor.py → features/feature_pipeline.py
特征矩阵 + 用户画像
       ↓ agent/agent_orchestrator.py（基于 agent/user_profile.py + agent/agent_factory.py）
Agent 辩论图
       ↓ data_loader/graph_builder.py
GNN 输入图
       ↓ gnn/model.py → gnn/trainer.py
情绪预测
       ↓ gnn/predictor.py
最终市场情绪输出 → 与币价涨跌对比验证
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
- **持久化浏览器配置**: Chromium 用户数据目录（`data_collection/tmp_chrome_profile/`）在多次运行间保留登录 Cookie



### todo
1、根据币安广场爬取代码设计金色财经的爬取