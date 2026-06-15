# 已处理数据来源

本目录只放已经正确处理、已经并入 `final.jsonl`，或已经被明确消费过的数据来源备份。

目录规则：

- `dataset/result/<数据集目录>/`：仍需处理的数据集，还没有完全并入 `final.jsonl`。
- `dataset/used/`：已处理来源备份、合并报告、可复现实验脚本。
- `archive/`：非主线代码、缓存、金色财经等暂不参与当前币安主流程的内容。

## 已并入 final 的旧解析 clean 候选

- `merged_into_final_20260611_old_parsed_clean.jsonl`
- `merged_into_final_20260611_old_parsed_clean.report.json`

来源为 `dataset/result/recovered_from_old_parsed_articles/clean_label_candidates.jsonl`，已在 2026-06-11 合并到 `dataset/result/final.jsonl`。

## 辅助脚本

- `recover_old_parsed_articles.py`：从 `dataset/result/update_news_legacy/parsed_articles/binance_square_articles_parsed.json` 重新生成恢复结果。该脚本仅用于复现整理过程，不代表数据已经全部处理完成。
