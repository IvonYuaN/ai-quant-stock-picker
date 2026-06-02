from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from aqsp.ledger.base import read_ledger


@dataclass(frozen=True)
class FailurePattern:
    pattern_name: str
    description: str
    occurrences: int
    avg_loss: float
    avoid_rule: str


def _parse_metrics(raw: str | dict | None) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _parse_strategies(raw: str | list | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(s) for s in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(s) for s in parsed]
        except (json.JSONDecodeError, TypeError):
            pass
        return [s.strip() for s in raw.split(",") if s.strip()]
    return []


def _collect_losses(ledger_rows: list[dict]) -> list[dict]:
    losses = []
    for row in ledger_rows:
        ret = row.get("return_pct")
        if ret is None:
            continue
        status = row.get("status", "")
        if status == "not_executable":
            continue
        try:
            ret_val = float(ret)
        except (TypeError, ValueError):
            continue
        if ret_val >= 0:
            continue
        row["_return_pct_float"] = ret_val
        row["_metrics"] = _parse_metrics(row.get("metrics"))
        row["_strategies"] = _parse_strategies(row.get("strategies"))
        losses.append(row)
    return losses


def _detect_high_rsi(losses: list[dict]) -> FailurePattern | None:
    matches = [r for r in losses if r["_metrics"].get("rsi12", 0) > 75]
    if not matches:
        return None
    avg_loss = sum(r["_return_pct_float"] for r in matches) / len(matches)
    return FailurePattern(
        pattern_name="high_rsi_entry",
        description=f"RSI > 75 时追高入场（共 {len(matches)} 次）",
        occurrences=len(matches),
        avg_loss=round(avg_loss, 2),
        avoid_rule="RSI > 75 时避免追高，等待回调至 60 以下再考虑入场",
    )


def _detect_volume_spike_chase(losses: list[dict]) -> FailurePattern | None:
    matches = [r for r in losses if r["_metrics"].get("volume_ratio", 0) > 3]
    if not matches:
        return None
    avg_loss = sum(r["_return_pct_float"] for r in matches) / len(matches)
    return FailurePattern(
        pattern_name="volume_spike_chase",
        description=f"成交量放大 > 3 倍时追入（共 {len(matches)} 次）",
        occurrences=len(matches),
        avg_loss=round(avg_loss, 2),
        avoid_rule="成交量突增 3 倍以上往往是散户跟风，等缩量确认后再入场",
    )


def _detect_weak_regime(losses: list[dict]) -> FailurePattern | None:
    matches = [r for r in losses if "bear" in str(r.get("regime_at_signal", ""))]
    if not matches:
        return None
    avg_loss = sum(r["_return_pct_float"] for r in matches) / len(matches)
    return FailurePattern(
        pattern_name="weak_market_regime",
        description=f"熊市 regime 下入场（共 {len(matches)} 次）",
        occurrences=len(matches),
        avg_loss=round(avg_loss, 2),
        avoid_rule="熊市 regime 下降低仓位或暂停入场，等待 regime 切换信号",
    )


def _detect_single_strategy(losses: list[dict]) -> FailurePattern | None:
    matches = [r for r in losses if len(r["_strategies"]) == 1]
    if not matches:
        return None
    avg_loss = sum(r["_return_pct_float"] for r in matches) / len(matches)
    return FailurePattern(
        pattern_name="single_strategy_reliance",
        description=f"仅 1 个策略触发、缺乏多策略共振（共 {len(matches)} 次）",
        occurrences=len(matches),
        avg_loss=round(avg_loss, 2),
        avoid_rule="仅 1 个策略触发时降低置信度，优先选择 >= 2 个策略共振的标的",
    )


def _detect_gap_down(losses: list[dict]) -> FailurePattern | None:
    matches = [r for r in losses if r["_return_pct_float"] < -5.0]
    if not matches:
        return None
    avg_loss = sum(r["_return_pct_float"] for r in matches) / len(matches)
    return FailurePattern(
        pattern_name="gap_down_after_entry",
        description=f"入场后大幅低开/跳空下跌（单次亏损 > 5%，共 {len(matches)} 次）",
        occurrences=len(matches),
        avg_loss=round(avg_loss, 2),
        avoid_rule="设置硬止损（如 -3%），避免持仓过夜遇利空跳空",
    )


def analyze_failures(
    ledger_df: pd.DataFrame,
    min_occurrences: int = 3,
) -> list[FailurePattern]:
    ledger_rows = ledger_df.to_dict("records") if not ledger_df.empty else []
    losses = _collect_losses(ledger_rows)
    if not losses:
        return []

    detectors = [
        _detect_high_rsi,
        _detect_volume_spike_chase,
        _detect_weak_regime,
        _detect_single_strategy,
        _detect_gap_down,
    ]

    patterns: list[FailurePattern] = []
    for detector in detectors:
        pattern = detector(losses)
        if pattern is not None and pattern.occurrences >= min_occurrences:
            patterns.append(pattern)

    patterns.sort(key=lambda p: p.avg_loss)
    return patterns


def analyze_failures_from_file(
    ledger_path: str,
    min_occurrences: int = 3,
) -> list[FailurePattern]:
    rows = read_ledger(ledger_path)
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    return analyze_failures(df, min_occurrences=min_occurrences)


def format_failure_patterns(patterns: list[FailurePattern]) -> str:
    if not patterns:
        return ""

    lines = ["## 失败模式分析", ""]
    lines.append("| 模式 | 描述 | 次数 | 平均亏损 | 规避建议 |")
    lines.append("|------|------|------|----------|----------|")
    for p in patterns:
        lines.append(
            f"| {p.pattern_name} | {p.description} | {p.occurrences} "
            f"| {p.avg_loss:.2f}% | {p.avoid_rule} |"
        )
    lines.append("")
    lines.append(f"*共发现 {len(patterns)} 个失败模式*")
    return "\n".join(lines)
