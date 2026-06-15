#!/usr/bin/env python3
"""
JSONL 去重工具。
默认按 post_id 去重，保留有效评论数最多的记录；若分数相同，保留后出现的记录。
适合把多个来源或分流后的 clean JSONL 合并成最终训练集。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按 post_id 去重 JSONL 数据集")
    parser.add_argument(
        "--input",
        nargs="+",
        required=True,
        help="输入 JSONL 文件，可传多个",
    )
    parser.add_argument(
        "--output",
        default="dataset/result/final.jsonl",
        help="输出 JSONL 路径，默认 dataset/result/final.jsonl",
    )
    parser.add_argument(
        "--report",
        default="",
        help="去重报告路径，默认与输出同目录的 final_dedupe_report.json",
    )
    parser.add_argument(
        "--key",
        default="post_id",
        help="去重键，默认 post_id",
    )
    return parser.parse_args()


def iter_comments(comments: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        yield comment
        yield from iter_comments(comment.get("replies", []))


## 有效评论数：要有情感label 是 1 或 -1，且没有 comment_error 的评论数量。
def valid_comment_count(post: Dict[str, Any]) -> int:
    total = 0
    for comment in iter_comments(post.get("comments", [])):
        if comment.get("label") in (1, -1) and not comment.get("comment_error"):
            total += 1
    return total


def total_comment_count(post: Dict[str, Any]) -> int:
    return sum(1 for _ in iter_comments(post.get("comments", [])))


def score_post(post: Dict[str, Any], order: int) -> Tuple[int, int, int]:
    """返回去重排序分数：有效评论数、总评论数、出现顺序。"""
    return (valid_comment_count(post), total_comment_count(post), order)


def read_inputs(paths: List[Path]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    bad_rows: List[Dict[str, Any]] = []
    order = 0
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                order += 1
                try:
                    post = json.loads(line)
                except json.JSONDecodeError as exc:
                    bad_rows.append({
                        "file": str(path),
                        "line": line_no,
                        "error": str(exc),
                    })
                    continue
                post["_dedupe_input_file"] = str(path)
                post["_dedupe_order"] = order
                rows.append(post)
    return rows, bad_rows


def dedupe_posts(rows: List[Dict[str, Any]], key: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    selected: Dict[str, Dict[str, Any]] = {}
    duplicate_keys: Dict[str, int] = {}
    missing_key_rows = 0

    for post in rows:
        value = str(post.get(key) or "").strip()
        if not value:
            missing_key_rows += 1
            value = f"__missing_key__:{post.get('_dedupe_order')}"

        if value in selected:
            duplicate_keys[value] = duplicate_keys.get(value, 1) + 1
            current = selected[value]
            if score_post(post, int(post["_dedupe_order"])) >= score_post(current, int(current["_dedupe_order"])):
                selected[value] = post
        else:
            selected[value] = post

    output = sorted(selected.values(), key=lambda p: int(p.get("_dedupe_order", 0)))
    for post in output:
        post.pop("_dedupe_input_file", None)
        post.pop("_dedupe_order", None)

    report = {
        "input_rows": len(rows),
        "output_rows": len(output),
        "removed_rows": len(rows) - len(output),
        "duplicate_keys": len(duplicate_keys),
        "missing_key_rows": missing_key_rows,
        "dedupe_key": key,
        "strategy": "keep max(valid_comment_count, total_comment_count, input_order)",
        "duplicate_samples": [
            {"key": k, "count": v}
            for k, v in list(duplicate_keys.items())[:20]
        ],
    }
    return output, report


def write_jsonl(path: Path, posts: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for post in posts:
            fh.write(json.dumps(post, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    input_paths = [Path(p) for p in args.input]
    rows, bad_rows = read_inputs(input_paths)
    output_posts, report = dedupe_posts(rows, args.key)
    report["input_files"] = [str(p) for p in input_paths]
    report["bad_rows"] = bad_rows

    output_path = Path(args.output)
    write_jsonl(output_path, output_posts)

    report_path = Path(args.report) if args.report else output_path.with_name("final_dedupe_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"去重完成: {report['input_rows']} -> {report['output_rows']}，删除重复 {report['removed_rows']} 行")
    print(f"输出文件: {output_path}")
    print(f"报告文件: {report_path}")


if __name__ == "__main__":
    main()
