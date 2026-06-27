#!/usr/bin/env python3
"""Run the production short-line walk-forward gate.

This wrapper is intentionally stricter than ad-hoc `aqsp walkforward` calls:
it requires a raw sqlite database with full-market coverage before it starts the
expensive gate run. A 300-symbol run is only a smoke test and must not be used as
before-live evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aqsp.core.time import now_shanghai
from aqsp.data.sqlite_db_source import SqliteDbSource
from aqsp.utils.jsonl_io import atomic_write_text
from aqsp.walkforward_gate import MIN_PRODUCTION_GATE_SYMBOLS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DB = Path("/opt/market-data/astocks_raw.db")
DEFAULT_START = "2018-01-01"
DEFAULT_END = "2024-12-31"
MIN_COVERAGE_RATIO = 0.8
DEFAULT_STATUS_PATH = "data/walkforward_production_status.json"
DEFAULT_SYMBOL_CACHE_PATH = "data/walkforward_production_symbols.json"


@dataclass(frozen=True)
class CoverageSummary:
    stock_symbols: int
    covered_symbols: int
    rows: int
    first_trade_date: str
    last_trade_date: str


@dataclass(frozen=True)
class CoverageInspection:
    summary: CoverageSummary
    covered_symbols: list[str]


def _status_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _symbol_cache_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _write_status(
    path: Path,
    *,
    status: str,
    args: argparse.Namespace,
    coverage: CoverageSummary | None = None,
    effective_symbols: int | None = None,
    command: list[str] | None = None,
    child_exit_code: int | None = None,
    detail: str = "",
) -> None:
    payload: dict[str, object] = {
        "status": status,
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "db_path": str(args.db),
        "start": args.start,
        "end": args.end,
        "grid_profile": args.grid_profile,
        "report_path": str(args.report),
        "gate_path": str(args.gate_path),
        "log_path": str(args.log),
        "timeout_seconds": args.timeout_seconds,
    }
    if detail:
        payload["detail"] = detail
    if coverage is not None:
        payload["coverage"] = {
            "stock_symbols": coverage.stock_symbols,
            "covered_symbols": coverage.covered_symbols,
            "rows": coverage.rows,
            "first_trade_date": coverage.first_trade_date,
            "last_trade_date": coverage.last_trade_date,
        }
    if effective_symbols is not None:
        payload["effective_symbols"] = effective_symbols
    if command is not None:
        payload["command"] = command
    if child_exit_code is not None:
        payload["child_exit_code"] = child_exit_code
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _compact_day(raw: str) -> str:
    return date.fromisoformat(raw).strftime("%Y%m%d")


def _parse_db_day(raw: str) -> date | None:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _date_within_lag(left: str, right: str, *, max_days: int) -> bool:
    left_day = _parse_db_day(left)
    right_day = _parse_db_day(right)
    if left_day is None or right_day is None:
        return False
    delta = (right_day - left_day).days
    return 0 <= delta <= max_days


def _symbol_from_ts_code(ts_code: object) -> str:
    text = str(ts_code).strip()
    return text.split(".", 1)[0] if "." in text else text


def _raw_sqlite_source(db_path: Path) -> SqliteDbSource:
    if not db_path.exists():
        raise SystemExit(f"raw sqlite db missing: {db_path}")
    source = SqliteDbSource(db_path=db_path, cache=None)
    price_mode = source.price_mode()
    if price_mode != "raw":
        raise SystemExit(
            f"production gate requires raw sqlite db, got price_mode={price_mode}: {db_path}"
        )
    return source


def select_covered_symbols(db_path: Path, *, start: str, end: str) -> list[str]:
    source = _raw_sqlite_source(db_path)
    return source.get_symbols_with_daily_coverage(
        source.get_available_symbols(),
        date.fromisoformat(start),
        date.fromisoformat(end),
        min_rows=None,
    )


def inspect_raw_coverage(db_path: Path, *, start: str, end: str) -> CoverageSummary:
    return inspect_raw_coverage_with_symbols(db_path, start=start, end=end).summary


def inspect_raw_coverage_with_symbols(
    db_path: Path, *, start: str, end: str
) -> CoverageInspection:
    _raw_sqlite_source(db_path)
    start_str = _compact_day(start)
    end_str = _compact_day(end)
    with sqlite3.connect(db_path) as conn:
        stock_rows = conn.execute(
            "SELECT ts_code FROM stocks ORDER BY ts_code"
        ).fetchall()
        row = conn.execute(
            """
            SELECT COUNT(*), MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date)
            FROM daily_qfq
            WHERE trade_date >= ? AND trade_date <= ?
            """,
            (start_str, end_str),
        ).fetchone()
        first_market_day = str(row[1] or "")
        last_market_day = str(row[2] or "")
        expected_rows = int(row[3] or 0)
        min_required_rows = max(1, int(expected_rows * MIN_COVERAGE_RATIO))
        coverage_rows = conn.execute(
            """
            SELECT ts_code, MIN(trade_date), MAX(trade_date), COUNT(*)
            FROM daily_qfq
            WHERE trade_date >= ? AND trade_date <= ?
            GROUP BY ts_code
            ORDER BY ts_code
            """,
            (start_str, end_str),
        ).fetchall()

    covered: list[str] = []
    if (
        first_market_day
        and last_market_day
        and _date_within_lag(start_str, first_market_day, max_days=10)
        and _date_within_lag(last_market_day, end_str, max_days=10)
    ):
        for ts_code, first_date, last_date, count in coverage_rows:
            first_text = str(first_date or "")
            last_text = str(last_date or "")
            if first_text > first_market_day or last_text < last_market_day:
                continue
            if int(count or 0) < min_required_rows:
                continue
            covered.append(_symbol_from_ts_code(ts_code))

    return CoverageInspection(
        summary=CoverageSummary(
            stock_symbols=len(stock_rows),
            covered_symbols=len(covered),
            rows=int(row[0] or 0),
            first_trade_date=first_market_day,
            last_trade_date=last_market_day,
        ),
        covered_symbols=covered,
    )


def _db_mtime_epoch(db_path: Path) -> int:
    return int(db_path.stat().st_mtime)


def load_cached_coverage_symbols(
    cache_path: Path,
    *,
    db_path: Path,
    start: str,
    end: str,
    min_symbols: int,
) -> CoverageInspection | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    expected = {
        "db_path": str(db_path),
        "db_mtime_epoch": _db_mtime_epoch(db_path),
        "start": start,
        "end": end,
        "min_symbols": int(min_symbols),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            return None
    summary_raw = payload.get("summary")
    covered_symbols = payload.get("covered_symbols")
    if not isinstance(summary_raw, dict) or not isinstance(covered_symbols, list):
        return None
    try:
        summary = CoverageSummary(
            stock_symbols=int(summary_raw["stock_symbols"]),
            covered_symbols=int(summary_raw["covered_symbols"]),
            rows=int(summary_raw["rows"]),
            first_trade_date=str(summary_raw["first_trade_date"]),
            last_trade_date=str(summary_raw["last_trade_date"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    normalized_symbols = [str(symbol).strip() for symbol in covered_symbols if str(symbol).strip()]
    if len(normalized_symbols) != summary.covered_symbols:
        return None
    return CoverageInspection(summary=summary, covered_symbols=normalized_symbols)


def write_cached_coverage_symbols(
    cache_path: Path,
    *,
    db_path: Path,
    start: str,
    end: str,
    min_symbols: int,
    inspection: CoverageInspection,
) -> None:
    payload = {
        "db_path": str(db_path),
        "db_mtime_epoch": _db_mtime_epoch(db_path),
        "start": start,
        "end": end,
        "min_symbols": int(min_symbols),
        "updated_at": now_shanghai().isoformat(timespec="seconds"),
        "summary": {
            "stock_symbols": inspection.summary.stock_symbols,
            "covered_symbols": inspection.summary.covered_symbols,
            "rows": inspection.summary.rows,
            "first_trade_date": inspection.summary.first_trade_date,
            "last_trade_date": inspection.summary.last_trade_date,
        },
        "covered_symbols": inspection.covered_symbols,
    }
    atomic_write_text(
        cache_path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def build_walkforward_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        "-m",
        "aqsp",
        "walkforward",
        "--source",
        "sqlite_db",
        "--pool",
        "all",
        "--start",
        args.start,
        "--end",
        args.end,
        "--grid-cscv",
        "--grid-profile",
        args.grid_profile,
        "--skip-pit-financials",
        *(
            ["--symbols-file", args.symbols_file]
            if getattr(args, "symbols_file", "")
            else []
        ),
        "--report",
        args.report,
        "--gate-path",
        args.gate_path,
        "--cache-path",
        args.cache_path,
        "--log",
        args.log,
    ]


def annotate_production_gate_metadata(
    *,
    gate_path: Path,
    db_path: Path,
    coverage: CoverageSummary,
    effective_symbols: int,
) -> None:
    """Stamp production coverage evidence onto the child gate sidecar."""
    if not gate_path.exists():
        raise SystemExit(
            f"production gate sidecar missing after child run: {gate_path}"
        )
    payload = json.loads(gate_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"production gate sidecar is not an object: {gate_path}")
    child_effective_symbols = payload.get("effective_symbols")
    if (
        isinstance(child_effective_symbols, bool)
        or not isinstance(child_effective_symbols, int)
        or child_effective_symbols != int(effective_symbols)
    ):
        raise SystemExit(
            "child walk-forward effective_symbols mismatch: "
            f"child={child_effective_symbols!r} wrapper={effective_symbols}; "
            "refusing to stamp production coverage over a different child universe"
        )
    payload.update(
        {
            "source": "sqlite_db",
            "sqlite_db_path": str(db_path),
            "price_mode": "raw",
            "production_gate_coverage": {
                "stock_symbols": coverage.stock_symbols,
                "covered_symbols": coverage.covered_symbols,
                "rows": coverage.rows,
                "first_trade_date": coverage.first_trade_date,
                "last_trade_date": coverage.last_trade_date,
            },
        }
    )
    atomic_write_text(
        gate_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    )
    print(
        "production gate metadata stamped: "
        f"effective_symbols={effective_symbols} price_mode=raw db={db_path}"
    )


def write_minimal_pbo_diagnostics(
    *,
    gate_path: Path,
    report_path: Path,
    coverage: CoverageSummary | None = None,
    overwrite: bool = False,
) -> bool:
    """Write a reviewable PBO failure report when the child report is missing."""
    if (report_path.exists() and not overwrite) or not gate_path.exists():
        return False
    try:
        payload = json.loads(gate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(payload, dict) or payload.get("pbo_pass") is not False:
        return False

    pbo = payload.get("pbo")
    dsr = payload.get("deflated_sharpe")
    diagnostic_lines: list[str] = []
    if not _append_grid_diagnostics_report(
        diagnostic_lines, payload.get("grid_diagnostics")
    ):
        diagnostic_lines.extend(
            [
                "### PBO 失败定位",
                "",
                "| 指标 | 值 |",
                "|------|-----|",
                "| CSCV 失败组合占比 | - |",
                "| Lambda 中位数 | - |",
                "| Lambda 均值 | - |",
                "| Sharpe 变体分散度 | - |",
                "| Return 变体分散度 | - |",
                "| 最优变体 | - |",
                "| 最弱变体 | - |",
                "",
                "| 最差对齐周期 | 测试窗口 | 平均收益 | 分散度 | 亏损变体数 | 全池平均收益 | 全池下跌占比 | 样本数 |",
                "|--------------|----------|----------|--------|------------|--------------|--------------|--------|",
                "| - | - | - | - | - | - | - | - |",
                "",
                "| 训练选中变体 | 训练块 | 测试块 | 训练 Sharpe | 测试 Sharpe | 测试倒数排名 | 测试最优变体 | Lambda |",
                "|--------------|--------|--------|-------------|-------------|--------------|--------------|--------|",
                "| - | - | - | - | - | - | - | - |",
                "",
            ]
        )
    lines = [
        "# Walk-Forward 生产门禁诊断",
        "",
        "## TL;DR",
        "",
        "- 结论: BLOCK，当前 sidecar 未通过双门，不允许作为上线证据。",
        f"- DSR: {_format_report_float(dsr)}",
        f"- PBO: {_format_report_pct(pbo)}",
        f"- Gate 日期: {payload.get('run_date', '-')}",
        f"- 回测区间: {payload.get('data_start', '-')} ~ {payload.get('data_end', '-')}",
        "",
        *diagnostic_lines,
        "## 生产覆盖",
        "",
        f"**标的数量**: {payload.get('effective_symbols', '-')}",
        "",
        "| 项目 | 值 |",
        "|------|-----|",
        f"| effective_symbols | {payload.get('effective_symbols', '-')} |",
        f"| price_mode | {payload.get('price_mode', '-')} |",
        f"| sqlite_db_path | {payload.get('sqlite_db_path', '-')} |",
    ]
    coverage_payload = payload.get("production_gate_coverage")
    if not isinstance(coverage_payload, dict) and coverage is not None:
        coverage_payload = {
            "stock_symbols": coverage.stock_symbols,
            "covered_symbols": coverage.covered_symbols,
            "rows": coverage.rows,
            "first_trade_date": coverage.first_trade_date,
            "last_trade_date": coverage.last_trade_date,
        }
    if isinstance(coverage_payload, dict):
        lines.extend(
            [
                f"| stock_symbols | {coverage_payload.get('stock_symbols', '-')} |",
                f"| covered_symbols | {coverage_payload.get('covered_symbols', '-')} |",
                f"| rows | {coverage_payload.get('rows', '-')} |",
                f"| first_trade_date | {coverage_payload.get('first_trade_date', '-')} |",
                f"| last_trade_date | {coverage_payload.get('last_trade_date', '-')} |",
            ]
        )
    lines.extend(
        [
            "",
            "## 处理",
            "",
            "- 重新运行 `scripts/run_production_walkforward_gate.py` 生成完整多变体 CSCV 报告。",
            "- 不要手工改写 DSR/PBO/pass 标志；只能由 walk-forward 结果写入 sidecar。",
        ]
    )
    atomic_write_text(report_path, "\n".join(lines) + "\n")
    return True


def diagnostic_report_path(report_path: Path) -> Path:
    name = report_path.name
    if name.endswith("-latest.md"):
        return report_path.with_name(
            f"{name[: -len('-latest.md')]}-diagnostic-latest.md"
        )
    return report_path.with_suffix(f".diagnostic{report_path.suffix}")


def formal_report_backup_path(report_path: Path) -> Path:
    name = report_path.name
    if name.endswith("-latest.md"):
        return report_path.with_name(f"{name[: -len('-latest.md')]}-formal-latest.md")
    return report_path.with_suffix(f".formal{report_path.suffix}")


def preserve_formal_report_snapshot(report_path: Path) -> None:
    if not report_path.exists():
        return
    backup_path = formal_report_backup_path(report_path)
    try:
        atomic_write_text(
            backup_path, report_path.read_text(encoding="utf-8")
        )
    except OSError:
        return


def warn_if_report_path_not_writable(report_path: Path) -> None:
    if not report_path.exists():
        return
    if os.access(report_path, os.W_OK):
        return
    print(
        "WARN: formal production walk-forward report is not writable by current user: "
        f"{report_path}. Child walk-forward may fail to refresh the formal report; "
        "fix file ownership/permissions on the server."
    )


def _format_report_float(value: object) -> str:
    return f"{float(value):.4f}" if isinstance(value, (int, float)) else "-"


def _format_report_pct(value: object) -> str:
    return f"{float(value):.2%}" if isinstance(value, (int, float)) else "-"


def _format_optional_float(value: object, *, pct: bool = False) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):.2%}" if pct else f"{float(value):.4f}"


def _append_grid_diagnostics_report(lines: list[str], details: object) -> bool:
    if not isinstance(details, dict) or not details:
        return False

    n_combos = details.get("n_combos")
    n_lambda_le_0 = details.get("n_lambda_le_0")
    fail_rate = None
    if isinstance(n_combos, int) and n_combos > 0 and isinstance(n_lambda_le_0, int):
        fail_rate = n_lambda_le_0 / n_combos

    lines.extend(
        [
            "### PBO 失败定位",
            "",
            "| 指标 | 值 |",
            "|------|-----|",
            f"| CSCV 组合数 | {n_combos if isinstance(n_combos, int) else '-'} |",
            f"| λ<=0 组合数 | {n_lambda_le_0 if isinstance(n_lambda_le_0, int) else '-'} |",
            f"| CSCV 失败组合占比 | {_format_optional_float(fail_rate, pct=True)} |",
            f"| Lambda 中位数 | {_format_optional_float(details.get('lambda_median'))} |",
            f"| Lambda 均值 | {_format_optional_float(details.get('lambda_mean'))} |",
            f"| Sharpe 变体分散度 | {_format_optional_float(details.get('variant_dispersion_sharpe'))} |",
            f"| Return 变体分散度 | {_format_optional_float(details.get('variant_dispersion_return'), pct=True)} |",
            f"| 最优变体 | {details.get('best_variant', '-')} |",
            f"| 最弱变体 | {details.get('worst_variant', '-')} |",
            "",
        ]
    )

    worst_periods = details.get("worst_periods")
    if isinstance(worst_periods, list) and worst_periods:
        lines.extend(
            [
                "| 最差对齐周期 | 测试窗口 | 平均收益 | 分散度 | 亏损变体数 | 全池平均收益 | 全池下跌占比 | 样本数 |",
                "|--------------|----------|----------|--------|------------|--------------|--------------|--------|",
            ]
        )
        for item in worst_periods:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| #{item.get('period_index', '-')} | "
                f"{item.get('period', '-')} | "
                f"{_format_optional_float(item.get('mean_return'), pct=True)} | "
                f"{_format_optional_float(item.get('dispersion'), pct=True)} | "
                f"{item.get('negative_variant_count', '-')} | "
                f"{_format_optional_float(item.get('market_avg_return'), pct=True)} | "
                f"{_format_optional_float(item.get('market_negative_ratio'), pct=True)} | "
                f"{item.get('market_sample_count', '-')} |"
            )
        lines.append("")

    inversions = details.get("selection_inversions")
    if isinstance(inversions, list) and inversions:
        lines.extend(
            [
                "| 训练选中变体 | 训练块 | 测试块 | 训练 Sharpe | 测试 Sharpe | 测试倒数排名 | 测试最优变体 | Lambda |",
                "|--------------|--------|--------|-------------|-------------|--------------|--------------|--------|",
            ]
        )
        for item in inversions:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| {item.get('selected_variant', '-')} | "
                f"{item.get('train_blocks', '-')} | "
                f"{item.get('test_blocks', '-')} | "
                f"{_format_optional_float(item.get('train_sharpe'))} | "
                f"{_format_optional_float(item.get('test_sharpe'))} | "
                f"{item.get('test_rank_from_bottom', '-')} | "
                f"{item.get('test_best_variant', '-')} | "
                f"{_format_optional_float(item.get('lambda'))} |"
            )
        lines.append("")
    return True


def _extract_report_value(text: str, key: str) -> str:
    patterns = (
        rf"\|\s*{re.escape(key)}\s*\|\s*([^\|\n]+?)\s*\|",
        rf"\*\*{re.escape(key)}\*\*\s*[:：]\s*([^\n]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return str(match.group(1)).strip()
    return ""


def repair_production_gate_metadata(
    *,
    gate_path: Path,
    report_path: Path,
    db_path: Path | None = None,
) -> bool:
    if not gate_path.exists() or not report_path.exists():
        return False
    try:
        payload = json.loads(gate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(payload, dict):
        return False
    try:
        report_text = report_path.read_text(encoding="utf-8")
    except OSError:
        return False

    changed = False
    if "effective_symbols" not in payload:
        raw_effective = _extract_report_value(report_text, "effective_symbols")
        if raw_effective in {"", "-"}:
            raw_effective = _extract_report_value(report_text, "标的数量")
        if raw_effective in {"", "-"}:
            raw_effective = _extract_report_value(report_text, "covered_symbols")
        if raw_effective.isdigit():
            payload["effective_symbols"] = int(raw_effective)
            changed = True
    if str(payload.get("price_mode") or "").strip() in {"", "-"}:
        raw_price_mode = _extract_report_value(report_text, "price_mode")
        if raw_price_mode in {"-", ""} and db_path:
            raw_price_mode = "raw" if "raw" in db_path.name.lower() else ""
        if raw_price_mode:
            payload["price_mode"] = raw_price_mode
            changed = True
    if str(payload.get("sqlite_db_path") or "").strip() in {"", "-"}:
        extracted_db = _extract_report_value(report_text, "sqlite_db_path")
        resolved_db = str(db_path) if db_path else extracted_db
        if extracted_db not in {"", "-"}:
            resolved_db = extracted_db
        if resolved_db and resolved_db != "-":
            payload["sqlite_db_path"] = resolved_db
            changed = True
    coverage_keys = (
        "stock_symbols",
        "covered_symbols",
        "rows",
        "first_trade_date",
        "last_trade_date",
    )
    coverage = payload.get("production_gate_coverage")
    if not isinstance(coverage, dict):
        coverage = {}
    coverage_changed = False
    for key in coverage_keys:
        if key in coverage and str(coverage.get(key) or "").strip():
            continue
        value = _extract_report_value(report_text, key)
        if not value:
            continue
        if key in {"stock_symbols", "covered_symbols", "rows"} and value.isdigit():
            coverage[key] = int(value)
        else:
            coverage[key] = value
        coverage_changed = True
    if coverage_changed:
        payload["production_gate_coverage"] = coverage
        changed = True
    if not changed:
        return False
    atomic_write_text(gate_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair-only", action="store_true")
    parser.add_argument("--db", type=Path, default=DEFAULT_RAW_DB)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--min-symbols", type=int, default=MIN_PRODUCTION_GATE_SYMBOLS)
    parser.add_argument(
        "--grid-profile", choices=("stable", "exploratory"), default="stable"
    )
    parser.add_argument(
        "--report", default="reports/walkforward-grid-raw-production-latest.md"
    )
    parser.add_argument(
        "--cache-path", default="data/walkforward_raw_production_cache.db"
    )
    parser.add_argument("--log", default="logs/walkforward-raw-production.log")
    parser.add_argument("--gate-path", default="data/walkforward_gate.json")
    parser.add_argument(
        "--status-path",
        default=DEFAULT_STATUS_PATH,
        help="write production walkforward wrapper status for remote diagnosis",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=7200,
        help="stop the production walk-forward run if it hangs during data loading/backtest",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--symbols-cache-path",
        default=DEFAULT_SYMBOL_CACHE_PATH,
        help="cache production coverage-selected symbols to avoid repeating full-market sqlite scans",
    )
    args = parser.parse_args()
    status_path = _status_path(args.status_path)
    symbols_cache_path = _symbol_cache_path(args.symbols_cache_path)

    if args.repair_only:
        repaired = repair_production_gate_metadata(
            gate_path=Path(args.gate_path),
            report_path=Path(args.report),
            db_path=args.db,
        )
        _write_status(
            status_path,
            status="repair_completed" if repaired else "repair_unchanged",
            args=args,
            detail="production gate metadata repair-only mode",
        )
        print("production gate metadata repaired" if repaired else "production gate metadata unchanged")
        return 0 if repaired else 1

    inspection = load_cached_coverage_symbols(
        symbols_cache_path,
        db_path=args.db,
        start=args.start,
        end=args.end,
        min_symbols=args.min_symbols,
    )
    if inspection is None:
        inspection = inspect_raw_coverage_with_symbols(
            args.db, start=args.start, end=args.end
        )
        write_cached_coverage_symbols(
            symbols_cache_path,
            db_path=args.db,
            start=args.start,
            end=args.end,
            min_symbols=args.min_symbols,
            inspection=inspection,
        )
        print(f"production gate symbols cache refreshed: {symbols_cache_path}")
    else:
        print(f"production gate symbols cache hit: {symbols_cache_path}")
    coverage = inspection.summary
    print(
        "production gate raw coverage: "
        f"stocks={coverage.stock_symbols} covered={coverage.covered_symbols} "
        f"rows={coverage.rows} range={coverage.first_trade_date}..{coverage.last_trade_date}"
    )
    if coverage.covered_symbols < args.min_symbols:
        _write_status(
            status_path,
            status="blocked_coverage",
            args=args,
            coverage=coverage,
            effective_symbols=coverage.covered_symbols,
            detail=f"need {args.min_symbols}, got {coverage.covered_symbols}",
        )
        print(
            "BLOCK: raw full-market coverage is insufficient; "
            f"need {args.min_symbols}, got {coverage.covered_symbols}."
        )
        print(
            "Backfill missing raw history first: .venv/bin/python "
            "scripts/update_sqlite_daily.py "
            f"{args.db} --price-mode raw --start-date {args.start} "
            f"--target-date {args.end} --fill-history-gaps --limit 0"
        )
        print(
            "Only for a clean rebuild, append --force-from-start after taking a database backup."
        )
        return 2

    covered_symbols = inspection.covered_symbols
    if len(covered_symbols) < args.min_symbols:
        _write_status(
            status_path,
            status="blocked_symbols",
            args=args,
            coverage=coverage,
            effective_symbols=len(covered_symbols),
            detail=f"need {args.min_symbols}, got {len(covered_symbols)} selected symbols",
        )
        print(
            "BLOCK: selected production symbols are insufficient; "
            f"need {args.min_symbols}, got {len(covered_symbols)}."
        )
        return 2

    tmp_symbols_path: Path | None = None
    if not args.dry_run:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix="aqsp-walkforward-symbols-",
            suffix=".txt",
            delete=False,
        ) as tmp_symbols:
            tmp_symbols.write("\n".join(covered_symbols) + "\n")
            tmp_symbols_path = Path(tmp_symbols.name)
        args.symbols_file = str(tmp_symbols_path)
        print(f"production gate selected symbols: {len(covered_symbols)}")

    command = build_walkforward_command(args)
    print("production gate command:", " ".join(command))
    if args.dry_run:
        _write_status(
            status_path,
            status="dry_run",
            args=args,
            coverage=coverage,
            effective_symbols=len(covered_symbols),
            command=command,
            detail="dry-run only; child walkforward not started",
        )
        return 0
    env = os.environ.copy()
    env["AQSP_SQLITE_DB_PATH"] = str(args.db)
    env["AQSP_SQLITE_PREFILTERED_SYMBOLS"] = "1"
    warn_if_report_path_not_writable(Path(args.report))
    preserve_formal_report_snapshot(Path(args.report))
    _write_status(
        status_path,
        status="running",
        args=args,
        coverage=coverage,
        effective_symbols=len(covered_symbols),
        command=command,
        detail="child walkforward started",
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            env=env,
            cwd=PROJECT_ROOT,
            timeout=args.timeout_seconds if args.timeout_seconds > 0 else None,
        )
        if result.returncode != 0:
            diagnostic_path = diagnostic_report_path(Path(args.report))
            if write_minimal_pbo_diagnostics(
                gate_path=Path(args.gate_path),
                report_path=diagnostic_path,
                coverage=coverage,
                overwrite=True,
            ):
                print(f"production gate diagnostic report written: {diagnostic_path}")
            _write_status(
                status_path,
                status="failed",
                args=args,
                coverage=coverage,
                effective_symbols=len(covered_symbols),
                command=command,
                child_exit_code=result.returncode,
                detail="child walkforward returned non-zero",
            )
            return result.returncode
        try:
            annotate_production_gate_metadata(
                gate_path=Path(args.gate_path),
                db_path=args.db,
                coverage=coverage,
                effective_symbols=len(covered_symbols),
            )
        except (json.JSONDecodeError, OSError, SystemExit) as exc:
            _write_status(
                status_path,
                status="failed_metadata",
                args=args,
                coverage=coverage,
                effective_symbols=len(covered_symbols),
                command=command,
                child_exit_code=result.returncode,
                detail=str(exc),
            )
            print(f"BLOCK: failed to stamp production gate metadata: {exc}")
            return 2
        diagnostic_path = diagnostic_report_path(Path(args.report))
        if write_minimal_pbo_diagnostics(
            gate_path=Path(args.gate_path),
            report_path=diagnostic_path,
            coverage=coverage,
            overwrite=True,
        ):
            print(f"production gate diagnostic report written: {diagnostic_path}")
        _write_status(
            status_path,
            status="completed",
            args=args,
            coverage=coverage,
            effective_symbols=len(covered_symbols),
            command=command,
            child_exit_code=result.returncode,
            detail="child walkforward completed",
        )
        return 0
    except subprocess.TimeoutExpired:
        _write_status(
            status_path,
            status="timeout",
            args=args,
            coverage=coverage,
            effective_symbols=len(covered_symbols),
            command=command,
            child_exit_code=124,
            detail="child walkforward timed out",
        )
        print(
            "BLOCK: production walk-forward timed out; "
            f"timeout_seconds={args.timeout_seconds}. "
            "Reduce the covered symbol batch or inspect sqlite fetch performance."
        )
        return 124
    finally:
        if tmp_symbols_path is not None:
            tmp_symbols_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
