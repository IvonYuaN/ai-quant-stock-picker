from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scripts.repair_gate_notify_state import repair_gate_notify_state


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_repair_gate_notify_state_overwrites_stale_cold_start_entry(
    tmp_path: Path, monkeypatch
) -> None:
    _write_jsonl(
        tmp_path / "data" / "predictions.jsonl",
        [
            {
                "signal_date": f"2026-05-{day:02d}",
                "symbol": "600519",
                "status": "watch_only",
            }
            for day in range(1, 31)
        ],
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "walkforward_gate.json").write_text(
        json.dumps(
            {
                "run_date": "2026-06-29",
                "deflated_sharpe": -0.5708,
                "pbo": 0.6,
                "pbo_valid": True,
                "dsr_pass": False,
                "pbo_pass": False,
                "both_pass": False,
                "n_periods": 19,
                "effective_symbols": 5157,
                "window_mode": "rolling_recent",
                "data_end": "2026-06-26",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "gate_notify_state.json").write_text(
        json.dumps(
            {
                "sent_by_date": {
                    "2026-06-29": {
                        "fingerprint": "cold_start|n_periods_invalid",
                        "status": "suppressed",
                    }
                }
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    # Pin today to 1 day after run_date so the gate is not considered stale.
    # Patch in cli_notification_gate because _check_notification_gate calls
    # today_shanghai() from that module's namespace, not from aqsp.cli.
    import aqsp.cli_notification_gate as gate_mod

    monkeypatch.setattr(gate_mod, "today_shanghai", lambda: date(2026, 6, 30))

    result = repair_gate_notify_state(tmp_path)
    payload = json.loads(
        (tmp_path / "data" / "gate_notify_state.json").read_text(encoding="utf-8")
    )

    assert result["status"] == "suppressed"
    assert result["run_date"] == "2026-06-29"
    assert result["signal_days"] == 30
    assert payload["sent_by_date"]["2026-06-29"]["fingerprint"] == "dsr|pbo"


def test_repair_gate_notify_state_clears_file_when_gate_passes(
    tmp_path: Path, monkeypatch
) -> None:
    _write_jsonl(
        tmp_path / "data" / "predictions.jsonl",
        [
            {
                "signal_date": f"2026-05-{day:02d}",
                "symbol": "600519",
                "status": "watch_only",
            }
            for day in range(1, 31)
        ],
    )
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "walkforward_gate.json").write_text(
        json.dumps(
            {
                "run_date": "2026-06-29",
                "deflated_sharpe": 1.2,
                "pbo": 0.2,
                "pbo_valid": True,
                "dsr_pass": True,
                "pbo_pass": True,
                "both_pass": True,
                "n_periods": 19,
                "effective_symbols": 5157,
                "window_mode": "rolling_recent",
                "data_end": "2026-06-26",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    state_path = tmp_path / "data" / "gate_notify_state.json"
    state_path.write_text('{"status":"suppressed"}\n', encoding="utf-8")

    # Pin today to 1 day after run_date so the gate is not considered stale.
    # Patch in cli_notification_gate because _check_notification_gate calls
    # today_shanghai() from that module's namespace, not from aqsp.cli.
    import aqsp.cli_notification_gate as gate_mod

    monkeypatch.setattr(gate_mod, "today_shanghai", lambda: date(2026, 6, 30))

    result = repair_gate_notify_state(tmp_path)

    assert result["status"] == "cleared"
    assert result["gate_reasons"] == []
    assert not state_path.exists()
