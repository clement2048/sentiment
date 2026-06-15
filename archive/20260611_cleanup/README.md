# 2026-06-11 归档说明

本目录用于存放从项目主目录移出的非当前主流程文件。

当前推荐主流程仍保留在根目录和主数据目录：

- `update_news.py`：通过 Binance BAPI 增量获取新闻列表 CSV。
- `process_csv.py`：从 CSV 下载 Binance Square HTML、拦截评论 API，并解析输出 JSONL。
- `parsers/parse_article.py`：解析 HTML 与 sidecar 评论。
- `repair/`：评论标签修复、分流、去重工具。
- `dataset/csv/`：币安新闻 CSV。
- `dataset/html/update_news/`：币安 HTML、API 原始响应、评论 sidecar。
- `dataset/result/`：币安解析与修复结果。
- `dataset/used/`：已经跑过、整理过、后续仍可能复用的数据集。

本次仍留在归档中的内容包括：

- `jinse/`：金色财经采集与处理脚本。
- `dataset/html/jinse/`：金色财经 HTML。
- `dataset/csv/jinse_news.csv`：金色财经 CSV。
- `dataset/result/jinse_parsed.jsonl`、`dataset/result/jinse_check.txt`：金色财经结果。
- `dataset/jinse_api_sniff.json`：金色财经 API 嗅探结果。
- `unused/`：历史未使用脚本。
- `news_readme.md`：旧 coin 定向采集流程说明。
- `crawler_profile_output/`、`csv_output/`：历史调试或旧流程输出。
- `tmp_chrome_profile_jinse/`：金色财经独立浏览器缓存目录。

说明：旧 `dataset/update_news/`、`dataset/update_news_v2/` 以及整理出的旧解析结果已经迁到 `dataset/used/`，因为这些数据仍可能用于后续重抓评论、修复标签或合并训练集。
