from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Mapping

from aqsp.backtest.audit import audit_backtest_assumptions


MIN_DSR = 1.0
MAX_PBO = 0.5
MAX_GATE_AGE_DAYS = 35
MIN_PRODUCTION_GATE_SYMBOLS = 3000
MIN_PRODUCTION_GATE_COVERAGE_RATIO = 0.9


@dataclass(frozen=True)
class WalkForwardGateValidation:
    ok: bool
    dsr: float | None
    pbo: float | None
    n_periods: int | None
    run_date: date | None
    age_days: int | None
    dsr_pass: bool | None
    pbo_pass: bool | None
    pbo_valid: bool | None
    both_pass: bool | None
    data_end: date | None
    thresholds_version: str | None
    assumption_audit_ok: bool | None
    assumption_audit_blockers: tuple[str, ...]
    blockers: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class WalkForwardGateEvidence:
    """可嵌入研究 proposal 的只读双门证据。"""

    ok: bool
    status: str
    dsr: float | None
    pbo: float | None
    n_periods: int | None
    run_date: date | None
    data_end: date | None
    thresholds_version: str | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class WalkForwardMarketCoverageValidation:
    ok: bool
    effective_symbols: int | None
    stock_symbols: int | None
    min_symbols: int
    required_symbols: int | None
    coverage_ratio: float | None
    blockers: tuple[str, ...]
    detail: str


def build_walkforward_gate_payload(
    *,
    dsr: float,
    pbo: float,
    run_date: str,
    start: str,
    end: str,
    n_periods: int,
    thresholds_version: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    pbo_valid = pbo > 0.0
    dsr_pass = dsr > MIN_DSR
    pbo_pass = pbo_valid and pbo < MAX_PBO
    payload: dict[str, object] = {
        "run_date": run_date,
        "deflated_sharpe": dsr,
        "pbo": pbo,
        "pbo_valid": pbo_valid,
        "dsr_pass": dsr_pass,
        "pbo_pass": pbo_pass,
        "both_pass": dsr_pass and pbo_pass,
        "data_start": start,
        "data_end": end,
        "n_periods": n_periods,
    }
    if metadata:
        payload.update({str(key): value for key, value in metadata.items()})
    if thresholds_version is not None:
        payload["thresholds_version"] = thresholds_version
    return payload


def validate_walkforward_market_coverage(
    payload: Mapping[str, object],
    *,
    min_symbols: int = MIN_PRODUCTION_GATE_SYMBOLS,
    min_coverage_ratio: float = MIN_PRODUCTION_GATE_COVERAGE_RATIO,
) -> WalkForwardMarketCoverageValidation:
    raw_count = payload.get("effective_symbols")
    raw_stock_symbols = payload.get("stock_symbols")
    selected_symbols = None
    if not isinstance(raw_stock_symbols, int):
        coverage_payload = payload.get("production_gate_coverage")
        if isinstance(coverage_payload, Mapping):
            raw_stock_symbols = coverage_payload.get("stock_symbols")
            selected_raw = coverage_payload.get("selected_symbols")
            if isinstance(selected_raw, int) and not isinstance(selected_raw, bool):
                selected_symbols = selected_raw
    blockers: list[str] = []
    effective_symbols = raw_count if isinstance(raw_count, int) else None
    stock_symbols = selected_symbols
    if stock_symbols is None:
        stock_symbols = (
            raw_stock_symbols if isinstance(raw_stock_symbols, int) else None
        )
    if isinstance(raw_count, bool) or not isinstance(raw_count, int):
        effective_symbols = None
        blockers.append("effective_symbols missing/invalid")
    if isinstance(raw_stock_symbols, bool) or (
        raw_stock_symbols is not None and not isinstance(raw_stock_symbols, int)
    ):
        stock_symbols = None
        blockers.append("stock_symbols invalid")

    required_symbols = min_symbols
    coverage_ratio = None
    if stock_symbols is not None and stock_symbols > 0:
        required_symbols = max(
            min_symbols, int(math.ceil(stock_symbols * min_coverage_ratio))
        )
        if effective_symbols is not None:
            coverage_ratio = effective_symbols / stock_symbols
    if effective_symbols is not None and effective_symbols < required_symbols:
        blockers.append(
            f"effective_symbols={effective_symbols} < required_symbols={required_symbols}"
        )

    if effective_symbols is None:
        detail = f"effective_symbols invalid; require >= {min_symbols}"
    elif stock_symbols is None:
        detail = f"{effective_symbols}/{required_symbols} effective symbols"
    else:
        ratio_text = (
            f"; coverage={coverage_ratio:.1%}" if coverage_ratio is not None else ""
        )
        detail = (
            f"{effective_symbols}/{stock_symbols} effective symbols; "
            f"require >= {required_symbols}{ratio_text}"
        )
    return WalkForwardMarketCoverageValidation(
        ok=not blockers,
        effective_symbols=effective_symbols,
        stock_symbols=stock_symbols,
        min_symbols=min_symbols,
        required_symbols=required_symbols,
        coverage_ratio=coverage_ratio,
        blockers=tuple(blockers),
        detail=detail,
    )


def validate_walkforward_gate_payload(
    payload: Mapping[str, object],
    *,
    today: date,
    max_age_days: int = MAX_GATE_AGE_DAYS,
    heldout_cutoff: date | None = None,
    expected_thresholds_version: str | None = None,
    require_assumption_audit: bool = False,
) -> WalkForwardGateValidation:
    run_date = _parse_date(payload.get("run_date"))
    dsr = _strict_float(payload.get("deflated_sharpe"))
    pbo = _strict_float(payload.get("pbo"))
    n_periods = _strict_int(payload.get("n_periods"))
    dsr_pass = _strict_bool(payload.get("dsr_pass"))
    pbo_pass = _strict_bool(payload.get("pbo_pass"))
    pbo_valid = _strict_bool(payload.get("pbo_valid"))
    both_pass = _strict_bool(payload.get("both_pass"))
    thresholds_version = _strict_text(payload.get("thresholds_version"))
    window_mode = (
        str(
            payload.get("window_mode")
            or payload.get("coverage_mode")
            or (
                payload.get("production_gate_coverage", {}).get("coverage_mode")
                if isinstance(payload.get("production_gate_coverage"), Mapping)
                else ""
            )
            or ""
        )
        .strip()
        .lower()
    )

    data_end = None
    if payload.get("data_end"):
        data_end = _parse_date(payload.get("data_end"))

    data_start = None
    if payload.get("data_start"):
        data_start = _parse_date(payload.get("data_start"))

    age_days = (today - run_date).days if run_date is not None else None
    blockers = _gate_blockers(
        dsr=dsr,
        pbo=pbo,
        n_periods=n_periods,
        run_date=run_date,
        age_days=age_days,
        max_age_days=max_age_days,
        dsr_pass=dsr_pass,
        pbo_pass=pbo_pass,
        pbo_valid=pbo_valid,
        both_pass=both_pass,
        raw_data_end=payload.get("data_end"),
        data_end=data_end,
        raw_data_start=payload.get("data_start"),
        data_start=data_start,
        heldout_cutoff=heldout_cutoff,
        window_mode=window_mode,
        thresholds_version=thresholds_version,
        expected_thresholds_version=expected_thresholds_version,
    )
    assumption_audit_ok = None
    assumption_audit_blockers: tuple[str, ...] = ()
    raw_assumptions = payload.get("backtest_assumptions")
    if isinstance(raw_assumptions, Mapping):
        audit = audit_backtest_assumptions(raw_assumptions)
        assumption_audit_ok = audit.ok
        assumption_audit_blockers = audit.blockers
        blockers.extend(f"assumption_audit: {item}" for item in audit.blockers)
    elif require_assumption_audit:
        blockers.append("assumption_audit missing/invalid")
    return WalkForwardGateValidation(
        ok=not blockers,
        dsr=dsr,
        pbo=pbo,
        n_periods=n_periods,
        run_date=run_date,
        age_days=age_days,
        dsr_pass=dsr_pass,
        pbo_pass=pbo_pass,
        pbo_valid=pbo_valid,
        both_pass=both_pass,
        data_end=data_end,
        thresholds_version=thresholds_version,
        assumption_audit_ok=assumption_audit_ok,
        assumption_audit_blockers=assumption_audit_blockers,
        blockers=tuple(blockers),
        detail=_format_gate_detail(
            dsr=dsr,
            pbo=pbo,
            n_periods=n_periods,
            age_days=age_days,
            dsr_pass=dsr_pass,
            pbo_pass=pbo_pass,
            pbo_valid=pbo_valid,
            both_pass=both_pass,
            blockers=blockers,
        ),
    )


def build_walkforward_gate_evidence(
    payload: Mapping[str, object],
    *,
    today: date,
    max_age_days: int = MAX_GATE_AGE_DAYS,
    heldout_cutoff: date | None = None,
    expected_thresholds_version: str | None = None,
    require_assumption_audit: bool = False,
) -> WalkForwardGateEvidence:
    """将 sidecar 校验结果整理为 proposal 可审计的证据摘要。

    该函数只读取和校验 payload，不改变 gate 判定，也不触碰 runtime 配置。
    """
    validation = validate_walkforward_gate_payload(
        payload,
        today=today,
        max_age_days=max_age_days,
        heldout_cutoff=heldout_cutoff,
        expected_thresholds_version=expected_thresholds_version,
        require_assumption_audit=require_assumption_audit,
    )
    return WalkForwardGateEvidence(
        ok=validation.ok,
        status="pass" if validation.ok else "fail",
        dsr=validation.dsr,
        pbo=validation.pbo,
        n_periods=validation.n_periods,
        run_date=validation.run_date,
        data_end=validation.data_end,
        thresholds_version=validation.thresholds_version,
        reasons=validation.blockers,
    )


def _gate_blockers(
    *,
    dsr: float | None,
    pbo: float | None,
    n_periods: int | None,
    run_date: date | None,
    age_days: int | None,
    max_age_days: int,
    dsr_pass: bool | None,
    pbo_pass: bool | None,
    pbo_valid: bool | None,
    both_pass: bool | None,
    raw_data_end: object,
    data_end: date | None,
    raw_data_start: object,
    data_start: date | None,
    heldout_cutoff: date | None,
    window_mode: str,
    thresholds_version: str | None,
    expected_thresholds_version: str | None,
) -> list[str]:
    blockers: list[str] = []
    if raw_data_start and data_start is None:
        blockers.append(f"data_start malformed: {raw_data_start!r}")
    if raw_data_end and data_end is None:
        blockers.append(f"data_end malformed: {raw_data_end!r}")
    if data_start is not None and data_end is not None and data_end < data_start:
        blockers.append(
            f"data_end={data_end.isoformat()} < data_start={data_start.isoformat()}"
        )
    if run_date is None:
        blockers.append("run_date missing/invalid")
    elif age_days is not None and age_days > max_age_days:
        blockers.append(f"gate stale: {age_days} days > {max_age_days}")

    if dsr is None:
        blockers.append("deflated_sharpe missing/invalid")
    elif dsr <= MIN_DSR:
        blockers.append(f"DSR={dsr:.4f} <= {MIN_DSR}")

    if pbo is None:
        blockers.append("pbo missing/invalid")
    elif not (0.0 < pbo < MAX_PBO):
        blockers.append(f"PBO={pbo:.2%} outside (0%, {MAX_PBO:.0%})")

    if n_periods is None:
        blockers.append("n_periods missing/invalid")
    elif n_periods <= 0:
        blockers.append("n_periods=0")

    if dsr_pass is not True:
        blockers.append("dsr_pass flag missing/invalid/false")
    if pbo_pass is not True:
        blockers.append("pbo_pass flag missing/invalid/false")
    if pbo_valid is not True:
        blockers.append("pbo_valid flag missing/invalid/false")
    if both_pass is not True:
        blockers.append("both_pass flag missing/invalid/false")

    if expected_thresholds_version is not None:
        if thresholds_version is None:
            blockers.append("thresholds_version missing/invalid")
        elif thresholds_version != expected_thresholds_version:
            blockers.append(
                "thresholds_version mismatch: "
                f"{thresholds_version!r} != {expected_thresholds_version!r}"
            )

    if (
        heldout_cutoff is not None
        and raw_data_end
        and window_mode
        not in {
            "auto_recent_window",
            "rolling_recent",
            "rolling_recent_window",
        }
    ):
        if data_end is None:
            blockers.append(f"data_end malformed: {raw_data_end!r}")
        elif data_end > heldout_cutoff:
            blockers.append(
                f"data_end={data_end.isoformat()} > heldout_cutoff={heldout_cutoff.isoformat()}"
            )
    return blockers


def _format_gate_detail(
    *,
    dsr: float | None,
    pbo: float | None,
    n_periods: int | None,
    age_days: int | None,
    dsr_pass: bool | None,
    pbo_pass: bool | None,
    pbo_valid: bool | None,
    both_pass: bool | None,
    blockers: list[str],
) -> str:
    metric_detail = (
        f"both_pass={_status_label(both_pass)}, "
        f"DSR={_metric_status(dsr, dsr is not None and dsr > MIN_DSR)}"
        f"({_fmt_float(dsr)} > {MIN_DSR}), "
        f"PBO={_metric_status(pbo, pbo is not None and 0.0 < pbo < MAX_PBO)}"
        f"({_fmt_pct(pbo)} in (0%, {MAX_PBO:.0%})), "
        f"pbo_valid={_status_label(pbo_valid)}, "
        f"dsr_pass={_status_label(dsr_pass)}, "
        f"pbo_pass={_status_label(pbo_pass)}, "
        f"n_periods={_metric_status(n_periods, n_periods is not None and n_periods > 0)}"
        f"({_fmt_int(n_periods)}), "
        f"age_days={_fmt_int(age_days)}"
    )
    if blockers:
        return f"{metric_detail}; blockers: {', '.join(blockers)}"
    return metric_detail


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _strict_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _strict_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _strict_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _strict_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _status_label(value: bool | None) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "INVALID"


def _metric_status(value: object, ok: bool) -> str:
    return "INVALID" if value is None else _status_label(ok)


def _fmt_float(value: float | None) -> str:
    return "invalid" if value is None else f"{value:.4f}"


def _fmt_pct(value: float | None) -> str:
    return "invalid" if value is None else f"{value:.2%}"


def _fmt_int(value: int | None) -> str:
    return "invalid" if value is None else str(value)
