import csv

from scripts.merge_intraday_batches import merge_batches


def _write(path, rows):
    headers = tuple(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_merge_batches_accumulates_current_day_and_replaces_duplicate(tmp_path) -> None:
    existing = tmp_path / "latest.csv"
    batch = tmp_path / "batch.csv"
    _write(
        existing,
        [
            {"symbol": "__RUN__", "signal_date": "2026-07-31"},
            {"symbol": "000001", "signal_date": "2026-07-31", "score": "60"},
            {"symbol": "000099", "signal_date": "2026-07-30", "score": "99"},
        ],
    )
    _write(
        batch,
        [
            {"symbol": "__RUN__", "signal_date": "2026-07-31"},
            {"symbol": "000001", "signal_date": "2026-07-31", "score": "80"},
            {"symbol": "000002", "signal_date": "2026-07-31", "score": "70"},
        ],
    )

    assert merge_batches(existing, batch, signal_date="2026-07-31") == 2
    with existing.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["symbol"] for row in rows] == ["__RUN__", "000001", "000002"]
    assert rows[1]["score"] == "80"


def test_merge_batches_preserves_existing_when_current_batch_has_no_candidates(
    tmp_path,
) -> None:
    existing = tmp_path / "latest.csv"
    batch = tmp_path / "batch.csv"
    _write(
        existing,
        [
            {"symbol": "__RUN__", "signal_date": "2026-07-31"},
            {
                "symbol": "000001",
                "signal_date": "2026-07-31",
                "score": "60",
                "volume_ratio": "1.20",
                "candidate_status": "质量观察",
            },
        ],
    )
    _write(
        batch,
        [{"symbol": "__RUN__", "signal_date": "2026-07-31", "source_status": "ok"}],
    )

    assert merge_batches(existing, batch, signal_date="2026-07-31") == 1
    with existing.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["symbol"] for row in rows] == ["__RUN__", "000001"]
    assert rows[1]["volume_ratio"] == "1.20"
    assert rows[1]["technical_quality_status"] == "incomplete"
    assert rows[1]["quality_gate_action"] == "observe"
    assert rows[1]["observation_only"] == "true"
    assert rows[1]["paper_review_eligible"] == "false"
    assert rows[1]["candidate_status"] == "质量观察"
    assert "macd_hist" in rows[1]["candidate_blocker"]
    assert "kdj_j" in rows[1]["candidate_blocker"]


def test_merge_batches_keeps_complete_technical_evidence_eligible(tmp_path) -> None:
    existing = tmp_path / "latest.csv"
    batch = tmp_path / "batch.csv"
    _write(existing, [{"symbol": "__RUN__", "signal_date": "2026-07-31"}])
    _write(
        batch,
        [
            {"symbol": "__RUN__", "signal_date": "2026-07-31"},
            {
                "symbol": "000001",
                "signal_date": "2026-07-31",
                "score": "80",
                "volume_ratio": "1.20",
                "macd_hist": "0.12",
                "kdj_j": "55.0",
                "quality_gate_action": "promote",
            },
        ],
    )

    assert merge_batches(existing, batch, signal_date="2026-07-31") == 1
    with existing.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[1]["quality_gate_action"] == "promote"
    assert rows[1]["technical_quality_status"] == ""
    assert rows[1]["candidate_blocker"] == ""


def test_merge_batches_rejects_invalid_current_run_without_overwriting_existing(
    tmp_path,
) -> None:
    existing = tmp_path / "latest.csv"
    batch = tmp_path / "batch.csv"
    _write(
        existing,
        [
            {"symbol": "__RUN__", "signal_date": "2026-07-31"},
            {"symbol": "000001", "signal_date": "2026-07-31", "score": "60"},
        ],
    )
    _write(batch, [{"symbol": "000002", "signal_date": "2026-07-31", "score": "80"}])

    before = existing.read_text(encoding="utf-8")
    try:
        merge_batches(existing, batch, signal_date="2026-07-31")
    except ValueError as error:
        assert str(error) == "intraday batch CSV lacks run metadata"
    else:
        raise AssertionError("expected invalid run metadata to be rejected")
    assert existing.read_text(encoding="utf-8") == before


def test_merge_batches_rejects_old_run_metadata_without_overwriting_existing(
    tmp_path,
) -> None:
    existing = tmp_path / "latest.csv"
    batch = tmp_path / "batch.csv"
    _write(
        existing,
        [
            {"symbol": "__RUN__", "signal_date": "2026-07-31"},
            {"symbol": "000001", "signal_date": "2026-07-31", "score": "60"},
        ],
    )
    _write(
        batch,
        [
            {"symbol": "__RUN__", "signal_date": "2026-07-30"},
            {"symbol": "000002", "signal_date": "2026-07-31", "score": "80"},
        ],
    )

    before = existing.read_text(encoding="utf-8")
    try:
        merge_batches(existing, batch, signal_date="2026-07-31")
    except ValueError as error:
        assert (
            str(error) == "intraday batch run metadata date does not match signal date"
        )
    else:
        raise AssertionError("expected stale run metadata to be rejected")
    assert existing.read_text(encoding="utf-8") == before
