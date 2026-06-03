from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from aqsp.research.summary import (
    ResearchFamilySummary,
    ResearchPipelineSummary,
    ResearchSummary,
)
from scripts.export_dashboard_db import export_db
from scripts.render_dashboard import (
    latest_candidate_date,
    read_candidates,
    read_ledger_rows,
    render_dashboard,
    summarize_ledger,
    summarize_paper,
)


def test_dashboard_renders_candidates_and_ledger_stats_when_inputs_exist(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "latest.csv"
    ledger_path = tmp_path / "predictions.jsonl"
    pd.DataFrame(
        [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "score": "71",
                "rating": "buy_candidate",
                "strategies": "ma_pullback",
                "ideal_buy": "1500",
                "close": "1498",
                "stop_loss": "1420",
                "take_profit": "1680",
                "position": "10%-30%",
                "reasons": "趋势回踩",
                "risks": "RSI偏热",
            }
        ]
    ).to_csv(csv_path, index=False)
    ledger_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "signal_date": "2026-05-28",
                        "symbol": "600519",
                        "score": 71,
                        "status": "pending",
                        "thresholds_version": "1.0.0",
                        "run_requested_source": "auto",
                        "run_actual_source": "eastmoney",
                        "run_source_freshness_tier": "realtime",
                        "run_source_coverage_tier": "multi_dimensional",
                        "run_source_health_label": "fallback",
                        "run_source_health_message": "fallback 到 eastmoney；plan成功/失败 5/1，源成功/失败 5/0",
                        "run_fallback_used": True,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "signal_date": "2026-05-20",
                        "symbol": "300750",
                        "score": 63,
                        "status": "validated",
                        "win": True,
                        "return_pct": 2.4,
                        "thresholds_version": "1.0.0",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    candidates = read_candidates(csv_path)
    rows = read_ledger_rows(ledger_path)
    paper_rows = [
        {
            "symbol": "600519",
            "status": "open",
            "entry_date": "2026-05-29",
            "entry_price": 1501,
        },
        {
            "symbol": "300750",
            "status": "closed",
            "return_pct": 1.2,
        },
        {
            "symbol": "000001",
            "status": "pending_entry",
            "signal_date": "2026-05-29",
        },
    ]
    html = render_dashboard(candidates, rows, "测试面板", paper_rows)
    stats = summarize_ledger(rows)
    paper_stats = summarize_paper(paper_rows)

    assert stats.total == 2
    assert stats.pending == 1
    assert stats.validated == 1
    assert paper_stats.open_positions == 1
    assert paper_stats.closed == 1
    assert paper_stats.pending_entry == 1
    assert "测试面板" in html
    assert "600519" in html
    assert "贵州茅台" in html
    assert "阈值版本 1.0.0" in html
    assert "候选数据日" in html
    assert "虚拟盘" in html
    assert "虚拟持仓" in html
    assert "等待入场数据" in html
    assert "等待 2026-05-29 次日开盘" in html
    assert "数据源状态" in html
    assert "fallback" in html
    assert "通知级别 warning" in html
    assert "数据源 fallback" in html
    assert "通知" in html
    assert "auto → eastmoney" in html
    assert "fallback 到 eastmoney" in html
    assert "fallback 数据源生成" in html
    assert "/ 观察候选" in html
    assert "buy_candidate" not in html


def test_dashboard_renders_research_absorption_panel() -> None:
    summary = ResearchSummary(
        generated_at="",
        total_findings=113,
        pipeline_summaries=(
            ResearchPipelineSummary(
                pipeline="data_source",
                total=24,
                p1=14,
                top_repo="mpquant/Ashare",
            ),
            ResearchPipelineSummary(
                pipeline="strategy",
                total=22,
                p1=10,
                top_repo="sngyai/Sequoia-X",
            ),
        ),
        absorbed_families=(
            ResearchFamilySummary(
                family_id="market_regime_timing_filter",
                name="大盘择时 / 市场状态过滤",
                status="research_absorbed",
                runtime_stage="gated_runtime",
                absorbed_from_count=4,
                runtime_gate_count=4,
            ),
        ),
        source_candidates=(),
        next_actions=(),
        prereq_items=(),
        implemented_family_count=5,
        report_only_family_count=0,
        gated_family_count=1,
    )

    html = render_dashboard([], [], "研究面板", research_summary=summary)

    assert "研究吸收" in html
    assert "113 findings" in html
    assert "mpquant/Ashare" in html
    assert "大盘择时 / 市场状态过滤" in html
    assert "门控中" in html


def test_dashboard_warns_when_source_is_degraded() -> None:
    rows = [
        {
            "signal_date": "2026-06-01",
            "symbol": "600519",
            "score": 71,
            "status": "pending",
            "thresholds_version": "1.0.0",
            "run_requested_source": "eastmoney",
            "run_actual_source": "eastmoney",
            "run_source_health_label": "degraded",
            "run_source_health_message": "eastmoney 最近失败偏多；源成功/失败 0/3",
            "run_fallback_used": False,
        }
    ]

    html = render_dashboard([], rows, "降级面板")

    assert "数据源处于降级状态" in html
    assert "不要把这次结果当成正常质量样本" in html


def test_dashboard_renders_debate_modal_with_shared_role_registry() -> None:
    candidates = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "date": "2026-06-02",
            "score": "71",
            "rating": "buy_candidate",
        }
    ]
    debate_map = {
        "600519": {
            "symbol": "600519",
            "name": "贵州茅台",
            "debate_date": "2026-06-02",
            "final_consensus": "多头略占优，维持观察",
            "recommended_adjustment": "keep",
            "original_score": 71.0,
            "adjusted_score": 71.0,
            "adjustment_weight": 0.0,
            "disagreement_score": 0.25,
            "final_vote": {
                "bull": "bullish",
                "risk_control": "neutral",
            },
            "rounds": [
                {
                    "round_num": 2,
                    "summary": "技术面维持强势，但风险端要求控制追高。",
                    "opinions": [
                        {
                            "role": "bull",
                            "stance": "bullish",
                            "confidence": 0.82,
                            "arguments": ["量价仍在主升段"],
                            "counterarguments": ["短线涨幅较大"],
                            "risk_factors": ["追高回撤风险"],
                            "opportunity_factors": ["趋势延续概率较高"],
                        },
                        {
                            "role": "risk_control",
                            "stance": "neutral",
                            "confidence": 0.73,
                            "arguments": ["需要确认次日成交质量"],
                            "counterarguments": [],
                            "risk_factors": ["高位波动放大"],
                            "opportunity_factors": [],
                        },
                    ],
                }
            ],
        }
    }

    html = render_dashboard(candidates, [], "辩论面板", debate_map=debate_map)

    assert "多Agent讨论摘要" in html
    assert "🐂 技术多头" in html
    assert "🛡️ 风险控制" in html
    assert "仅保留最终一轮" in html
    assert "趋势延续概率较高" in html
    assert "高位波动放大" in html


def test_dashboard_uses_clean_decision_labels_for_watch_candidates() -> None:
    candidates = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "date": "2026-06-02",
            "score": "71",
            "rating": "watch",
            "reasons": "等待右侧确认",
            "risks": "追高风险",
        }
    ]

    html = render_dashboard(candidates, [], "观察面板")

    assert "/ 候选观察池" in html
    assert ">风险: 追高风险<" in html
    assert "watch" not in html


def test_dashboard_warns_when_candidates_are_stale(tmp_path: Path) -> None:
    csv_path = tmp_path / "latest.csv"
    pd.DataFrame(
        [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "date": "2025-05-20",
                "score": "48",
            }
        ]
    ).to_csv(csv_path, index=False)

    candidates = read_candidates(csv_path)
    html = render_dashboard(candidates, [], "陈旧面板")

    assert latest_candidate_date(candidates) == "2025-05-20"
    assert "不是今天" in html
    assert "不要按这个页面下单" in html


def test_dashboard_handles_missing_inputs() -> None:
    html = render_dashboard([], [], "空面板")

    assert "空面板" in html
    assert "本次没有候选股" in html
    assert "暂无真实候选输出" in html
    assert "暂无 ledger 记录" in html
    assert "暂无虚拟盘记录" in html


def test_read_candidates_handles_empty_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "latest.csv"
    csv_path.write_text("", encoding="utf-8")

    assert read_candidates(csv_path) == []


def test_export_dashboard_db_writes_candidates_ledger_and_meta(
    tmp_path: Path,
    monkeypatch,
) -> None:
    csv_path = tmp_path / "latest.csv"
    ledger_path = tmp_path / "predictions.jsonl"
    db_path = tmp_path / "dashboard" / "aqsp.db"

    pd.DataFrame(
        [
            {
                "symbol": "600519",
                "name": "贵州茅台",
                "score": "71",
            }
        ]
    ).to_csv(csv_path, index=False)
    ledger_path.write_text(
        json.dumps(
            {
                "signal_date": "2026-05-29",
                "symbol": "600519",
                "strategies": ["ma_pullback"],
                "run_requested_source": "auto",
                "run_actual_source": "eastmoney",
                "run_source_health_label": "healthy",
                "run_source_health_message": "eastmoney 健康；源成功/失败 3/0",
                "run_fallback_used": False,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.export_dashboard_db.load_research_summary",
        lambda: ResearchSummary(
            generated_at="",
            total_findings=113,
            pipeline_summaries=(),
            absorbed_families=(
                ResearchFamilySummary(
                    family_id="x",
                    name="执行可行性与交易风险过滤",
                    status="research_absorbed",
                    runtime_stage="gated_runtime",
                    absorbed_from_count=4,
                    runtime_gate_count=4,
                ),
            ),
            source_candidates=(),
            next_actions=(),
            prereq_items=(),
            implemented_family_count=5,
            report_only_family_count=0,
            gated_family_count=1,
        ),
    )

    export_db(csv_path, ledger_path, db_path)

    with sqlite3.connect(db_path) as conn:
        candidate_count = conn.execute(
            "select count(*) from latest_candidates"
        ).fetchone()
        ledger_count = conn.execute("select count(*) from ledger").fetchone()
        meta = conn.execute(
            "select candidate_count, ledger_count, requested_source, actual_source, source_health_label, notify_level, fallback_used, research_total_findings, research_absorbed_families, research_report_only_families, research_gated_families from run_meta"
        ).fetchone()
        strategies = conn.execute("select strategies from ledger").fetchone()

    assert candidate_count == (1,)
    assert ledger_count == (1,)
    assert meta == (1, 1, "auto", "eastmoney", "healthy", "info", 0, 113, 1, 0, 1)
    assert strategies == ('["ma_pullback"]',)


def test_export_dashboard_db_uses_latest_runtime_row_when_last_row_has_no_meta(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "latest.csv"
    ledger_path = tmp_path / "predictions.jsonl"
    db_path = tmp_path / "dashboard" / "aqsp.db"

    pd.DataFrame([{"symbol": "600519", "name": "贵州茅台", "score": "71"}]).to_csv(
        csv_path,
        index=False,
    )
    ledger_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "signal_date": "2026-05-29",
                        "symbol": "600519",
                        "run_requested_source": "auto",
                        "run_actual_source": "sina",
                        "run_source_health_label": "fallback",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "signal_date": "2026-05-30",
                        "symbol": "300750",
                        "status": "validated",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    export_db(csv_path, ledger_path, db_path)

    with sqlite3.connect(db_path) as conn:
        meta = conn.execute(
            "select requested_source, actual_source, notify_level from run_meta"
        ).fetchone()

    assert meta == ("auto", "sina", "warning")


def test_export_dashboard_db_handles_empty_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "latest.csv"
    ledger_path = tmp_path / "predictions.jsonl"
    db_path = tmp_path / "dashboard" / "aqsp.db"

    csv_path.write_text("", encoding="utf-8")
    export_db(csv_path, ledger_path, db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
        meta = conn.execute(
            "select candidate_count, ledger_count from run_meta"
        ).fetchone()

    assert {"latest_candidates", "ledger", "run_meta"} <= tables
    assert meta == (0, 0)
