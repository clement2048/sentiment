#!/usr/bin/env python3
"""按 final.jsonl 拆分 result 下各 JSONL。

规则：
1. 加载 final.jsonl 的 post_id 集合（视为已消费）。
2. 对每个待处理 JSONL：
   - 拆分 in_final（已并入 final）与 only_here 两组。
   - in_final → 备份到 dataset/used/<name>_in_final_<ts>.jsonl。
   - only_here 中有错的 → 按错误类型 append 到 dataset/result/pending_*.jsonl。
   - only_here 中 clean 的 → 备份到 dataset/used/<name>_only_here_clean_<ts>.jsonl。
   - 源文件整体备份到 dataset/used/<name>_<ts>.jsonl。
3. 全在 final 的（clean_labeled、test_stream、recovered/clean_label_candidates）
   直接整文件备份到 used。
4. pending_future_price.jsonl 为空则删除。
5. 写出处理报告 dataset/used/split_against_final_report_<ts>.json。

错误分类沿用 split_label_errors.py 的规则：
  structure > future > missing_symbol > price_error > clean
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = PROJECT_ROOT / "dataset" / "result"
USED_DIR = PROJECT_ROOT / "dataset" / "used"

PENDING_FILES = {
    "missing_symbol": "pending_missing_symbol.jsonl",
    "price_error": "pending_price_error.jsonl",
    "structure_error": "pending_structure_error.jsonl",
    "future": "pending_future_price.jsonl",
    "clean": None,  # clean 不写 pending
}

# 顶层文件处理顺序（不包含 pending_*.jsonl，它们是输出目标）
TOP_LEVEL_TARGETS = [
    "clean_labeled.jsonl",
    "test_stream.jsonl",
    "6-3.jsonl",
    "6-4.jsonl",
    "parsed_28.jsonl",
    "parsed_29.jsonl",
    "parsed_dy.jsonl",
]

# pending_*.jsonl：只把 in_final 部分备份到 used，不重新 append
PENDING_CLEANUP_TARGETS = [
    "pending_price_error.jsonl",
    "pending_structure_error.jsonl",
    "pending_missing_symbol.jsonl",
    "pending_future_price.jsonl",
]

# 子目录文件
SUBDIR_TARGETS = {
    "recovered_from_old_parsed_articles": [
        "clean_label_candidates.jsonl",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按 final.jsonl 拆分 result 下各 JSONL")
    parser.add_argument(
        "--final",
        default=str(RESULT_DIR / "final.jsonl"),
        help="最终 JSONL 路径",
    )
    parser.add_argument(
        "--result-dir",
        default=str(RESULT_DIR),
        help="结果目录",
    )
    parser.add_argument(
        "--used-dir",
        default=str(USED_DIR),
        help="已处理数据目录",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不移动文件")
    return parser.parse_args()


def iter_comments(comments: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        yield c
        yield from iter_comments(c.get("replies", []))


def count_comments(comments: Iterable[Dict[str, Any]]) -> int:
    return sum(1 for _ in iter_comments(comments))


def is_future_case(comment: Dict[str, Any]) -> bool:
    err = str(comment.get("comment_error") or "")
    if err == "future_price_unavailable":
        return True
    t0 = str(comment.get("t0") or "").strip()
    if not t0 or comment.get("label") is not None:
        return False
    try:
        t0_ms = int(datetime.strptime(t0, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    except ValueError:
        return False
    import re as _re
    win_raw = str(comment.get("t_window") or "24h").lower()
    m = _re.search(r"(\d+)", win_raw)
    hours = int(m.group(1)) if m else 24
    return t0_ms + hours * 3600 * 1000 > int(datetime.now().timestamp() * 1000)


def classify_post(post: Dict[str, Any]) -> str:
    """参考 split_label_errors.classify_post 的规则。"""
    if post.get("label_error") == "missing_symbol":
        return "missing_symbol"

    has_missing_symbol = False
    has_future = False
    has_price = False
    has_structure = False

    comments = list(iter_comments(post.get("comments", [])))
    if not comments:
        return "structure_error"

    for c in comments:
        err = str(c.get("comment_error") or "")
        label = c.get("label")
        if err == "missing_symbol":
            has_missing_symbol = True
        elif err == "future_price_unavailable" or is_future_case(c):
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
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # 保留坏行
                rows.append({"_parse_error": True, "_raw": line})
    return rows


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def backup_to_used(src: Path, used_dir: Path, ts: str, suffix: str = "") -> Path | None:
    """备份到 used，文件名加时间戳和可选后缀。返回备份路径，源文件被删除。"""
    if not src.exists():
        return None
    suffix_part = f"_{suffix}" if suffix else ""
    dest = used_dir / f"{src.stem}{suffix_part}_{ts}{src.suffix}"
    used_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest


def main() -> None:
    args = parse_args()
    result_dir = Path(args.result_dir)
    used_dir = Path(args.used_dir)
    final_path = Path(args.final)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 1) 加载 final_ids
    final_rows = read_jsonl(final_path)
    final_ids: set[str] = set()
    for r in final_rows:
        pid = str(r.get("post_id") or "").strip()
        if pid:
            final_ids.add(pid)
    print(f"[final] {final_path.name}: {len(final_ids)} unique post_ids")

    report: Dict[str, Any] = {
        "ts": ts,
        "final_path": str(final_path),
        "final_unique_post_ids": len(final_ids),
        "actions": [],
        "pending_appends": {},
    }

    def record(action: str, **details: Any) -> None:
        details["action"] = action
        report["actions"].append(details)

    # 2) 处理顶层文件
    for fname in TOP_LEVEL_TARGETS:
        src = result_dir / fname
        if not src.exists():
            continue
        rows = read_jsonl(src)
        if not rows:
            # 空文件，备份后删除
            backup = backup_to_used(src, used_dir, ts, "empty") if not args.dry_run else None
            record("empty_file", file=fname, backup=str(backup) if backup else None)
            continue

        in_final_rows: List[Dict[str, Any]] = []
        only_here_rows: List[Dict[str, Any]] = []
        for r in rows:
            pid = str(r.get("post_id") or "").strip()
            if pid and pid in final_ids:
                in_final_rows.append(r)
            else:
                only_here_rows.append(r)

        # 备份源文件（无论是否为空都备份）
        src_backup = backup_to_used(src, used_dir, ts) if not args.dry_run else None
        record(
            "process_top_level",
            file=fname,
            total=len(rows),
            in_final=len(in_final_rows),
            only_here=len(only_here_rows),
            source_backup=str(src_backup) if src_backup else None,
        )

        # in_final 备份
        if in_final_rows and not args.dry_run:
            dest = used_dir / f"{src.stem}_in_final_{ts}{src.suffix}"
            write_jsonl(dest, in_final_rows)
            record("backup_in_final", file=fname, count=len(in_final_rows), dest=str(dest))

        # only_here 按错误类型分流
        if only_here_rows:
            buckets: Dict[str, List[Dict[str, Any]]] = {k: [] for k in PENDING_FILES}
            for r in only_here_rows:
                cls = classify_post(r)
                buckets[cls].append(r)
            for cls, items in buckets.items():
                if not items:
                    continue
                report["pending_appends"].setdefault(cls, []).append({
                    "source_file": fname,
                    "count": len(items),
                })
                if args.dry_run:
                    record("dry_run_pending", file=fname, pending=cls, count=len(items))
                    continue
                if cls == "clean":
                    dest = used_dir / f"{src.stem}_only_here_clean_{ts}{src.suffix}"
                    write_jsonl(dest, items)
                    record("backup_only_here_clean", file=fname, count=len(items), dest=str(dest))
                else:
                    pending_name = PENDING_FILES[cls]
                    pending_path = result_dir / pending_name
                    with pending_path.open("a", encoding="utf-8") as fh:
                        for it in items:
                            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
                    record("append_pending", file=fname, pending=pending_name, count=len(items))

    # 3) 处理子目录
    for sub, files in SUBDIR_TARGETS.items():
        sub_dir = result_dir / sub
        if not sub_dir.exists():
            continue
        for fname in files:
            src = sub_dir / fname
            if not src.exists():
                continue
            rows = read_jsonl(src)
            in_final = sum(
                1 for r in rows
                if str(r.get("post_id") or "").strip() in final_ids
            )
            only_here = len(rows) - in_final

            # 100% 在 final 的话直接备份；否则整个子目录文件都备份（按用户指示：源文件也放到 used）
            dest = used_dir / f"{sub}_{src.stem}_{ts}{src.suffix}"
            if not args.dry_run:
                used_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
            record(
                "process_subdir",
                subdir=sub,
                file=fname,
                total=len(rows),
                in_final=in_final,
                only_here=only_here,
                dest=str(dest),
            )

    # 4) 处理 pending_*.jsonl：拆 in_final 备份，源文件备份，不重新 append
    for fname in PENDING_CLEANUP_TARGETS:
        src = result_dir / fname
        if not src.exists():
            continue
        rows = read_jsonl(src)
        if not rows:
            # 空文件
            backup = backup_to_used(src, used_dir, ts, "empty") if not args.dry_run else None
            record("empty_file", file=fname, backup=str(backup) if backup else None)
            continue

        in_final_rows: List[Dict[str, Any]] = []
        only_here_rows: List[Dict[str, Any]] = []
        for r in rows:
            pid = str(r.get("post_id") or "").strip()
            if pid and pid in final_ids:
                in_final_rows.append(r)
            else:
                only_here_rows.append(r)

        # 备份源文件
        src_backup = backup_to_used(src, used_dir, ts) if not args.dry_run else None
        record(
            "cleanup_pending",
            file=fname,
            total=len(rows),
            in_final=len(in_final_rows),
            only_here=len(only_here_rows),
            source_backup=str(src_backup) if src_backup else None,
        )

        # in_final 备份到 used
        if in_final_rows and not args.dry_run:
            dest = used_dir / f"{src.stem}_in_final_{ts}{src.suffix}"
            write_jsonl(dest, in_final_rows)
            record("backup_in_final", file=fname, count=len(in_final_rows), dest=str(dest))

        # only_here 重新写回 result
        if not args.dry_run:
            write_jsonl(src, only_here_rows)
            record("rewrite_only_here", file=fname, count=len(only_here_rows), dest=str(src))

    # 5) 写报告
    if not args.dry_run:
        report_path = used_dir / f"split_against_final_report_{ts}.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[report] {report_path}")

    # 6) 汇总
    print("\n=== Summary ===")
    for a in report["actions"]:
        print(f"  {a}")
    print("\n=== Pending appends (only_here) ===")
    for cls, items in report["pending_appends"].items():
        total = sum(i["count"] for i in items)
        sources = ", ".join(f"{i['source_file']}({i['count']})" for i in items)
        print(f"  {cls}: {total} from {sources}")


if __name__ == "__main__":
    main()
