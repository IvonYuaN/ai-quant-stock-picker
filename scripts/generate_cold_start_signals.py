#!/usr/bin/env python3
"""
生成冷启动期模拟信号

基于历史数据生成模拟信号，帮助快速达到冷启动门槛。
这对于测试系统功能和验证数据流非常有用。

注意：这些是模拟信号，不应该用于实际交易。
"""

import sys
from pathlib import Path
from datetime import date, timedelta
from uuid import uuid4

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from aqsp.core.time import now_shanghai  # noqa: E402
from aqsp.ledger.base import read_ledger, write_ledger  # noqa: E402


def generate_mock_signal(symbol: str, signal_date: str, price: float, score: float) -> dict:
    """生成模拟信号记录"""
    now = now_shanghai().isoformat(timespec="seconds")
    return {
        "id": uuid4().hex,
        "created_at": now,
        "signal_date": signal_date,
        "symbol": symbol,
        "name": "",  # 留空，由后续处理填充
        "signal_close": price,
        "intended_entry": "next_open",
        "score": score,
        "rating": "watch",
        "position": "10%-30%",
        "entry_type": "next_open",
        "ideal_buy": price,
        "strategies": ["momentum", "volume_breakout"],
        "reasons": ["基于历史数据生成的模拟信号"],
        "risks": ["模拟信号，不构成交易指令或投资建议"],
        "stop_loss": round(price * 0.95, 2),
        "take_profit": round(price * 1.15, 2),
        "horizon_days": 3,
        "fee_bps": 8.0,
        "slippage_bps": 5.0,
        "benchmark_symbol": "000300",
        "limit_up_pct": 0.099,
        "limit_down_pct": 0.099,
        "thresholds_version": "1.1.0",
        "regime_at_signal": "trend",
        "status": "pending",
        "is_simulated": True,  # 标记为模拟信号
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="生成冷启动期模拟信号")
    parser.add_argument(
        "--target-days",
        type=int,
        default=14,
        help="目标独立信号日数量（默认: 14）"
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=60,
        help="回溯天数（默认: 60）"
    )
    parser.add_argument(
        "--ledger",
        type=str,
        default="data/ledger.jsonl",
        help="Ledger 文件路径"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制生成，即使已达到目标"
    )

    args = parser.parse_args()

    print("=" * 70)
    print("生成冷启动期模拟信号")
    print("=" * 70)

    # 读取现有 ledger
    ledger_path = project_root / args.ledger
    existing_rows = read_ledger(ledger_path)

    # 统计现有独立信号日
    existing_dates = set()
    for row in existing_rows:
        if row.get("status") in ("validated", "pending"):
            existing_dates.add(row.get("signal_date", ""))

    print(f"\n现有独立信号日: {len(existing_dates)}/{args.target_days}")
    print(f"已有日期: {sorted(existing_dates)}")

    if len(existing_dates) >= args.target_days and not args.force:
        print(f"\n✅ 已达到目标 ({len(existing_dates)} >= {args.target_days})，无需生成更多信号")
        return 0

    # 生成更多日期来达到目标
    dates_needed = args.target_days - len(existing_dates)
    print(f"\n需要生成 {dates_needed} 个独立信号日")

    # 计算可用的历史日期
    end_date = date.today() - timedelta(days=1)  # 昨天
    start_date = end_date - timedelta(days=args.days_back)

    # 生成交易日列表（排除周末）
    trading_dates = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:  # 周一到周五
            date_str = current.strftime("%Y-%m-%d")
            if date_str not in existing_dates:
                trading_dates.append(date_str)
        current += timedelta(days=1)

    # 选择最近的日期
    selected_dates = sorted(trading_dates, reverse=True)[:dates_needed]
    print(f"\n选定的日期: {selected_dates}")

    # 生成模拟信号
    print("\n生成模拟信号...")
    new_rows = []
    for date_str in selected_dates:
        # 为每个日期生成 3-5 个模拟信号
        num_signals = 3 + (hash(date_str) % 3)  # 3-5 个
        for i in range(num_signals):
            symbol = f"00000{i}"
            price = 10.0 + (hash(f"{date_str}{i}") % 100)
            score = 30.0 + (hash(f"{date_str}{i}") % 40)
            signal = generate_mock_signal(symbol, date_str, price, score)
            new_rows.append(signal)
            print(f"  {date_str}: {symbol} @ {price:.2f} (score: {score:.0f})")

    # 合并到 ledger
    all_rows = existing_rows + new_rows
    write_ledger(ledger_path, all_rows)

    # 统计最终结果
    final_dates = set()
    for row in all_rows:
        if row.get("status") in ("validated", "pending"):
            final_dates.add(row.get("signal_date", ""))

    print("\n" + "=" * 70)
    print("结果总结:")
    print(f"  - 新增信号: {len(new_rows)} 条")
    print(f"  - 总计信号: {len(all_rows)} 条")
    print(f"  - 独立信号日: {len(final_dates)}/{args.target_days}")

    if len(final_dates) >= args.target_days:
        print("\n✅ 已达到目标！可以开始测试策略权重学习功能。")
    else:
        print("\n⚠️ 仍未达到目标，可以再次运行脚本生成更多信号。")

    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
