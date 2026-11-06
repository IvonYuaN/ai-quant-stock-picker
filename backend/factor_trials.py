"""Bounded raw-price factor previews over an explicit local symbol sample."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import json
from typing import Any
from uuid import uuid4

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import latest_completed_trading_day, now_shanghai
from aqsp.data import source_factory
from aqsp.research.factor_expression import compile_factor_expression
from aqsp.strategies.factor_backtest import FactorBacktester


TRIAL_FIELDS = frozenset(
    {"open", "high", "low", "close", "volume", "amount", "prev_close", "raw_close"}
)


@dataclass(frozen=True)
class FactorTrialLimits:
    min_symbols: int = 20
    max_symbols: int = 200
    max_signal_calendar_days: int = 120
    max_lookback_bars: int = 120
    min_oos_dates: int = 20
    max_holding_days: int = 20


LIMITS = FactorTrialLimits()


def _data_sample_sha256(panel: pd.DataFrame) -> str:
    """Fingerprint the exact raw OHLCV rows used by this diagnostic trial."""
    frame = panel.reset_index()
    columns = ["date", "symbol", *sorted(TRIAL_FIELDS & set(frame.columns))]
    frame = frame.loc[:, columns].sort_values(["date", "symbol"], kind="stable")
    rows = json.loads(frame.to_json(orient="records", double_precision=15))
    canonical = json.dumps(
        {"columns": columns, "rows": rows},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_factor_trial(
    *,
    expression: str,
    symbols: list[str],
    signal_start: str,
    signal_end: str,
    holding_days: int,
    quantile: int = 5,
) -> dict[str, Any]:
    """Return a diagnostic cross-sectional IC preview; never a promotion verdict."""
    compiled = compile_factor_expression(expression)
    unsupported = sorted(set(compiled.fields) - TRIAL_FIELDS)
    if unsupported:
        raise ValueError(f"本机 PIT raw 试算暂不支持字段: {', '.join(unsupported)}")
    if compiled.max_lookback > LIMITS.max_lookback_bars:
        raise ValueError(f"最大预热窗口不能超过 {LIMITS.max_lookback_bars} 根日线")
    if type(holding_days) is not int or not 1 <= holding_days <= LIMITS.max_holding_days:
        raise ValueError(f"holding_days 必须在 1 到 {LIMITS.max_holding_days} 之间")
    if type(quantile) is not int or not 2 <= quantile <= 10:
        raise ValueError("分层数必须在 2 到 10 之间")

    normalized_symbols = list(dict.fromkeys(str(symbol).strip() for symbol in symbols))
    if not LIMITS.min_symbols <= len(normalized_symbols) <= LIMITS.max_symbols:
        raise ValueError(
            f"单次试算需提供 {LIMITS.min_symbols} 到 {LIMITS.max_symbols} 个去重标的"
        )
    if any(len(symbol) != 6 or not symbol.isdigit() for symbol in normalized_symbols):
        raise ValueError("symbols 必须是 6 位数字代码")

    try:
        start = date.fromisoformat(signal_start)
        end = date.fromisoformat(signal_end)
    except ValueError as exc:
        raise ValueError("signal_start/signal_end 必须是 YYYY-MM-DD") from exc
    if start >= end:
        raise ValueError("signal_start 必须早于 signal_end")
    if (end - start).days > LIMITS.max_signal_calendar_days:
        raise ValueError(f"单次因子试算区间不能超过 {LIMITS.max_signal_calendar_days} 个自然日")
    now = now_shanghai()
    if end > latest_completed_trading_day(now):
        raise ValueError("signal_end 不能晚于最近已收盘交易日")

    source = source_factory.build_data_source("sqlite_db", cache=None)
    set_workload = getattr(source, "set_workload", None)
    if callable(set_workload):
        set_workload("walkforward")
    source_id = str(getattr(source, "name", ""))
    price_mode_fn = getattr(source, "price_mode", None)
    price_mode = str(price_mode_fn()) if callable(price_mode_fn) else "unknown"
    if source_id != "sqlite_db" or price_mode != "raw":
        raise DataError("因子试算要求可核验的 sqlite_db raw 不复权数据源")
    available = set(source.get_available_symbols())
    missing_symbols = sorted(set(normalized_symbols) - available)
    if missing_symbols:
        raise ValueError(f"本地历史库缺少标的: {', '.join(missing_symbols[:10])}")

    warmup_calendar_days = LIMITS.max_lookback_bars * 2
    warmup_start = start - timedelta(days=warmup_calendar_days)
    frames = source.fetch_daily(normalized_symbols, warmup_start, end, adjust="")
    panel_parts: list[pd.DataFrame] = []
    missing_data_symbols: list[str] = []
    for symbol in normalized_symbols:
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            missing_data_symbols.append(symbol)
            continue
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        frame = frame.loc[frame["date"].notna()].sort_values("date")
        if frame["date"].duplicated().any():
            raise DataError(f"{symbol} 日线有重复交易日，因子试算已停止")
        if frame.empty:
            missing_data_symbols.append(symbol)
            continue
        frame["symbol"] = symbol
        frame["raw_close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame["prev_close"] = pd.to_numeric(frame["close"], errors="coerce").shift(1)
        panel_parts.append(frame)
    if len(panel_parts) < LIMITS.min_symbols:
        raise DataError(
            f"本地历史库仅返回 {len(panel_parts)} 个有效标的，至少需要 {LIMITS.min_symbols} 个"
        )

    panel = pd.concat(panel_parts, ignore_index=True)
    panel = panel.set_index(["date", "symbol"]).sort_index()
    factor_values = compiled.evaluate(panel)
    signal_dates = panel.index.get_level_values("date")
    signal_mask = (signal_dates >= start.isoformat()) & (signal_dates <= end.isoformat())
    factor_values = factor_values.loc[signal_mask]

    close = pd.to_numeric(panel["close"], errors="coerce")
    future_outcome_by_realized_date = close.groupby(level="symbol", sort=False).transform(
        lambda series: (series / series.shift(holding_days) - 1.0) * 100.0
    )
    backtest = FactorBacktester().backtest_factor(
        factor_values,
        future_outcome_by_realized_date,
        quantile=quantile,
        holding_days=holding_days,
    )
    metrics = asdict(backtest)
    metrics["factor_name"] = compiled.expression
    latest_data_date = str(panel.index.get_level_values("date").max())
    blockers = []
    if missing_data_symbols:
        blockers.append(
            f"本地历史库未返回 {len(missing_data_symbols)}/{len(normalized_symbols)} 个指定标的；"
            "当前结果不是完整指定样本"
        )
    if backtest.total_trades < LIMITS.min_oos_dates:
        blockers.append(
            f"有效横截面日期 {backtest.total_trades} 少于 {LIMITS.min_oos_dates}；仅诊断结果，不作晋级依据"
        )

    symbol_digest = hashlib.sha256(",".join(normalized_symbols).encode("ascii")).hexdigest()
    data_sample_digest = _data_sample_sha256(panel)
    return {
        "trial_id": str(uuid4()),
        "generated_at": now.isoformat(),
        "expression": compiled.expression,
        "expression_sha256": hashlib.sha256(compiled.expression.encode("utf-8")).hexdigest(),
        "fields": list(compiled.fields),
        "max_lookback": compiled.max_lookback,
        "source": source_id,
        "price_mode": price_mode,
        "pit_scope": "raw_ohlcv_only",
        "data_start": str(panel.index.get_level_values("date").min()),
        "warmup_start": warmup_start.isoformat(),
        "signal_start": start.isoformat(),
        "signal_end": end.isoformat(),
        "latest_data_date": latest_data_date,
        "requested_symbols": len(normalized_symbols),
        "used_symbols": len(panel_parts),
        "data_rows": len(panel),
        "missing_data_symbols": missing_data_symbols,
        "symbol_set_sha256": symbol_digest,
        "data_sample_sha256": data_sample_digest,
        "holding_days": holding_days,
        "quantile": quantile,
        "effective_cross_section_dates": backtest.total_trades,
        "metrics": metrics,
        "daily_ic": [{"date": day, "ic": value} for day, value in backtest.daily_ic],
        "evidence_status": "preview_only" if not blockers else "blocked",
        "blockers": blockers,
        "human_review_required": True,
        "promotion_authorized": False,
    }
