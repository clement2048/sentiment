# 数据集处理与清理方案

## 目录规则

- `dataset/result/final.jsonl`：当前最终可用训练/分析数据，只放已经带评论、可用 label、并按 `post_id` 去重后的记录。
- `dataset/result/<数据集目录>/`：仍需处理的数据集。放在这里表示“还没有完全并入 final”。
- `dataset/used/`：已经正确处理、已经并入 final，或已经被明确消费过的数据来源备份。
- `archive/`：非主线代码、缓存、无关旧流程文件，不作为后续数据处理入口。

## 当前待处理数据集

### `dataset/result/recovered_from_old_parsed_articles/`

来源：旧 `binance_square_articles_parsed.json` 恢复结果。

当前状态：

- `clean_label_candidates.jsonl`：218 条，已在 2026-06-11 并入 `dataset/result/final.jsonl`。
- `pending_missing_symbol.jsonl`：10 条，缺少币种或交易对，需要修复 symbol 后再标注价格。
- `pending_other.jsonl`：17 条，主要是 `fallback_post_time` 与 `no_kline_data`，需要复核评论时间或重试 K 线。
- `urls_to_recrawl.csv`：245 条 URL，全量保留，后续可重抓 HTML 或评论。

已处理部分备份：

- `dataset/used/merged_into_final_20260611_old_parsed_clean.jsonl`
- `dataset/used/merged_into_final_20260611_old_parsed_clean.report.json`

下一步建议：

1. 对 `pending_missing_symbol.jsonl` 运行 symbol 修复或人工补币种。
2. 对 `pending_other.jsonl` 复核 `fallback_post_time`，并重试 `no_kline_data`。
3. 修复后重新分流 clean/pending。
4. clean 部分再用 `repair/dedupe_jsonl.py` 合并到 `final.jsonl`。

### `dataset/result/update_news_v2/`

来源：旧 `crawler_v2` 流程产出的 Square 帖子索引。

当前状态：

- `binance_square_posts.csv`：15050 行。
- `binance_square_posts_raw.json`：15050 条。
- `square_posts_v2.db`：旧 SQLite 数据库。
- `urls_to_process.csv`：已生成的当前流程友好 URL 清单。

注意：这批主要是 URL 索引，不等于已有评论数据。当前统计显示本地没有对应 HTML 和评论 sidecar，因此需要重新下载页面、拦截评论 API、解析并标注。

下一步建议：

1. 先用 `urls_to_process.csv` 小规模下载验证。
2. 确认页面类型：如果是 Binance 官方 News Article，用 `parsers/parse_article.py`；如果是用户帖子，应使用旧用户帖子解析器 `parsers/parse_binance_square_html_final.py` 或新增适配分流逻辑。
3. 生成新的解析 JSONL。
4. 分流 clean/pending。
5. clean 部分合并进 `final.jsonl`。

建议小样本命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
D:\anaconda\Scripts\conda.exe run -n sentiment python process_csv.py --csv dataset/result/update_news_v2/urls_to_process.csv --html-dir dataset/html/update_news --output dataset/result/update_news_v2_parsed_check.jsonl --limit 3 --headless --timeout-seconds 20 --comment-wait 2 --comment-pages 1 --pause-seconds 0
```

### `dataset/result/update_news_legacy/`

来源：旧 `update_news` 目录。

当前状态：

- `parsed_articles/binance_square_articles_parsed.json` 已恢复到 `dataset/result/recovered_from_old_parsed_articles/`。
- `master_news_dataset_28.csv` 和 `saved_news/temp_binance_news_2026-04-28_15-56-08.csv` 是旧 CSV 来源，可作为 URL 或内容补充。

下一步建议：

1. 保留作为恢复来源，不再直接合并。
2. 若 `recovered_from_old_parsed_articles` 的 pending 全部处理完成，再把该目录整体移动到 `dataset/used/`。

## 合并到 final 的标准

一条记录进入 `final.jsonl` 前应满足：

- 有稳定 `post_id`。
- 有 `post_url` 或可追溯来源。
- `comments` 非空。
- 评论有 `label`，且 `comment_error` 为空。
- `label_error` 为空。
- 按 `post_id` 去重后保留更高质量记录。

合并命令模板：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
D:\anaconda\Scripts\conda.exe run -n sentiment python repair\dedupe_jsonl.py --input dataset\result\final.jsonl <新的_clean_jsonl> --output dataset\result\final.jsonl --report dataset\result\final_dedupe_report.json
```

## 处理完成后的移动规则

- 如果整个数据集都已经并入 final 或明确不再需要处理，移动到 `dataset/used/`。
- 如果只有一部分处理完，不移动整个目录；只在 `dataset/used/` 保存已合并部分的备份与报告。
- 如果只是 URL 索引，还没有抓评论，不放入 `used`。
