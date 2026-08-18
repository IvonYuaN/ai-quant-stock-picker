from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from aqsp.core.errors import DataError
from aqsp.core.time import latest_completed_trading_day
from aqsp.news.catalysts import CatalystEvent, CatalystReport, serialize_catalyst_report
from aqsp.web.home_snapshot import (
    load_home_dashboard_snapshot,
    load_home_snapshot_index,
)
from scripts import write_home_snapshot


def _variant_result_payload(count: int = 100) -> dict[str, object]:
    end_date = "2026-07-24"
    variants = []
    for index in range(count):
        symbol = f"{index:06d}"
        evidence = {
            "date": end_date,
            "execution_date": end_date,
            "symbol": symbol,
            "macd_hist": 0.12,
            "kdj_j": 55.0,
            "volume_ratio": 1.35,
            "atr_pct": 2.4,
        }
        variants.append(
            {
                "variant_id": f"variant-{index}",
                "strategy_signature": f"mode-{index}",
                "holdings_signature": f"{symbol}:100",
                "holdings_date": end_date,
                "previous_holdings_date": "2026-07-23",
                "initial_cash": 100000.0,
                "holdings": [
                    {
                        "symbol": symbol,
                        "name": f"样本{index}",
                        "quantity": 100,
                        "entry_evidence": evidence,
                    }
                ],
                "previous_holdings": [],
                "recent_actions": [
                    {
                        "date": end_date,
                        "symbol": symbol,
                        "side": "buy",
                        "reason": "MACD/KDJ/量比确认",
                        "evidence": evidence,
                    }
                ],
                "adjustments": [f"买入 {symbol}：MACD/KDJ/量比/ATR 技术面确认。"],
                "technical_evidence": [evidence],
            }
        )
    return {
        "schema_version": "variant-suite-v2",
        "generated_at": "2026-07-24T18:00:00+08:00",
        "data_mode": "historical_raw_unadjusted",
        "end_date": end_date,
        "initial_cash": 100000.0,
        "universe": {
            "supported_symbols": 4920,
            "selected_symbols": 600,
            "batch_active": True,
            "batch_id": "3:1200",
            "batch_size": 600,
            "cycle_id": 3,
            "coverage_pct": 0.3659,
            "filters": "沪市主板+深市主板+创业板；排除 ST/*ST/PT/退市/科创/北交/B股",
        },
        "variants": variants,
    }


def _candidate(symbol: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        display_name=f"{symbol} 示例",
        score=score,
        action_label="纸面复核",
        status_label="观察中",
        next_step=f"核对 {symbol} 量能",
        reasons=("MA20 斜率向上",),
        strategies=("ma_pullback",),
        news_catalyst_summary=f"{symbol} 消息催化",
        cross_market_summary="不应取用",
        adjusted_score=99.0,
        close=12.34,
        ret5_pct=4.5,
        ret20_pct=12.75,
        volume_ratio=1.6,
        rsi12=64.2,
        macd_hist=0.1234,
        kdj_j=58.6,
        bias20_pct=2.1,
        stop_loss=11.1,
        take_profit=14.8,
        data_source="eastmoney",
        data_fetched_at="2026-07-10T14:59:00+08:00",
        data_timestamp_source="bar_time",
        freshness="fresh",
    )


def _write_walkforward_artifacts(
    tmp_path: Path,
    *,
    status: str = "completed",
    run_date: object = "2026-07-18",
    both_pass: object = True,
) -> None:
    status_path = tmp_path / "walkforward_production_status.json"
    gate_path = tmp_path / "walkforward_gate.json"
    status_path.write_text(
        json.dumps({"status": status}, ensure_ascii=False), encoding="utf-8"
    )
    gate_path.write_text(
        json.dumps({"run_date": run_date, "both_pass": both_pass}),
        encoding="utf-8",
    )


def test_walkforward_evidence_reads_completed_status_and_sidecar_in_shanghai(
    monkeypatch, tmp_path
) -> None:
    _write_walkforward_artifacts(tmp_path)
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_PRODUCTION_STATUS",
        str(tmp_path / "walkforward_production_status.json"),
    )
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_GATE_PATH", str(tmp_path / "walkforward_gate.json")
    )

    ok, updated_at = write_home_snapshot._walkforward_evidence(
        evaluated_at=datetime(2026, 7, 19, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert ok is True
    assert updated_at is not None
    assert updated_at.tzinfo == ZoneInfo("Asia/Shanghai")
    assert updated_at.isoformat() == "2026-07-18T00:00:00+08:00"


def test_variant_suite_snapshot_reads_variant_results_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "variant_results.json"
    path.write_text(
        json.dumps(
            _variant_result_payload(),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(path))

    suite = write_home_snapshot._variant_suite_snapshot()

    assert suite.schema_version == "variant-suite-v2"
    assert suite.end_date == "2026-07-24"
    assert suite.variant_count == 100
    assert suite.selected_symbols == 600
    assert suite.batch_id == "3:1200"
    assert suite.coverage_pct == 0.3659


def test_universe_snapshot_exposes_partial_daily_research_coverage(
    monkeypatch, tmp_path: Path
) -> None:
    cursor_path = tmp_path / "daily-research-cursor.json"
    cursor_path.write_text(
        json.dumps(
            {
                "trade_date": "2026-07-29",
                "universe_count": 237,
                "batch_size": 10,
                "scanned_count": 10,
                "last_batch_id": "2026-07-29:1:0",
                "cycle_id": 1,
                "coverage_pct": 10 / 237,
                "active_state": "committed",
                "last_error": "",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_DAILY_RESEARCH_CURSOR_PATH", str(cursor_path))
    monkeypatch.setattr(
        write_home_snapshot, "today_shanghai", lambda: date(2026, 7, 29)
    )

    universe = write_home_snapshot._universe_snapshot()

    assert universe.total == 237
    assert universe.resolved == 10
    assert universe.screened == 10
    assert universe.batch_id == "2026-07-29:1:0"
    assert universe.batch_size == 10
    assert universe.coverage_pct == pytest.approx(10 / 237)
    assert universe.source == "sqlite_db"


def test_universe_snapshot_exposes_verified_raw_refresh_coverage(
    monkeypatch, tmp_path: Path
) -> None:
    cursor_path = tmp_path / "sqlite-refresh-cursor.json"
    cursor_path.write_text(
        json.dumps(
            {
                "target_day": "2026-07-29",
                "universe_size": 4464,
                "offset": 360,
                "target_day_symbols": ["600000", "000001", "300001", "600000"],
                "last_batch": {"processed_symbols": 120, "coverage_error": None},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_SQLITE_REFRESH_CURSOR_PATH", str(cursor_path))
    monkeypatch.setattr(
        write_home_snapshot,
        "latest_completed_trading_day",
        lambda: date(2026, 7, 29),
    )

    universe = write_home_snapshot._universe_snapshot()

    assert universe.total == 4464
    assert universe.resolved == 3
    assert universe.screened == 3
    assert universe.source == "sqlite_raw_refresh"
    assert universe.batch_active is True
    assert universe.batch_id == "2026-07-29"
    assert universe.batch_size == 120
    assert universe.cycle_id == 4
    assert universe.coverage_pct == pytest.approx(3 / 4464)
    assert universe.last_error == "原始日线仅覆盖 3/4464；全市场刷新尚未完成"


def test_universe_snapshot_exposes_partial_raw_rebuild_coverage(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = tmp_path / "raw-rebuild-cursor.json"
    state_path.write_text(
        json.dumps(
            {
                "target_day": "2026-07-29",
                "universe_size": 4464,
                "covered_ts_codes": ["600000.SH", "000001.SZ", "600000.SH"],
                "next_offset": 32,
                "complete": False,
                "publish_ready": False,
                "update": {"processed_symbols": 16},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RAW_REBUILD_STATE_PATH", str(state_path))
    monkeypatch.setattr(
        write_home_snapshot,
        "latest_completed_trading_day",
        lambda: date(2026, 7, 29),
    )

    universe = write_home_snapshot._universe_snapshot()

    assert universe.source == "sqlite_raw_rebuild"
    assert universe.total == 4464
    assert universe.resolved == 2
    assert universe.batch_active is True
    assert universe.batch_size == 16
    assert universe.cycle_id == 3
    assert universe.coverage_pct == pytest.approx(2 / 4464)
    assert universe.last_error == "原始日线重建仅覆盖 2/4464；全市场重建尚未完成"


def test_universe_snapshot_accepts_verified_raw_exclusions_without_cursor_reset(
    monkeypatch, tmp_path: Path
) -> None:
    cursor_path = tmp_path / "sqlite-refresh-cursor.json"
    cursor_path.write_text(
        json.dumps(
            {
                "target_day": "2026-07-29",
                "universe_size": 4466,
                "offset": 2040,
                "target_day_symbols": [f"600{index:03d}" for index in range(4399)],
                "last_batch": {
                    "processed_symbols": 2880,
                    "raw_max_trade_date": "2026-07-29",
                    "failed_symbols": 67,
                    "coverage_error": None,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_SQLITE_REFRESH_CURSOR_PATH", str(cursor_path))
    monkeypatch.setattr(
        write_home_snapshot,
        "latest_completed_trading_day",
        lambda: date(2026, 7, 29),
    )

    universe = write_home_snapshot._universe_snapshot()

    assert universe.batch_active is False
    assert universe.coverage_pct == pytest.approx(4399 / 4466)
    assert (
        universe.last_error
        == "原始日线当日可用 4399/4466；67 只未返回当日日线，已排除；"
        "完成轮次覆盖达到 98% 下限，成功股票进入研究池"
    )


def test_write_home_snapshot_parser_uses_runtime_output_path(
    monkeypatch, tmp_path: Path
) -> None:
    output = tmp_path / "runtime" / "home_dashboard_snapshot.json"
    monkeypatch.setenv("AQSP_HOME_SNAPSHOT_PATH", str(output))

    args = write_home_snapshot.build_parser().parse_args([])

    assert args.output == str(output)


def test_empty_same_day_refresh_does_not_replace_valid_snapshot() -> None:
    candidate = write_home_snapshot.HomeSnapshotCandidate(
        symbol="600001",
        display_name="样本",
        score=80.0,
        research_status="纸面复核",
        next_step="观察",
        context="趋势",
        deterministic_reasons=("趋势",),
    )
    existing = SimpleNamespace(selected_date="2026-07-24", candidates=(candidate,))
    refreshed = SimpleNamespace(selected_date="2026-07-24", candidates=())

    with pytest.raises(DataError, match="non-empty same-day"):
        write_home_snapshot._guard_empty_same_day_refresh(existing, refreshed)


def test_variant_snapshot_keeps_all_standard_experiment_variants(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "variant_results.json"
    path.write_text(
        json.dumps(_variant_result_payload(148)),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(path))

    variants = write_home_snapshot._variant_snapshot()

    assert len(variants) == 148


def test_research_chain_links_current_candidate_review_and_variant(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "variant_results.json"
    payload = _variant_result_payload()
    first = payload["variants"][0]
    assert isinstance(first, dict)
    first["holdings"][0]["symbol"] = "600001"
    first["holdings"][0]["entry_evidence"]["symbol"] = "600001"
    first["holdings_signature"] = "600001:100"
    first["recent_actions"][0]["symbol"] = "600001"
    first["recent_actions"][0]["evidence"]["symbol"] = "600001"
    first["technical_evidence"][0]["symbol"] = "600001"
    first["adjustments"] = ["买入 600001：MACD/KDJ/量比/ATR 技术面确认。"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(path))

    candidate = write_home_snapshot._snapshot_candidate(_candidate("600001", 88.0))
    assert candidate is not None
    debate = write_home_snapshot.HomeSnapshotDebate(
        symbol="600001",
        display_name="600001 示例",
        conclusion="规则证据与独立风险条件齐全。",
        primary_risk_gate="跌破纸面止损则复核失效。",
        next_trigger="下一交易日确认量能。",
        active_roles=("量化研究员", "风险审查员", "反方审查员"),
    )
    chain = write_home_snapshot._research_chain_snapshot(
        (candidate,),
        (debate,),
        write_home_snapshot._variant_suite_snapshot(),
        write_home_snapshot._variant_snapshot(),
        ("600001",),
    )

    assert chain.status == "linked"
    assert chain.candidate_symbols == ("600001",)
    assert chain.debated_symbols == ("600001",)
    assert chain.variant_candidate_symbols == ("600001",)
    assert chain.variant_review_symbols == ("600001",)
    assert chain.variant_holding_candidate_symbols == ("600001",)
    assert chain.variant_holding_review_symbols == ("600001",)


def test_research_chain_links_experiment_coverage_without_current_holding() -> None:
    candidate = write_home_snapshot._snapshot_candidate(_candidate("600001", 88.0))
    assert candidate is not None
    debate = write_home_snapshot.HomeSnapshotDebate(
        symbol="600001",
        display_name="600001 示例",
        conclusion="规则证据与独立风险条件齐全。",
        primary_risk_gate="跌破纸面止损则复核失效。",
        next_trigger="下一交易日确认量能。",
        active_roles=("量化研究员", "风险审查员", "反方审查员"),
    )

    chain = write_home_snapshot._research_chain_snapshot(
        (candidate,),
        (debate,),
        write_home_snapshot.HomeSnapshotVariantSuite(variant_count=24),
        (),
        ("600001",),
    )

    assert chain.status == "linked"
    assert chain.variant_candidate_symbols == ("600001",)
    assert chain.variant_review_symbols == ("600001",)
    assert chain.variant_holding_candidate_symbols == ()
    assert chain.variant_holding_review_symbols == ()


def test_research_chain_waits_until_all_candidates_have_variant_coverage() -> None:
    candidates = tuple(
        write_home_snapshot._snapshot_candidate(_candidate(symbol, 88.0))
        for symbol in ("600001", "600002")
    )
    assert all(candidate is not None for candidate in candidates)
    debates = tuple(
        write_home_snapshot.HomeSnapshotDebate(
            symbol=symbol,
            display_name=f"{symbol} 示例",
            conclusion="已完成讨论。",
            primary_risk_gate="跌破止损则失效。",
            next_trigger="等待下一次确认。",
            active_roles=("量化研究员", "风险审查员"),
        )
        for symbol in ("600001", "600002")
    )

    chain = write_home_snapshot._research_chain_snapshot(
        candidates,
        debates,
        write_home_snapshot.HomeSnapshotVariantSuite(variant_count=24),
        (),
        ("600001",),
    )

    assert chain.status == "waiting_validation"
    assert chain.variant_candidate_symbols == ("600001",)
    assert chain.blocker == "当天候选尚未全部进入本轮 raw 变体实验池，等待下轮覆盖。"


def test_research_chain_rejects_stale_variant_results_for_selected_date() -> None:
    candidate = write_home_snapshot._snapshot_candidate(_candidate("600001", 88.0))
    assert candidate is not None
    debate = write_home_snapshot.HomeSnapshotDebate(
        symbol="600001",
        display_name="600001 示例",
        conclusion="规则证据与独立风险条件齐全。",
        primary_risk_gate="跌破纸面止损则复核失效。",
        next_trigger="下一交易日确认量能。",
        active_roles=("量化研究员", "风险审查员", "反方审查员"),
    )

    chain = write_home_snapshot._research_chain_snapshot(
        (candidate,),
        (debate,),
        write_home_snapshot.HomeSnapshotVariantSuite(
            variant_count=24,
            end_date="2026-08-12",
        ),
        (),
        ("600001",),
        selected_date="2026-08-14",
    )

    assert chain.status == "waiting_validation"
    assert chain.variant_candidate_symbols == ()
    assert "2026-08-12" in chain.blocker


def test_research_chain_exposes_missing_variant_as_blocker(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(tmp_path / "missing.json"))
    candidate = write_home_snapshot._snapshot_candidate(_candidate("600001", 88.0))
    assert candidate is not None

    chain = write_home_snapshot._research_chain_snapshot(
        (candidate,),
        (),
        write_home_snapshot._variant_suite_snapshot(),
        (),
    )

    assert chain.status == "blocked"
    assert chain.blocker == "变体产物不存在。"


def test_variant_suite_snapshot_hides_legacy_or_insufficient_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "variant_results.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "variant-suite-v1",
                "initial_cash": 100000.0,
                "universe": {"selected_symbols": 0},
                "variants": [{"variant_id": "legacy"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(path))

    assert write_home_snapshot._variant_suite_snapshot().variant_count == 0
    assert write_home_snapshot._variant_snapshot() == ()


def test_variant_suite_snapshot_exposes_raw_refresh_blocker(
    monkeypatch, tmp_path: Path
) -> None:
    target_day = latest_completed_trading_day().isoformat()
    cursor_path = tmp_path / "sqlite-refresh-cursor.json"
    cursor_path.write_text(
        json.dumps(
            {
                "target_day": target_day,
                "universe_size": 4464,
                "offset": 120,
                "target_day_symbols": [f"600{index:03d}" for index in range(122)],
                "last_batch": {"target_day_symbol_count": 122, "total_symbols": 4464},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_SQLITE_REFRESH_CURSOR_PATH", str(cursor_path))
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(tmp_path / "missing.json"))

    suite = write_home_snapshot._variant_suite_snapshot()

    assert suite.variant_count == 0
    assert suite.last_error == "变体等待：原始日线仅覆盖 122/4464；全市场刷新尚未完成"


def test_recommendation_gate_blocks_partial_raw_refresh_coverage() -> None:
    gate = write_home_snapshot._recommendation_gate(
        provider=SimpleNamespace(paper_ledger_path=None),
        runtime=SimpleNamespace(),
        source=SimpleNamespace(status="run_completed", lag_days=0),
        message_status="可用",
        evaluated_at=datetime(2026, 7, 30, 16, tzinfo=ZoneInfo("Asia/Shanghai")),
        universe=write_home_snapshot.HomeSnapshotUniverse(
            total=4464,
            resolved=122,
            source="sqlite_raw_refresh",
            batch_active=True,
            last_error="原始日线仅覆盖 122/4464；全市场刷新尚未完成",
        ),
    )

    assert gate.recommendation_allowed is False
    assert gate.status == "blocked_incomplete_raw_data"
    assert gate.reasons == ("原始日线仅覆盖 122/4464；全市场刷新尚未完成",)


def test_recommendation_gate_blocks_partial_raw_rebuild_coverage() -> None:
    gate = write_home_snapshot._recommendation_gate(
        provider=SimpleNamespace(paper_ledger_path=None),
        runtime=SimpleNamespace(),
        source=SimpleNamespace(status="run_completed", lag_days=0),
        message_status="可用",
        evaluated_at=datetime(2026, 7, 30, 16, tzinfo=ZoneInfo("Asia/Shanghai")),
        universe=write_home_snapshot.HomeSnapshotUniverse(
            total=4464,
            resolved=122,
            source="sqlite_raw_rebuild",
            batch_active=True,
            coverage_pct=122 / 4464,
            last_error="原始日线重建仅覆盖 122/4464；全市场重建尚未完成",
        ),
    )

    assert gate.recommendation_allowed is False
    assert gate.status == "blocked_incomplete_raw_data"
    assert gate.reasons == ("原始日线重建仅覆盖 122/4464；全市场重建尚未完成",)


def test_recommendation_gate_allows_completed_raw_refresh_with_excluded_symbols() -> (
    None
):
    gate = write_home_snapshot._recommendation_gate(
        provider=SimpleNamespace(paper_ledger_path=None),
        runtime=SimpleNamespace(),
        source=SimpleNamespace(status="run_completed", lag_days=0),
        message_status="可用",
        evaluated_at=datetime(2026, 7, 30, 18, tzinfo=write_home_snapshot.SHANGHAI_TZ),
        universe=write_home_snapshot.HomeSnapshotUniverse(
            total=4466,
            resolved=4399,
            source="sqlite_raw_refresh",
            batch_active=False,
            last_error=(
                "原始日线当日可用 4399/4466；67 只未返回当日日线，已排除；"
                "完成轮次覆盖达到 98% 下限，成功股票进入研究池"
            ),
        ),
    )

    assert gate.status != "blocked_incomplete_raw_data"


def test_variant_suite_snapshot_hides_artifact_without_current_technical_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "variant_results.json"
    payload = _variant_result_payload()
    first = payload["variants"][0]
    first["technical_evidence"] = []
    first["recent_actions"] = []
    first["holdings"][0].pop("entry_evidence")
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(path))

    assert write_home_snapshot._variant_suite_snapshot().variant_count == 0
    assert write_home_snapshot._variant_snapshot() == ()


@pytest.mark.parametrize("status", ["blocked_resources", "timeout", "failed"])
def test_walkforward_evidence_rejects_non_completed_production_status(
    monkeypatch, tmp_path, status: str
) -> None:
    _write_walkforward_artifacts(tmp_path, status=status)
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_PRODUCTION_STATUS",
        str(tmp_path / "walkforward_production_status.json"),
    )
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_GATE_PATH", str(tmp_path / "walkforward_gate.json")
    )

    assert write_home_snapshot._walkforward_evidence(
        evaluated_at=datetime(2026, 7, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    ) == (False, None)


def test_walkforward_evidence_rejects_old_or_invalid_sidecar(
    monkeypatch, tmp_path
) -> None:
    _write_walkforward_artifacts(tmp_path, run_date="2026-05-01")
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_PRODUCTION_STATUS",
        str(tmp_path / "walkforward_production_status.json"),
    )
    monkeypatch.setenv(
        "AQSP_WALKFORWARD_GATE_PATH", str(tmp_path / "walkforward_gate.json")
    )

    ok, updated_at = write_home_snapshot._walkforward_evidence(
        evaluated_at=datetime(2026, 7, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert ok is False
    assert updated_at is not None
    assert updated_at.isoformat() == "2026-05-01T00:00:00+08:00"

    _write_walkforward_artifacts(tmp_path, both_pass="true")
    assert write_home_snapshot._walkforward_evidence(
        evaluated_at=datetime(2026, 7, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    ) == (False, None)


class _Provider:
    def __init__(self, debate_symbol: str = "600003") -> None:
        self.digest_calls: list[tuple[str, str]] = []
        self.runtime_dates: list[str] = []
        self.debate_symbol = debate_symbol

    def default_task_id(self) -> str:
        return "main_chain"

    def home_digest_payload(
        self,
        task_id: str,
        signal_date: str = "",
    ) -> SimpleNamespace:
        self.digest_calls.append((task_id, signal_date))
        return SimpleNamespace(
            task_view=SimpleNamespace(
                selected_date="2026-07-10",
                latest_date="2026-07-10",
                available_dates=(
                    "2026-07-10",
                    "2026-07-09",
                    "2026-07-08",
                    "2026-07-07",
                    "2026-07-04",
                ),
                detail_cards=(
                    _candidate("600001", 88.0),
                    _candidate("600002", 80.0),
                    _candidate("600003", 72.0),
                    _candidate("600004", 66.0),
                ),
                source_status={"actual_source": "sina", "lag_days": "0"},
                headline="主链已落盘",
            ),
            spotlights=(
                _candidate("600002", 5.0),
                _candidate("600005", 99.0),
            ),
            debates=(
                SimpleNamespace(
                    symbol=self.debate_symbol,
                    display_name=f"{self.debate_symbol} 示例",
                    research_verdict="委员会建议复核",
                    consensus="不应取用",
                    primary_risk_gate="量能未确认",
                    next_trigger="放量站稳",
                    adjusted_score=999.0,
                    recommended_adjustment="raise",
                    process_recorded=True,
                    conclusion_recorded=True,
                    evidence_sufficient=True,
                    round_count=2,
                    bull_count=1,
                    bear_count=0,
                    neutral_count=1,
                    agent_views=(
                        SimpleNamespace(role_id="bull"),
                        SimpleNamespace(role_id="risk_control"),
                    ),
                ),
            ),
            overview=SimpleNamespace(
                focus_headline="重点看首个确定性候选",
                blocker_headline="量能阻塞待解除",
                top_headline="主链候选已生成",
            ),
        )

    def runtime_overview(self, signal_date: str = "") -> SimpleNamespace:
        self.runtime_dates.append(signal_date)
        return SimpleNamespace(
            conclusion="当前运行已落盘",
            effective_source="sina",
            requested_source="akshare",
            data_latest_trade_date="2026-07-10",
            lag_days="0",
            run_status="fresh",
            source_reason="实时源正常",
            coldstart_progress="样本累积中",
            coldstart_handoff_line="等待最小样本量",
            gate_blocker_line="",
        )


def test_write_home_snapshot_builds_bounded_advisory_only_payload(monkeypatch) -> None:
    provider = _Provider()
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(
            2026,
            7,
            10,
            15,
            1,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert provider.digest_calls == [("intraday", "2026-07-10")]
    assert provider.runtime_dates == ["2026-07-10"]
    assert snapshot.available_dates == (
        "2026-07-10",
        "2026-07-09",
        "2026-07-08",
        "2026-07-07",
    )
    assert [item.symbol for item in snapshot.candidates] == [
        "600001",
        "600002",
        "600003",
        "600004",
        "600005",
    ]

    assert [item.score for item in snapshot.candidates] == [
        88.0,
        80.0,
        72.0,
        66.0,
        99.0,
    ]
    assert snapshot.candidates[0].deterministic_reasons == ("MA20 斜率向上",)
    assert snapshot.candidates[0].strategies == ("ma_pullback",)
    assert snapshot.candidates[0].evidence_status == "有独立规则证据"
    assert snapshot.candidates[0].context.endswith("数据源: eastmoney")
    assert snapshot.candidates[0].data_source == "eastmoney"
    assert snapshot.candidates[0].data_fetched_at == "2026-07-10T14:59:00+08:00"
    assert snapshot.candidates[0].data_timestamp_source == "bar_time"
    assert snapshot.candidates[0].freshness == "fresh"
    assert [
        (item.label, item.value) for item in snapshot.candidates[0].technical_metrics
    ] == [
        ("现价", "12.34"),
        ("5日动能", "+4.50%"),
        ("20日动能", "+12.75%"),
        ("量比", "1.60x"),
        ("RSI12", "64.2"),
        ("MACD柱", "+0.123"),
        ("KDJ-J", "58.6"),
        ("MA20偏离", "+2.10%"),
        ("纸面止损", "11.10"),
        ("纸面止盈", "14.80"),
    ]
    assert snapshot.debate is None
    assert "999" not in snapshot.to_json()
    assert "raise" not in snapshot.to_json()
    assert snapshot.summaries == (
        "盘前：未产出，等待盘前任务完成。",
        "盘中：判断：5 个对象通过盘中筛选；600005 示例 的MA20 斜率向上仍有效。约束：未形成独立复核结论。",
        "盘后：未产出，等待盘后任务完成。",
    )
    assert "讨论复核 1/5 只；4 只未通过质量门，已隐藏" not in snapshot.summaries
    assert snapshot.stale_after == "2026-07-10T15:31:00+08:00"


def test_snapshot_debates_preserves_role_specific_views_and_deduplicates_rounds() -> (
    None
):
    debate = SimpleNamespace(
        symbol="600001",
        display_name="示例",
        research_verdict="保留纸面复核",
        consensus="",
        primary_risk_gate="量能确认",
        next_trigger="等待承接",
        process_recorded=True,
        conclusion_recorded=True,
        evidence_sufficient=True,
        round_count=2,
        bull_count=1,
        bear_count=1,
        neutral_count=1,
        rounds=(
            SimpleNamespace(summary="第 1 轮：技术与风险初筛"),
            SimpleNamespace(summary="第1轮：技术与风险初筛"),
        ),
        agent_views=(
            SimpleNamespace(
                role_id="bull",
                stance="bullish",
                confidence=0.82,
                key_argument="量价共振仍在",
                key_opportunity="趋势延续",
                key_risk="无",
            ),
            SimpleNamespace(
                role_id="risk_control",
                stance="bearish",
                confidence=0.71,
                key_argument="不可把高分当成交确认",
                key_opportunity="",
                key_risk="冲高回落将失效",
            ),
            SimpleNamespace(
                role_id="sector_leader",
                stance="neutral",
                confidence=0.63,
                key_argument="等待板块扩散",
                key_opportunity="",
                key_risk="",
            ),
        ),
        viewpoint_buckets={
            "technical": ("量价共振",),
            "risk_counterevidence": ("承接待确认",),
        },
        disagreement_points=("风控要求先确认成交承接",),
        uncertainty_points=(),
    )
    payload = SimpleNamespace(debates=(debate,))

    snapshots = write_home_snapshot._snapshot_debates(
        payload,
        (write_home_snapshot._snapshot_candidate(_candidate("600001", 80.0)),),
    )

    assert snapshots[0].round_summaries == (
        "technical：量价共振",
        "risk_counterevidence：承接待确认",
        "分歧：风控要求先确认成交承接",
    )
    assert [(view.role, view.stance) for view in snapshots[0].agent_views] == [
        ("bull", "bullish"),
        ("risk_control", "bearish"),
    ]
    assert snapshots[0].agent_views[0].arguments == ("量价共振仍在",)
    assert snapshots[0].agent_views[1].risks == ("冲高回落将失效",)


def test_snapshot_debates_hides_shared_context_without_candidate_disagreement() -> None:
    debate = SimpleNamespace(
        symbol="600001",
        process_recorded=True,
        conclusion_recorded=True,
        evidence_sufficient=True,
        round_count=2,
        bull_count=1,
        bear_count=1,
        neutral_count=1,
        agent_views=(
            SimpleNamespace(role_id="bull"),
            SimpleNamespace(role_id="bear"),
            SimpleNamespace(role_id="risk_control"),
        ),
        viewpoint_buckets={"technical": ("候选专属证据: 模板",)},
        disagreement_points=(
            "技术多头质询bear: 当前bullish立场与该主张方向相反；若该主张成立，当前方向假设将失效",
        ),
    )

    snapshots = write_home_snapshot._snapshot_debates(
        SimpleNamespace(debates=(debate,)),
        (write_home_snapshot._snapshot_candidate(_candidate("600001", 80.0)),),
    )

    assert snapshots == ()


def test_snapshot_debates_blocks_copied_content_across_symbols() -> None:
    base = SimpleNamespace(
        symbol="600001",
        display_name="甲公司",
        research_verdict="保持观察，等待量价确认",
        consensus="",
        primary_risk_gate="承接不足",
        next_trigger="放量突破",
        process_recorded=True,
        conclusion_recorded=True,
        evidence_sufficient=True,
        round_count=2,
        bull_count=1,
        bear_count=1,
        neutral_count=1,
        agent_views=(
            SimpleNamespace(
                role_id="bull",
                key_argument="趋势仍在",
                key_opportunity="放量",
                key_risk="",
            ),
            SimpleNamespace(
                role_id="bear",
                key_argument="承接不足",
                key_opportunity="",
                key_risk="冲高回落",
            ),
            SimpleNamespace(
                role_id="risk",
                key_argument="等待确认",
                key_opportunity="",
                key_risk="失效风险",
            ),
        ),
        viewpoint_buckets={
            "technical": ("趋势仍在",),
            "risk_counterevidence": ("承接不足",),
        },
        disagreement_points=("是否已有有效承接",),
        uncertainty_points=("量能待确认",),
    )
    copied = SimpleNamespace(
        **{**vars(base), "symbol": "600002", "display_name": "乙公司"}
    )

    snapshots = write_home_snapshot._snapshot_debates(
        SimpleNamespace(debates=(base, copied)),
        tuple(
            write_home_snapshot._snapshot_candidate(_candidate(symbol, score))
            for symbol, score in (("600001", 80.0), ("600002", 79.0))
        ),
    )

    assert [item.symbol for item in snapshots] == ["600001"]


def test_snapshot_debates_keeps_one_distinct_review_for_each_home_candidate() -> None:
    candidates = tuple(
        write_home_snapshot._snapshot_candidate(_candidate(symbol, 90.0 - index))
        for index, symbol in enumerate(
            ("600001", "600002", "600003", "600004", "600005")
        )
    )
    debates = tuple(
        SimpleNamespace(
            symbol=candidate.symbol,
            display_name=f"示例{index}",
            research_verdict=f"候选 {candidate.symbol} 保持观察，独立场景 {index}",
            consensus="",
            primary_risk_gate=f"候选 {candidate.symbol} 跌破止损则失效，风险级别 {index}",
            next_trigger=f"候选 {candidate.symbol} 放量确认",
            process_recorded=True,
            conclusion_recorded=True,
            evidence_sufficient=True,
            round_count=2,
            bull_count=1,
            bear_count=1,
            neutral_count=1,
            agent_views=(
                SimpleNamespace(role_id="bull", key_argument=f"{candidate.symbol} 趋势仍在"),
                SimpleNamespace(role_id="bear", key_argument=f"{candidate.symbol} 承接不足"),
                SimpleNamespace(role_id="risk_control", key_argument=f"{candidate.symbol} 等待确认"),
            ),
            viewpoint_buckets={
                "technical": (f"{candidate.symbol} 趋势仍在",),
                "risk_counterevidence": (f"{candidate.symbol} 承接不足",),
            },
            disagreement_points=(f"{candidate.symbol} 是否已有有效承接",),
            uncertainty_points=(f"{candidate.symbol} 量能待确认",),
        )
        for index, candidate in enumerate(candidates)
    )

    snapshots = write_home_snapshot._snapshot_debates(
        SimpleNamespace(debates=debates),
        candidates,
    )

    assert [item.symbol for item in snapshots] == [
        "600001",
        "600002",
        "600003",
        "600004",
        "600005",
    ]


def test_recommendation_gate_blocks_candidates_without_linked_news_evidence() -> None:
    provider = _Provider()
    runtime = provider.runtime_overview("2026-07-10")
    source = SimpleNamespace(status="完成", lag_days=0)
    candidate = write_home_snapshot._snapshot_candidate(_candidate("600010", 88.0))
    assert candidate is not None

    gate = write_home_snapshot._recommendation_gate(
        provider,
        runtime,
        source,
        "来源失败",
        evaluated_at=datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
        candidates=(candidate,),
    )

    assert gate.recommendation_allowed is False
    assert gate.status == "research_evidence_not_ready"
    assert gate.reasons == ("候选缺少可引用消息证据：600010",)


def test_recommendation_gate_allows_technical_candidate_without_news_dependency() -> None:
    provider = _Provider()
    runtime = provider.runtime_overview("2026-07-10")
    candidate = write_home_snapshot._snapshot_candidate(_candidate("600010", 88.0))
    assert candidate is not None
    candidate = replace(candidate, news_catalyst_summary="")
    chain = write_home_snapshot.HomeSnapshotResearchChain(
        status="linked",
        candidate_symbols=("600010",),
        debated_symbols=("600010",),
        variant_candidate_symbols=("600010",),
        variant_review_symbols=("600010",),
    )

    gate = write_home_snapshot._recommendation_gate(
        provider,
        runtime,
        SimpleNamespace(status="完成", lag_days=0),
        "来源失败",
        evaluated_at=datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
        candidates=(candidate,),
        research_chain=chain,
    )

    assert gate.recommendation_allowed is True


def test_recommendation_gate_blocks_unlinked_variant_validation() -> None:
    candidate = write_home_snapshot._snapshot_candidate(_candidate("600010", 88.0))
    assert candidate is not None
    message = write_home_snapshot.HomeSnapshotMessage(
        title="有来源的个股消息",
        summary="摘要",
        impact="中性",
        category="公司",
        source="交易所",
        published_at="2026-07-10T14:30:00+08:00",
        source_url="https://example.com/news",
        affected_symbols=("600010",),
    )

    gate = write_home_snapshot._recommendation_gate(
        provider=SimpleNamespace(paper_ledger_path=None),
        runtime=SimpleNamespace(),
        source=SimpleNamespace(status="完成", lag_days=0),
        message_status="可用",
        evaluated_at=datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
        candidates=(candidate,),
        messages=(message,),
        research_chain=write_home_snapshot.HomeSnapshotResearchChain(
            status="waiting_validation",
            candidate_symbols=("600010",),
            debated_symbols=("600010",),
            blocker="当天变体尚未覆盖候选",
        ),
    )

    assert gate.recommendation_allowed is False
    assert gate.status == "research_validation_not_ready"
    assert gate.reasons == ("当天变体尚未覆盖候选",)


def test_recommendation_gate_blocks_stale_home_candidates() -> None:
    candidate = write_home_snapshot._snapshot_candidate(_candidate("600010", 88.0))
    assert candidate is not None
    candidate = replace(candidate, freshness="stale")

    gate = write_home_snapshot._recommendation_gate(
        provider=SimpleNamespace(paper_ledger_path=None),
        runtime=SimpleNamespace(),
        source=SimpleNamespace(status="完成", lag_days=0),
        message_status="可用",
        evaluated_at=datetime(2026, 7, 10, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        candidates=(candidate,),
    )

    assert gate.recommendation_allowed is False
    assert gate.status == "freshness_not_ready"
    assert gate.reasons == ("候选行情已过期：600010",)


def test_snapshot_candidates_keeps_live_recommendation_with_technical_evidence() -> (
    None
):
    live = _candidate("600010", 88.0)
    live.action_label = "实时推荐"
    payload = SimpleNamespace(
        task_view=SimpleNamespace(detail_cards=(live,)), spotlights=()
    )

    candidates = write_home_snapshot._snapshot_candidates(payload)

    assert [candidate.symbol for candidate in candidates] == ["600010"]
    assert any(metric.label == "MACD柱" for metric in candidates[0].technical_metrics)


def test_snapshot_candidate_keeps_required_technical_contract_when_values_missing() -> (
    None
):
    candidate = _candidate("600011", 88.0)
    candidate.volume_ratio = None
    candidate.macd_hist = None
    candidate.kdj_j = None

    snapshot_candidate = write_home_snapshot._snapshot_candidate(candidate)

    assert snapshot_candidate is not None
    required = {
        metric.key: metric.value
        for metric in snapshot_candidate.technical_metrics
        if metric.key in {"volume_ratio", "macd_hist", "kdj_j"}
    }
    assert required == {
        "volume_ratio": "未提供",
        "macd_hist": "未提供",
        "kdj_j": "未提供",
    }


def test_snapshot_candidate_reads_required_metrics_from_preserved_runtime_mapping() -> (
    None
):
    candidate = _candidate("600012", 88.0)
    candidate.volume_ratio = None
    candidate.macd_hist = None
    candidate.kdj_j = None
    candidate.metrics = {
        "volume_ratio": 1.42,
        "macd_hist": 0.1234,
        "kdj_j": 58.6,
    }

    snapshot_candidate = write_home_snapshot._snapshot_candidate(candidate)

    assert snapshot_candidate is not None
    assert {
        metric.key: metric.value
        for metric in snapshot_candidate.technical_metrics
        if metric.key in {"volume_ratio", "macd_hist", "kdj_j"}
    } == {
        "volume_ratio": "1.42x",
        "macd_hist": "+0.123",
        "kdj_j": "58.6",
    }


def test_write_home_snapshot_hides_discussion_when_multi_agent_artifact_missing(
    monkeypatch,
) -> None:
    provider = _Provider()
    original = provider.home_digest_payload

    def payload_without_agent_debate(
        task_id: str, signal_date: str = ""
    ) -> SimpleNamespace:
        payload = original(task_id, signal_date)
        first = payload.task_view.detail_cards[0]
        first.risks = ("量能确认前不形成正式推荐",)
        payload.debates = ()
        return payload

    provider.home_digest_payload = payload_without_agent_debate
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.debates == ()


def test_write_home_snapshot_rejects_two_role_runtime_debate(
    monkeypatch, tmp_path: Path
) -> None:
    provider = _Provider()
    debate_path = tmp_path / "debates.jsonl"
    debate_path.write_text(
        json.dumps(
            {
                "symbol": "600001",
                "name": "示例",
                "related_signal_date": "2026-07-10",
                "rounds": [
                    {"round_num": 1, "opinions": []},
                    {
                        "round_num": 2,
                        "opinions": [
                            {"role": "bull", "stance": "bullish"},
                            {"role": "risk_control", "stance": "bearish"},
                        ],
                    },
                ],
                "final_vote": {"bull": "bullish", "risk_control": "bearish"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_DEBATE_RESULTS", str(debate_path))

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.debates == ()


def test_variant_suite_reports_missing_artifact_reason(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(tmp_path / "missing.json"))

    suite = write_home_snapshot._variant_suite_snapshot()

    assert suite.last_error == "变体产物不存在。"


def test_variant_suite_exposes_staged_refresh_progress(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(tmp_path / "missing.json"))
    status_path = tmp_path / "variant_refresh_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "staged",
                "message": "等待下一错峰窗口继续。",
                "profiles_staged": 32,
                "profiles_total": 128,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_VARIANT_REFRESH_STATUS", str(status_path))

    suite = write_home_snapshot._variant_suite_snapshot()

    assert (
        suite.last_error
        == "变体分段构建中：已完成 32/128 个变体；等待下一错峰窗口继续。"
    )


def test_variant_suite_exposes_waiting_refresh_window(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(tmp_path / "missing.json"))
    status_path = tmp_path / "variant_refresh_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "waiting",
                "message": "等待收盘后错峰运行。",
                "generated_at": write_home_snapshot.now_shanghai().isoformat(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_VARIANT_REFRESH_STATUS", str(status_path))

    suite = write_home_snapshot._variant_suite_snapshot()

    assert suite.last_error == "变体等待：等待收盘后错峰运行。"


def test_variant_suite_rejects_expired_waiting_status(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(tmp_path / "missing.json"))
    status_path = tmp_path / "variant_refresh_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "waiting",
                "message": "等待收盘后错峰运行。",
                "generated_at": "2026-08-03T12:06:43+08:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_VARIANT_REFRESH_STATUS", str(status_path))

    suite = write_home_snapshot._variant_suite_snapshot()

    assert suite.last_error == "变体调度状态已过期，等待下一次正式刷新。"


def test_phase_conclusion_summaries_keep_each_market_phase_separate() -> None:
    class PhaseProvider(_Provider):
        def _signal_task_rows_for_date(self, task_id: str, _signal_date: str):
            rows = {
                "main_chain": [
                    {
                        "symbol": "600001",
                        "name": "盘前样本",
                        "score": 80,
                        "reasons": "开盘强度确认；不取第二条",
                    }
                ],
                "intraday": [
                    {
                        "symbol": "600002",
                        "name": "盘中样本",
                        "score": 81,
                        "reasons": "量价承接确认；不取第二条",
                    }
                ],
                "closing_review": [
                    {
                        "symbol": "600003",
                        "name": "盘后样本",
                        "score": 82,
                        "reasons": "收盘结构确认；不取第二条",
                    }
                ],
            }
            return rows[task_id]

    provider = PhaseProvider()
    debates = (
        write_home_snapshot.HomeSnapshotDebate(
            symbol="600002",
            display_name="盘中样本",
            conclusion="待复核",
            primary_risk_gate="量能回落即失效",
            next_trigger="等待收盘确认",
            active_roles=(),
        ),
    )

    summaries = write_home_snapshot._phase_conclusion_summaries(
        provider,
        "2026-07-10",
        debates,
    )

    assert summaries == (
        "盘前：计划：1 个对象进入开盘观察；优先核对 盘前样本 的开盘强度确认，开盘后只确认量价承接与数据新鲜度。",
        "盘中：判断：1 个对象通过盘中筛选；盘中样本 的量价承接确认仍有效。约束：量能回落即失效。",
        "盘后：复盘：1 个对象写入收盘记录；盘后样本 的收盘结构确认仅保留为次日观察依据，不把盘中信号外推为结论。",
    )


def test_phase_conclusion_summaries_mark_closing_reuse_without_duplicate_conclusion() -> (
    None
):
    class ReusedClosingProvider(_Provider):
        def _signal_task_rows_for_date(self, task_id: str, _signal_date: str):
            row = {
                "symbol": "600001",
                "name": "同一对象",
                "score": 80,
                "reasons": "量价确认",
            }
            return [row] if task_id in {"intraday", "closing_review"} else []

    summaries = write_home_snapshot._phase_conclusion_summaries(
        ReusedClosingProvider(), "2026-07-10", ()
    )

    assert (
        summaries[-1]
        == "盘后：未形成独立收盘复盘；本轮仅复用盘中结果，不重复计入当天结论。"
    )


def test_variant_snapshot_derives_holding_entry_date_and_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "variant_results.json"
    payload = _variant_result_payload(100)
    monkeypatch.setenv("AQSP_VARIANT_RESULTS", str(path))
    path.write_text(json.dumps(payload), encoding="utf-8")

    holding = write_home_snapshot._variant_snapshot()[0].holdings[0]

    assert holding.entry_date == "2026-07-24"
    assert holding.holding_days == 0


def test_phase_conclusion_summaries_use_current_candidates_when_intraday_source_is_stale() -> (
    None
):
    class StaleProvider(_Provider):
        def _signal_task_rows_for_date(self, _task_id: str, _signal_date: str):
            return []

    summaries = write_home_snapshot._phase_conclusion_summaries(
        StaleProvider(), "2026-07-10", (), (_candidate("600001", 80.0),)
    )

    assert summaries[1].startswith("盘中：判断：1 个对象通过盘中筛选")


def test_snapshot_candidate_maps_freshness_label_when_status_is_missing() -> None:
    candidate = _candidate("600006", 70.0)
    candidate.freshness = ""
    candidate.freshness_label = "新鲜"

    snapshot_candidate = write_home_snapshot._snapshot_candidate(candidate)

    assert snapshot_candidate is not None
    assert snapshot_candidate.freshness == "fresh"


def test_write_home_snapshot_hides_quality_failed_debate() -> None:
    provider = _Provider()
    original = provider.home_digest_payload

    def payload_with_failed_debate(
        task_id: str, signal_date: str = ""
    ) -> SimpleNamespace:
        payload = original(task_id, signal_date)
        payload.debates = (
            SimpleNamespace(
                **{
                    **vars(payload.debates[0]),
                    "debate_quality_issues": ("missing_support_viewpoint",),
                }
            ),
        )
        return payload

    provider.home_digest_payload = payload_with_failed_debate
    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.debates == ()


def test_write_home_snapshot_backfills_current_runtime_debate_when_provider_omits_it(
    monkeypatch, tmp_path
) -> None:
    provider = _Provider()
    original = provider.home_digest_payload

    def payload_without_debate(task_id: str, signal_date: str = "") -> SimpleNamespace:
        payload = original(task_id, signal_date)
        payload.debates = ()
        return payload

    provider.home_digest_payload = payload_without_debate
    runtime_root = tmp_path / "runtime"
    debate_path = runtime_root / "data" / "debate_results.jsonl"
    debate_path.parent.mkdir(parents=True)
    debate_path.write_text(
        json.dumps(
            {
                "symbol": "600001",
                "candidate_signal_date": "2026-07-10",
                "name": "测试候选",
                "research_verdict": "保留观察",
                "final_consensus": "neutral",
                "primary_risk_gate": "量能",
                "next_trigger": "放量",
                "final_vote": {"bull": "bullish", "bear": "bearish", "risk": "neutral"},
                "process_recorded": True,
                "conclusion_recorded": True,
                "evidence_sufficient": True,
                "viewpoint_buckets": {
                    "technical": ["MACD 由负转正"],
                    "risk_counterevidence": ["量能不足"],
                },
                "disagreement_points": ["趋势延续与量能不足存在分歧"],
                "rounds": [
                    {"round_num": 1, "summary": "首轮"},
                    {"round_num": 2, "summary": "复核"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_RUNTIME_ROOT", str(runtime_root))

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert [item.symbol for item in snapshot.debates] == ["600001"]
    assert snapshot.debates[0].round_count == 2


def test_write_home_snapshot_resolves_news_sidecar_from_runtime_root(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("AQSP_NEWS_JSON_OUTPUT", raising=False)
    monkeypatch.setenv("AQSP_RUNTIME_ROOT", str(tmp_path))

    assert write_home_snapshot._news_json_report_path() == (
        tmp_path / "data/runtime/news_catalysts_latest.json"
    )


def test_write_home_snapshot_keeps_candidate_conclusion_first_when_list_is_capped(
    monkeypatch,
) -> None:
    provider = _Provider()
    original_payload = provider.home_digest_payload
    original_runtime = provider.runtime_overview

    def payload_with_six_candidates(task_id: str, signal_date: str = ""):
        payload = original_payload(task_id, signal_date)
        payload.task_view.detail_cards = (
            *payload.task_view.detail_cards,
            _candidate("600006", 60.0),
            _candidate("600007", 59.0),
        )
        payload.spotlights = ()
        delattr(payload, "debates")
        return payload

    def runtime_with_count(signal_date: str = ""):
        runtime = original_runtime(signal_date)
        runtime.conclusion = "待复核 6 只，先看 600001、600002、600003"
        return runtime

    monkeypatch.setattr(provider, "home_digest_payload", payload_with_six_candidates)
    monkeypatch.setattr(provider, "runtime_overview", runtime_with_count)
    monkeypatch.setattr(
        write_home_snapshot,
        "_recommendation_gate",
        lambda *args, **kwargs: write_home_snapshot.HomeSnapshotRecommendationGate(
            recommendation_allowed=True,
            status="open",
            reasons=(),
        ),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert len(snapshot.candidates) == 5
    assert snapshot.summaries[0].startswith("盘前：")
    assert snapshot.summaries[1].startswith("盘中：")
    assert snapshot.summaries[2].startswith("盘后：")
    assert not any("首页展示" in summary for summary in snapshot.summaries)


def test_write_home_snapshot_downgrades_recommendations_when_gate_is_blocked(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        write_home_snapshot,
        "_recommendation_gate",
        lambda *args, **kwargs: write_home_snapshot.HomeSnapshotRecommendationGate(
            recommendation_allowed=False,
            status="blocked",
            reasons=("walkforward_failed",),
        ),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.recommendation_gate.recommendation_allowed is False
    assert snapshot.candidates
    assert all(
        not write_home_snapshot.is_home_recommendation(candidate)
        for candidate in snapshot.candidates
    )
    assert all(
        "仅观察（推荐 gate 阻塞）" in candidate.research_status
        for candidate in snapshot.candidates
    )


def test_snapshot_realtime_cross_market_reads_sidecar_without_network(
    monkeypatch, tmp_path: Path
) -> None:
    sidecar = tmp_path / "realtime_cross_market_context.json"
    payload = {
        "SPX": {
            "value": 5500.0,
            "change_pct": 0.8,
            "observed_at": "2026-07-10T14:59:00+08:00",
            "source": "test",
        }
    }
    sidecar.write_text(
        json.dumps({"schema_version": "v1", "status": "fresh", "payload": payload}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_REALTIME_CROSS_MARKET_PATH", str(sidecar))

    assert write_home_snapshot._snapshot_realtime_cross_market("intraday") == payload
    assert write_home_snapshot._snapshot_realtime_cross_market("daily") is None

    sidecar.write_text("not-json", encoding="utf-8")
    assert write_home_snapshot._snapshot_realtime_cross_market("intraday") is None


def test_write_home_snapshot_keeps_realtime_context_when_news_report_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AQSP_MARKET_CONTEXT_LIVE_SOURCE", "true")
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(
            2026,
            7,
            10,
            15,
            0,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ),
    )
    monkeypatch.setattr(
        write_home_snapshot,
        "_snapshot_realtime_cross_market",
        lambda _task_id: {
            "SPX": {
                "value": 5500.0,
                "change_pct": 0.8,
                "observed_at": "2026-07-10T14:59:00+08:00",
                "fetched_at": "2026-07-10T15:00:00+08:00",
                "source": "test-feed",
                "source_url": "https://example.test/spx",
                "timestamp_source": "vendor",
            }
        },
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.market_context is not None
    assert snapshot.market_context.cross_market == ()
    assert any(
        line.startswith("实时跨市:") for line in snapshot.market_context.summary_lines
    )


def test_write_home_snapshot_normalizes_legacy_news_and_cross_market_timestamps(
    monkeypatch, tmp_path
) -> None:
    report_path = tmp_path / "news.json"
    current_time = datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report = CatalystReport(
        date="2026-07-10",
        generated_at=current_time.replace(tzinfo=None).isoformat(timespec="seconds"),
        source_status="ok",
        events=(
            CatalystEvent(
                title="SpaceX 评估 IPO 上市窗口",
                source="Reuters",
                published_at="2026-07-10T01:00:00Z",
                impact="positive",
                category="资本运作",
                inference="海外商业航天风险偏好升温",
                source_region="international",
            ),
            CatalystEvent(
                title="历史事件",
                source="旧缓存",
                published_at="2026-07-09T09:00:00+08:00",
                impact="positive",
            ),
            CatalystEvent(
                title="无时间事件",
                source="未知",
                published_at="",
                impact="positive",
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setattr("aqsp.news.catalysts.now_shanghai", lambda: current_time)
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.messages[0].published_at == "2026-07-10T09:00:00+08:00"
    assert snapshot.market_context is not None
    assert (
        snapshot.market_context.cross_market[0].source_published_at
        == "2026-07-10T09:00:00+08:00"
    )
    assert all("2026-07-09" not in item.title for item in snapshot.messages)


def test_messages_prioritize_distinct_topics_before_repeating_one_topic() -> None:
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T15:00:00+08:00",
        source_status="ok",
        events=tuple(
            CatalystEvent(
                title=title,
                source="feed",
                published_at=f"2026-07-10T09:{index:02d}:00+08:00",
                impact="positive",
                category=category,
                inference=title,
                source_region=region,
            )
            for index, (title, category, region) in enumerate(
                (
                    ("英伟达新品 1", "海外公司事件", "international"),
                    ("英伟达新品 2", "海外公司事件", "international"),
                    ("PCB 涨价", "供应链/价格变化", "domestic"),
                    ("商业航天 IPO", "海外公司事件", "international"),
                    ("军工订单", "地缘事件", "mixed"),
                    ("政策支持", "产业政策", "domestic"),
                )
            )
        ),
    )

    messages = write_home_snapshot._messages_from_catalyst_report(report)

    assert [message.title for message in messages] == [
        "英伟达新品 1",
        "PCB 涨价",
        "商业航天 IPO",
        "军工订单",
        "政策支持",
    ]


def test_messages_bound_one_source_when_multiple_sources_are_available() -> None:
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T15:00:00+08:00",
        source_status="ok",
        events=tuple(
            CatalystEvent(
                title=f"主源事件 {index}",
                source="主源",
                published_at=f"2026-07-10T09:{index:02d}:00+08:00",
                impact="positive",
                category=f"主源类别 {index}",
                inference=f"主源摘要 {index}",
            )
            for index in range(5)
        )
        + tuple(
            CatalystEvent(
                title=f"备用源事件 {index}",
                source="备用源",
                published_at=f"2026-07-10T10:{index:02d}:00+08:00",
                impact="neutral",
                category=f"备用类别 {index}",
                inference=f"备用源摘要 {index}",
            )
            for index in range(2)
        ),
    )

    messages = write_home_snapshot._messages_from_catalyst_report(report)

    assert len(messages) == 4
    assert {message.source for message in messages} == {"主源", "备用源"}
    assert sum(message.source == "主源" for message in messages) == 2


def test_messages_bound_sources_even_when_digest_has_fewer_than_five_items() -> None:
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T15:00:00+08:00",
        source_status="ok",
        events=tuple(
            CatalystEvent(
                title=f"主源事件 {index}",
                source="主源",
                published_at=f"2026-07-10T09:{index:02d}:00+08:00",
                category=f"主源类别 {index}",
            )
            for index in range(3)
        )
        + (
            CatalystEvent(
                title="备用源事件",
                source="备用源",
                published_at="2026-07-10T10:00:00+08:00",
                category="备用类别",
            ),
        ),
    )

    messages = write_home_snapshot._messages_from_catalyst_report(report)

    assert [message.source for message in messages] == ["主源", "主源", "备用源"]


def test_messages_exclude_events_without_traceable_source() -> None:
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T15:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="无来源事件",
                source="",
                published_at="2026-07-10T09:00:00+08:00",
                impact="positive",
                inference="不应进入首页摘要",
            ),
        ),
    )

    assert write_home_snapshot._messages_from_catalyst_report(report) == ()


def test_messages_use_market_clues_only_when_high_impact_events_are_empty() -> None:
    clue = CatalystEvent(
        title="EIA 发布周度能源数据",
        source="EIA",
        published_at="2026-07-10T09:00:00+08:00",
        category="可核验市场线索",
        inference="可核验市场线索，非个股直接证据；需结合量价复核。",
        url="https://www.eia.gov/example",
        verification="原文可追踪",
        transmission_hypothesis="非个股直接证据；不参与高影响事件判断。",
    )
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T09:05:00+08:00",
        source_status="ok",
        event_status="no_high_impact",
        events=(),
        market_clues=(clue,),
    )

    messages = write_home_snapshot._messages_from_catalyst_report(report)

    assert len(messages) == 1
    assert messages[0].category == "可核验市场线索"
    assert messages[0].source_url == "https://www.eia.gov/example"
    assert messages[0].affected_symbols == ()
    assert "非个股直接证据" in messages[0].summary


def test_messages_reject_market_clue_without_traceable_url() -> None:
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T09:05:00+08:00",
        source_status="ok",
        event_status="no_high_impact",
        events=(),
        market_clues=(
            CatalystEvent(
                title="无原文市场线索",
                source="某媒体",
                published_at="2026-07-10T09:00:00+08:00",
                category="可核验市场线索",
            ),
        ),
    )

    assert write_home_snapshot._messages_from_catalyst_report(report) == ()


def test_messages_do_not_mix_market_clues_into_high_impact_events() -> None:
    high_impact = CatalystEvent(
        title="产业政策正式发布",
        source="政府网站",
        published_at="2026-07-10T09:00:00+08:00",
        category="政策催化",
        url="https://www.gov.cn/example",
    )
    clue = replace(
        high_impact,
        title="普通市场线索",
        category="可核验市场线索",
        url="https://example.com/clue",
    )
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T09:05:00+08:00",
        source_status="ok",
        event_status="high_impact",
        events=(high_impact,),
        market_clues=(clue,),
    )

    messages = write_home_snapshot._messages_from_catalyst_report(report)

    assert [message.title for message in messages] == ["产业政策正式发布"]


def test_home_snapshot_exposes_current_traceable_clue_from_json(
    monkeypatch, tmp_path
) -> None:
    current_time = datetime(2026, 7, 10, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report_path = tmp_path / "news.json"
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T10:00:00+08:00",
        source_status="ok",
        event_status="no_high_impact",
        raw_news_count=3,
        events=(),
        market_clues=(
            CatalystEvent(
                title="SEC 发布市场结构例行更新",
                source="SEC",
                published_at="2026-07-10T09:30:00+08:00",
                category="可核验市场线索",
                inference="可核验市场线索，非个股直接证据；需结合量价复核。",
                url="https://www.sec.gov/news/example",
                verification="原文可追踪",
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setattr(write_home_snapshot, "now_shanghai", lambda: current_time)
    monkeypatch.setattr("aqsp.news.catalysts.now_shanghai", lambda: current_time)

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "可用"
    assert len(snapshot.messages) == 1
    assert snapshot.messages[0].source_url == "https://www.sec.gov/news/example"
    assert "非个股直接证据" in snapshot.messages[0].summary


def test_write_home_snapshot_excludes_future_dated_news(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "news.json"
    current_time = datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report = CatalystReport(
        date="2026-07-10",
        generated_at=current_time.isoformat(),
        source_status="ok",
        events=(
            CatalystEvent(
                title="未来事件",
                source="feed",
                published_at="2026-07-10T15:02:00+08:00",
                impact="positive",
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setattr(write_home_snapshot, "now_shanghai", lambda: current_time)

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.messages == ()


def test_write_home_snapshot_rejects_stale_current_news_without_markdown_fallback(
    monkeypatch, tmp_path
) -> None:
    current_time = datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report_path = tmp_path / "news.json"
    markdown_path = tmp_path / "news.md"
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T08:00:00+08:00",
        source_status="ok",
        events=(
            CatalystEvent(
                title="旧消息",
                source="旧缓存",
                published_at="2026-07-10T08:00:00+08:00",
                impact="positive",
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    markdown_path.write_text(
        "# 消息面雷达-2026-07-10|可用\n\n"
        "## 事件\n\n"
        "- 1. 利好 | 全市场 | 消息\n"
        "- 结果: 不应回退的旧 Markdown\n"
        "- 时间: 2026-07-10T08:00:00+08:00\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setenv("AQSP_NEWS_OUTPUT", str(markdown_path))
    monkeypatch.setattr(write_home_snapshot, "now_shanghai", lambda: current_time)
    monkeypatch.setattr("aqsp.news.catalysts.now_shanghai", lambda: current_time)

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "超时"
    assert snapshot.messages == ()
    assert snapshot.market_context is not None
    assert any(
        "超过 6 小时有效窗口" in item for item in snapshot.market_context.warnings
    )
    assert "不应回退的旧 Markdown" not in snapshot.to_json()


def test_write_home_snapshot_preserves_catalyst_chain_evidence(
    monkeypatch, tmp_path
) -> None:
    current_time = datetime(2026, 7, 10, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report_path = tmp_path / "news.json"
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T10:00:00+08:00",
        source_status="ok",
        event_status="high_impact",
        events=(
            CatalystEvent(
                title="NVIDIA 发布 Physical AI 新平台",
                source="NVIDIA",
                published_at="2026-07-10T09:30:00+08:00",
                impact="positive",
                category="科技催化",
                inference="映射机器人和边缘算力链",
                url="https://nvidia.example/news",
                affected_sectors=("机器人", "AI算力"),
                affected_symbols=("000977",),
                transmission_hypothesis="海外大厂发布 -> A股机器人映射",
                supporting_evidence=("NVIDIA: Physical AI 新平台",),
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setattr(write_home_snapshot, "now_shanghai", lambda: current_time)
    monkeypatch.setattr("aqsp.news.catalysts.now_shanghai", lambda: current_time)

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "可用"
    message = snapshot.messages[0]
    assert message.event_type == "新品发布"
    assert message.affected_sectors == ("机器人", "AI算力")
    assert message.affected_symbols == ("000977",)
    assert message.transmission_hypothesis == "海外大厂发布 -> A股机器人映射"
    assert message.supporting_evidence == ("NVIDIA: Physical AI 新平台",)
    assert message.source_url == "https://nvidia.example/news"


def test_write_home_snapshot_clears_messages_when_current_source_failed(
    monkeypatch, tmp_path
) -> None:
    current_time = datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    report_path = tmp_path / "news.json"
    report = CatalystReport(
        date="2026-07-10",
        generated_at="2026-07-10T15:00:00+08:00",
        source_status="failed",
        warnings=("国际源超时",),
        event_status="source_failed",
        events=(
            CatalystEvent(
                title="失败源残留消息",
                source="旧缓存",
                published_at="2026-07-10T15:00:00+08:00",
                impact="positive",
            ),
        ),
    )
    report_path.write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(report_path))
    monkeypatch.setattr(write_home_snapshot, "now_shanghai", lambda: current_time)
    monkeypatch.setattr("aqsp.news.catalysts.now_shanghai", lambda: current_time)

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "来源失败"
    assert snapshot.messages == ()
    assert snapshot.market_context is not None
    assert "国际源超时" in snapshot.market_context.warnings
    assert snapshot.market_context.cross_market == ()


def test_write_home_snapshot_rejects_provider_historical_date_fallback() -> None:
    with pytest.raises(ValueError, match="historical date"):
        write_home_snapshot.build_home_snapshot(
            _Provider(), signal_date="2026-07-11", task_id="intraday"
        )


def test_write_home_snapshot_keeps_observation_and_blocked_cards_after_recommendations() -> (
    None
):
    provider = _Provider()
    original = provider.home_digest_payload

    def _mixed_payload(task_id: str, signal_date: str = "") -> SimpleNamespace:
        payload = original(task_id, signal_date)
        payload.task_view.detail_cards = (
            SimpleNamespace(
                symbol="699999",
                display_name="699999 阻塞项",
                score=100.0,
                action_label="阻塞观察",
                status_label="阻塞观察",
                rank_label="阻塞观察",
                blocker="流动性不足",
            ),
            *payload.task_view.detail_cards,
        )
        payload.spotlights = (
            SimpleNamespace(
                symbol="688888",
                display_name="688888 观察项",
                score=101.0,
                action_label="继续观察",
                status_label="观察",
                rank_label="观察",
                blocker="",
            ),
            _candidate("600005", 99.0),
        )
        return payload

    provider.home_digest_payload = _mixed_payload

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert [item.symbol for item in snapshot.candidates] == [
        "600001",
        "600002",
        "600003",
        "600004",
        "600005",
    ]


def test_write_home_snapshot_keeps_only_observation_cards_when_no_recommendation_exists() -> (
    None
):
    provider = _Provider()
    original = provider.home_digest_payload

    def _observation_payload(task_id: str, signal_date: str = "") -> SimpleNamespace:
        payload = original(task_id, signal_date)
        payload.task_view.detail_cards = (
            SimpleNamespace(
                symbol="699999",
                display_name="699999 阻塞项",
                score=100.0,
                action_label="阻塞观察",
                status_label="阻塞观察",
                rank_label="阻塞观察",
                blocker="流动性不足",
            ),
            SimpleNamespace(
                symbol="688888",
                display_name="688888 观察项",
                score=90.0,
                action_label="继续观察",
                status_label="观察",
                rank_label="观察",
                blocker="",
            ),
        )
        payload.spotlights = ()
        payload.debates = ()
        return payload

    provider.home_digest_payload = _observation_payload
    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert [item.symbol for item in snapshot.candidates] == ["699999", "688888"]
    assert all(
        not write_home_snapshot.is_home_recommendation(item)
        for item in snapshot.candidates
    )


def test_write_home_snapshot_maps_midday_to_today_intraday_artifact() -> None:
    provider = _DateAwareProvider()

    write_home_snapshot.build_home_snapshot(provider, task_id="midday")

    assert provider.digest_calls == [
        ("intraday", write_home_snapshot.today_shanghai().isoformat())
    ]


def test_write_home_snapshot_hides_debate_for_non_current_candidate(
    monkeypatch,
) -> None:
    provider = _Provider(debate_symbol="600999")
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    snapshot = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.debate is None
    assert snapshot.summaries == (
        "盘前：未产出，等待盘前任务完成。",
        "盘中：判断：5 个对象通过盘中筛选；600005 示例 的MA20 斜率向上仍有效。约束：未形成独立复核结论。",
        "盘后：未产出，等待盘后任务完成。",
    )
    assert "600999" not in snapshot.to_json()


def test_write_home_snapshot_reads_only_current_day_news_report(
    monkeypatch, tmp_path
) -> None:
    report = tmp_path / "news_catalysts.md"
    report.write_text(
        "\n".join(
            (
                "# 消息面雷达-2026-07-10|部分可用",
                "",
                "## 事件",
                "",
                "- 1. 利好 | 市场/行业 | 跨市",
                "- 结果: 海外主线",
                "- 结论: 等待 A 股板块确认",
                "- 影响: 短线观察",
                "- 来源: RSS",
                "- 时间: 2026-07-10T09:00:00+08:00",
                "",
                "## 状态",
                "",
                "- 状态: partial",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_OUTPUT", str(report))

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "部分可用"
    assert len(snapshot.messages) == 1
    assert snapshot.messages[0].title == "海外主线"
    assert snapshot.messages[0].source == "RSS"

    assert (
        write_home_snapshot.build_home_snapshot(
            _DateAwareProvider(), signal_date="2026-07-09", task_id="intraday"
        ).messages
        == ()
    )


def test_write_home_snapshot_reads_dated_news_archive_when_latest_is_missing(
    monkeypatch, tmp_path
) -> None:
    archive_dir = tmp_path / "news_archive"
    archive_dir.mkdir()
    report = CatalystReport(
        date="2026-07-09",
        generated_at="2026-07-09T10:00:00+08:00",
        events=(
            CatalystEvent(
                title="PCB 供需变化",
                source="eastmoney_domestic",
                published_at="2026-07-09T09:30:00+08:00",
                impact="positive",
                category="涨价/供需催化",
                confidence=0.9,
                inference="短线关注产业链确认",
                source_region="domestic",
            ),
        ),
        source_status="partial",
        event_status="high_impact",
        raw_news_count=1,
    )
    (archive_dir / "news-2026-07-09.json").write_text(
        json.dumps(serialize_catalyst_report(report), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_JSON_OUTPUT", str(tmp_path / "missing-latest.json"))
    monkeypatch.setenv("AQSP_NEWS_ARCHIVE_DIR", str(archive_dir))

    snapshot = write_home_snapshot.build_home_snapshot(
        _DateAwareProvider(), signal_date="2026-07-09", task_id="intraday"
    )

    assert snapshot.message_status == "部分可用"
    assert [item.title for item in snapshot.messages] == ["PCB 供需变化"]


def test_write_home_snapshot_structures_current_cross_market_context(
    monkeypatch, tmp_path
) -> None:
    report = tmp_path / "news_catalysts.md"
    report.write_text(
        "\n".join(
            (
                "# 消息面雷达-2026-07-10|可用",
                "",
                "## 结论",
                "",
                "- 海外商业航天催化",
                "- 数据状态: 可用",
                "- 事件状态: 已筛出高影响事件",
                "",
                "## 事件",
                "",
                "- 1. 利好 | 全市场 | 资本运作",
                "- 结果: SpaceX 评估 IPO 上市窗口",
                "- 结论: 海外商业航天风险偏好升温",
                "- 影响: 利好",
                "- 来源: 新华社 | 质量 多源/权威媒体（3/4） | 区域 international",
                "- 时间: 2026-07-10T09:00:00+08:00",
                "",
                "## 状态",
                "",
                "- 状态: ok",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_OUTPUT", str(report))

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.market_context is not None
    assert snapshot.market_context.cross_market[0].rule_id == "commercial_space"
    assert snapshot.market_context.cross_market[0].source_region == "international"
    assert any(message.category == "跨市场传导" for message in snapshot.messages)


def test_write_home_snapshot_treats_explicit_empty_event_report_as_no_high_impact() -> (
    None
):
    from scripts import write_home_snapshot

    status = write_home_snapshot._report_event_status(
        "## 事件\n\n- 未筛出高影响消息\n## 状态\n", "partial"
    )

    assert status == "no_high_impact"


def test_write_home_snapshot_marks_historical_news_as_excluded(
    monkeypatch, tmp_path
) -> None:
    report = tmp_path / "news_catalysts.md"
    report.write_text(
        "# 消息面雷达-2026-07-09|可用\n\n## 事件\n\n- 1. 利好 | 全市场 | 跨市\n"
        "- 结果: 历史消息\n- 结论: 不应进入当天快照\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_OUTPUT", str(report))

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "历史消息已排除"
    assert snapshot.messages == ()
    assert snapshot.market_context is not None
    assert snapshot.market_context.status == "历史消息已排除"


def test_normalize_catalyst_report_downgrades_historical_high_impact_status() -> None:
    report = CatalystReport(
        date="2026-07-19",
        generated_at="2026-07-19T15:36:11+08:00",
        source_status="partial",
        event_status="high_impact",
        events=(
            CatalystEvent(
                title="旧事件",
                source="fixture",
                published_at="2026-07-17T09:00:00+08:00",
                impact="positive",
            ),
        ),
    )

    normalized, historical_count, invalid_count = (
        write_home_snapshot._normalize_catalyst_report_for_snapshot(
            report, "2026-07-19"
        )
    )

    assert historical_count == 1
    assert invalid_count == 0
    assert normalized.events == ()
    assert normalized.news_status == "stale_only"


def test_normalize_catalyst_report_keeps_previous_trade_day_news_for_current_session(
    monkeypatch,
) -> None:
    current_time = datetime(2026, 7, 20, 5, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(
        write_home_snapshot, "today_shanghai", lambda: current_time.date()
    )
    monkeypatch.setattr(write_home_snapshot, "now_shanghai", lambda: current_time)
    report = CatalystReport(
        date="2026-07-20",
        generated_at=current_time.isoformat(),
        source_status="ok",
        event_status="high_impact",
        events=(
            CatalystEvent(
                title="周末海外事件",
                source="fixture",
                published_at="2026-07-18T09:00:00+08:00",
                impact="positive",
            ),
        ),
    )

    normalized, historical_count, invalid_count = (
        write_home_snapshot._normalize_catalyst_report_for_snapshot(
            report, "2026-07-20"
        )
    )

    assert historical_count == 0
    assert invalid_count == 0
    assert len(normalized.events) == 1


def test_write_home_snapshot_explains_empty_current_news(monkeypatch, tmp_path) -> None:
    report = tmp_path / "news_catalysts.md"
    report.write_text(
        "# 消息面雷达-2026-07-10|可用\n\n## 结论\n\n"
        "- 无强事件\n- 数据状态: 可用\n- 事件状态: 抓取成功但未筛出高影响事件\n"
        "\n## 事件\n\n- 未筛出高影响消息\n\n## 状态\n\n- 状态: ok\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_NEWS_OUTPUT", str(report))

    snapshot = write_home_snapshot.build_home_snapshot(
        _Provider(), signal_date="2026-07-10", task_id="intraday"
    )

    assert snapshot.message_status == "无高影响消息"
    assert snapshot.messages == ()
    assert snapshot.market_context is not None
    assert snapshot.market_context.status == "无高影响消息"
    assert any(
        line == "消息结果: 抓取成功但未筛出高影响事件"
        for line in snapshot.market_context.summary_lines
    )


def test_write_home_snapshot_does_not_label_domestic_news_as_overseas_risk() -> None:
    artifact = SimpleNamespace(
        summary_lines=("海外风险: 偏多（正面 1 / 负面 0）", "消息状态: 部分可用"),
        catalyst_events=(SimpleNamespace(source_region="domestic"),),
        cross_market_implications=(),
        cross_market_overview="",
        source_status="partial",
        warnings=(),
    )

    context = write_home_snapshot._snapshot_market_context(artifact)

    assert context.summary_lines == ("消息状态: 部分可用",)


class _DateAwareProvider(_Provider):
    def home_digest_payload(
        self, task_id: str, signal_date: str = ""
    ) -> SimpleNamespace:
        payload = super().home_digest_payload(task_id, signal_date)
        selected_date = signal_date or payload.task_view.selected_date
        payload.task_view.selected_date = selected_date
        payload.task_view.latest_date = selected_date
        return payload


def test_write_home_snapshot_uses_today_for_intraday_during_market_hours(
    monkeypatch,
) -> None:
    provider = _DateAwareProvider()
    monkeypatch.setattr(
        write_home_snapshot,
        "today_shanghai",
        lambda: date(2026, 7, 10),
    )
    monkeypatch.setattr(
        write_home_snapshot,
        "latest_completed_trading_day",
        lambda: date(2026, 7, 9),
    )
    monkeypatch.setattr(
        write_home_snapshot,
        "today_shanghai",
        lambda: date(2026, 7, 10),
    )

    snapshot = write_home_snapshot.build_home_snapshot(provider, task_id="intraday")

    assert snapshot.selected_date == "2026-07-10"
    assert provider.digest_calls == [("intraday", "2026-07-10")]
    assert provider.runtime_dates == ["2026-07-10"]


def test_snapshot_source_uses_intraday_provenance_for_completed_day(
    monkeypatch, tmp_path
) -> None:
    status_path = tmp_path / "intraday_refresh_status.json"
    status_path.write_text(
        json.dumps(
            {
                "provenance": {
                    "requested_source": "online_first",
                    "actual_source": "tencent",
                    "latest_trade_date": "2026-07-10",
                    "lag_days": 0,
                    "status": "verified",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_INTRADAY_STATUS", str(status_path))
    monkeypatch.setattr(
        write_home_snapshot,
        "latest_completed_trading_day",
        lambda: date(2026, 7, 9),
    )

    source = write_home_snapshot._snapshot_source(
        SimpleNamespace(), SimpleNamespace(source_status={}), selected_date="2026-07-09"
    )

    assert source.effective == "tencent"
    assert source.latest_trade_date == "2026-07-09"
    assert source.lag_days == 0
    assert source.status == "verified"


def test_snapshot_source_surfaces_latest_intraday_failure_over_old_run_status(
    monkeypatch, tmp_path
) -> None:
    status_path = tmp_path / "intraday_refresh_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "reason": "盘中任务被中断，保留上一版盘中产物",
                "provenance": {
                    "actual_source": "tencent",
                    "latest_trade_date": "2026-07-10",
                    "lag_days": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_INTRADAY_STATUS", str(status_path))

    source = write_home_snapshot._snapshot_source(
        SimpleNamespace(
            run_status="blocked_by_circuit_breaker",
            effective_source="tencent",
            lag_days=0,
        ),
        SimpleNamespace(source_status={}),
        selected_date="2026-07-10",
    )

    assert source.status == "盘中任务失败：盘中任务被中断，保留上一版盘中产物"


def test_snapshot_source_surfaces_running_intraday_over_old_run_status(
    monkeypatch, tmp_path
) -> None:
    status_path = tmp_path / "intraday_refresh_status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "running",
                "reason": "正在解析实时股票池",
                "provenance": {
                    "actual_source": "tencent",
                    "latest_trade_date": "2026-07-10",
                    "lag_days": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_INTRADAY_STATUS", str(status_path))

    source = write_home_snapshot._snapshot_source(
        SimpleNamespace(
            run_status="blocked_by_circuit_breaker",
            effective_source="tencent",
            lag_days=0,
        ),
        SimpleNamespace(source_status={}),
        selected_date="2026-07-10",
    )

    assert source.status == "盘中刷新中：正在解析实时股票池"


def test_write_home_snapshot_builds_seven_day_index(monkeypatch) -> None:
    provider = _DateAwareProvider()
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    index = write_home_snapshot.build_home_snapshot_index(
        provider, signal_date="2026-07-10", task_id="intraday"
    )

    assert index.available_dates == (
        "2026-07-10",
        "2026-07-09",
        "2026-07-08",
        "2026-07-07",
        "2026-07-06",
        "2026-07-03",
        "2026-07-02",
    )
    assert index.snapshot_for_date("2026-07-09") is not None
    assert index.snapshot_for_date("2026-07-04") is None


def test_snapshot_dates_excludes_uncompleted_trading_day(monkeypatch) -> None:
    monkeypatch.setattr(
        write_home_snapshot,
        "latest_completed_trading_day",
        lambda: date(2026, 7, 9),
    )

    dates = write_home_snapshot._snapshot_dates(
        SimpleNamespace(available_dates=("2026-07-10", "2026-07-09", "2026-07-08")),
        "2026-07-09",
    )

    assert dates == ("2026-07-09", "2026-07-08")


def test_snapshot_dates_excludes_old_gap_from_live_history(monkeypatch) -> None:
    monkeypatch.setattr(
        write_home_snapshot,
        "latest_completed_trading_day",
        lambda: date(2026, 8, 13),
    )

    dates = write_home_snapshot._snapshot_dates(
        SimpleNamespace(available_dates=("2026-08-14", "2026-07-20")),
        "2026-08-14",
    )

    assert dates == ("2026-08-14",)


def test_home_snapshot_excludes_uncompleted_date_from_final_output(monkeypatch) -> None:
    provider = _DateAwareProvider()
    original_payload = provider.home_digest_payload

    def payload_with_uncompleted_date(task_id: str, signal_date: str = ""):
        payload = original_payload(task_id, signal_date)
        payload.task_view.available_dates = ("2026-07-10", "2026-07-09")
        return payload

    monkeypatch.setattr(provider, "home_digest_payload", payload_with_uncompleted_date)
    monkeypatch.setattr(
        write_home_snapshot,
        "latest_completed_trading_day",
        lambda: date(2026, 7, 9),
    )
    monkeypatch.setattr(
        write_home_snapshot,
        "today_shanghai",
        lambda: date(2026, 7, 10),
    )

    snapshot = write_home_snapshot.build_home_snapshot(provider, task_id="intraday")

    assert snapshot.available_dates == ("2026-07-10", "2026-07-09")


def test_merge_home_snapshot_index_drops_uncompleted_date(monkeypatch) -> None:
    provider = _DateAwareProvider()
    existing = write_home_snapshot.build_home_snapshot_index(
        provider, signal_date="2026-07-10", task_id="intraday"
    )
    refreshed = write_home_snapshot.build_home_snapshot_index(
        provider, signal_date="2026-07-09", task_id="intraday"
    )
    monkeypatch.setattr(
        write_home_snapshot,
        "latest_completed_trading_day",
        lambda: date(2026, 7, 9),
    )

    merged = write_home_snapshot.merge_home_snapshot_index(existing, refreshed)

    assert merged.available_dates == (
        "2026-07-09",
        "2026-07-08",
        "2026-07-07",
        "2026-07-06",
        "2026-07-03",
        "2026-07-02",
    )


def test_merge_home_snapshot_index_preserves_unrequested_history() -> None:
    provider = _DateAwareProvider()
    existing = write_home_snapshot.build_home_snapshot_index(
        provider, signal_date="2026-07-10", task_id="intraday"
    )
    refreshed = write_home_snapshot.build_home_snapshot_index(
        provider, signal_date="2026-07-09", task_id="intraday"
    )

    merged = write_home_snapshot.merge_home_snapshot_index(existing, refreshed)

    assert merged.selected_date == "2026-07-09"
    refreshed_selected = refreshed.snapshot_for_date("2026-07-09")
    merged_selected = merged.snapshot_for_date("2026-07-09")
    assert merged_selected.candidates == refreshed_selected.candidates
    assert merged_selected.debates == refreshed_selected.debates
    historical = merged.snapshot_for_date("2026-07-10")
    original = existing.snapshot_for_date("2026-07-10")
    assert historical.candidates == original.candidates
    assert historical.available_dates == merged.available_dates


def test_home_snapshot_index_keeps_existing_history_without_rebuilding_it(
    monkeypatch,
) -> None:
    provider = _DateAwareProvider()
    existing = write_home_snapshot.build_home_snapshot_index(
        provider, signal_date="2026-07-10", task_id="intraday"
    )
    current = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-11", task_id="intraday"
    )
    current = replace(current, available_dates=("2026-07-11", "2026-07-10"))

    def fail_historical(*_args, **_kwargs):
        raise AssertionError("existing history must not be rebuilt")

    monkeypatch.setattr(write_home_snapshot, "build_home_snapshot", fail_historical)

    index = write_home_snapshot.build_home_snapshot_index(
        provider,
        task_id="intraday",
        initial_snapshot=current,
        existing_index=existing,
    )

    assert index.available_dates == ("2026-07-11", "2026-07-10")
    assert index.snapshot_for_date("2026-07-10") == existing.snapshot_for_date(
        "2026-07-10"
    )


def test_home_snapshot_index_keeps_current_snapshot_when_new_history_is_missing(
    monkeypatch,
) -> None:
    provider = _DateAwareProvider()
    current = write_home_snapshot.build_home_snapshot(
        provider, signal_date="2026-07-11", task_id="intraday"
    )
    current = replace(current, available_dates=("2026-07-11", "2026-07-10"))

    def missing_history(*_args, **_kwargs):
        raise DataError("historical artifact missing")

    monkeypatch.setattr(write_home_snapshot, "build_home_snapshot", missing_history)

    index = write_home_snapshot.build_home_snapshot_index(
        provider,
        task_id="intraday",
        initial_snapshot=current,
    )

    assert index.available_dates == (
        "2026-07-11",
        "2026-07-10",
        "2026-07-09",
        "2026-07-08",
        "2026-07-07",
        "2026-07-06",
        "2026-07-03",
    )
    assert index.snapshot_for_date("2026-07-11") == current
    missing = index.snapshot_for_date("2026-07-10")
    assert missing is not None
    assert missing.candidates == ()
    assert missing.debates == ()
    assert missing.variants == ()
    assert missing.message_status == "历史归档缺失"
    assert missing.research_chain.blocker.endswith("不使用其他日期数据代填。")


def test_write_home_snapshot_cli_honors_output_date_and_task_id(
    monkeypatch, tmp_path, capsys
) -> None:
    provider = _DateAwareProvider()
    monkeypatch.setattr(write_home_snapshot, "DashboardDataProvider", lambda: provider)
    output = tmp_path / "snapshot.json"
    index_output = tmp_path / "snapshot-index.json"

    result = write_home_snapshot.main(
        [
            "--output",
            str(output),
            "--date",
            "2026-07-10",
            "--task-id",
            "intraday",
            "--index-output",
            str(index_output),
        ]
    )

    snapshot = load_home_dashboard_snapshot(output)
    assert result == 0
    assert snapshot is not None
    assert snapshot.selected_date == "2026-07-10"
    assert provider.digest_calls == [
        ("intraday", date)
        for date in (
            "2026-07-10",
            "2026-07-09",
            "2026-07-08",
            "2026-07-07",
            "2026-07-06",
            "2026-07-03",
            "2026-07-02",
        )
    ]
    assert "task=intraday" in capsys.readouterr().out
    assert load_home_snapshot_index(index_output) is not None


def test_write_home_snapshot_cli_writes_default_index_and_env_overrides_path(
    monkeypatch, tmp_path
) -> None:
    provider = _DateAwareProvider()
    monkeypatch.setattr(write_home_snapshot, "DashboardDataProvider", lambda: provider)
    monkeypatch.setattr(
        write_home_snapshot,
        "now_shanghai",
        lambda: datetime(2026, 7, 10, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    output = tmp_path / "snapshot.json"
    index_output = tmp_path / "snapshot-index.json"
    monkeypatch.setenv("AQSP_HOME_SNAPSHOT_INDEX_PATH", str(index_output))

    result = write_home_snapshot.main(
        [
            "--output",
            str(output),
            "--date",
            "2026-07-10",
            "--task-id",
            "intraday",
        ]
    )

    index = load_home_snapshot_index(index_output)
    assert result == 0
    assert index is not None
    assert len(index.days) == 7


def test_write_home_snapshot_cli_rejects_shared_snapshot_and_index_path(
    monkeypatch, tmp_path
) -> None:
    provider = _DateAwareProvider()
    monkeypatch.setattr(write_home_snapshot, "DashboardDataProvider", lambda: provider)
    output = tmp_path / "same.json"

    with pytest.raises(ValueError, match="different paths"):
        write_home_snapshot.main(
            [
                "--output",
                str(output),
                "--index-output",
                str(output),
                "--date",
                "2026-07-10",
            ]
        )
