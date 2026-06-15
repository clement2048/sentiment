from __future__ import annotations

import collections
import csv
import datetime
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULT_ROOT = PROJECT_ROOT / "dataset" / "result"
SOURCE_FILE = RESULT_ROOT / "update_news_legacy" / "parsed_articles" / "binance_square_articles_parsed.json"
OUTPUT_DIR = RESULT_ROOT / "recovered_from_old_parsed_articles"


def iter_comments(comments):
    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        yield comment
        yield from iter_comments(comment.get("replies", []))


def comment_stats(post):
    comments = list(iter_comments(post.get("comments", [])))
    valid_count = sum(
        1
        for comment in comments
        if comment.get("label") in (1, -1) and not comment.get("comment_error")
    )
    error_counts = collections.Counter(
        str(comment.get("comment_error") or "")
        for comment in comments
        if comment.get("comment_error")
    )
    return len(comments), valid_count, dict(error_counts)


def read_existing_ids():
    result_dir = PROJECT_ROOT / "dataset" / "result"
    names = [
        "final.jsonl",
        "clean_labeled.jsonl",
        "pending_missing_symbol.jsonl",
        "pending_price_error.jsonl",
        "pending_structure_error.jsonl",
        "parsed_28.jsonl",
        "parsed_29.jsonl",
        "parsed_dy.jsonl",
        "6-3.jsonl",
        "6-4.jsonl",
    ]
    result = {}
    for name in names:
        path = result_dir / name
        ids = set()
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ids.add(str(json.loads(line).get("post_id") or ""))
                    except json.JSONDecodeError:
                        continue
        result[name] = ids
    return result


def write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
    existing_ids = read_existing_ids()
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    full = []
    clean = []
    pending_missing = []
    pending_other = []

    for original in data:
        post = dict(original)
        post_id = str(post.get("post_id") or "")
        total_comments, valid_comments, error_counts = comment_stats(post)
        overlap_files = [name for name, ids in existing_ids.items() if post_id in ids]
        source_file = str(post.get("source_file") or "")

        post["_archive_recovery"] = {
            "recovered_from": str(SOURCE_FILE),
            "recovered_at": generated_at,
            "original_source_file": source_file,
            "post_url_preserved": bool(post.get("post_url")),
            "comment_total_recount": total_comments,
            "valid_comment_count": valid_comments,
            "comment_error_counts": error_counts,
            "overlap_existing_result_files": overlap_files,
            "source_html_currently_exists": (PROJECT_ROOT / source_file).exists(),
        }

        full.append(post)
        if post.get("label_error") == "missing_symbol" or "missing_symbol" in error_counts:
            pending_missing.append(post)
        elif total_comments > 0 and valid_comments == total_comments and not post.get("label_error"):
            clean.append(post)
        else:
            pending_other.append(post)

    write_jsonl(OUTPUT_DIR / "all_preserved.jsonl", full)
    write_jsonl(OUTPUT_DIR / "clean_label_candidates.jsonl", clean)
    write_jsonl(OUTPUT_DIR / "pending_missing_symbol.jsonl", pending_missing)
    write_jsonl(OUTPUT_DIR / "pending_other.jsonl", pending_other)

    with (OUTPUT_DIR / "urls_to_recrawl.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "category",
            "post_id",
            "post_url",
            "original_source_file",
            "comment_total_recount",
            "valid_comment_count",
            "label_error",
            "products",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for category, rows in [
            ("clean_label_candidates", clean),
            ("pending_missing_symbol", pending_missing),
            ("pending_other", pending_other),
        ]:
            for post in rows:
                meta = post.get("_archive_recovery", {})
                writer.writerow({
                    "category": category,
                    "post_id": post.get("post_id", ""),
                    "post_url": post.get("post_url", ""),
                    "original_source_file": meta.get("original_source_file") or post.get("source_file", ""),
                    "comment_total_recount": meta.get("comment_total_recount", ""),
                    "valid_comment_count": meta.get("valid_comment_count", ""),
                    "label_error": post.get("label_error", ""),
                    "products": "|".join(post.get("products") or []),
                })

    report = {
        "source_file": str(SOURCE_FILE),
        "generated_at": generated_at,
        "rows": len(full),
        "unique_post_ids": len({str(post.get("post_id") or "") for post in full}),
        "with_post_url": sum(1 for post in full if post.get("post_url")),
        "with_comments": sum(
            1 for post in full if post["_archive_recovery"]["comment_total_recount"] > 0
        ),
        "total_comments": sum(
            post["_archive_recovery"]["comment_total_recount"] for post in full
        ),
        "total_valid_comments": sum(
            post["_archive_recovery"]["valid_comment_count"] for post in full
        ),
        "clean_label_candidates": len(clean),
        "pending_missing_symbol": len(pending_missing),
        "pending_other": len(pending_other),
        "overlap_with_any_current_result": sum(
            1 for post in full if post["_archive_recovery"]["overlap_existing_result_files"]
        ),
        "source_html_exists_now": sum(
            1 for post in full if post["_archive_recovery"]["source_html_currently_exists"]
        ),
        "outputs": {
            "all_preserved": str(OUTPUT_DIR / "all_preserved.jsonl"),
            "clean_label_candidates": str(OUTPUT_DIR / "clean_label_candidates.jsonl"),
            "pending_missing_symbol": str(OUTPUT_DIR / "pending_missing_symbol.jsonl"),
            "pending_other": str(OUTPUT_DIR / "pending_other.jsonl"),
            "urls_to_recrawl": str(OUTPUT_DIR / "urls_to_recrawl.csv"),
        },
    }
    (OUTPUT_DIR / "recovery_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    readme_lines = [
        "# 旧 parsed_articles 恢复报告",
        "",
        f"- 来源文件：`{SOURCE_FILE}`",
        f"- 生成时间：{report['generated_at']}",
        f"- 总记录数：{report['rows']}",
        f"- 唯一 post_id：{report['unique_post_ids']}",
        f"- 保留 post_url 的记录数：{report['with_post_url']}",
        f"- 有评论记录数：{report['with_comments']}",
        f"- 总评论数：{report['total_comments']}",
        f"- 有效标注评论数：{report['total_valid_comments']}",
        f"- 可直接作为 clean 候选的记录数：{report['clean_label_candidates']}",
        f"- 待修复 missing_symbol 的记录数：{report['pending_missing_symbol']}",
        f"- 其他待检查记录数：{report['pending_other']}",
        f"- 与当前 `dataset/result` 结果重叠的记录数：{report['overlap_with_any_current_result']}",
        f"- 当前还能在本项目路径找到原始 source_file HTML 的记录数：{report['source_html_exists_now']}",
        "",
        "## 输出文件",
        "",
        "- `all_preserved.jsonl`：完整保留全部字段，并附加 `_archive_recovery` 元信息。",
        "- `clean_label_candidates.jsonl`：评论已带有效 label 且无 comment_error 的候选。",
        "- `pending_missing_symbol.jsonl`：缺少币种或交易对，后续需要人工或规则修复。",
        "- `pending_other.jsonl`：其他未归类问题。",
        "- `urls_to_recrawl.csv`：轻量 URL 索引，适合后续按 URL 重抓 HTML 或评论。",
        "",
        "说明：这些记录均保留了 `post_url`。即使原始 HTML 不在当前主流程目录，也可以后续按 URL 重新下载 HTML 或重抓评论。",
    ]
    (OUTPUT_DIR / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
