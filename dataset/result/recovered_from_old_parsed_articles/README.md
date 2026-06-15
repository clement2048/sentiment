# 旧 parsed_articles 恢复报告

- 来源文件：`E:\code\sentiment\dataset\result\update_news_legacy\parsed_articles\binance_square_articles_parsed.json`
- 生成时间：2026-06-11 18:42:43
- 总记录数：245
- 唯一 post_id：245
- 保留 post_url 的记录数：245
- 有评论记录数：245
- 总评论数：1085
- 有效标注评论数：961
- 可直接作为 clean 候选的记录数：218
- 待修复 missing_symbol 的记录数：10
- 其他待检查记录数：17
- 与当前 `dataset/result` 结果重叠的记录数：218
- 当前还能在本项目路径找到原始 source_file HTML 的记录数：0

## 输出文件

- `all_preserved.jsonl`：完整保留全部字段，并附加 `_archive_recovery` 元信息。
- `clean_label_candidates.jsonl`：评论已带有效 label 且无 comment_error 的候选。
- `pending_missing_symbol.jsonl`：缺少币种或交易对，后续需要人工或规则修复。
- `pending_other.jsonl`：其他未归类问题。
- `urls_to_recrawl.csv`：轻量 URL 索引，适合后续按 URL 重抓 HTML 或评论。

说明：这些记录均保留了 `post_url`。即使原始 HTML 不在当前主流程目录，也可以后续按 URL 重新下载 HTML 或重抓评论。
