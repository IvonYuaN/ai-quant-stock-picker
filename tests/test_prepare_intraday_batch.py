import importlib.util
import json
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "prepare_intraday_batch.py"
    spec = importlib.util.spec_from_file_location("prepare_intraday_batch_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, symbol: str, score: str) -> None:
    path.write_text(
        "symbol,score\n__RUN__,0\n" + f"{symbol},{score}\n",
        encoding="utf-8",
    )


def _args(tmp_path: Path, current: Path, previous: Path, ledger: Path) -> object:
    return type(
        "Args",
        (),
        {
            "current_csv": str(current),
            "previous_csv": str(previous),
            "current_ledger": str(ledger),
            "previous_ledger": str(tmp_path / "previous.jsonl"),
            "snapshot_state": str(tmp_path / "snapshot.json"),
            "trade_date": "2026-07-23",
            "cycle_id": 1,
            "universe_version": "sha256:full-pool",
            "coverage_pct": 1.0,
        },
    )()


def test_prepare_intraday_batch_merges_previous_cycle_results_when_same_pool(
    tmp_path: Path,
) -> None:
    module = _load_module()
    current = tmp_path / "current.csv"
    previous = tmp_path / "latest.csv"
    ledger = tmp_path / "current.jsonl"
    previous_ledger = tmp_path / "previous.jsonl"
    _write_csv(previous, "000001", "80")
    _write_csv(current, "000002", "90")
    previous_ledger.write_text(
        json.dumps(
            {"signal_date": "2026-07-23", "symbol": "000001", "strategy_id": "s1"}
        )
        + "\n",
        encoding="utf-8",
    )
    ledger.write_text(
        json.dumps(
            {"signal_date": "2026-07-23", "symbol": "000002", "strategy_id": "s1"}
        )
        + "\n",
        encoding="utf-8",
    )
    state = tmp_path / "snapshot.json"
    state.write_text(
        json.dumps(
            {
                "trade_date": "2026-07-23",
                "cycle_id": 1,
                "universe_version": "sha256:full-pool",
            }
        ),
        encoding="utf-8",
    )
    args = _args(tmp_path, current, previous, ledger)
    args.previous_ledger = str(previous_ledger)

    assert module.merge_snapshot_artifacts(args) == 0
    assert "000001" in current.read_text(encoding="utf-8")
    assert "000002" in current.read_text(encoding="utf-8")
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2
    assert '"intraday_snapshot_complete": true' in ledger.read_text(encoding="utf-8")


def test_prepare_intraday_batch_resets_snapshot_when_cycle_changes(
    tmp_path: Path,
) -> None:
    module = _load_module()
    current = tmp_path / "current.csv"
    previous = tmp_path / "latest.csv"
    ledger = tmp_path / "current.jsonl"
    _write_csv(previous, "000001", "80")
    _write_csv(current, "000003", "90")
    ledger.write_text(
        json.dumps(
            {"signal_date": "2026-07-23", "symbol": "000003", "strategy_id": "s1"}
        )
        + "\n",
        encoding="utf-8",
    )
    state = tmp_path / "snapshot.json"
    state.write_text(
        json.dumps(
            {
                "trade_date": "2026-07-23",
                "cycle_id": 1,
                "universe_version": "sha256:old-pool",
            }
        ),
        encoding="utf-8",
    )
    args = _args(tmp_path, current, previous, ledger)
    assert module.merge_snapshot_artifacts(args) == 0
    text = current.read_text(encoding="utf-8")
    assert "000003" in text
    assert "000001" not in text
