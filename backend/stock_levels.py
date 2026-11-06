"""Bounded single-stock structural levels from raw daily OHLC data."""
from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any

import pandas as pd


SUPPORTED_LEVELS = (
    "rolling_range",
    "previous_session",
    "classic_pivot",
    "keltner_short",
    "keltner_mid",
    "keltner_long",
    "atr_channel",
    "fibonacci",
    "round_numbers",
)
UNSUPPORTED_LEVELS = (
    "volume_profile",
    "gap_levels",
)

FIBONACCI_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)


def load_stock_levels(code: str, requested_date: date | None = None) -> dict[str, Any]:
    """Load one symbol's raw daily bars and cached PIT factors for a current as-of date."""
    from datetime import datetime, time, timedelta
    from pathlib import Path

    import pandas as pd
    import portfolio as pf
    from aqsp.core.errors import DataError
    from aqsp.core.time import SHANGHAI_TZ, latest_completed_trading_day, now_shanghai
    from aqsp.data.cache import DataCache
    from aqsp.data.source_factory import build_data_source

    current = now_shanghai()
    if requested_date and requested_date > current.date():
        raise ValueError("as_of 不能晚于上海当日")
    target = requested_date or current.date()
    end = latest_completed_trading_day(
        datetime.combine(target, time(16, 0), tzinfo=SHANGHAI_TZ)
    )
    start = end - timedelta(days=180)
    cache = DataCache(Path(pf._tenant_dir()) / "stock-level-history.db")
    source = build_data_source("tencent", cache=cache)
    source.set_workload("historical")
    frames = source.fetch_daily([code], start, end, adjust="")
    frame = frames.get(code)
    if frame is None:
        raise DataError("个股 raw 日线未返回")

    factor_cache = DataCache()
    bar_dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    dates = sorted({day for day in bar_dates if not pd.isna(day) and day <= end})
    factor_records = {
        day: factor_cache.get_adj_factor_record(code, day) for day in dates
    }
    as_of_factor = factor_cache.get_adj_factor_record(code, end)
    frame["adj_factor"] = [
        factor_records[day].value
        if not pd.isna(day) and day <= end and factor_records.get(day)
        else None
        for day in bar_dates
    ]
    frame.attrs["pit_as_of_factor"] = as_of_factor.value if as_of_factor else None
    frame.attrs["pit_factor_source"] = ", ".join(
        sorted(
            {record.source for record in factor_records.values() if record}
            | ({as_of_factor.source} if as_of_factor else set())
        )
    )
    frame.attrs["pit_factor_fetched_at"] = as_of_factor.fetched_at if as_of_factor else None
    frame.attrs["pit_factor_as_of"] = as_of_factor.date.isoformat() if as_of_factor else None
    frame.attrs["price_mode"] = "raw"
    return calculate_stock_levels(frame, requested_as_of=end)


def _unavailable(
    level_type: str,
    reason: str,
    formula: str = "",
    price_basis: str = "raw",
) -> dict[str, Any]:
    return {
        "type": level_type,
        "status": "unavailable",
        "formula": formula,
        "reason": reason,
        "as_of": None,
        "price_basis": price_basis,
        "source": "",
        "levels": [],
    }


def calculate_stock_levels(
    frame: pd.DataFrame,
    *,
    requested_as_of: date,
    minimum_rows: int = 20,
    rolling_window: int = 60,
) -> dict[str, Any]:
    """Calculate non-signaling structural levels; caller must provide raw daily bars.

    Historical raw OHLC is never silently mixed with qfq display bars. Levels
    retain source dates and formulas and are descriptive, not trading advice.
    """
    if minimum_rows < 2 or rolling_window < minimum_rows:
        raise ValueError("价位窗口参数无效")
    if frame.empty:
        return {
            "as_of": None,
            "price_basis": "raw",
            "source": "",
            "fetched_at": None,
            "available_rows": 0,
            "warning": "没有可用日线。",
            "levels": [
                _unavailable(kind, "没有可用不复权日线。")
                for kind in SUPPORTED_LEVELS + UNSUPPORTED_LEVELS
            ],
        }
    if str(frame.attrs.get("price_mode") or "") != "raw":
        return {
            "as_of": None,
            "price_basis": "unknown",
            "source": "",
            "fetched_at": None,
            "available_rows": 0,
            "warning": "数据源未确认不复权口径，价位不可计算。",
            "levels": [
                _unavailable(
                    kind,
                    "来源价格口径不是已确认的 raw。",
                    price_basis="unknown",
                )
                for kind in SUPPORTED_LEVELS + UNSUPPORTED_LEVELS
            ],
        }
    source = str(frame.attrs.get("source_name") or frame.attrs.get("source") or "")
    fetched_at = str(frame.attrs.get("fetched_at") or "")
    if not source or not fetched_at:
        return {
            "as_of": None,
            "price_basis": "raw",
            "source": source,
            "fetched_at": fetched_at or None,
            "available_rows": 0,
            "warning": "日线缺少来源或取数时间。",
            "levels": [
                _unavailable(kind, "日线缺少可核验来源或取数时间。")
                for kind in SUPPORTED_LEVELS + UNSUPPORTED_LEVELS
            ],
        }

    required = {"date", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("日线缺少必要的 OHLC 字段")
    rows = frame.copy()
    dates = pd.to_datetime(rows["date"], errors="coerce")
    rows = rows.loc[dates.notna()].copy()
    rows["_day"] = dates.loc[dates.notna()].dt.date
    rows = (
        rows.loc[rows["_day"] <= requested_as_of]
        .sort_values("_day")
        .drop_duplicates("_day", keep="last")
    )
    for column in ("open", "high", "low", "close"):
        rows[column] = pd.to_numeric(rows[column], errors="coerce")
    rows = rows.dropna(subset=["open", "high", "low", "close"])
    valid = (
        rows[["open", "high", "low", "close"]].map(isfinite).all(axis=1)
        & (rows[["open", "high", "low", "close"]] > 0).all(axis=1)
        & (rows["high"] >= rows[["open", "close", "low"]].max(axis=1))
        & (rows["low"] <= rows[["open", "close", "high"]].min(axis=1))
    )
    rows = rows.loc[valid].reset_index(drop=True)
    as_of = rows["_day"].iloc[-1].isoformat() if not rows.empty else None
    factor_source = str(frame.attrs.get("pit_factor_source") or "")
    factor_fetched_at = str(frame.attrs.get("pit_factor_fetched_at") or "") or None
    factor_as_of = str(frame.attrs.get("pit_factor_as_of") or "") or None
    as_of_factor = pd.to_numeric(
        pd.Series([frame.attrs.get("pit_as_of_factor")]), errors="coerce"
    ).iloc[0]
    factors = (
        pd.to_numeric(rows["adj_factor"], errors="coerce")
        if "adj_factor" in rows.columns
        else pd.Series(dtype="float64")
    )
    pit_basis_ready = (
        not rows.empty
        and len(factors) == len(rows)
        and factors.notna().all()
        and factors.map(isfinite).all()
        and (factors > 0).all()
        and isfinite(as_of_factor)
        and as_of_factor > 0
    )
    if pit_basis_ready:
        ratio = factors / float(as_of_factor)
        for column in ("open", "high", "low", "close"):
            rows[column] = rows[column].astype(float) * ratio
    level_price_basis = "pit_as_of" if pit_basis_ready else "raw"
    level_source = (
        f"{source} + PIT factors ({factor_source or '来源未知'})"
        if pit_basis_ready
        else source
    )

    levels: list[dict[str, Any]] = []
    if len(rows) >= minimum_rows and pit_basis_ready:
        window = rows.tail(rolling_window)
        levels.append({
            "type": "rolling_range",
            "status": "available",
            "formula": f"过去 {len(window)} 个交易日 OHLC 的最高/最低价，按 as-of PIT 复权因子换算",
            "reason": "",
            "as_of": as_of,
            "price_basis": level_price_basis,
            "source": level_source,
            "levels": [
                {"label": "区间压力", "price": float(window["high"].max())},
                {"label": "区间支撑", "price": float(window["low"].min())},
            ],
        })
    else:
        reason = (
            f"有效日线不足 {minimum_rows} 根。"
            if len(rows) < minimum_rows
            else "缺少完整的 as-of PIT 复权因子，不能比较跨日 raw 价位。"
        )
        levels.append(_unavailable("rolling_range", reason))

    if len(rows) >= 2:
        previous = rows.iloc[-2]
        levels.append({
            "type": "previous_session",
            "status": "available",
            "formula": (
                "前一已完成交易日 high/low；按 as-of PIT 因子统一价基"
                if pit_basis_ready
                else "前一已完成交易日 raw high/low；仅对应其记录日期的价格"
            ),
            "reason": "",
            "as_of": as_of,
            "price_basis": level_price_basis,
            "source": level_source,
            "levels": [
                {"label": "前高", "price": float(previous["high"]), "date": previous["_day"].isoformat()},
                {"label": "前低", "price": float(previous["low"]), "date": previous["_day"].isoformat()},
            ],
        })
        pivot = (float(previous["high"]) + float(previous["low"]) + float(previous["close"])) / 3
        levels.append({
            "type": "classic_pivot",
            "status": "available",
            "formula": "(前一已完成交易日 high + low + close) / 3；R1/S1 = 2P-low/high",
            "reason": "",
            "as_of": as_of,
            "price_basis": level_price_basis,
            "source": level_source,
            "levels": [
                {"label": "P", "price": round(pivot, 2)},
                {"label": "R1", "price": round(2 * pivot - float(previous["low"]), 2)},
                {"label": "S1", "price": round(2 * pivot - float(previous["high"]), 2)},
            ],
        })
    else:
        levels.extend(
            _unavailable(kind, "至少需要两个已完成交易日。")
            for kind in ("previous_session", "classic_pivot")
        )

    close_values = rows["close"].astype(float)
    high_values = rows["high"].astype(float)
    low_values = rows["low"].astype(float)
    true_range = pd.concat(
        [
            high_values - low_values,
            (high_values - close_values.shift(1)).abs(),
            (low_values - close_values.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_14 = (
        float(true_range.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
        if pit_basis_ready and len(rows) >= 14
        else None
    )

    def append_available(
        level_type: str,
        formula: str,
        values: list[dict[str, Any]],
    ) -> None:
        levels.append(
            {
                "type": level_type,
                "status": "available" if values else "unavailable",
                "formula": formula,
                "reason": "" if values else (
                    "缺少完整的 as-of PIT 复权因子，不能比较跨日 raw 价位。"
                    if not pit_basis_ready
                    else "有效日线不足该类型所需窗口。"
                ),
                "as_of": as_of if values else None,
                "price_basis": level_price_basis,
                "source": level_source if values else "",
                "levels": values,
            }
        )

    def value(label: str, price: float) -> dict[str, Any]:
        return {"label": label, "price": round(price, 2)}

    for level_type, window, multiplier, label in (
        ("keltner_short", 20, 2.0, "短期"),
        ("keltner_mid", 60, 2.5, "中期"),
        ("keltner_long", 120, 3.0, "长期"),
    ):
        values: list[dict[str, Any]] = []
        if atr_14 is not None and len(rows) >= window:
            mean = float(close_values.tail(window).mean())
            values = [
                value(f"{label}通道上轨", mean + multiplier * atr_14),
                value(f"{label}通道下轨", mean - multiplier * atr_14),
            ]
        append_available(
            level_type,
            f"MA{window}(close) ± {multiplier:g} × Wilder ATR14，OHLC 使用 as-of PIT 价基",
            values,
        )

    atr_values: list[dict[str, Any]] = []
    if atr_14 is not None and not rows.empty:
        last_close = float(close_values.iloc[-1])
        atr_values = [
            value("ATR 上轨(+2)", last_close + 2 * atr_14),
            value("ATR 上轨(+1.5)", last_close + 1.5 * atr_14),
            value("ATR 下轨(-1.5)", last_close - 1.5 * atr_14),
            value("ATR 下轨(-2)", last_close - 2 * atr_14),
        ]
    append_available(
        "atr_channel",
        "最新 close ± 1.5/2 × Wilder ATR14，OHLC 使用 as-of PIT 价基",
        atr_values,
    )

    fib_values: list[dict[str, Any]] = []
    if pit_basis_ready and len(rows) >= 10:
        window = rows.tail(120)
        high_position = int(window["high"].to_numpy().argmax())
        low_position = int(window["low"].to_numpy().argmin())
        swing_high = float(window["high"].iloc[high_position])
        swing_low = float(window["low"].iloc[low_position])
        if swing_high > swing_low:
            swing_range = swing_high - swing_low
            up_swing = high_position > low_position
            fib_values = [
                value(
                    f"Fib {ratio * 100:.1f}%",
                    swing_high - swing_range * ratio
                    if up_swing
                    else swing_low + swing_range * ratio,
                )
                for ratio in FIBONACCI_RATIOS
            ]
    append_available(
        "fibonacci",
        "近120根有效日线最高/最低点；按两端点时间先后选波段方向，数据统一到 as-of PIT 价基，比例 23.6/38.2/50/61.8/78.6%",
        fib_values,
    )

    round_values: list[dict[str, Any]] = []
    if not rows.empty:
        last_close = float(close_values.iloc[-1])
        step = 0.5 if last_close < 10 else 1.0 if last_close < 20 else 5.0 if last_close < 100 else 10.0 if last_close < 500 else 50.0
        lower = last_close * 0.9
        upper = last_close * 1.1
        first = int(lower / step) + (1 if lower % step > 0 else 0)
        candidates = [
            round(index * step, 2)
            for index in range(first, int(upper / step) + 1)
            if index * step > 0 and abs(index * step - last_close) / last_close >= 0.01
        ]
        candidates.sort(key=lambda price: abs(price - last_close))
        round_values = [value(f"整数关口 {price:g}", price) for price in candidates[:8]]
    append_available(
        "round_numbers",
        "最新 close 附近 ±10%；按价格区间选步长(0.5/1/5/10/50)，排除距现价小于1%的点，最多8个",
        round_values,
    )

    levels.extend(
        _unavailable(kind, {
            "volume_profile": "缺少已审计的价位成交量分布契约。",
            "gap_levels": "缺少跨除权日一致价基的缺口判定。",
        }[kind])
        for kind in UNSUPPORTED_LEVELS
    )
    return {
        "as_of": as_of,
        "price_basis": "pit_as_of" if pit_basis_ready else "raw_partial",
        "source": source,
        "fetched_at": fetched_at,
        "adjustment_factor_source": factor_source,
        "adjustment_factor_fetched_at": factor_fetched_at,
        "adjustment_factor_as_of": factor_as_of,
        "available_rows": len(rows),
        "warning": (
            f"OHLC 使用截至 {factor_as_of or as_of or '未知'} 可得的 PIT 因子统一价基；仅作结构参考，不是买卖信号。"
            if pit_basis_ready
            else "缺少完整的 PIT 复权因子；仅显示单日 raw 参考，跨日价位已阻断。"
        ),
        "levels": levels,
    }
