"""Ledger, validation, and circuit-breaker helpers extracted from ``cli.py``.

Contains independent signal day counting, formal runtime ledger path resolution,
safe ledger write path enforcement, high-frequency markdown trimming, high-
frequency provisional output writing, no-candidate reason construction, ledger
signal date extraction, real PnL computation, validation summary formatting,
circuit-breaker block handling, observation allowance during breaker, and
execution cost basis-point resolution.

All symbols are re-exported by ``cli.py`` for backward compatibility.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from aqsp import cli_notify_helpers
from aqsp.cli_notification_gate import (
    GATE_NOTIFY_STATE_PATH,
    _format_notification_gate_block,
    _mark_gate_notification_failed,
    _mark_gate_notification_sent,
    _mark_gate_notification_suppressed,
    _resolve_runtime_state_path,
    _should_send_gate_notification,
)
from aqsp.cli_runtime_catalyst_helpers import _is_high_frequency_task
from aqsp.config import load_runtime_config
from aqsp.core.types import RunMetadata
from aqsp.ledger import (
    append_run_event,
    compute_paper_mark_to_market_pnl,
    compute_real_pnl,
    count_independent_signal_days,
    execution_config_from_thresholds,
)
from aqsp.models import PickResult
from aqsp.notification_runtime import (
    dispatch_gate_notification,
    finalize_scheduled_notification,
    finalize_scheduled_outputs,
)
from aqsp.notifier import notify_markdown as _notify_markdown_default
from aqsp.report import to_dataframe, to_intraday_dataframe, to_markdown
from aqsp.utils.jsonl_io import atomic_write_text


def _count_independent_signal_days(ledger_path: str) -> int:
    return count_independent_signal_days(ledger_path)


def _formal_runtime_ledger_path(current_ledger_path: str, *, task_id: str) -> str:
    normalized_task_id = str(task_id or "").strip().lower()
    current = str(current_ledger_path or "").strip()
    if normalized_task_id in {"intraday", "midday"}:
        env_ledger = str(
            os.getenv("AQSP_LEDGER", "data/predictions.jsonl") or ""
        ).strip()
        return env_ledger or current
    return current


def _safe_write_ledger_path(requested_path: str, *, task_id: str) -> str:
    """对 intraday/midday 任务,拒绝写入正式收盘 ledger,强制隔离到 intraday ledger。

    运维脚本已通过 --ledger 传临时文件,此函数只在"未隔离"时兜底:
    当 task_id 是 intraday/midday 且 requested_path 等于正式收盘 ledger 时,
    重定向到 AQSP_INTRADAY_LEDGER(默认 data/intraday_predictions.jsonl),
    防止盘中信号污染收盘胜率统计。
    """
    normalized_task_id = str(task_id or "").strip().lower()
    if normalized_task_id not in {"intraday", "midday"}:
        return requested_path
    formal = str(
        os.getenv("AQSP_LEDGER", "data/predictions.jsonl") or "data/predictions.jsonl"
    ).strip()
    requested = str(requested_path or "").strip()
    if requested == formal:
        intraday_ledger = str(
            os.getenv("AQSP_INTRADAY_LEDGER", "data/intraday_predictions.jsonl")
            or "data/intraday_predictions.jsonl"
        ).strip()
        return intraday_ledger
    return requested


def _trim_high_frequency_markdown(markdown: str) -> str:
    if not markdown.strip():
        return markdown
    keep_sections = {
        "## 数据与规则",
        "## 市场上下文",
        "## 今日重点看板",
        "## 组合保护",
        "## 选股变化",
        "## 板块集中度",
        "## 候选股相关性",
    }
    lines = markdown.splitlines()
    kept: list[str] = []
    current_header = ""
    keep_current = True
    for line in lines:
        if line.startswith("## "):
            current_header = line.strip()
            keep_current = current_header in keep_sections or current_header == ""
        if keep_current:
            kept.append(line)
    return "\n".join(kept).rstrip() + "\n"


def _write_high_frequency_provisional_outputs(
    *,
    task_id: str,
    args: argparse.Namespace,
    picks: list[PickResult],
    latest: date,
    mode: str,
    run_metadata: RunMetadata,
) -> None:
    if not _is_high_frequency_task(task_id):
        return
    report_path = str(getattr(args, "report", "") or "").strip()
    output_csv_path = str(getattr(args, "output_csv", "") or "").strip()
    mirror_report_path = str(os.environ.get("AQSP_PROVISIONAL_REPORT") or "").strip()
    mirror_csv_path = str(os.environ.get("AQSP_PROVISIONAL_OUTPUT_CSV") or "").strip()
    if not any((report_path, output_csv_path, mirror_report_path, mirror_csv_path)):
        return

    table = to_intraday_dataframe(picks, metadata=run_metadata)
    markdown = to_markdown(
        picks,
        title=f"AI 量化选股盘中快照({mode}, 数据日期 {latest.isoformat()})",
        metadata=replace(
            run_metadata,
            final_count=len(picks),
        ),
    )
    markdown = _trim_high_frequency_markdown(markdown)
    report_targets = tuple(
        dict.fromkeys(p for p in (report_path, mirror_report_path) if p)
    )
    csv_targets = tuple(
        dict.fromkeys(p for p in (output_csv_path, mirror_csv_path) if p)
    )
    for target in report_targets:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, markdown)
    csv_payload = table.to_csv(index=False)
    for target in csv_targets:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, csv_payload)
    print("盘中候选快照已先行落盘")


def _no_candidate_reason(*, screened_count: int, final_count: int) -> str:
    """Explain an empty result without turning prior candidates into fallback data."""
    if final_count > 0:
        return ""
    if screened_count <= 0:
        return "策略筛选未产生符合条件的候选"
    return "筛选出的候选在排雷、T+1 或组合约束后全部移除"


def _ledger_signal_date(row: dict[str, Any]) -> str:
    from aqsp.ledger.runtime import ledger_signal_date

    return ledger_signal_date(row)


def _compute_real_pnl(
    ledger_path: str,
    paper_ledger_path: str | None = None,
    frames: dict[str, pd.DataFrame] | None = None,
) -> tuple[float, float, float]:
    if paper_ledger_path and frames:
        paper_pnl = compute_paper_mark_to_market_pnl(paper_ledger_path, frames)
        if paper_pnl is not None:
            return paper_pnl
    return compute_real_pnl(ledger_path)


def _format_validation_summary_lines(validation: Any) -> list[str]:
    skipped = int(getattr(validation, "skipped_not_executable", 0) or 0)
    if skipped <= 0:
        return []

    lines = [f"- 不可成交跳过: {skipped} 条"]
    reasons = getattr(validation, "not_executable_reasons", None) or {}
    if isinstance(reasons, dict) and reasons:
        top_reasons = sorted(
            ((str(reason), int(count)) for reason, count in reasons.items()),
            key=lambda item: (-item[1], item[0]),
        )[:3]
        lines.append(
            "- 不可成交原因: "
            + ", ".join(f"{reason}×{count}" for reason, count in top_reasons)
        )
    rates = getattr(validation, "strategy_not_executable_rates", None) or {}
    if isinstance(rates, dict) and rates:
        top_rates = sorted(
            ((str(strategy), float(rate)) for strategy, rate in rates.items()),
            key=lambda item: (-item[1], item[0]),
        )[:3]
        lines.append(
            "- 不可成交策略: "
            + ", ".join(f"{strategy} {rate:.0%}" for strategy, rate in top_rates)
        )
    return lines


def _validation_summary_payload(validation: Any) -> dict[str, object] | None:
    if validation is None:
        return None
    return {
        "checked": int(getattr(validation, "checked", 0) or 0),
        "wins": int(getattr(validation, "wins", 0) or 0),
        "avg_return_pct": float(getattr(validation, "avg_return_pct", 0.0) or 0.0),
        "avg_excess_pct": float(getattr(validation, "avg_excess_pct", 0.0) or 0.0),
        "skipped_not_executable": int(
            getattr(validation, "skipped_not_executable", 0) or 0
        ),
        "not_executable_reasons": getattr(validation, "not_executable_reasons", None)
        or {},
        "strategy_not_executable_rates": getattr(
            validation, "strategy_not_executable_rates", None
        )
        or {},
    }


def _handle_circuit_breaker_block(
    *,
    args: argparse.Namespace,
    status: Any,
    latest: date,
    run_metadata: RunMetadata,
    validation: Any,
    cold_start_days: int,
    cold_start_min_days: int,
    daily_pnl: float,
    weekly_pnl: float,
    monthly_pnl: float,
    task_id: str,
) -> int:
    print(f"🛡️ 组合保护已触发，停止新增候选生成: {status.reason}")
    append_run_event(
        args.ledger,
        event_date=latest.isoformat(),
        status="blocked_by_circuit_breaker",
        reason=status.reason,
        run_metadata=run_metadata,
        details={
            "daily_pnl_pct": round(daily_pnl, 4),
            "weekly_pnl_pct": round(weekly_pnl, 4),
            "monthly_pnl_pct": round(monthly_pnl, 4),
        },
    )

    markdown = "\n".join(
        [
            f"# AI 量化选股报告(组合保护, 数据日期 {latest.isoformat()})",
            "",
            "## 执行摘要",
            "",
            f"🛡️ **组合保护已触发**: {status.reason}，本次停止新增纸面复核。",
            "",
            "## 组合保护",
            "",
            f"- 日度损益: {daily_pnl:.2f}%",
            f"- 周度损益: {weekly_pnl:.2f}%",
            f"- 月度损益: {monthly_pnl:.2f}%",
        ]
    )
    if validation is not None:
        markdown += "\n\n## 策略自检\n"
        if cold_start_days < cold_start_min_days:
            markdown += f"- 冷启动期: 已积累 {cold_start_days}/{cold_start_min_days} 个独立信号日\n"
        elif getattr(validation, "checked", 0):
            markdown += f"- 本次验证历史预测: {validation.checked} 条\n"
        else:
            markdown += "- 本次暂无可验证历史预测\n"
        validation_summary_lines = _format_validation_summary_lines(validation)
        if validation_summary_lines:
            markdown += "\n".join(validation_summary_lines) + "\n"

    notification_artifacts = finalize_scheduled_notification(
        markdown=markdown,
        args_notify=args.notify,
        gate_ok=False,
        gate_reasons=[status.reason],
        next_actions=["组合保护解除前暂停新增纸面复核，仅保留风险观察。"],
        latest_iso=latest.isoformat(),
        notify_mode=load_runtime_config().notify_mode,
        dispatch_gate_notification_fn=dispatch_gate_notification,
        should_send_gate_notification_fn=lambda **kwargs: (
            _should_send_gate_notification(
                gate_ok=kwargs["gate_ok"],
                gate_reasons=kwargs["gate_reasons"],
                run_date=kwargs["run_date"],
            )
        ),
        format_notification_gate_block_fn=_format_notification_gate_block,
        legacy_notify_fn=cli_notify_helpers.notify_markdown
        if cli_notify_helpers.notify_markdown is not _notify_markdown_default
        else None,
        print_fn=print,
        mark_gate_notification_sent_fn=lambda **kwargs: _mark_gate_notification_sent(
            gate_reasons=kwargs["gate_reasons"],
            run_date=kwargs["run_date"],
        ),
        mark_gate_notification_failed_fn=lambda **kwargs: (
            _mark_gate_notification_failed(
                gate_reasons=kwargs["gate_reasons"],
                run_date=kwargs["run_date"],
            )
        ),
        mark_gate_notification_suppressed_fn=lambda **kwargs: (
            _mark_gate_notification_suppressed(
                gate_reasons=kwargs["gate_reasons"],
                run_date=kwargs["run_date"],
            )
        ),
        gate_state_path=_resolve_runtime_state_path(
            os.getenv("AQSP_GATE_NOTIFY_STATE_PATH", GATE_NOTIFY_STATE_PATH)
        ),
        task_id=task_id,
    )
    report_path = str(getattr(args, "report", "") or "").strip()
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    finalize_scheduled_outputs(
        markdown=notification_artifacts.markdown,
        report_path=report_path,
        output_csv_path=str(getattr(args, "output_csv", "") or "").strip(),
        table=to_dataframe([]),
        print_fn=print,
    )
    return 2


def _allow_observation_during_circuit_breaker(task_id: str) -> bool:
    """Keep research generation independent from paper-portfolio protection.

    The breaker is an account-level action constraint.  It must not suppress
    fresh candidate generation or evidence collection for any scheduled task.
    Paper actions remain marked as restricted by the caller.
    """
    del task_id
    return True


def _execution_cost_bps_from_thresholds(thresholds: Any) -> tuple[float, float]:
    execution = execution_config_from_thresholds(thresholds)
    return execution.fee_bps, execution.slippage_bps


def _resolve_execution_cost_bps(
    thresholds: Any,
    *,
    fee_bps: float | None,
    slippage_bps: float | None,
) -> tuple[float, float]:
    default_fee_bps, default_slippage_bps = _execution_cost_bps_from_thresholds(
        thresholds
    )
    return (
        default_fee_bps if fee_bps is None else float(fee_bps),
        default_slippage_bps if slippage_bps is None else float(slippage_bps),
    )
