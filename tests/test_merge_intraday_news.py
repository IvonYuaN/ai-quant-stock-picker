from __future__ import annotations

import csv
import json
from pathlib import Path

from aqsp.core.time import now_shanghai, today_shanghai
from aqsp.news.catalysts import CatalystEvent, CatalystReport, serialize_catalyst_report
from scripts.merge_intraday_news import merge_intraday_news


def test_merge_intraday_news_adds_current_event_to_candidate_and_run_context(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "intraday.csv"
    news_path = tmp_path / "news.json"
    signal_date = today_shanghai().isoformat()
    generated_at = now_shanghai().isoformat(timespec="seconds")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("symbol", "name", "date", "close", "score", "rating"),
        )
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "__RUN__",
                "name": "run_event",
                "date": signal_date,
                "close": "",
                "score": "",
                "rating": "",
            }
        )
        writer.writerow(
            {
                "symbol": "600001",
                "name": "测试标的",
                "date": signal_date,
                "close": "10",
                "score": "80",
                "rating": "watch",
            }
        )
    report = CatalystReport(
        date=signal_date,
        generated_at=generated_at,
        source_status="ok",
        event_status="high_impact",
        events=(
            CatalystEvent(
                title="公司订单落地",
                source="交易所公告",
                published_at=generated_at,
                symbol="600001",
                name="测试标的",
                impact="positive",
                category="公司事件",
                confidence=0.9,
                affected_sectors=("测试行业",),
            ),
        ),
    )
    news_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )

    assert merge_intraday_news(csv_path, news_path) == 1

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    run_row = rows[0]
    candidate = rows[1]
    assert "公司订单落地" in run_row["run_market_context_lines"]
    assert candidate["news_catalyst_title"] == "公司订单落地"
    assert candidate["news_catalyst_source"] == "交易所公告"
    assert candidate["news_catalyst_published_at"] == generated_at
