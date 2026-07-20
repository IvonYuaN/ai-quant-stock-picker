from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from scripts import backfill_intraday_debate


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _write_candidates(path: Path, symbols: tuple[str, ...]) -> None:
    fieldnames = (
        "symbol",
        "name",
        "date",
        "close",
        "score",
        "rating",
        "strategies",
        "reasons",
        "risks",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, symbol in enumerate(symbols):
            writer.writerow(
                {
                    "symbol": symbol,
                    "name": f"测试标的{index + 1}",
                    "date": "2026-07-10",
                    "close": str(100 + index),
                    "score": str(80 - index),
                    "rating": "watch",
                    "strategies": "momentum",
                    "reasons": "放量突破",
                    "risks": "追高波动",
                }
            )


def _patch_runtime(
    monkeypatch,
    *,
    fail_symbol: str = "",
    max_rounds: int = 1,
    enabled: bool = True,
    captured: dict[str, object] | None = None,
) -> None:
    fixed_now = datetime(2026, 7, 10, 14, 30, tzinfo=SHANGHAI_TZ)
    monkeypatch.setattr(backfill_intraday_debate, "now_shanghai", lambda: fixed_now)
    monkeypatch.setattr(
        backfill_intraday_debate,
        "load_debate_runtime_config",
        lambda task_id: SimpleNamespace(
            enabled=enabled,
            enable_llm=False,
            max_rounds=max_rounds,
            max_candidates=5,
            language="zh-CN",
            roles=("bull", "risk_control"),
            role_runtime=(),
        ),
    )
    monkeypatch.setattr(
        backfill_intraday_debate,
        "load_thresholds",
        lambda: SimpleNamespace(version="test-thresholds"),
    )
    monkeypatch.setattr(
        backfill_intraday_debate,
        "_resolve_pick_debate_roles",
        lambda runtime, *, pick, market_context_lines: tuple(runtime.roles),
    )

    class _Coordinator:
        def run_debate(self, pick, frame, signal_date, *, market_context_lines=()):
            if pick.symbol == fail_symbol:
                raise RuntimeError("simulated candidate failure")
            if captured is not None:
                captured["market_context_lines"] = tuple(market_context_lines)
            return SimpleNamespace(
                debate_id=f"debate-{pick.symbol}",
                symbol=pick.symbol,
                name=pick.name,
                original_score=pick.score,
                rating=pick.rating,
                recommended_adjustment="keep",
                disagreement_score=0.1,
            )

    monkeypatch.setattr(
        backfill_intraday_debate,
        "_build_debate_coordinator",
        lambda *args, **kwargs: _Coordinator(),
    )
    monkeypatch.setattr(
        backfill_intraday_debate,
        "serialize_debate_result",
        lambda result: {
            "debate_id": result.debate_id,
            "symbol": result.symbol,
            "name": result.name,
            "original_score": result.original_score,
            "rating": result.rating,
            "recommended_adjustment": result.recommended_adjustment,
            "disagreement_score": result.disagreement_score,
            "data_status": "available",
            "final_consensus": "bullish",
            "final_vote": {"bull": "bullish", "risk_control": "bearish"},
            "support_points": ["放量突破"],
            "opposition_points": ["风险条件需要确认"],
            "risk_warnings": ["若冲高回落则降级"],
            "next_trigger": "确认盘中承接",
            "falsifiable_conditions": ["若冲高回落则降级"],
            "advisory_only": True,
            "deterministic_score": result.original_score,
            "deterministic_score_unchanged": True,
            "rounds": [
                {
                    "round_num": 1,
                    "opinions": [
                        {
                            "agent_id": "bull-agent",
                            "role": "bull",
                            "stance": "bullish",
                            "arguments": ["放量突破"],
                            "risk_factors": [],
                            "opportunity_factors": ["量价延续"],
                        },
                        {
                            "agent_id": "risk-agent",
                            "role": "risk_control",
                            "stance": "bearish",
                            "arguments": ["风险条件需要确认"],
                            "risk_factors": ["若冲高回落则降级"],
                            "opportunity_factors": [],
                        },
                    ],
                },
                {
                    "round_num": 2,
                    "opinions": [
                        {
                            "agent_id": "bull-agent",
                            "role": "bull",
                            "stance": "bullish",
                            "arguments": ["放量突破"],
                            "risk_factors": [],
                            "opportunity_factors": ["量价延续"],
                            "counterarguments": [],
                            "peer_reviewed_roles": ["risk_control"],
                            "rebuttal_records": [],
                        },
                        {
                            "agent_id": "risk-agent",
                            "role": "risk_control",
                            "stance": "bearish",
                            "arguments": ["风险条件需要确认"],
                            "risk_factors": ["若冲高回落则降级"],
                            "opportunity_factors": [],
                            "counterarguments": ["已质询多头放量延续"],
                            "counterargument_roles": ["bull"],
                            "peer_reviewed_roles": ["bull"],
                            "rebuttal_records": [
                                {
                                    "challenged_role": "bull",
                                    "challenged_claim": "放量突破",
                                    "rebuttal_reason": "风险条件需要确认，若失效则降级",
                                    "challenged_stance": "bullish",
                                    "opposing_stance": "bearish",
                                }
                            ],
                        },
                    ],
                },
            ],
        },
    )


def _read_status(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_debate_quality_gate_rejects_failure_and_any_quality_issue() -> None:
    assert "failure" in backfill_intraday_debate._debate_payload_quality_failure(
        {"failure": "讨论执行失败"}
    )
    assert "missing_cross_market_viewpoint" in (
        backfill_intraday_debate._debate_payload_quality_failure(
            {"debate_quality_issues": ["missing_cross_market_viewpoint"]}
        )
    )
    assert (
        backfill_intraday_debate._debate_payload_quality_failure(
            {"advisory_boundary_ok": False}
        )
        == "debate advisory boundary is not valid"
    )
    assert backfill_intraday_debate._debate_payload_quality_failure({}) == ""


def test_debate_quality_gate_rejects_neutral_only_opposition_payload(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    from aqsp.briefing.agent_roles import AgentRole
    from aqsp.briefing.debate import AShareDebateCoordinator

    pick = backfill_intraday_debate._pick_from_row(
        {
            "symbol": "000001",
            "name": "测试标的",
            "date": "2026-07-10",
            "close": "10.5",
            "score": "72",
            "rating": "watch",
            "reasons": "放量突破",
        }
    )
    result = AShareDebateCoordinator(
        max_rounds=2,
        roles=(AgentRole.BULL, AgentRole.BEAR),
    ).run_debate(
        pick,
        pd.DataFrame({"close": [100.0, 101.0]}),
        signal_date=pick.date,
    )
    payload = backfill_intraday_debate.serialize_debate_result(result)

    failure = backfill_intraday_debate._debate_payload_quality_failure(
        payload,
        candidate=pick,
        expected_roles=("bull", "bear"),
        expected_signal_date=pick.date,
    )

    assert "missing_real_opposition" in failure


def test_backfill_continues_after_candidate_failure_and_persists_success(
    tmp_path: Path, monkeypatch
) -> None:
    input_csv = tmp_path / "intraday_latest.csv"
    output_path = tmp_path / "debate_results.jsonl"
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "backfill.lock"
    _write_candidates(input_csv, ("000001", "000002"))
    _patch_runtime(monkeypatch, fail_symbol="000002")

    count = backfill_intraday_debate.run_backfill(
        input_csv=input_csv,
        output_path=output_path,
        task_id="intraday",
        max_candidates=5,
        force=True,
        status_path=status_path,
        lock_path=lock_path,
    )

    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    status = _read_status(status_path)
    assert count == 1
    assert [row["symbol"] for row in rows] == ["000001"]
    assert status["status"] == "failed"
    assert status["candidate_count"] == 2
    assert status["succeeded_count"] == 1
    assert status["failed_count"] == 1
    assert status["failed_candidates"][0]["symbol"] == "000002"
    assert rows[0]["run_id"] == status["run_id"]
    failed_state = next(
        item for item in status["candidate_states"] if item["symbol"] == "000002"
    )
    assert failed_state["status"] == "failed"
    assert failed_state["attempts"] == 2
    assert failed_state["retryable"] is True
    assert not lock_path.exists()


def test_load_intraday_picks_includes_observation_only_only_when_explicit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "intraday_latest.csv"
    path.write_text(
        "symbol,name,date,score,rating,reasons,quality_gate_action,observation_only\n"
        "000001,测试,2026-07-10,80,watch,放量突破,observe,true\n",
        encoding="utf-8",
    )

    assert backfill_intraday_debate.load_intraday_picks(path, 5) == []
    assert (
        len(
            backfill_intraday_debate.load_intraday_picks(
                path, 5, include_observation_only=True
            )
        )
        == 1
    )


def test_backfill_force_runs_rules_when_global_debate_is_disabled(
    tmp_path: Path, monkeypatch
) -> None:
    input_csv = tmp_path / "intraday_latest.csv"
    output_path = tmp_path / "debate_results.jsonl"
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "backfill.lock"
    _write_candidates(input_csv, ("000001",))
    _patch_runtime(monkeypatch, enabled=False)
    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "false")

    count = backfill_intraday_debate.run_backfill(
        input_csv=input_csv,
        output_path=output_path,
        task_id="intraday",
        max_candidates=5,
        force=True,
        status_path=status_path,
        lock_path=lock_path,
    )

    assert count == 1
    assert _read_status(status_path)["status"] == "succeeded"
    assert output_path.exists()


def test_backfill_retries_candidate_and_persists_attempt_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    input_csv = tmp_path / "intraday_latest.csv"
    output_path = tmp_path / "debate_results.jsonl"
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "backfill.lock"
    _write_candidates(input_csv, ("000001",))
    _patch_runtime(monkeypatch)
    calls = {"000001": 0}

    class _RetryCoordinator:
        def run_debate(self, pick, frame, signal_date, *, market_context_lines=()):
            calls[pick.symbol] += 1
            if calls[pick.symbol] == 1:
                raise TimeoutError("temporary provider timeout")
            return SimpleNamespace(
                debate_id=f"debate-{pick.symbol}",
                symbol=pick.symbol,
                name=pick.name,
                original_score=pick.score,
                rating=pick.rating,
                recommended_adjustment="keep",
                disagreement_score=0.1,
            )

    monkeypatch.setattr(
        backfill_intraday_debate,
        "_build_debate_coordinator",
        lambda *args, **kwargs: _RetryCoordinator(),
    )

    count = backfill_intraday_debate.run_backfill(
        input_csv=input_csv,
        output_path=output_path,
        task_id="intraday",
        max_candidates=5,
        force=True,
        status_path=status_path,
        lock_path=lock_path,
    )

    status = _read_status(status_path)
    state = status["candidate_states"][0]
    assert count == 1
    assert calls["000001"] == 2
    assert state["status"] == "succeeded"
    assert state["attempts"] == 2
    assert state["previous_status"] == ""
    row = json.loads(output_path.read_text(encoding="utf-8"))
    assert row["advisory_only"] is True
    assert row["adjusted_score_is_advisory"] is True
    assert row["deterministic_score"] == row["original_score"]


def test_backfill_recovers_stale_lock_and_finishes_succeeded(
    tmp_path: Path, monkeypatch
) -> None:
    input_csv = tmp_path / "intraday_latest.csv"
    output_path = tmp_path / "debate_results.jsonl"
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "backfill.lock"
    _write_candidates(input_csv, ("000001",))
    _patch_runtime(monkeypatch)
    lock_path.mkdir()
    (lock_path / "meta.json").write_text(
        json.dumps(
            {
                "pid": 99999999,
                "run_id": "old-run",
                "started_at": "2026-07-10T12:00:00+08:00",
                "updated_at": "2026-07-10T12:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    count = backfill_intraday_debate.run_backfill(
        input_csv=input_csv,
        output_path=output_path,
        task_id="intraday",
        max_candidates=5,
        force=True,
        status_path=status_path,
        lock_path=lock_path,
        stale_lock_seconds=1,
    )

    status = _read_status(status_path)
    assert count == 1
    assert status["status"] == "succeeded"
    assert status["stale_recovered"] is True
    assert status["succeeded_count"] == 1
    assert not lock_path.exists()


def test_backfill_does_not_remove_active_lock_and_marks_failed(
    tmp_path: Path, monkeypatch
) -> None:
    input_csv = tmp_path / "intraday_latest.csv"
    output_path = tmp_path / "debate_results.jsonl"
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "backfill.lock"
    _write_candidates(input_csv, ("000001",))
    _patch_runtime(monkeypatch)
    lock_path.mkdir()
    (lock_path / "meta.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "run_id": "active-run",
                "started_at": "2026-07-10T14:29:00+08:00",
                "updated_at": "2026-07-10T14:29:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    count = backfill_intraday_debate.run_backfill(
        input_csv=input_csv,
        output_path=output_path,
        task_id="intraday",
        max_candidates=5,
        force=True,
        status_path=status_path,
        lock_path=lock_path,
        stale_lock_seconds=3600,
    )

    status = _read_status(status_path)
    assert count == 0
    assert status["status"] == "failed"
    assert "active backfill lock" in str(status["detail"])
    assert lock_path.exists()


def test_backfill_second_round_records_new_evidence_and_is_complete(
    tmp_path: Path, monkeypatch
) -> None:
    input_csv = tmp_path / "intraday_latest.csv"
    output_path = tmp_path / "debate_results.jsonl"
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "backfill.lock"
    captured: dict[str, object] = {}
    _write_candidates(input_csv, ("000001",))
    _patch_runtime(
        monkeypatch,
        max_rounds=2,
        captured=captured,
    )

    count = backfill_intraday_debate.run_backfill(
        input_csv=input_csv,
        output_path=output_path,
        task_id="intraday",
        max_candidates=5,
        force=True,
        status_path=status_path,
        lock_path=lock_path,
    )

    row = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert count == 1
    assert row["debate_rounds_requested"] == 2
    assert row["debate_rounds_completed"] == 2
    assert [item["round_num"] for item in row["rounds"]] == [1, 2]
    assert row["debate_reconsideration"]["round_num"] == 2
    assert "synthetic_context" in row["debate_reconsideration"]["new_evidence"][0]
    assert any("第2轮复议新证据" in line for line in captured["market_context_lines"])


def test_debate_frame_prefers_complete_runtime_ohlcv_over_synthetic_context() -> None:
    pick = backfill_intraday_debate._pick_from_row(
        {
            "symbol": "000001",
            "name": "测试标的",
            "date": "2026-07-10",
            "close": "10.5",
            "open": "10.0",
            "high": "11.0",
            "low": "9.8",
            "volume": "12345",
            "amount": "130000",
        }
    )

    frame, data_context = backfill_intraday_debate._debate_frame(pick)

    assert data_context == "runtime_ohlcv"
    assert len(frame) == 1
    assert frame.iloc[0]["open"] == 10.0
    assert frame.iloc[0]["volume"] == 12345.0


def test_market_context_lines_for_pick_preserve_news_direction_counts_and_provenance() -> (
    None
):
    pick = backfill_intraday_debate._pick_from_row(
        {
            "symbol": "000001",
            "name": "测试标的",
            "date": "2026-07-10",
            "close": "10.5",
            "news_catalyst_lead": "上游材料涨价，产业链供给收紧",
            "news_catalyst_source": "交易所公告",
            "news_catalyst_published_at": "2026-07-10T09:05:00+08:00",
            "news_catalyst_supports": "上游涨价",
            "news_catalyst_opposes": "下游需求尚未验证",
            "news_catalyst_needs_review": "板块扩散待确认",
            "news_catalyst_support_count": "1",
            "news_catalyst_oppose_count": "1",
            "news_catalyst_review_count": "1",
        }
    )

    lines = backfill_intraday_debate._market_context_lines_for_pick(
        pick,
        run_market_context_lines=(),
    )

    assert "消息支持: 上游涨价" in lines
    assert "消息压力: 下游需求尚未验证" in lines
    assert "消息待复核: 板块扩散待确认" in lines
    assert "消息证据堆栈: 支持 1 条｜反对 1 条｜待复核 1 条" in lines
    assert any(line.startswith("消息溯源: 交易所公告 /") for line in lines)


def test_backfill_keeps_same_candidate_isolated_by_task_id(
    tmp_path: Path, monkeypatch
) -> None:
    input_csv = tmp_path / "intraday_latest.csv"
    output_path = tmp_path / "debate_results.jsonl"
    status_path = tmp_path / "status.json"
    lock_path = tmp_path / "backfill.lock"
    _write_candidates(input_csv, ("000001",))
    _patch_runtime(monkeypatch)

    for task_id in ("intraday", "closing_review"):
        assert (
            backfill_intraday_debate.run_backfill(
                input_csv=input_csv,
                output_path=output_path,
                task_id=task_id,
                max_candidates=5,
                force=True,
                status_path=status_path,
                lock_path=lock_path,
            )
            == 1
        )

    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {row["task_id"] for row in rows} == {"intraday", "closing_review"}
    assert len({row["candidate_fingerprint"] for row in rows}) == 1
    assert _read_status(status_path)["task_id"] == "closing_review"
