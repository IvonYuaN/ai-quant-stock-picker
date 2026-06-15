#!/usr/bin/env python3
"""
清理过期的信号数据和模拟信号

移除过期的信号，保持 ledger 的清洁。
"""

import sys
from pathlib import Path
from datetime import timedelta

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from aqsp.ledger.base import read_ledger, write_ledger  # noqa: E402
from aqsp.core.time import today_shanghai  # noqa: E402


def main():
    import argparse

    parser = argparse.ArgumentParser(description="清理过期的信号数据")
    parser.add_argument(
        "--ledger", type=str, default="data/ledger.jsonl", help="Ledger 文件路径"
    )
    parser.add_argument(
        "--max-age-days", type=int, default=90, help="信号最大保留天数（默认: 90）"
    )
    parser.add_argument(
        "--remove-simulated", action="store_true", help="删除所有模拟信号"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只显示将要删除的内容，不实际删除"
    )

    args = parser.parse_args()

    print("=" * 70)
    print("清理过期信号数据")
    print("=" * 70)

    # 读取现有 ledger
    ledger_path = project_root / args.ledger
    existing_rows = read_ledger(ledger_path)

    print(f"\n总记录数: {len(existing_rows)}")

    # 计算截止日期
    cutoff_date = (today_shanghai() - timedelta(days=args.max_age_days)).isoformat()
    print(f"截止日期: {cutoff_date} (保留 {args.max_age_days} 天内的数据)")

    # 分类统计
    stats = {
        "total": len(existing_rows),
        "expired": 0,
        "simulated": 0,
        "keep": 0,
    }

    filtered_rows = []
    expired_dates = set()
    simulated_dates = set()

    for row in existing_rows:
        signal_date = row.get("signal_date", "")
        is_simulated = row.get("is_simulated", False)
        is_expired = signal_date < cutoff_date

        if is_simulated and args.remove_simulated:
            stats["simulated"] += 1
            simulated_dates.add(signal_date)
            continue

        if is_expired:
            stats["expired"] += 1
            expired_dates.add(signal_date)
            if not args.dry_run:
                continue  # 不保留过期数据

        filtered_rows.append(row)
        stats["keep"] += 1

    print("\n统计:")
    print(f"  - 总记录: {stats['total']}")
    print(f"  - 将保留: {stats['keep']}")
    print(f"  - 已过期: {stats['expired']} (日期: {sorted(expired_dates)[:5]}...)")
    if args.remove_simulated:
        print(
            f"  - 模拟信号: {stats['simulated']} (日期: {sorted(simulated_dates)[:5]}...)"
        )

    if args.dry_run:
        print("\n⚠️  DRY RUN: 未实际删除任何数据")
        print("  如果确认无误，重新运行不加 --dry-run")
    else:
        write_ledger(ledger_path, filtered_rows)
        print("\n✅ 已删除过期数据")

    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
