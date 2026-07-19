from __future__ import annotations

import csv
import json
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts import backfill_intraday_debate
from scripts.generate_sample_debate import generate_sample_debate_data
from scripts.generate_cold_start_signals import generate_mock_signal
from scripts.manage_data_lifecycle import analyze_debate_file, clean_old_debates
from scripts.merge_server_ledgers import merge_ledgers
from scripts import cleanup_ledger

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def test_backfill_intraday_debate_default_coverage_matches_runtime_limit() -> None:
    assert backfill_intraday_debate.DEFAULT_MAX_CANDIDATES == 5


def test_backfill_intraday_debate_writes_current_task_records(
    tmp_path, monkeypatch
) -> None:
    fixed_now = datetime(2026, 7, 10, 14, 30, 0, tzinfo=SHANGHAI_TZ)
    input_csv = tmp_path / "intraday_latest.csv"
    output_path = tmp_path / "debate_results.jsonl"
    fieldnames = [
        "symbol",
        "name",
        "date",
        "close",
        "score",
        "rating",
        "entry_type",
        "ideal_buy",
        "stop_loss",
        "take_profit",
        "position",
        "strategies",
        "reasons",
        "risks",
        "run_market_context_overview",
        "run_market_context_lines",
        "cross_market_primary_theme",
        "cross_market_action",
        "cross_market_chain_summary",
        "cross_market_validation_signals",
        "cross_market_invalidation_signals",
        "cross_market_summaries",
        "cross_market_evidence_stack_summary",
        "news_catalyst_lead",
    ]
    with input_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "__RUN__",
                "run_market_context_overview": "美股科技风险偏好修复",
                "run_market_context_lines": "海外风险: 纳指期货走强；北向资金: 近5日偏强",
            }
        )
        writer.writerow(
            {
                "symbol": "603019",
                "name": "中科曙光",
                "date": "2026-07-10",
                "close": "109.55",
                "score": "71.57",
                "rating": "strong_buy_candidate",
                "entry_type": "relative_strength",
                "ideal_buy": "109.55",
                "stop_loss": "91.326",
                "take_profit": "142.354",
                "position": "30%-30%",
                "strategies": "rps_momentum",
                "reasons": "MA多头排列",
                "risks": "前一交易日振幅过大",
                "cross_market_primary_theme": "海外AI算力映射",
                "cross_market_action": "优先复核",
                "cross_market_chain_summary": "英伟达走强｜确认 算力链竞价承接｜失效 高开低走",
                "cross_market_validation_signals": "算力链竞价承接；成交额放大",
                "cross_market_invalidation_signals": "高开低走；板块无扩散",
                "cross_market_summaries": "传导推演[海外AI算力映射]: 英伟达走强传导A股算力链",
                "cross_market_evidence_stack_summary": "同向 2 条｜反向 0 条",
                "news_catalyst_lead": "603019 中科曙光 偏多｜AI算力｜海外龙头上行",
            }
        )
    output_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "debate_id": "main-chain-recent",
                        "symbol": "000938",
                        "related_signal_date": "2026-07-09",
                        "debate_date": "2026-07-09",
                        "task_id": "main_chain",
                        "candidate_fingerprint": "main-chain-fingerprint",
                        "created_at": "2026-07-09T18:00:00+08:00",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "debate_id": "expired",
                        "symbol": "000001",
                        "related_signal_date": "2026-06-01",
                        "debate_date": "2026-06-01",
                        "task_id": "main_chain",
                        "candidate_fingerprint": "expired-fingerprint",
                        "created_at": "2026-06-01T18:00:00+08:00",
                    },
                    ensure_ascii=False,
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(backfill_intraday_debate, "now_shanghai", lambda: fixed_now)
    monkeypatch.setattr(
        backfill_intraday_debate,
        "load_debate_runtime_config",
        lambda task_id: SimpleNamespace(
            enabled=False,
            enable_llm=False,
            max_rounds=1,
            max_candidates=3,
            language="zh-CN",
            roles=("bull", "risk_control", "cross_market"),
            role_runtime=(),
        ),
    )
    monkeypatch.setattr(
        backfill_intraday_debate,
        "load_thresholds",
        lambda: SimpleNamespace(version="test-thresholds"),
    )
    captured: dict[str, tuple[str, ...]] = {}

    def _resolve_roles(runtime, *, pick, market_context_lines):
        captured["resolved_context"] = tuple(market_context_lines)
        return tuple(runtime.roles)

    monkeypatch.setattr(
        backfill_intraday_debate,
        "_resolve_pick_debate_roles",
        _resolve_roles,
    )

    class _Coordinator:
        def run_debate(
            self,
            pick,
            df,
            signal_date,
            *,
            market_context_lines=(),
            task_id=None,
        ):
            captured["debate_context"] = tuple(market_context_lines)
            captured["task_id"] = task_id
            return SimpleNamespace(
                debate_id="debate-1",
                symbol=pick.symbol,
                name=pick.name,
                original_score=pick.score,
                rating=pick.rating,
                recommended_adjustment="keep",
                disagreement_score=0.2,
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
            "final_consensus": "split",
            "final_vote": {
                "bull": "bullish",
                "risk_control": "bearish",
                "cross_market": "neutral",
            },
            "support_points": ["先观察承接"],
            "opposition_points": ["高开低走则失效"],
            "risk_warnings": ["高开低走则失效"],
            "next_trigger": "确认板块承接",
            "falsifiable_conditions": ["高开低走则失效"],
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
                            "confidence": 0.7,
                            "arguments": ["先观察承接。"],
                            "risk_factors": [],
                            "opportunity_factors": ["量价承接"],
                        },
                        {
                            "agent_id": "risk-agent",
                            "role": "risk_control",
                            "stance": "bearish",
                            "confidence": 0.7,
                            "arguments": ["高开低走则失效"],
                            "risk_factors": ["高开低走则失效"],
                            "opportunity_factors": [],
                        },
                        {
                            "agent_id": "cross-agent",
                            "role": "cross_market",
                            "stance": "neutral",
                            "confidence": 0.5,
                            "arguments": ["等待A股映射确认"],
                            "risk_factors": ["海外叙事需盘中验证"],
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
                            "confidence": 0.7,
                            "arguments": ["先观察承接。"],
                            "risk_factors": [],
                            "opportunity_factors": ["量价承接"],
                            "peer_reviewed_roles": ["risk_control"],
                            "counterarguments": [],
                            "rebuttal_records": [],
                        },
                        {
                            "agent_id": "risk-agent",
                            "role": "risk_control",
                            "stance": "bearish",
                            "confidence": 0.7,
                            "arguments": ["高开低走则失效"],
                            "risk_factors": ["高开低走则失效"],
                            "opportunity_factors": [],
                            "counterarguments": ["已质询承接延续"],
                            "counterargument_roles": ["bull"],
                            "peer_reviewed_roles": ["bull"],
                            "rebuttal_records": [
                                {
                                    "challenged_role": "bull",
                                    "challenged_claim": "先观察承接。",
                                    "rebuttal_reason": "高开低走则失效",
                                    "challenged_stance": "bullish",
                                    "opposing_stance": "bearish",
                                }
                            ],
                        },
                        {
                            "agent_id": "cross-agent",
                            "role": "cross_market",
                            "stance": "neutral",
                            "confidence": 0.5,
                            "arguments": ["等待A股映射确认"],
                            "risk_factors": ["海外叙事需盘中验证"],
                            "opportunity_factors": [],
                            "counterarguments": ["等待A股映射确认"],
                            "peer_reviewed_roles": ["bull"],
                            "rebuttal_records": [],
                        },
                    ],
                },
            ],
        },
    )

    count = backfill_intraday_debate.run_backfill(
        input_csv=input_csv,
        output_path=output_path,
        task_id="intraday",
        max_candidates=3,
        force=True,
    )

    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert count == 1
    rows_by_symbol = {row["symbol"]: row for row in rows}
    current_row = rows_by_symbol["603019"]
    assert set(rows_by_symbol) == {"000938", "603019"}
    assert current_row["task_id"] == "intraday"
    assert current_row["related_signal_date"] == "2026-07-10"
    assert current_row["candidate_signal_date"] == "2026-07-10"
    assert current_row["candidate_fingerprint"]
    assert current_row["created_at"] == "2026-07-10T14:30:00+08:00"
    assert current_row["debate_context_quality"] == "structured_context"
    assert current_row["debate_data_context"] == "synthetic_context"
    assert "缺少完整盘中 OHLCV" in current_row["debate_data_context_warning"]
    assert "debate_context_warning" not in current_row
    assert any("海外AI算力映射" in line for line in current_row["market_context_lines"])
    assert any("消息催化" in line for line in current_row["market_context_lines"])
    assert (
        tuple(captured["debate_context"][: len(captured["resolved_context"])])
        == (captured["resolved_context"])
    )
    assert any(
        line.startswith("第2轮复议新证据:") for line in captured["debate_context"]
    )
    assert captured["debate_context"]


def test_backfill_intraday_debate_restores_tuple_metrics_from_csv() -> None:
    pick = backfill_intraday_debate._pick_from_row(
        {
            "symbol": "603019",
            "name": "中科曙光",
            "date": "2026-07-10",
            "close": "109.55",
            "score": "71.57",
            "cross_market_validation_signals": "算力链竞价承接；成交额放大",
            "cross_market_rule_ids": "['ai_compute', 'risk_on']",
            "news_catalyst_supports": "海外龙头上行|政策支持",
            "cross_market_support_event_count": "2",
        }
    )

    assert pick.metrics["cross_market_validation_signals"] == (
        "算力链竞价承接",
        "成交额放大",
    )
    assert pick.metrics["cross_market_rule_ids"] == ("ai_compute", "risk_on")
    assert pick.metrics["news_catalyst_supports"] == ("海外龙头上行", "政策支持")
    assert pick.metrics["cross_market_support_event_count"] == 2


def test_generate_sample_debate_data_uses_research_safe_wording_and_timezone(
    tmp_path, monkeypatch
) -> None:
    fixed_now = datetime(2026, 6, 10, 9, 30, 0, tzinfo=SHANGHAI_TZ)
    monkeypatch.setattr(
        "scripts.generate_sample_debate.now_shanghai",
        lambda: fixed_now,
    )

    output_path = tmp_path / "debate_results.jsonl"
    generate_sample_debate_data(output_path=output_path)

    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 3
    assert all(row["created_at"].endswith("+08:00") for row in rows)
    assert all(row["debate_date"] == "2026-06-10" for row in rows)
    assert all("建议关注" not in row["final_consensus"] for row in rows)
    assert all("建议观望" not in row["final_consensus"] for row in rows)
    assert all("保持原评级" not in row["final_consensus"] for row in rows)
    assert all("辩论建议" not in row["adjustment_reason"] for row in rows)
    assert all(
        "买入" not in " ".join(row["rounds"][0]["opinions"][0]["arguments"])
        for row in rows
    )


def test_generate_cold_start_signal_uses_research_safe_disclaimer(
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 6, 10, 9, 30, 0, tzinfo=SHANGHAI_TZ)
    monkeypatch.setattr(
        "scripts.generate_cold_start_signals.now_shanghai",
        lambda: fixed_now,
    )

    signal = generate_mock_signal("600519", "2026-06-10", 1500.0, 80.0)

    assert signal["created_at"].endswith("+08:00")
    assert "不构成交易指令或投资建议" in " ".join(signal["risks"])
    assert "不构成投资建议" not in " ".join(signal["risks"])


def test_manage_data_lifecycle_uses_shanghai_clock_for_analysis_and_cleanup(
    tmp_path, monkeypatch
) -> None:
    fixed_now = datetime(2026, 6, 10, 9, 30, 0, tzinfo=SHANGHAI_TZ)
    monkeypatch.setattr(
        "scripts.manage_data_lifecycle.now_shanghai",
        lambda: fixed_now,
    )

    debate_path = tmp_path / "debate_results.jsonl"
    debate_path.write_text(
        "\n".join(
            [
                json.dumps({"symbol": "600519", "debate_date": "2026-06-01"}),
                json.dumps({"symbol": "000858", "debate_date": "2026-06-09"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    analysis = analyze_debate_file(debate_path)
    preview = clean_old_debates(debate_path, keep_days=5, dry_run=True)

    assert analysis["today"] == "2026-06-10"
    assert analysis["days_oldest"] == 9
    assert analysis["days_newest"] == 1
    assert preview["cutoff_date"] == "2026-06-05"
    assert preview["would_keep"] == 1
    assert preview["would_delete"] == 1


def test_merge_ledgers_backup_stamp_uses_shanghai_clock(tmp_path, monkeypatch) -> None:
    fixed_now = datetime(2026, 6, 10, 9, 30, 0, tzinfo=SHANGHAI_TZ)
    monkeypatch.setattr(
        "scripts.merge_server_ledgers.now_shanghai",
        lambda: fixed_now,
    )

    target = tmp_path / "predictions.jsonl"
    source = tmp_path / "ledger.jsonl"
    target.write_text(
        json.dumps({"signal_date": "2026-06-01", "symbol": "000001"}) + "\n",
        encoding="utf-8",
    )
    source.write_text(
        json.dumps({"signal_date": "2026-06-02", "symbol": "000002"}) + "\n",
        encoding="utf-8",
    )

    summary = merge_ledgers(target, source, backup=True)

    assert summary.backup_paths
    assert all("20260610-093000" in path.name for path in summary.backup_paths)


def test_runtime_output_scripts_use_atomic_writes() -> None:
    script_paths = (
        Path("scripts/run_production_walkforward_gate.py"),
        Path("scripts/merge_server_ledgers.py"),
        Path("scripts/render_dashboard.py"),
    )

    for path in script_paths:
        text = path.read_text(encoding="utf-8")
        # Dashboard output delegates the atomic write to the canonical entrypoint.
        assert "atomic_write_text" in text or (
            path.name == "render_dashboard.py" and "write_dashboard_artifact" in text
        )
        assert ".write_text(" not in text


def test_server_sync_and_runtime_publish_share_the_server_runtime_lock() -> None:
    script = Path("scripts/server_sync_and_run.sh").read_text(encoding="utf-8")

    assert "sync_runtime_files_to_server.py" in script
    assert 'LOCK_FILE="${LOCK_DIR}/server-runtime.lock"' in script


def test_runtime_maintenance_scripts_default_to_predictions_ledger() -> None:
    cold_text = Path("scripts/generate_cold_start_signals.py").read_text(
        encoding="utf-8"
    )
    cleanup_text = Path("scripts/cleanup_ledger.py").read_text(encoding="utf-8")

    assert 'default="data/predictions.jsonl"' in cold_text
    assert 'default="data/predictions.jsonl"' in cleanup_text


def test_cleanup_ledger_simulated_only_keeps_expired_real_rows(
    tmp_path, monkeypatch, capsys
) -> None:
    ledger = tmp_path / "predictions.jsonl"
    ledger.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "signal_date": "2026-01-01",
                        "symbol": "600000",
                        "status": "pending",
                    }
                ),
                json.dumps(
                    {
                        "signal_date": "2026-06-01",
                        "symbol": "600001",
                        "status": "pending",
                        "is_simulated": True,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cleanup_ledger, "project_root", tmp_path)
    monkeypatch.setattr(
        cleanup_ledger,
        "today_shanghai",
        lambda: datetime(2026, 6, 27, 9, 30, 0, tzinfo=SHANGHAI_TZ).date(),
    )

    exit_code = cleanup_ledger.main(
        [
            "--ledger",
            "predictions.jsonl",
            "--remove-simulated",
            "--simulated-only",
        ]
    )
    output = capsys.readouterr().out
    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert exit_code == 0
    assert "模式: 仅删除模拟信号" in output
    assert len(rows) == 1
    assert rows[0]["signal_date"] == "2026-01-01"
