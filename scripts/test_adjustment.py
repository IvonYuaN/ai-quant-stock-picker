#!/usr/bin/env python3
"""
验证完整的复权数据流
"""

import sys
from pathlib import Path
from datetime import timedelta

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


from aqsp.data.adjust import AdjustmentService  # noqa: E402
from aqsp.data.sqlite_db_source import SqliteDbSource  # noqa: E402
from aqsp.core.time import today_shanghai  # noqa: E402


def main():
    print("=" * 70)
    print("验证完整的复权数据流")
    print("=" * 70)

    # 1. 清理旧缓存
    cache_path = project_root / "data" / "cache.db"
    if cache_path.exists():
        print(f"\n清理旧缓存: {cache_path}")
        cache_path.unlink()

    # 2. 初始化数据源
    print("\n初始化数据源...")
    sqlite_source = SqliteDbSource(
        db_path=project_root / "A股量化分析数据" / "astocks_qfq.db"
    )
    adjust_service = AdjustmentService()

    # 3. 获取数据：用000001作为测试标的
    symbol = "000001"
    end_date = today_shanghai()
    start_date = end_date - timedelta(days=100)
    print(f"\n获取标的: {symbol}, 日期范围: {start_date} ~ {end_date}")

    # 4. 获取不复权原始数据（用于ledger/回测）
    data = sqlite_source.fetch_daily([symbol], start_date, end_date, adjust="")
    if not data or symbol not in data:
        print("⚠️ 没有获取到数据")
        return 1

    df_raw = data[symbol]
    print(f"\n✅ 原始数据: {len(df_raw)} 行")
    print("前5行:")
    print(df_raw[["date", "open", "high", "low", "close", "volume", "amount"]].head())

    # 5. 检查 amount 估算是否生效
    has_zero_amount = (df_raw["amount"] <= 0).any()
    if has_zero_amount:
        print("\n⚠️ 仍有 amount 为 0 或缺失的行，验证估算逻辑")

    # 6. 应用前复权（用于指标计算）
    print("\n--- 前复权数据 ---")
    df_qfq = adjust_service.get_adjusted_df(df_raw, adjust="qfq")
    print(
        df_qfq[
            [
                "date",
                "open",
                "high",
                "low",
                "close",
                "open_qfq",
                "close_qfq",
                "adj_factor",
            ]
        ].head()
    )

    # 7. 应用后复权
    print("\n--- 后复权数据 ---")
    df_hfq = adjust_service.get_adjusted_df(df_raw, adjust="hfq")
    print(
        df_hfq[
            [
                "date",
                "open",
                "high",
                "low",
                "close",
                "open_hfq",
                "close_hfq",
                "adj_factor",
            ]
        ].head()
    )

    # 8. 计算简单移动平均，验证指标计算
    print("\n--- 技术指标示例 ---")
    df_qfq_sorted = df_qfq.sort_values("date").reset_index(drop=True)
    df_qfq_sorted["ma5_qfq"] = df_qfq_sorted["close_qfq"].rolling(5).mean()
    df_qfq_sorted["ma5_raw"] = df_qfq_sorted["close"].rolling(5).mean()

    print("最后5天均线（前复权 vs 不复权）:")
    last_5 = df_qfq_sorted[["date", "close", "close_qfq", "ma5_qfq", "ma5_raw"]].tail()
    print(last_5.to_string(index=False))

    print("\n" + "=" * 70)
    print("验证完成！体系总结:")
    print("- 原始不复权数据用于 ledger/回测（真实价格）")
    print("- 前复权数据用于技术指标计算（连续K线）")
    print("- 复权因子单独缓存，point-in-time 使用")
    print("- amount 缺失时自动估算（volume × 均价）")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
