#!/usr/bin/env python3
"""Read-only contract check for the live intraday production status artifact.

The checker deliberately does not run the intraday pipeline or mutate its
cursor.  It is suitable for a post-run health check from cron, CI, or a
production smoke command.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from aqsp.core.time import is_trading_day, today_shanghai  # noqa: E402

EXIT_OK = 0
EXIT_CONTRACT_FAILED = 1
EXIT_SKIPPED = 10


@dataclass(frozen=True)
class ContractCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ContractResult:
    classification: str
    status_path: str
    trade_date: str
    checks: tuple[ContractCheck, ...]
    observed: dict[str, Any]

    @property
    def exit_code(self) -> int:
        if self.classification == "skipped_non_trading_day":
            return EXIT_SKIPPED
        return (
            EXIT_OK if all(check.ok for check in self.checks) else EXIT_CONTRACT_FAILED
        )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_nonnegative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _as_nonnegative_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _check(name: str, ok: bool, detail: str) -> ContractCheck:
    return ContractCheck(name=name, ok=ok, detail=detail)


def _load_status(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "status file does not exist"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"status file is unreadable: {exc}"
    if not isinstance(payload, dict):
        return None, "status root must be a JSON object"
    return payload, None


def _status_date(payload: dict[str, Any]) -> str:
    task_id = str(payload.get("task_id") or "")
    if task_id.startswith("intraday-") and len(task_id) >= 19:
        candidate = task_id[9:19]
        try:
            date.fromisoformat(candidate)
            return candidate
        except ValueError:
            pass
    updated_at = str(payload.get("updated_at") or "")
    if len(updated_at) >= 10:
        candidate = updated_at[:10]
        try:
            date.fromisoformat(candidate)
            return candidate
        except ValueError:
            pass
    return ""


def evaluate_status(
    payload: dict[str, Any] | None,
    *,
    trade_date: date,
    status_path: str = "",
) -> ContractResult:
    """Evaluate a status payload without reading or writing any other artifact."""

    if not is_trading_day(trade_date):
        return ContractResult(
            classification="skipped_non_trading_day",
            status_path=status_path,
            trade_date=trade_date.isoformat(),
            checks=(),
            observed={"status_present": payload is not None},
        )

    checks: list[ContractCheck] = []
    if payload is None:
        checks.append(
            _check("status_present", False, "trading day has no status artifact")
        )
        return ContractResult(
            classification="failed",
            status_path=status_path,
            trade_date=trade_date.isoformat(),
            checks=tuple(checks),
            observed={},
        )

    observed_date = _status_date(payload)
    checks.append(
        _check(
            "status_date",
            observed_date == trade_date.isoformat(),
            f"expected {trade_date.isoformat()}, observed {observed_date or 'missing'}",
        )
    )

    status = str(payload.get("status") or "").strip().lower()
    execution = payload.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    universe = payload.get("universe")
    universe = universe if isinstance(universe, dict) else {}
    freshness = payload.get("freshness")
    freshness = freshness if isinstance(freshness, dict) else {}

    resource_killed_value = execution.get("resource_killed")
    resource_killed = _as_bool(resource_killed_value)
    catalyst_mode = str(execution.get("catalyst_fetch_mode") or "").strip().lower()
    checks.append(
        _check(
            "resource_killed",
            resource_killed_value is not None and not resource_killed,
            f"resource_killed={resource_killed_value if resource_killed_value is not None else 'missing'}",
        )
    )
    checks.append(
        _check(
            "catalyst_fetch_mode",
            catalyst_mode == "thread",
            f"catalyst_fetch_mode={catalyst_mode or 'missing'}; expected thread",
        )
    )

    batch_id = str(universe.get("batch_id") or "").strip()
    universe_count = _as_nonnegative_int(universe.get("universe_count"))
    batch_size = _as_nonnegative_int(universe.get("batch_size"))
    coverage_pct = _as_nonnegative_float(universe.get("coverage_pct"))
    checks.extend(
        (
            _check(
                "universe_batch_id", bool(batch_id), f"batch_id={batch_id or 'missing'}"
            ),
            _check(
                "universe_count",
                universe_count is not None and universe_count > 0,
                f"universe_count={universe_count}",
            ),
            _check(
                "batch_size",
                batch_size is not None
                and batch_size > 0
                and (universe_count is None or batch_size <= universe_count),
                f"batch_size={batch_size}; universe_count={universe_count}",
            ),
            _check(
                "universe_coverage",
                coverage_pct is not None and 0 < coverage_pct <= 1,
                f"coverage_pct={coverage_pct}",
            ),
        )
    )

    candidate_count = _as_nonnegative_int(payload.get("candidate_count"))
    actionable_count = _as_nonnegative_int(payload.get("actionable_count"))
    blocked_count = _as_nonnegative_int(payload.get("blocked_count"))
    freshness_status = str(freshness.get("status") or "").strip().lower()
    freshness_checked = _as_nonnegative_int(
        freshness.get(
            "checked_count", payload.get("quality_gate", {}).get("checked_count")
        )
        if isinstance(payload.get("quality_gate", {}), dict)
        else freshness.get("checked_count")
    )
    resolved_count = _as_nonnegative_int(universe.get("resolved_count"))
    fetched_count = _as_nonnegative_int(universe.get("fetched_count"))
    skipped_count = _as_nonnegative_int(universe.get("skipped_count"))
    data_coverage_pct = _as_nonnegative_float(universe.get("data_coverage_pct"))
    coverage_detail_ok = (
        resolved_count is not None
        and resolved_count > 0
        and batch_size is not None
        and resolved_count == batch_size
        and fetched_count is not None
        and fetched_count > 0
        and skipped_count is not None
        and fetched_count + skipped_count == resolved_count
        and data_coverage_pct is not None
        and math.isclose(
            data_coverage_pct,
            fetched_count / resolved_count,
            rel_tol=0.0,
            abs_tol=1e-6,
        )
    )
    checks.extend(
        (
            _check(
                "candidate_count",
                candidate_count is not None,
                f"candidate_count={candidate_count}",
            ),
            _check(
                "actionable_count",
                actionable_count is not None,
                f"actionable_count={actionable_count}",
            ),
            _check(
                "blocked_count",
                blocked_count is not None,
                f"blocked_count={blocked_count}",
            ),
            _check(
                "freshness_status",
                freshness_status in {"fresh", "watch"},
                f"freshness.status={freshness_status or 'missing'}",
            ),
            _check(
                "freshness_checked_count",
                freshness_checked is not None and freshness_checked > 0,
                f"freshness.checked_count={freshness_checked}",
            ),
            _check(
                "coverage_detail",
                coverage_detail_ok,
                "resolved={}; fetched={}; skipped={}; data_coverage_pct={}".format(
                    resolved_count,
                    fetched_count,
                    skipped_count,
                    data_coverage_pct,
                ),
            ),
        )
    )

    checks.append(
        _check(
            "run_status",
            status == "completed",
            f"status={status or 'missing'}; expected completed",
        )
    )
    observed = {
        "status": status,
        "resource_killed": resource_killed,
        "catalyst_fetch_mode": catalyst_mode,
        "batch_id": batch_id,
        "universe_count": universe_count,
        "batch_size": batch_size,
        "coverage_pct": coverage_pct,
        "candidate_count": candidate_count,
        "actionable_count": actionable_count,
        "blocked_count": blocked_count,
        "freshness_status": freshness_status,
        "freshness_checked_count": freshness_checked,
        "resolved_count": resolved_count,
        "fetched_count": fetched_count,
        "skipped_count": skipped_count,
        "data_coverage_pct": data_coverage_pct,
    }
    return ContractResult(
        classification="success" if all(check.ok for check in checks) else "failed",
        status_path=status_path,
        trade_date=trade_date.isoformat(),
        checks=tuple(checks),
        observed=observed,
    )


def _parse_date(value: str | None) -> date:
    if not value:
        return today_shanghai()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date: {value}") from exc


def _result_json(result: ContractResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["checks"] = [asdict(check) for check in result.checks]
    payload["exit_code"] = result.exit_code
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-path", default="data/intraday_refresh_status.json")
    parser.add_argument(
        "--date", type=_parse_date, default=None, help="北京时间 YYYY-MM-DD"
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    trade_date = args.date or today_shanghai()
    path = Path(args.status_path).expanduser()
    payload, error = _load_status(path)
    if error and is_trading_day(trade_date):
        payload = None
    result = evaluate_status(payload, trade_date=trade_date, status_path=str(path))
    if error and result.classification != "skipped_non_trading_day":
        result = ContractResult(
            classification="failed",
            status_path=result.status_path,
            trade_date=result.trade_date,
            checks=(*result.checks, _check("status_read", False, error)),
            observed=result.observed,
        )
    if args.as_json:
        print(json.dumps(_result_json(result), ensure_ascii=False, indent=2))
    else:
        print(f"{result.classification}: {result.trade_date}")
        for check in result.checks:
            print(f"{'OK' if check.ok else 'FAIL'} {check.name}: {check.detail}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
