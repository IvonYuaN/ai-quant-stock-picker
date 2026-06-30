from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from aqsp.cli import GATE_NOTIFY_STATE_PATH, WALKFORWARD_GATE_PATH, _check_notification_gate
from aqsp.core.time import today_shanghai
from aqsp.ledger import count_independent_signal_days
from aqsp.runtime.gate_notify import mark_gate_notification_suppressed


def _resolve_project_root(raw: str) -> Path:
    if raw.strip():
        return Path(raw).expanduser().resolve(strict=False)
    return Path(__file__).resolve().parents[1]


def _resolve_runtime_path(project_root: Path, env_name: str, default: str) -> Path:
    raw = str(os.getenv(env_name, "") or "").strip()
    path = Path(raw or default).expanduser()
    if path.is_absolute():
        return path.resolve(strict=False)
    return (project_root / path).resolve(strict=False)


def _load_gate_run_date(gate_path: Path) -> str:
    try:
        payload = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return today_shanghai().isoformat()
    if not isinstance(payload, dict):
        return today_shanghai().isoformat()
    value = str(payload.get("run_date") or "").strip()
    if len(value) >= 10:
        return value[:10]
    return today_shanghai().isoformat()


def repair_gate_notify_state(project_root: Path) -> dict[str, Any]:
    ledger_path = _resolve_runtime_path(project_root, "AQSP_LEDGER", "data/predictions.jsonl")
    gate_path = _resolve_runtime_path(project_root, "AQSP_WALKFORWARD_GATE_PATH", WALKFORWARD_GATE_PATH)
    state_path = _resolve_runtime_path(
        project_root,
        "AQSP_GATE_NOTIFY_STATE_PATH",
        GATE_NOTIFY_STATE_PATH,
    )
    signal_days = count_independent_signal_days(str(ledger_path))
    gate_ok, gate_reasons = _check_notification_gate(
        cold_start_days=signal_days,
        gate_path=str(gate_path),
    )
    run_date = _load_gate_run_date(gate_path)
    if gate_ok:
        state_path.unlink(missing_ok=True)
        return {
            "status": "cleared",
            "signal_days": signal_days,
            "run_date": run_date,
            "state_path": str(state_path),
            "gate_reasons": [],
        }
    mark_gate_notification_suppressed(
        gate_reasons=gate_reasons,
        state_path=state_path,
        run_date=run_date,
    )
    return {
        "status": "suppressed",
        "signal_days": signal_days,
        "run_date": run_date,
        "state_path": str(state_path),
        "gate_reasons": gate_reasons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="repair_gate_notify_state",
        description="Repair AQSP gate notify state from current ledger and walkforward gate.",
    )
    parser.add_argument("--project-root", default="", help="project root path")
    args = parser.parse_args(argv)
    result = repair_gate_notify_state(_resolve_project_root(str(args.project_root or "")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
