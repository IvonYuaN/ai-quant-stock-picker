#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class MergeSummary:
    target_rows: int
    source_rows: int
    merged_rows: int
    cold_start_days: int
    backup_paths: tuple[Path, ...]


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []

    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def prediction_key(row: dict) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("signal_date", "")),
        str(row.get("symbol", "")),
        str(row.get("thresholds_version", "")),
        str(row.get("regime_at_signal", row.get("regime", ""))),
        str(row.get("intended_entry", "next_open")),
    )


def normalize_row(row: dict) -> dict:
    normalized = dict(row)
    signal_date = str(normalized.get("signal_date", ""))
    if not normalized.get("signal_day_group"):
        normalized["signal_day_group"] = signal_date
    return normalized


def merge_rows(target_rows: list[dict], source_rows: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for row in target_rows + source_rows:
        normalized = normalize_row(row)
        row_key = prediction_key(normalized)
        if row_key in seen:
            continue
        seen.add(row_key)
        merged.append(normalized)
    return merged


def count_independent_signal_days(rows: list[dict]) -> int:
    signal_dates = {
        str(row.get("signal_date", ""))
        for row in rows
        if row.get("status") in {"pending", "validated"} and row.get("signal_date")
    }
    return len(signal_dates)


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows
    )
    path.write_text((text + "\n") if text else "", encoding="utf-8")


def backup_file(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    backup_path = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def merge_ledgers(
    target_path: Path,
    source_path: Path,
    *,
    backup: bool = True,
) -> MergeSummary:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups: list[Path] = []

    if backup:
        for path in {target_path, source_path}:
            backup_path = backup_file(path, stamp)
            if backup_path is not None:
                backups.append(backup_path)

    target_rows = load_rows(target_path)
    source_rows = [] if source_path == target_path else load_rows(source_path)
    merged_rows = merge_rows(target_rows, source_rows)
    write_rows(target_path, merged_rows)

    return MergeSummary(
        target_rows=len(target_rows),
        source_rows=len(source_rows),
        merged_rows=len(merged_rows),
        cold_start_days=count_independent_signal_days(merged_rows),
        backup_paths=tuple(sorted(backups)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合并服务器上的 ledger 文件")
    parser.add_argument(
        "--target",
        default="data/predictions.jsonl",
        help="主 ledger 路径（默认: data/predictions.jsonl）",
    )
    parser.add_argument(
        "--source",
        default="data/ledger.jsonl",
        help="待并入 ledger 路径（默认: data/ledger.jsonl）",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="不生成 .bak-时间戳 备份文件",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target_path = Path(args.target)
    source_path = Path(args.source)

    summary = merge_ledgers(
        target_path=target_path,
        source_path=source_path,
        backup=not args.no_backup,
    )

    print(f"target={target_path}")
    print(f"source={source_path}")
    print(
        f"rows target/source/merged="
        f"{summary.target_rows}/{summary.source_rows}/{summary.merged_rows}"
    )
    print(f"cold_start_days={summary.cold_start_days}")
    if summary.backup_paths:
        for backup_path in summary.backup_paths:
            print(f"backup={backup_path}")
    else:
        print("backup=disabled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
