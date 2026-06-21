#!/usr/bin/env python3
"""Fail-closed readiness gate for human-reviewed semi-live operation."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aqsp.core.time import today_shanghai
from aqsp.utils.env import read_env_value
from aqsp.walkforward_gate import (
    MAX_GATE_AGE_DAYS,
    validate_walkforward_gate_payload,
)


MIN_INDEPENDENT_SIGNAL_DAYS = 30
MIN_SUCCESSFUL_RUN_DAYS = 5


@dataclass(frozen=True)
class ReadinessFinding:
    gate: str
    ok: bool
    detail: str


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _read_pipeline_history(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "logs" / "pipeline").glob("*.json")):
        payload = _read_json(path)
        if not payload:
            continue
        rows.append(
            {
                "date": path.stem,
                "success": payload.get("overall_success") is True,
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "successful_steps": sum(
                    1
                    for step in payload.get("steps", [])
                    if isinstance(step, dict) and step.get("success") is True
                ),
                "total_steps": len(
                    [
                        step
                        for step in payload.get("steps", [])
                        if isinstance(step, dict)
                    ]
                ),
            }
        )
    return rows


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _file_has_content(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def check_before_live(
    *,
    root: Path,
    today: date,
    gate_path: Path | None = None,
    ledger_path: Path | None = None,
    run_history_path: Path | None = None,
    cron_path: Path | None = None,
    cron_dir: Path | None = None,
) -> list[ReadinessFinding]:
    gate_path = gate_path or root / "data" / "walkforward_gate.json"
    ledger_path = ledger_path or root / "data" / "predictions.jsonl"
    run_history_path = run_history_path or root / "data" / "daily_run_history.jsonl"

    findings: list[ReadinessFinding] = []
    findings.append(_check_walkforward_gate(gate_path, today))
    findings.append(_check_walkforward_price_mode(root, gate_path))
    findings.append(_check_pbo_diagnostics(root, gate_path))
    findings.append(_check_paper_sample_size(ledger_path))
    findings.append(_check_successful_runs(run_history_path, root=root))
    findings.append(
        _check_scheduler_notify_cadence(root, cron_path=cron_path, cron_dir=cron_dir)
    )
    findings.append(_check_gate_notify_state_path(root))
    findings.extend(_check_runtime_outputs(root))
    return findings


def _check_walkforward_gate(path: Path, today: date) -> ReadinessFinding:
    gate = _read_json(path)
    if not gate:
        return ReadinessFinding(
            "walkforward_gate",
            False,
            f"missing or unreadable gate: {path}",
        )

    validation = validate_walkforward_gate_payload(
        gate,
        today=today,
        max_age_days=MAX_GATE_AGE_DAYS,
    )
    return ReadinessFinding(
        "walkforward_gate",
        validation.ok,
        validation.detail,
    )


def _check_walkforward_price_mode(root: Path, gate_path: Path) -> ReadinessFinding:
    gate = _read_json(gate_path)
    if not gate:
        return ReadinessFinding("walkforward_price_mode", False, "gate missing")

    source = str(gate.get("source") or "sqlite_db")
    price_mode = str(gate.get("price_mode") or "").strip().lower()
    db_path = str(gate.get("sqlite_db_path") or "").strip()
    if not db_path:
        db_path = read_env_value(root / ".env", "AQSP_SQLITE_DB_PATH")
    if not db_path:
        db_path = "A股量化分析数据/astocks_qfq.db"
    db_name = Path(db_path).name.lower()
    if not price_mode:
        price_mode = (
            "qfq" if "qfq" in db_name else "raw" if "raw" in db_name else "unknown"
        )
    if source == "sqlite_db" and price_mode == "qfq":
        return ReadinessFinding(
            "walkforward_price_mode",
            False,
            "sqlite_db gate uses qfq historical database; real gate requires raw prices or point-in-time adjustment factors",
        )
    return ReadinessFinding("walkforward_price_mode", True, "ok")


def _check_pbo_diagnostics(root: Path, gate_path: Path) -> ReadinessFinding:
    gate = _read_json(gate_path)
    if not gate or gate.get("pbo_pass") is not False:
        return ReadinessFinding("pbo_diagnostics", True, "not required")

    report_path = root / "reports" / "walkforward-grid-latest.md"
    if not report_path.exists():
        return ReadinessFinding(
            "pbo_diagnostics",
            False,
            f"PBO failed but diagnostics report missing: {report_path}",
        )
    text = report_path.read_text(encoding="utf-8")
    required = (
        "### PBO 失败定位",
        "CSCV 失败组合占比",
        "最差对齐周期",
        "训练选中变体",
        "测试最优变体",
    )
    missing = [item for item in required if item not in text]
    return ReadinessFinding(
        "pbo_diagnostics",
        not missing,
        "ok" if not missing else "missing: " + ", ".join(missing),
    )


def _check_paper_sample_size(path: Path) -> ReadinessFinding:
    rows = _read_jsonl(path)
    signal_days = {
        signal_date
        for row in rows
        if _is_real_signal_row(row)
        for signal_date in [_row_signal_date(row)]
        if _parse_date(signal_date) is not None
    }
    count = len(signal_days)
    return ReadinessFinding(
        "paper_sample_size",
        count >= MIN_INDEPENDENT_SIGNAL_DAYS,
        f"{count}/{MIN_INDEPENDENT_SIGNAL_DAYS} real independent signal days",
    )


def _is_real_signal_row(row: dict[str, Any]) -> bool:
    if bool(row.get("is_simulated")):
        return False
    if not str(row.get("symbol") or "").strip():
        return False
    if str(row.get("status") or "").strip() == "not_executable":
        return False
    return any(
        row.get(key) not in (None, "")
        for key in ("thresholds_version", "status", "rating", "score", "strategies")
    )


def _row_signal_date(row: dict[str, Any]) -> str:
    for key in ("signal_date", "signal_day_group", "date", "created_at"):
        raw = str(row.get(key) or "").strip()
        if len(raw) >= 10:
            candidate = raw[:10]
            if _parse_date(candidate) is not None:
                return candidate
    return ""


def _check_successful_runs(path: Path, *, root: Path) -> ReadinessFinding:
    history_rows = _read_jsonl(path)
    pipeline_rows = _read_pipeline_history(root)
    rows = history_rows + [
        row
        for row in pipeline_rows
        if str(row.get("date") or "").strip()
        not in {
            str(item.get("date") or item.get("run_date") or "").strip()
            for item in history_rows
        }
    ]
    source = (
        "daily_run_history+pipeline_logs"
        if history_rows and pipeline_rows
        else "daily_run_history"
        if history_rows
        else "pipeline_logs"
    )
    successful_days = {
        str(row.get("date") or row.get("run_date") or "").strip()
        for row in rows
        if row.get("success") is True or row.get("exit_code") == 0
    }
    count = len({day for day in successful_days if day})
    return ReadinessFinding(
        "successful_daily_runs",
        count >= MIN_SUCCESSFUL_RUN_DAYS,
        f"{count}/{MIN_SUCCESSFUL_RUN_DAYS} successful daily run days ({source})",
    )


def _check_scheduler_notify_cadence(
    root: Path, *, cron_path: Path | None, cron_dir: Path | None = None
) -> ReadinessFinding:
    texts: list[tuple[str, str]] = []
    for path in (
        root / "scripts" / "daily_pipeline.sh",
        root / "scripts" / "daily_run.sh",
        root / "scripts" / "intraday_refresh.sh",
        root / "scripts" / "midday_refresh.sh",
        root / "scripts" / "news_catalysts.sh",
        root / "scripts" / "server_monitor.sh",
    ):
        if path.exists():
            texts.append(
                (str(path.relative_to(root)), path.read_text(encoding="utf-8"))
            )
    if cron_path is not None and cron_path.exists():
        texts.append((str(cron_path), cron_path.read_text(encoding="utf-8")))
    texts.extend(_read_cron_wrapper_texts(cron_dir))

    blockers: list[str] = []
    for label, text in texts:
        for line in text.splitlines():
            clean = line.strip()
            if not clean or clean.startswith("#"):
                continue
            if "--notify" not in clean:
                continue
            if "notify-critical-only" in clean:
                continue
            if _looks_like_high_frequency_schedule(clean):
                blockers.append(f"{label}: {clean}")
            if any(task in clean for task in ("intraday", "midday")):
                blockers.append(f"{label}: {clean}")
        if _should_check_cron_entry_bypass(label) and _cron_wrapper_bypasses_bt_task(
            text
        ):
            blockers.append(f"{label}: trading job bypasses bt_task.sh")

    return ReadinessFinding(
        "scheduler_notify_cadence",
        not blockers,
        "ok"
        if not blockers
        else "high-frequency notify risk: " + " | ".join(blockers[:3]),
    )


def _looks_like_high_frequency_schedule(line: str) -> bool:
    fields = line.split()
    if len(fields) < 6:
        return False
    minute = fields[0]
    return minute.startswith("*/") or "," in minute or "-" in minute


def _read_cron_wrapper_texts(cron_dir: Path | None) -> list[tuple[str, str]]:
    if cron_dir is None or not cron_dir.exists() or not cron_dir.is_dir():
        return []
    texts: list[tuple[str, str]] = []
    for path in sorted(cron_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix in {".log", ".lock", ".pl"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "aqsp" in content.lower() or "/opt/aqsp" in content:
            texts.append((str(path), content))
    return texts


def _should_check_cron_entry_bypass(label: str) -> bool:
    return not label.startswith("scripts/")


def _cron_wrapper_bypasses_bt_task(text: str) -> bool:
    lower = text.lower()
    if "bt_task.sh" in lower:
        return False
    if "aqsp" not in lower and "/opt/aqsp" not in lower:
        return False
    return any(
        token in lower
        for token in (
            "daily_pipeline.sh",
            "intraday_refresh.sh",
            "midday_refresh.sh",
            "coldstart_daily.sh",
            "-m aqsp run",
            "aqsp.cli run",
            "news_catalysts.sh",
        )
    )


def _check_gate_notify_state_path(root: Path) -> ReadinessFinding:
    env_path = root / ".env"
    if not env_path.exists():
        return ReadinessFinding("gate_notify_state_path", True, "env missing")
    value = ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("AQSP_GATE_NOTIFY_STATE_PATH="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
    if not value:
        value = "data/gate_notify_state.json"
    path = Path(value)
    ok = not path.is_absolute() or str(path).startswith(str(root))
    return ReadinessFinding(
        "gate_notify_state_path",
        ok,
        value if ok else f"unstable external path: {value}",
    )


def _check_runtime_outputs(root: Path) -> list[ReadinessFinding]:
    required = (
        ("latest_report", root / "reports" / "latest.md"),
        ("briefing_report", root / "reports" / "briefing.md"),
        ("closing_review", root / "reports" / "closing_review.md"),
        ("dashboard_html", root / "dist" / "dashboard" / "index.html"),
    )
    return [
        ReadinessFinding(name, _file_has_content(path), str(path))
        for name, path in required
    ]


def _print_findings(findings: list[ReadinessFinding]) -> None:
    ready = all(finding.ok for finding in findings)
    print("BEFORE_LIVE_STATUS=" + ("PASS" if ready else "BLOCK"))
    for finding in findings:
        status = "PASS" if finding.ok else "BLOCK"
        print(f"[{status}] {finding.gate}: {finding.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="AQSP runtime/project root")
    parser.add_argument("--today", default="", help="Override today as YYYY-MM-DD")
    parser.add_argument("--gate", default="", help="Override walkforward gate path")
    parser.add_argument("--ledger", default="", help="Override predictions ledger path")
    parser.add_argument(
        "--run-history", default="", help="Override daily run history jsonl"
    )
    parser.add_argument("--cron", default="", help="Optional crontab dump to audit")
    parser.add_argument(
        "--cron-dir",
        default="/www/server/cron",
        help="Optional BT Panel cron wrapper directory to audit",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    today = _parse_date(args.today) or today_shanghai()
    findings = check_before_live(
        root=root,
        today=today,
        gate_path=Path(args.gate) if args.gate else None,
        ledger_path=Path(args.ledger) if args.ledger else None,
        run_history_path=Path(args.run_history) if args.run_history else None,
        cron_path=Path(args.cron) if args.cron else None,
        cron_dir=Path(args.cron_dir) if args.cron_dir else None,
    )
    _print_findings(findings)
    return 0 if all(finding.ok for finding in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
