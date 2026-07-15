from __future__ import annotations

import csv
from pathlib import Path

from scripts.mark_intraday_observation import mark_intraday_observation


def test_mark_intraday_observation_marks_fresh_csv_without_ledger(tmp_path: Path) -> None:
    csv_path = tmp_path / "intraday.csv"
    report_path = tmp_path / "intraday.md"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "symbol",
                "run_data_lag_days",
                "run_source_freshness_tier",
                "rating",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "__RUN__",
                "run_data_lag_days": "0",
                "run_source_freshness_tier": "realtime",
            }
        )
        writer.writerow({"symbol": "600000", "rating": "buy_candidate"})
    report_path.write_text("# report\n", encoding="utf-8")

    result = mark_intraday_observation(
        csv_path,
        report_path,
        reason="timeout",
        minimum_mtime=csv_path.stat().st_mtime - 1,
    )

    assert result["freshness_status"] == "fresh"
    assert result["candidate_count"] == 1
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[1]["observation_only"] == "true"
    assert rows[1]["paper_review_eligible"] == "false"
    assert rows[1]["portfolio_action"] == "observation_only"
    assert "observation_only: true" in report_path.read_text(encoding="utf-8")


def test_mark_intraday_observation_rejects_stale_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "intraday.csv"
    report_path = tmp_path / "intraday.md"
    csv_path.write_text(
        "symbol,run_data_lag_days,run_source_freshness_tier\n__RUN__,2,realtime\n",
        encoding="utf-8",
    )
    report_path.write_text("# report\n", encoding="utf-8")

    try:
        mark_intraday_observation(
            csv_path,
            report_path,
            reason="timeout",
            minimum_mtime=csv_path.stat().st_mtime - 1,
        )
    except ValueError as exc:
        assert "freshness" in str(exc)
    else:
        raise AssertionError("stale provisional CSV must not be promoted")
