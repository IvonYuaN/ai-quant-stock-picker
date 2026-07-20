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


def test_merge_intraday_news_appends_message_observation_for_current_batch_symbol(
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
    report = CatalystReport(
        date=signal_date,
        generated_at=generated_at,
        source_status="ok",
        event_status="high_impact",
        events=(
            CatalystEvent(
                title="600002 公司订单落地",
                source="交易所公告",
                published_at=generated_at,
                symbol="600002",
                name="消息标的",
                impact="positive",
                category="公司事件",
                confidence=0.9,
                source_quality_label="权威来源",
                source_quality_score=4,
                affected_symbols=("600002",),
                validation_signals=("订单继续兑现",),
            ),
        ),
    )
    news_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )

    assert merge_intraday_news(csv_path, news_path, symbols=("600002",)) == 1

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    observation = rows[1]
    assert observation["symbol"] == "600002"
    assert observation["observation_only"] == "true"
    assert observation["quality_gate_action"] == "observe"
    assert observation["paper_review_eligible"] == "false"


def test_merge_intraday_news_expands_industry_event_using_entity_graph(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "intraday.csv"
    news_path = tmp_path / "news.json"
    signal_date = today_shanghai().isoformat()
    generated_at = now_shanghai().isoformat(timespec="seconds")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("symbol", "name", "date", "score", "rating"),
        )
        writer.writeheader()
        writer.writerow(
            {"symbol": "__RUN__", "name": "run_event", "date": signal_date}
        )
    report = CatalystReport(
        date=signal_date,
        generated_at=generated_at,
        source_status="ok",
        event_status="high_impact",
        events=(
            CatalystEvent(
                title="PCB覆铜板报价上调，供应紧张",
                source="行业报价",
                published_at=generated_at,
                impact="positive",
                category="涨价/供需催化",
                confidence=0.9,
                source_quality_label="行业来源",
                source_quality_score=3,
                affected_sectors=("PCB",),
                transmission_path=("覆铜板涨价", "PCB厂商利润分化"),
            ),
        ),
    )
    news_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )

    assert merge_intraday_news(csv_path, news_path, symbols=("002463",)) == 1

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    observation = rows[1]
    assert observation["symbol"] == "002463"
    assert observation["name"] == "沪电股份"
    assert observation["candidate_status"] == "消息产业链观察"
    assert observation["news_catalyst_sectors"] == "PCB"
