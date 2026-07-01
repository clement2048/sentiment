# dataset/result 清理与待办

本文件记录 [dataset/result/](.) 目录最近一次按 `final.jsonl` 拆分清理的进展与待办。

## 当前状态（2026-06-29 21:43）

### 顶层文件

| 文件 | 行数 | 说明 |
|---|---:|---|
| `final.jsonl` | 438 | 主数据集，未变动 |
| `final_dedupe_report.json` | — | 上次 final 合并的报告 |
| `label_error_split_report.md` | — | 上次 split 报告（已过时，待重跑） |
| `pending_missing_symbol.jsonl` | 251 | 全部不在 final，待 symbol 修复 |
| `pending_price_error.jsonl` | 5 | 全部不在 final，待价格接口修复 |
| `pending_structure_error.jsonl` | 36 | 全部不在 final，待结构/字段补全 |
| `parsed_only_here.json` | 2 | parsed.json 拆分后保留的 2 条 not_in_final |
| `pending_future_price.jsonl` | — | 空文件，已删除 |

### 子目录

| 目录 | 说明 |
|---|---|
| `recovered_from_old_parsed_articles/` | 245 行 all_preserved、10 行 pending_missing_symbol、17 行 pending_other、245 行 urls_to_recrawl。<br>**注意**：`clean_label_candidates.jsonl`（218 行）**已丢失**——见下方"事故记录" |
| `update_news_legacy/` | 旧 URL 来源备份 + 245 行大 JSON `binance_square_articles_parsed.json`，不动 |
| `update_news_v2/` | 实验性数据，已 gitignore。`chunk_probe/` 目录（134 个 .js）已删除 |

### `dataset/used/` 本次新增

**时间戳 `20260629_162023`（清理主流程）**
- `split_against_final_report_20260629_162023.json`：处理报告
- `pending_price_error_20260629_162023.jsonl`：38 行（原文件，dedupe 前）
- `pending_price_error_in_final_20260629_162023.jsonl`：33 行（已在 final 的部分）
- `pending_missing_symbol_20260629_162023.jsonl`：251 行
- `pending_structure_error_20260629_162023.jsonl`：36 行

**时间戳 `20260629_214329`（parsed.json 拆分）**
- `parsed_20260629_214329.json`：源文件（7 条 JSON array）
- `parsed_in_final_20260629_214329.json`：5 条 in_final

> 注：首次执行（时间戳 `20260629_161543`）产生的中间文件因发现 `pending_*.jsonl` 自身被重复处理（dry-run 也会 append）的 bug，已全部清除。

## 事故记录

### `clean_label_candidates.jsonl` 已丢失

- **时间**：2026-06-29
- **影响**：`recovered_from_old_parsed_articles/clean_label_candidates.jsonl`（218 行）已不存在
- **经过**：第一次清理 `split_against_final.py --dry-run` 时把它移到 `used/recovered_from_old_parsed_articles_clean_label_candidates_20260629_161543.jsonl`；随后发现 dry-run 仍会修改文件（bug），为恢复一致状态清除了 `*20260629_161543*` 全部备份
- **数据是否丢失**：
  - 该文件的 218 条数据 **100% 已在 final.jsonl 中**（2026-06-11 已合并，见 [used/merged_into_final_20260611_old_parsed_clean.jsonl](../used/merged_into_final_20260611_old_parsed_clean.jsonl)）
  - 但作为「来源备份」的对象本身已无法复现
- **教训**：`--dry-run` 必须严格不改文件；目前 [repair/split_against_final.py](../../repair/split_against_final.py) 已修复该 bug（`if args.dry_run: continue` 提前返回）

### 7 个顶层源文件已丢失

- **影响**：`clean_labeled` / `test_stream` / `6-3` / `6-4` / `parsed_28` / `parsed_29` / `parsed_dy` 这 7 个 JSONL **完全不存在**：
  - result 下：被 dry-run 移走
  - used 下：被清除 `*20260629_161543*` 备份时一起删了
- **可恢复性**：
  - `clean_labeled.jsonl`：[dataset/repair/label_fix_20260605/after_split/clean_labeled.jsonl](../repair/label_fix_20260605/after_split/clean_labeled.jsonl) 还在（385 行，可参考）
  - 其他 6 个（test_stream、6-3、6-4、parsed_28、parsed_29、parsed_dy）**无任何备份**——彻底丢失
- **数据影响**：
  - `clean_labeled` 100% 在 final；`test_stream` 100% 在 final；`6-3` 2/3 在 final；`6-4` 0/1 在 final
  - `parsed_28/29/dy` 各自有 121/108/283 条 not_in_final（含错误），这些数据的来源备份丢失了
  - 但错误数据本身已分流到 result 下的 `pending_*.jsonl`，没有数据丢失
  - 唯一损失：`parsed_28/29/dy` 中 clean 且不在 final 的部分（合计约几十条），无法复现"来源 → 拆分过程"
- **教训**：以后清理前先确认 used 里有完整备份再清；或者干脆 never 删 used 备份

## 已完成

1. 提取 `final.jsonl` 全部 438 个 `post_id` 作为已消费集合。
2. 顶层原始文件（`clean_labeled` / `test_stream` / `6-3` / `6-4` / `parsed_28` / `parsed_29` / `parsed_dy`）按 `in_final` / `only_here` 拆分：
   - `in_final` 部分 → `used/`
   - `only_here` 中的错误行 → 对应 `pending_*.jsonl`
   - `only_here` 中的 clean 行 → `used/`
   - 源文件 → `used/`
3. 三个 `pending_*.jsonl` 按 `post_id` dedupe（split_label_errors 不去重，本身存在重复）：
   - `pending_missing_symbol`：454 → 251
   - `pending_price_error`：41 → 38
   - `pending_structure_error`：55 → 36
4. `pending_price_error.jsonl` 中 33 个已在 final 的行备份到 `used/`，剩 5 行保留在 result。
5. 空文件 `pending_future_price.jsonl` 删除。
6. `parsed.json`（7 条 JSON array）拆分：5 条 in_final → `used/`，2 条 only_here → `result/parsed_only_here.json`，源文件备份到 `used/`。
7. `update_news_v2/chunk_probe/`（134 个 .js）删除。

## 逐条 post_id 验证

2026-06-29 对所有 jsonl/json vs final 做了严格 post_id 集合比对，确认：
- `dataset/used/` 下 5 个 jsonl 与 `final.jsonl` 的重叠无差错
- `dataset/result/` 下 3 个 pending_*.jsonl 内容 = `used 备份 orig - in_final`（逐条一致）
- `parsed.json` 中 5 in_final + 2 not_in_final 的拆分与 7 条 post_id 完全对应
- `recovered/all_preserved.jsonl` 的 245 条 = `binance_square_articles_parsed.json` 的 245 条（差集为 0）
- `all_preserved.not_in_final` 27 条 = `pending_missing_symbol` 10 + `pending_other` 17

## 脚本

- 主脚本：[repair/split_against_final.py](../../repair/split_against_final.py)
- 用法：

```bash
# 干跑，不改文件
python repair/split_against_final.py --dry-run

# 实际执行
python repair/split_against_final.py
```

## 待办（按优先级）

### P0：当前 `pending_*.jsonl` 的修复

- [ ] **pending_missing_symbol（251 条）**
  - 来源：帖子未识别出币种 / 交易对
  - 建议：在 [parsers/parse_article.py](../../parsers/parse_article.py) 中增强 `extract_products_from_html`，对 251 条样本先抽样，看是 HTML 结构变化、币种名称还是 APP_DATA 缺失
  - 修复后用 `repair/dedupe_jsonl.py` 合并进 `final.jsonl`

- [ ] **pending_structure_error（36 条）**
  - 来源：评论 `label` 为空但 `comment_error` 字段也为空
  - 建议：先 `head` 看样本，确认是评论时间缺失、HTML 解析遗漏还是其他
  - 修复后再走 split → 合并

- [ ] **pending_price_error（5 条）**
  - 来源：Binance API `price_api_error` / `no_kline_data`
  - 建议：等 API 恢复后重试，或人工补 `p0` / `p1`

- [ ] **parsed_only_here（2 条）**
  - post_id: `317313961111665`、`317304222737666`
  - 这两个 post_id 同时出现在 `recovered/pending_missing_symbol.jsonl` 中，疑似 `missing_symbol` 类型
  - 建议：与 pending_missing_symbol 合并处理

### P1：`recovered_from_old_parsed_articles/` 收尾

- [ ] `pending_missing_symbol` (10) + `pending_other` (17) = 27 条待修
- [ ] `urls_to_recrawl.csv`（245 行）作为未来重抓来源保留
- [ ] `all_preserved.jsonl`（245 行）作为来源备份保留

### P2：`update_news_v2/`

- [ ] `parsed_direct_check.jsonl`（152 条）评估质量：0 在 final，需要抽样确认 schema 和数据质量
- [ ] `parsed_direct_check_sample5.jsonl`（5 条）是上面的小样本
- [ ] `binance_square_posts.csv` + `urls_to_process.csv` 是 15050 条 URL 索引，是否重抓需要决策
- [ ] 几个 `*probe*.py` / `fetch_comments_direct.py` / `parse_direct_api_to_jsonl.py` 是实验脚本

### P3：`update_news_legacy/`

- [ ] `binance_square_articles_parsed.json`（245 条）跟 `recovered/all_preserved.jsonl` 完全一致（已逐条验证）
- [ ] 是否需要将 218 条 in_final 部分移到 `used/`，27 条 not_in_final 单独处理？

## 复现命令

```bash
# 重新跑 split 流程（已处理完，再次跑会幂等：源文件已不存在于 result）
python repair/split_against_final.py --dry-run
```

## 后续合并到 final 的命令

```bash
# pending 修复完成后
python repair/dedupe_jsonl.py \
  --input dataset/result/final.jsonl \
          dataset/result/pending_missing_symbol.jsonl \
          dataset/result/pending_price_error.jsonl \
          dataset/result/pending_structure_error.jsonl \
  --output dataset/result/final.jsonl \
  --report dataset/result/final_dedupe_report.json
```
