#!/usr/bin/env python3
"""JSONL label 异常分流与诊断工具。

默认读取 dataset/result 下的 Binance 评论 JSONL，排除金色财经数据。
输出 clean_labeled 和各类 pending JSONL，方便后续人工复查或补标。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULT_DIR = PROJECT_ROOT / "dataset" / "result"


OUTPUT_FILES = {
    "clean": "clean_labeled.jsonl",
    "future": "pending_future_price.jsonl",
    "missing_symbol": "pending_missing_symbol.jsonl",
    "price_error": "pending_price_error.jsonl",
    "structure_error": "pending_structure_error.jsonl",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分流 JSONL label 异常样本")
    parser.add_argument(
        "--input",
        nargs="+",
        default=None,
        help="输入 JSONL 文件；默认扫描 dataset/result/*.jsonl，并排除 jinse 文件",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_RESULT_DIR),
        help="输出目录，默认 dataset/result",
    )
    parser.add_argument(
        "--include-jinse",
        action="store_true",
        help="包含金色财经 JSONL。默认排除，因为其 schema 与评论标注不一致",
    )
    return parser.parse_args()


def iter_comments(comments: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        yield comment
        yield from iter_comments(comment.get("replies", []))


def count_comments(comments: Iterable[Dict[str, Any]]) -> int:
    return sum(1 for _ in iter_comments(comments))


def parse_t0_ms(comment: Dict[str, Any]) -> int:
    value = str(comment.get("t0") or "").strip()
    if not value:
        return 0
    try:
        return int(datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    except ValueError:
        return 0


def parse_t_window_hours(comment: Dict[str, Any]) -> int:
    raw = str(comment.get("t_window") or "24h").strip().lower()
    match = re.search(r"(\d+)", raw)
    return int(match.group(1)) if match else 24


def is_future_price_case(comment: Dict[str, Any]) -> bool:
    if comment.get("comment_error") == "future_price_unavailable":
        return True
    if comment.get("label") is not None:
        return False
    t0_ms = parse_t0_ms(comment)
    if t0_ms <= 0:
        return False
    t1_ms = t0_ms + parse_t_window_hours(comment) * 3600 * 1000
    return t1_ms > int(datetime.now().timestamp() * 1000)


def normalize_comment_error(comment: Dict[str, Any]) -> None:
    err = str(comment.get("comment_error") or "")
    if err.startswith("price_api_error:"):
        comment["debug_error"] = err
        comment["comment_error"] = "price_api_error"
    elif err == "fallback_post_time":
        comment["label_warning"] = "fallback_post_time"
        comment["comment_error"] = ""

    if comment.get("label") is None and not comment.get("comment_error"):
        if is_future_price_case(comment):
            comment["comment_error"] = "future_price_unavailable"
        else:
            comment["comment_error"] = "missing_label_reason"


def normalize_post(post: Dict[str, Any]) -> Dict[str, Any]:
    for comment in iter_comments(post.get("comments", [])):
        normalize_comment_error(comment)
    post["comment_total_num"] = count_comments(post.get("comments", []))
    return post


def classify_post(post: Dict[str, Any]) -> str:
    if post.get("label_error") == "missing_symbol":
        return "missing_symbol"

    has_missing_symbol = False
    has_future = False
    has_price = False
    has_structure = False

    comments = list(iter_comments(post.get("comments", [])))
    if not comments:
        return "structure_error"

    for comment in comments:
        err = str(comment.get("comment_error") or "")
        label = comment.get("label")
        if err == "missing_symbol":
            has_missing_symbol = True
        elif err == "future_price_unavailable":
            has_future = True
        elif err in {"price_api_error", "no_kline_data"} or err.startswith("invalid_"):
            has_price = True
        elif label is None or err:
            has_structure = True

    if has_structure:
        return "structure_error"
    if has_future:
        return "future"
    if has_missing_symbol:
        return "missing_symbol"
    if has_price:
        return "price_error"
    return "clean"


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    posts: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                post = json.loads(line)
            except json.JSONDecodeError as exc:
                posts.append({
                    "source_file": str(path),
                    "post_id": "",
                    "comments": [],
                    "label_error": f"json_decode_error:{exc}",
                    "_split_error_line": line_no,
                })
                continue
            post["_input_file"] = path.name
            posts.append(post)
    return posts


def write_jsonl(path: Path, posts: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for post in posts:
            cleaned = dict(post)
            cleaned.pop("_input_file", None)
            fh.write(json.dumps(cleaned, ensure_ascii=False) + "\n")


def write_report(path: Path, groups: Dict[str, List[Dict[str, Any]]], skipped: List[str]) -> None:
    lines = [
        "# JSONL label 异常分流报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 跳过文件：{', '.join(skipped) if skipped else '无'}",
        "",
        "## 分类统计",
        "",
        "| 分类 | 帖子数 | 评论数 | 输出文件 | 样例 post_id |",
        "|---|---:|---:|---|---|",
    ]
    for key, filename in OUTPUT_FILES.items():
        posts = groups.get(key, [])
        sample_ids = [str(p.get("post_id") or "") for p in posts[:5]]
        lines.append(
            f"| {key} | {len(posts)} | {sum(count_comments(p.get('comments', [])) for p in posts)} "
            f"| `{filename}` | {', '.join([s for s in sample_ids if s]) or '-'} |"
        )

    error_counter: Counter[str] = Counter()
    for posts in groups.values():
        for post in posts:
            if post.get("label_error"):
                error_counter[f"label_error:{post['label_error']}"] += 1
            for comment in iter_comments(post.get("comments", [])):
                err = comment.get("comment_error")
                if err:
                    error_counter[f"comment_error:{err}"] += 1
                warning = comment.get("label_warning")
                if warning:
                    error_counter[f"label_warning:{warning}"] += 1

    lines.extend(["", "## 错误码统计", ""])
    for key, value in error_counter.most_common():
        lines.append(f"- `{key}`：{value}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.input:
        input_files = [Path(p) for p in args.input]
    else:
        input_files = sorted(DEFAULT_RESULT_DIR.glob("*.jsonl"))

    skipped: List[str] = []
    filtered: List[Path] = []
    for path in input_files:
        if not args.include_jinse and "jinse" in path.name.lower():
            skipped.append(path.name)
            continue
        filtered.append(path)

    groups: Dict[str, List[Dict[str, Any]]] = {key: [] for key in OUTPUT_FILES}
    for path in filtered:
        for post in read_jsonl(path):
            normalized = normalize_post(post)
            groups[classify_post(normalized)].append(normalized)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for key, filename in OUTPUT_FILES.items():
        write_jsonl(output_dir / filename, groups.get(key, []))

    report_path = output_dir / "label_error_split_report.md"
    write_report(report_path, groups, skipped)
    print(f"分流完成，报告已写入: {report_path}")


if __name__ == "__main__":
    main()
