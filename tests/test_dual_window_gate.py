from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_dual_window_gate import (
    parse_window_spec,
    summarize_window,
)


def test_parse_window_spec_valid() -> None:
    assert parse_window_spec("3y:2023-01-01:2025-12-31") == (
        "3y",
        "2023-01-01",
        "2025-12-31",
    )


@pytest.mark.parametrize("spec", ["bad", "a:b", "label::end", ":start:end"])
def test_parse_window_spec_invalid(spec: str) -> None:
    with pytest.raises(Exception):
        parse_window_spec(spec)


def test_summarize_window_extracts_gate_fields() -> None:
    record = summarize_window(
        {
            "deflated_sharpe": 1.1,
            "pbo": 0.3,
            "n_variants": 8,
            "both_pass": True,
        },
        label="w1",
        start="2023-01-01",
        end="2025-12-31",
    )
    assert record == {
        "label": "w1",
        "start": "2023-01-01",
        "end": "2025-12-31",
        "deflated_sharpe": 1.1,
        "pbo": 0.3,
        "n_variants": 8,
        "both_pass": True,
    }


def test_main_stamps_consistent_and_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.run_dual_window_gate as module

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "w1-gate.json").write_text(json.dumps({"both_pass": True}))

    def fake_run_window(*, window, args, run_dir):  # noqa: ANN001, ARG001
        return {
            "label": window[0],
            "start": window[1],
            "end": window[2],
            "both_pass": True,
        }

    monkeypatch.setattr(module, "run_window", fake_run_window)
    rc = module.main(
        [
            "--window",
            "w1:2023-01-01:2025-12-31",
            "--window",
            "w2:2020-01-01:2022-12-31",
            "--db",
            str(tmp_path / "x.db"),
            "--run-dir",
            str(run_dir),
        ]
    )
    assert rc == 0
    stamped = json.loads((run_dir / "w1-gate.json").read_text(encoding="utf-8"))
    assert stamped["window_consistency"]["consistent"] is True
    assert stamped["window_consistency"]["n_windows"] == 2


def test_main_returns_nonzero_when_windows_disagree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.run_dual_window_gate as module

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "w1-gate.json").write_text(json.dumps({"both_pass": True}))

    def fake_run_window(*, window, args, run_dir):  # noqa: ANN001, ARG001
        return {
            "label": window[0],
            "start": window[1],
            "end": window[2],
            "both_pass": window[0] == "w1",
        }

    monkeypatch.setattr(module, "run_window", fake_run_window)
    rc = module.main(
        [
            "--window",
            "w1:2023-01-01:2025-12-31",
            "--window",
            "w2:2020-01-01:2022-12-31",
            "--db",
            str(tmp_path / "x.db"),
            "--run-dir",
            str(run_dir),
        ]
    )
    assert rc == 1
    stamped = json.loads((run_dir / "w1-gate.json").read_text(encoding="utf-8"))
    assert stamped["window_consistency"]["consistent"] is False


def test_main_requires_two_windows(tmp_path: Path) -> None:
    import scripts.run_dual_window_gate as module

    with pytest.raises(SystemExit):
        module.main(
            [
                "--window",
                "w1:2023-01-01:2025-12-31",
                "--db",
                str(tmp_path / "x.db"),
            ]
        )
