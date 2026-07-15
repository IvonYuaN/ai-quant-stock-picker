from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd


@dataclass(frozen=True)
class BacktestAssumptionAudit:
    ok: bool
    blockers: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class BacktestFrameAudit:
    ok: bool
    blockers: tuple[str, ...]


_REQUIRED_FRAME_COLUMNS = frozenset({"date", "open", "high", "low", "close"})
_ADJUSTED_PRICE_MODES = frozenset(
    {"qfq", "hfq", "adjusted", "front_adjusted", "back_adjusted"}
)


def validate_backtest_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
) -> BacktestFrameAudit:
    """Reject explicit live/adjusted inputs before walk-forward slicing."""
    blockers: list[str] = []
    missing = sorted(_REQUIRED_FRAME_COLUMNS - set(frame.columns))
    if missing:
        blockers.append(f"{symbol}: missing columns {','.join(missing)}")

    workload = str(frame.attrs.get("workload", "") or "").strip().lower()
    if workload == "live_short":
        blockers.append(f"{symbol}: live_short data cannot enter backtest")

    price_mode = str(
        frame.attrs.get("price_mode", frame.attrs.get("adjust", "")) or ""
    ).strip().lower()
    if price_mode in _ADJUSTED_PRICE_MODES:
        blockers.append(f"{symbol}: adjusted price mode {price_mode} cannot enter backtest")

    dates = pd.to_datetime(frame["date"], errors="coerce") if "date" in frame else None
    if dates is not None:
        if dates.isna().any():
            blockers.append(f"{symbol}: invalid date present")
        if dates.duplicated().any():
            blockers.append(f"{symbol}: duplicate date present")
        if not dates.is_monotonic_increasing:
            blockers.append(f"{symbol}: dates are not ordered")

    return BacktestFrameAudit(ok=not blockers, blockers=tuple(blockers))


_REQUIRED_TRUE_FLAGS = {
    "uses_raw_prices": "回测必须使用不复权原始价格",
    "uses_point_in_time_data": "财务/成分/事件必须 point-in-time",
    "train_test_separated": "训练/验证窗口必须分离",
    "has_purge_window": "walk-forward 必须有 purge/embargo 窗口",
    "includes_transaction_costs": "回测必须包含手续费",
    "includes_slippage": "回测必须包含滑点",
    "excludes_not_executable": "not_executable 样本不得进入胜率统计",
}


def audit_backtest_assumptions(
    payload: Mapping[str, object],
) -> BacktestAssumptionAudit:
    blockers: list[str] = []
    for flag, message in _REQUIRED_TRUE_FLAGS.items():
        if payload.get(flag) is not True:
            blockers.append(f"{flag}: {message}")
    cost_model = str(payload.get("cost_model", "") or "").strip()
    if not cost_model:
        blockers.append("cost_model: 缺少成本模型说明")
    data_cutoff = str(payload.get("data_cutoff", "") or "").strip()
    signal_cutoff = str(payload.get("signal_cutoff", "") or "").strip()
    if data_cutoff and signal_cutoff and data_cutoff > signal_cutoff:
        blockers.append("data_cutoff: 数据可见时间晚于信号时间")
    price_mode = str(payload.get("price_mode", "") or "").strip().lower()
    if price_mode and price_mode != "raw":
        blockers.append("price_mode: 回测必须声明 raw 不复权价格")
    if payload.get("future_data_used") is True:
        blockers.append("future_data_used: 回测声明使用了未来数据")
    detail = "backtest assumptions ok" if not blockers else "; ".join(blockers[:3])
    return BacktestAssumptionAudit(
        ok=not blockers,
        blockers=tuple(blockers),
        detail=detail,
    )
