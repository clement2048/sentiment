# JSONL label 异常分流报告

- 生成时间：2026-06-05 15:51:25
- 跳过文件：无

## 分类统计

| 分类 | 帖子数 | 评论数 | 输出文件 | 样例 post_id |
|---|---:|---:|---|---|
| clean | 385 | 591 | `clean_labeled.jsonl` | 328639879438513, 326773302228449, 317353268392961, 317346282722866, 317330071987458 |
| future | 0 | 0 | `pending_future_price.jsonl` | - |
| missing_symbol | 445 | 680 | `pending_missing_symbol.jsonl` | 317304222737666, 307474388264593, 307468795389345, 307456470654930, 307432472334834 |
| price_error | 50 | 87 | `pending_price_error.jsonl` | 317313961111665, 307321331371265, 317313961111665, 307321331371265, 307321331371265 |
| structure_error | 55 | 367 | `pending_structure_error.jsonl` | 307372825581650, 307324797607281, 307043267795553, 306721722477682, 306627600725842 |

## 错误码统计

- `comment_error:missing_symbol`：671
- `label_error:missing_symbol`：445
- `comment_error:price_api_error`：84
- `comment_error:missing_label_reason`：69
- `label_warning:fallback_post_time`：1
