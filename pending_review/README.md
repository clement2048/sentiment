# 待检查脚本说明

这个目录用于暂存根目录中不属于当前推荐主流程、且暂未确认仍需使用的历史脚本、调试脚本和备用脚本。

当前推荐主流程仍然是：

```text
update_news.py
  -> process_csv.py
  -> parsers/parse_article.py
```

默认不要把本目录中的脚本当作当前主流程入口，也不要在没有明确需求时建议优先运行这些脚本。

## 当前暂存脚本

- `debug_extract.py`
- `extract_app_data.py`
- `fetch_comment.py`
- `fetch_pages_from_csv.py`
- `parse_binance_square_txt.py`
- `_check_logged_comment.py`
- `_check_profile_open.py`

这些文件没有直接删除，是为了后续人工复查。如果确认无用，再单独删除或归档。
