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
from aqsp.web.dashboard import (
    _action_status_label,
    _archive_conclusion_title,
    _archive_followup_action_context,
    _archive_symbol_order,
    _archive_next_action_lines,
    _archive_conclusion_context,
    _candidate_research_context_lines,
    _candidate_has_expanded_path,
    _candidate_focus_spotlight_lines,
    _candidate_review_path_lines,
    _card_primary_blocker,
    _candidate_research_title,
    _candidate_research_lines,
    _candidate_score_context_line,
    _candidate_score_metric_label,
    _candidate_next_step_lines,
    _candidate_action_plan_title,
    _candidate_empty_journey_message,
    _candidate_linkage_context,
    _candidate_symbol_order,
    _command_center_brief_lines,
    _archive_debate_evidence_lines,
    _debate_evidence_composition_line,
    _debate_overview_lines,
    _debate_vote_snapshot_lines,
    _card_emphasis,
    _home_focus_spotlights,
    _home_focus_action_targets,
    _home_spotlight_lines,
    _home_action_rail_items,
    _home_execution_blocked_summary,
    _home_primary_focus_card,
    _has_review_meta,
    _holding_metric_label,
    _execution_path_context_lines,
    _execution_research_context_lines,
    _focus_summary_lines,
    _filter_frame_by_symbol,
    _home_execution_snapshot_context,
    _home_workspace_hint,
    _include_pending_symbol,
    _line_mentions_symbol,
    _partition_symbol_lines,
    _phase_nav_label,
    _quick_bar_symbols,
    _research_task_id_for_review_card,
    _review_context_for_symbol,
    _review_source_label,
    _resolve_task_for_date,
    _report_archive_status,
    _resolve_workspace_symbol,
    _signal_evidence_context,
    _should_render_candidate_journey,
    _spotlight_as_candidate_card,
    _symbol_option_label,
    _top_navigation_context,
    _unique_lines,
    _review_phase_switch_rows,
    _workspace_context_brief,
    _workspace_focus_lines,
    _workspace_focus_title,
    _workspace_quick_symbol_label,
    _workspace_reality_lines,
    _workspace_reality_tone,
    _review_meta_line,
    _workspace_research_status,
    _workspace_handoff_payload,
    _workspace_widget_state,
    _workspace_jump_state,
    _review_to_archive_handoff_lines,
    _archive_to_review_handoff_lines,
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


class _DummyExecutionFocus:
    def __init__(
        self,
        *,
        display_name: str = "600519 贵州茅台",
        research_status: str = "研究结论已落盘",
        execution_status: str = "等待开盘验证",
        holding_status: str = "尚未形成纸面持有",
        research_lines: tuple[str, ...] = (),
        readiness_lines: tuple[str, ...] = (),
        execution_lines: tuple[str, ...] = (),
        holding_lines: tuple[str, ...] = (),
    ) -> None:
        self.display_name = display_name
        self.research_status = research_status
        self.execution_status = execution_status
        self.holding_status = holding_status
        self.research_lines = research_lines
        self.readiness_lines = readiness_lines
        self.execution_lines = execution_lines
        self.holding_lines = holding_lines


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
    assert "主链状态总览" in html
    assert "主链动作" in html
    assert "候选分层" in html
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


def test_dashboard_surfaces_watch_candidate_lifecycle_details() -> None:
    candidates = [
        {
            "symbol": "688981",
            "name": "中芯国际",
            "date": "2026-06-05",
            "score": "-9",
            "rating": "watch",
            "reasons": "MA20 斜率向上",
            "risks": "收盘价低于 MA20",
            "candidate_status": "新晋",
            "candidate_next_step": "等待量价继续走强后，再评估是否转入执行名单",
            "candidate_review_window": "盘中走强后",
            "candidate_review_priority": "high",
        },
        {
            "symbol": "000001",
            "name": "平安银行",
            "date": "2026-06-05",
            "score": "-18",
            "rating": "watch",
            "reasons": "估值防守",
            "risks": "缺少量能确认",
            "candidate_status": "观察阻塞",
            "candidate_blocker": "板块集中度过高，压低银行暴露",
            "candidate_next_step": "等待板块暴露回落后，再重新评估执行顺位",
            "candidate_review_window": "板块分化时",
            "candidate_review_priority": "medium",
        },
    ]

    html = render_dashboard(candidates, [], "主链观察面板")

    assert "主链状态总览" in html
    assert "观察复核" in html
    assert "主链动作" in html
    assert "当前阻塞" in html
    assert "明日动作" in html
    assert "/ 候选观察池" in html
    assert "新晋" in html
    assert "下一步: 等待量价继续走强后，再评估是否转入执行名单" in html
    assert "复核: 高优先级 / 盘中走强后" in html
    assert "观察阻塞" in html
    assert "阻塞: 板块集中度过高，压低银行暴露" in html
    assert "复核: 中优先级 / 板块分化时" in html


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
                "portfolio_action": "promote",
                "candidate_status": "延续上升",
                "candidate_next_step": "等待开盘承接确认后，再决定是否保留主仓",
                "candidate_review_window": "开盘前后",
                "candidate_review_priority": "high",
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
        candidate_meta = conn.execute(
            "select portfolio_action, candidate_status, candidate_next_step, candidate_review_window, candidate_review_priority from latest_candidates"
        ).fetchone()

    assert candidate_count == (1,)
    assert ledger_count == (1,)
    assert meta == (1, 1, "auto", "eastmoney", "healthy", "info", 0, 113, 1, 0, 1)
    assert strategies == ('["ma_pullback"]',)
    assert candidate_meta == (
        "promote",
        "延续上升",
        "等待开盘承接确认后，再决定是否保留主仓",
        "开盘前后",
        "high",
    )


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
        candidate_cols = {
            row[1]
            for row in conn.execute("pragma table_info(latest_candidates)").fetchall()
        }

    assert {"latest_candidates", "ledger", "run_meta"} <= tables
    assert meta == (0, 0)
    assert {
        "portfolio_action",
        "candidate_status",
        "candidate_blocker",
        "candidate_next_step",
        "candidate_review_window",
        "candidate_review_priority",
    } <= candidate_cols


def test_dashboard_action_status_label_dedupes_identical_action_and_status() -> None:
    assert _action_status_label("维持原排序", "维持原排序") == "维持原排序"
    assert _action_status_label("上调优先级", "延续上升") == "上调优先级 / 延续上升"
    assert _action_status_label("", "观察中") == "观察中"


def test_dashboard_unique_lines_preserves_order_and_removes_blank_duplicates() -> None:
    assert _unique_lines(
        ("", "第一条", "第二条", "第一条"),
        ("第二条", "第三条", " "),
    ) == ("第一条", "第二条", "第三条")


def test_dashboard_partition_symbol_lines_separates_current_symbol_from_global_lines() -> (
    None
):
    lines = (
        "600519 贵州茅台 | 下一步: 等待承接确认",
        "先核对卡点: 000001 平安银行 | 当前卡点: 板块集中度过高",
        "市场态势: 震荡偏强",
        "**600519 贵州茅台**: 观察量能是否延续",
    )

    matched, remainder = _partition_symbol_lines(lines, "600519")

    assert matched == (
        "600519 贵州茅台 | 下一步: 等待承接确认",
        "**600519 贵州茅台**: 观察量能是否延续",
    )
    assert remainder == (
        "先核对卡点: 000001 平安银行 | 当前卡点: 板块集中度过高",
        "市场态势: 震荡偏强",
    )
    assert _line_mentions_symbol("市场态势: 震荡偏强", "600519") is False
    assert _line_mentions_symbol("600519: 归档后先看承接", "600519") is True
    assert _line_mentions_symbol("600519｜复盘动作: 等分歧收敛", "600519") is True
    assert _line_mentions_symbol("【600519】复盘动作: 等分歧收敛", "600519") is True
    assert _line_mentions_symbol("1600519 不是当前标的", "600519") is False


def test_dashboard_filter_frame_by_symbol_fails_closed_when_symbol_column_missing() -> (
    None
):
    frame = pd.DataFrame(
        [
            {"名称": "贵州茅台", "事件": "当前标的不应看到这行"},
            {"名称": "宁德时代", "事件": "更不应看到这行"},
        ]
    )

    filtered = _filter_frame_by_symbol(frame, "600519")

    assert filtered.empty
    assert list(filtered.columns) == ["名称", "事件"]


def test_dashboard_filter_frame_by_symbol_supports_symbol_column() -> None:
    frame = pd.DataFrame(
        [
            {"symbol": "600519", "事件": "当前标的事件"},
            {"symbol": "300750", "事件": "其他标的事件"},
        ]
    )

    filtered = _filter_frame_by_symbol(frame, "600519")

    assert filtered.to_dict("records") == [{"symbol": "600519", "事件": "当前标的事件"}]


def test_dashboard_focus_summary_lines_use_candidate_contract_when_selected_card_exists() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="PM 已上调优先级，进入优先跟踪序列",
        next_step="等待量能确认后决定是否保留主仓",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=("量价齐升",),
        risks=("追高波动",),
        strategies=("volume_breakout",),
        data_source="eastmoney",
    )
    execution_focus = _DummyExecutionFocus(
        research_lines=("研究动作: 不应回退到 execution 侧文本",),
    )

    lines = _focus_summary_lines(
        selected_card=card,
        selected_spotlight=None,
        execution_focus=execution_focus,
    )

    assert lines == (
        "动作 / 状态: 上调优先级 / 延续上升",
        "排队层级: 首选 / 评分 88.0",
        "下一步: 等待量能确认后决定是否保留主仓",
        "复核节奏: 高优先级 / 开盘前后",
    )
    assert not any("不应回退" in line for line in lines)


def test_dashboard_review_meta_helpers_hide_placeholder_values() -> None:
    assert _has_review_meta("高优先级 / 开盘前后") is True
    assert _has_review_meta("") is False
    assert _has_review_meta("-") is False
    assert _has_review_meta("暂无额外复核节奏") is False

    assert (
        _review_meta_line("复核节奏", "高优先级 / 开盘前后")
        == "复核节奏: 高优先级 / 开盘前后"
    )
    assert _review_meta_line("复核节奏", "-") == ""


def test_dashboard_focus_summary_lines_hide_placeholder_review_meta() -> None:
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    card = DashboardCandidateCard(
        symbol="000021",
        name="深科技",
        display_name="000021 深科技",
        rank_label="阻塞观察",
        score=69.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="按当前顺位继续跟踪",
        next_step="",
        blocker="",
        review_meta="-",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="000021",
        display_name="000021 深科技",
        score=69.0,
        action_label="降级观察",
        status_label="等待确认",
        blocker="",
        next_step="继续跟踪",
        review_meta="-",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=(),
        risks=(),
    )
    execution_focus = _DummyExecutionFocus()

    card_lines = _focus_summary_lines(
        selected_card=card,
        selected_spotlight=None,
        execution_focus=execution_focus,
    )
    spotlight_lines = _focus_summary_lines(
        selected_card=None,
        selected_spotlight=spotlight,
        execution_focus=execution_focus,
    )

    assert not any("复核节奏: -" in line for line in card_lines)
    assert not any("统一复核: -" in line for line in spotlight_lines)


def test_dashboard_focus_summary_lines_falls_back_to_execution_focus_when_no_candidate_context() -> (
    None
):
    execution_focus = _DummyExecutionFocus(
        research_lines=(
            "研究动作: 维持原排序",
            "评分 72.0",
            "复核节奏: 高优先级 / 开盘前后",
            "研究下一步: 等待承接确认",
        ),
    )

    lines = _focus_summary_lines(
        selected_card=None,
        selected_spotlight=None,
        execution_focus=execution_focus,
    )

    assert lines == (
        "研究动作: 维持原排序",
        "评分 72.0",
        "复核节奏: 高优先级 / 开盘前后",
    )
    assert not any("维持原排序 / 维持原排序" in line for line in lines)


def test_dashboard_focus_summary_lines_preserve_candidate_semantics_when_only_spotlight_context_exists() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    execution_focus = _DummyExecutionFocus(
        research_lines=(
            "该标的当前不在研究候选中，主要从纸面记录回看。",
            "当前没有新的纸面入场或不可成交事件。",
        ),
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="300750",
        display_name="300750 宁德时代",
        score=83.0,
        action_label="降级观察",
        status_label="观察阻塞",
        blocker="高位波动放大，先等待分歧收敛",
        next_step="等待量价重新共振后再评估是否回到主推队列",
        review_meta="中优先级 / 午后回看",
        task_labels=("早盘策略", "尾盘策略"),
        reasons=("高景气主线延续",),
        risks=("高位分歧放大",),
    )

    lines = _focus_summary_lines(
        selected_card=None,
        selected_spotlight=spotlight,
        execution_focus=execution_focus,
    )

    assert lines == (
        "来源任务: 早盘策略、尾盘策略",
        "动作 / 状态: 降级观察 / 观察阻塞",
        "当前重点: 高位波动放大，先等待分歧收敛",
        "统一复核: 中优先级 / 午后回看",
    )
    assert not any("主要从纸面记录回看" in line for line in lines)


def test_dashboard_symbol_option_label_uses_spotlight_context_when_card_is_missing() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    spotlight = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=79.0,
        action_label="维持原排序",
        status_label="维持原排序",
        blocker="",
        next_step="尾盘确认承接后再决定是否隔夜跟踪",
        review_meta="高优先级 / 收盘前",
        task_labels=("尾盘策略",),
        reasons=("承接稳定",),
        risks=("隔夜波动",),
    )

    label = _symbol_option_label(
        symbol="002594",
        cards=(),
        spotlights=(spotlight,),
    )

    assert label == "002594 比亚迪 · 同日联动 · 维持原排序"


def test_dashboard_resolve_task_for_date_keeps_current_task_when_same_day_data_exists() -> (
    None
):
    class _Row:
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id

    class _Provider:
        def same_day_task_rows(self, signal_date: str):
            assert signal_date == "2026-06-05"
            return (_Row("main_chain"), _Row("briefing"), _Row("closing_review"))

        def preferred_task_for_date(self, signal_date: str) -> str:
            raise AssertionError("same-day task exists, should not fallback")

    resolved = _resolve_task_for_date(
        provider=_Provider(),
        current_task_id="briefing",
        signal_date="2026-06-05",
    )

    assert resolved == "briefing"


def test_dashboard_resolve_task_for_date_falls_back_to_preferred_task_when_current_task_missing() -> (
    None
):
    class _Row:
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id

    class _Provider:
        def same_day_task_rows(self, signal_date: str):
            assert signal_date == "2026-06-04"
            return (_Row("main_chain"), _Row("morning_breakout"))

        def preferred_task_for_date(self, signal_date: str) -> str:
            assert signal_date == "2026-06-04"
            return "main_chain"

    resolved = _resolve_task_for_date(
        provider=_Provider(),
        current_task_id="briefing",
        signal_date="2026-06-04",
    )

    assert resolved == "main_chain"


def test_dashboard_signal_evidence_context_falls_back_to_main_chain_for_report_tasks() -> (
    None
):
    assert _signal_evidence_context("main_chain") == ("main_chain", "当日任务证据")
    assert _signal_evidence_context("briefing") == (
        "main_chain",
        "同日主链证据（当前任务无独立选股表）",
    )
    assert _signal_evidence_context("closing_review") == (
        "main_chain",
        "同日主链证据（当前任务无独立选股表）",
    )


def test_dashboard_research_task_id_for_review_card_matches_same_day_journey_stage() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateJourneyStep,
    )

    review_card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="同日聚合",
        score=83.0,
        action_label="上调优先级",
        status_label="尾盘确认",
        decision_note="尾盘放量确认",
        next_step="次日看承接",
        blocker="",
        review_meta="高优先级 / 收盘前",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    journey_steps = (
        DashboardCandidateJourneyStep(
            task_id="main_chain",
            task_label="主链推荐",
            phase_label="盘前主链",
            score=78.0,
            action_label="维持原排序",
            status_label="等待确认",
            blocker="",
            next_step="",
            review_meta="",
            reasons=(),
            risks=(),
        ),
        DashboardCandidateJourneyStep(
            task_id="closing_premium",
            task_label="尾盘策略",
            phase_label="尾盘确认",
            score=83.0,
            action_label="上调优先级",
            status_label="尾盘确认",
            blocker="",
            next_step="",
            review_meta="",
            reasons=(),
            risks=(),
        ),
    )

    assert (
        _research_task_id_for_review_card(
            review_card=review_card,
            journey_steps=journey_steps,
            fallback_task_id="main_chain",
        )
        == "closing_premium"
    )


def test_dashboard_spotlight_as_candidate_card_preserves_review_contract() -> None:
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    spotlight = DashboardCandidateSpotlight(
        symbol="300750",
        display_name="300750 宁德时代",
        score=67.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="",
        next_step="等待量能继续放大",
        review_meta="高优先级 / 开盘前后",
        task_labels=("早盘策略", "尾盘策略"),
        reasons=("量能放大",),
        risks=("追高回撤",),
    )

    card = _spotlight_as_candidate_card(spotlight)

    assert card.symbol == "300750"
    assert card.name == "宁德时代"
    assert card.rank_label == "同日联动"
    assert card.data_source == "同日联动"
    assert card.reasons == ("量能放大",)


def test_dashboard_candidate_symbol_order_includes_spotlight_only_symbols() -> None:
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    cards = (
        DashboardCandidateCard(
            symbol="600519",
            name="贵州茅台",
            display_name="600519 贵州茅台",
            rank_label="首选",
            score=88.0,
            action_label="上调优先级",
            status_label="延续上升",
            decision_note="进入优先跟踪序列",
            next_step="等待量能确认",
            blocker="",
            review_meta="高优先级 / 开盘前后",
            reasons=(),
            risks=(),
            strategies=(),
            data_source="eastmoney",
        ),
    )
    spotlights = (
        DashboardCandidateSpotlight(
            symbol="600519",
            display_name="600519 贵州茅台",
            score=88.0,
            action_label="上调优先级",
            status_label="延续上升",
            blocker="",
            next_step="等待量能确认",
            review_meta="高优先级 / 开盘前后",
            task_labels=("主链推荐",),
            reasons=(),
            risks=(),
        ),
        DashboardCandidateSpotlight(
            symbol="002594",
            display_name="002594 比亚迪",
            score=72.0,
            action_label="维持原排序",
            status_label="等待确认",
            blocker="",
            next_step="观察承接",
            review_meta="中优先级 / 收盘前",
            task_labels=("尾盘策略",),
            reasons=(),
            risks=(),
        ),
    )

    assert _candidate_symbol_order(cards, spotlights) == ["600519", "002594"]


def test_dashboard_debate_overview_lines_show_summary_and_top_agent_views() -> None:
    from aqsp.web.data_provider import DashboardDebateAgentView, DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600519",
        display_name="600519 贵州茅台",
        debate_id="debate-1",
        rating="A+",
        original_score=80.0,
        adjusted_score=82.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.33,
        consensus="维持主推，但需要控制追高节奏",
        adjustment_reason="多头略占优",
        bull_count=4,
        bear_count=2,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=(
            "建议上调评分: 80.0 -> 82.0",
            "辩论共识: 维持主推，但需要控制追高节奏",
        ),
        round_summaries=(),
        risk_warnings=("高位波动放大",),
        opportunity_highlights=("主线延续",),
        agent_views=(
            DashboardDebateAgentView(
                role_id="bull",
                role_label="技术多头",
                stance="bullish",
                stance_label="看多",
                confidence=0.88,
                key_argument="量价仍在主升段",
                key_risk="",
                key_opportunity="",
            ),
            DashboardDebateAgentView(
                role_id="risk_control",
                role_label="风控",
                stance="neutral",
                stance_label="中性",
                confidence=0.72,
                key_argument="先确认次日成交质量",
                key_risk="",
                key_opportunity="",
            ),
        ),
    )

    lines = _debate_overview_lines(debate)

    assert lines[0] == "建议上调评分: 80.0 -> 82.0"
    assert any("技术多头: 看多 / 置信 88%" in line for line in lines)
    assert any("风控: 中性 / 置信 72%" in line for line in lines)


def test_dashboard_review_context_uses_debate_summary_when_card_and_spotlight_missing() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-only",
        rating="A",
        original_score=74.0,
        adjusted_score=77.0,
        adjustment_weight=0.2,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.42,
        consensus="维持观察但优先级上调",
        adjustment_reason="分歧收敛后更偏正面",
        bull_count=3,
        bear_count=1,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=("建议上调评分: 74.0 -> 77.0",),
        round_summaries=(),
        risk_warnings=("需确认银行板块承接",),
        opportunity_highlights=("权重股修复",),
        agent_views=(),
    )

    selected_card, selected_spotlight, selected_debate, review_card = (
        _review_context_for_symbol(
            symbol="600036",
            cards=(),
            spotlights=(),
            debates=(debate,),
        )
    )

    assert selected_card is None
    assert selected_spotlight is None
    assert selected_debate == debate
    assert review_card is not None
    assert review_card.display_name == "600036 招商银行"
    assert review_card.rank_label == "辩论主结论"
    assert review_card.action_label == "建议上调评分"
    assert review_card.blocker == "需确认银行板块承接"


def test_dashboard_candidate_symbol_order_includes_debate_only_symbols() -> None:
    from aqsp.web.data_provider import DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-only",
        rating="A",
        original_score=74.0,
        adjusted_score=77.0,
        adjustment_weight=0.2,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.42,
        consensus="维持观察但优先级上调",
        adjustment_reason="分歧收敛后更偏正面",
        bull_count=3,
        bear_count=1,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=("建议上调评分: 74.0 -> 77.0",),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    assert _candidate_symbol_order((), (), (debate,)) == ["600036"]


def test_dashboard_candidate_symbol_order_prioritizes_debate_only_symbols_before_spotlight_only() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateSpotlight,
        DashboardDebateSummary,
    )

    spotlight = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=72.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="",
        next_step="观察承接",
        review_meta="中优先级 / 收盘前",
        task_labels=("尾盘策略",),
        reasons=(),
        risks=(),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-only",
        rating="A",
        original_score=74.0,
        adjusted_score=77.0,
        adjustment_weight=0.2,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.42,
        consensus="维持观察但优先级上调",
        adjustment_reason="分歧收敛后更偏正面",
        bull_count=3,
        bear_count=1,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=("建议上调评分: 74.0 -> 77.0",),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    assert _candidate_symbol_order((), (spotlight,), (debate,)) == ["600036", "002594"]


def test_dashboard_workspace_jump_state_routes_symbol_to_matching_pending_key() -> None:
    review_state = _workspace_jump_state("候选复盘", "600519")
    execution_state = _workspace_jump_state("虚拟盘跟踪", "300750")
    archive_state = _workspace_jump_state("归档回看", "000858")

    assert review_state == {
        "dashboard_pending_workspace": "候选复盘",
        "dashboard_pending_review_symbol": "600519",
    }
    assert execution_state == {
        "dashboard_pending_workspace": "虚拟盘跟踪",
        "dashboard_pending_execution_symbol": "300750",
    }
    assert archive_state == {
        "dashboard_pending_workspace": "归档回看",
        "dashboard_pending_archive_symbol": "000858",
    }


def test_dashboard_workspace_jump_state_omits_symbol_key_when_symbol_blank() -> None:
    assert _workspace_jump_state("候选复盘", "  ") == {
        "dashboard_pending_workspace": "候选复盘",
    }


def test_dashboard_workspace_handoff_payload_skips_empty_content() -> None:
    assert (
        _workspace_handoff_payload(
            target_workspace="归档回看",
            source_workspace="候选复盘",
            title=" ",
            lines=(" ",),
        )
        == {}
    )


def test_dashboard_workspace_handoff_payload_keeps_target_source_and_lines() -> None:
    payload = _workspace_handoff_payload(
        target_workspace="归档回看",
        source_workspace="候选复盘",
        title="带着当前判断去看归档",
        lines=("当前标的: 600036 招商银行", "当前结论: 建议维持评分 / 分歧 0.48"),
    )

    assert payload == {
        "dashboard_pending_handoff_target": "归档回看",
        "dashboard_pending_handoff_source": "候选复盘",
        "dashboard_pending_handoff_title": "带着当前判断去看归档",
        "dashboard_pending_handoff_lines": (
            "当前标的: 600036 招商银行",
            "当前结论: 建议维持评分 / 分歧 0.48",
        ),
    }


def test_dashboard_workspace_widget_state_prefers_pending_without_radio_default_conflict() -> (
    None
):
    workspace_options = ("决策首页", "候选复盘", "虚拟盘跟踪", "归档回看")

    assert (
        _workspace_widget_state(
            pending_workspace="候选复盘",
            current_workspace="决策首页",
            workspace_options=workspace_options,
        )
        == "候选复盘"
    )
    assert (
        _workspace_widget_state(
            pending_workspace=None,
            current_workspace="虚拟盘跟踪",
            workspace_options=workspace_options,
        )
        == "虚拟盘跟踪"
    )
    assert (
        _workspace_widget_state(
            pending_workspace="不存在",
            current_workspace="坏状态",
            workspace_options=workspace_options,
        )
        == "决策首页"
    )


def test_dashboard_resolve_workspace_symbol_prefers_pending_symbol_and_current_selection() -> (
    None
):
    assert (
        _resolve_workspace_symbol(
            symbol_order=["000021", "000338", "600036"],
            pending_symbol="000338",
            current_value="000021",
        )
        == "000338"
    )
    assert (
        _resolve_workspace_symbol(
            symbol_order=["000021", "000338", "600036"],
            pending_symbol=None,
            current_value="600036",
        )
        == "600036"
    )
    assert (
        _resolve_workspace_symbol(
            symbol_order=["000021", "000338", "600036"],
            pending_symbol=None,
            current_value="999999",
        )
        == "000021"
    )
    assert (
        _resolve_workspace_symbol(
            symbol_order=["002594", "000338"],
            pending_symbol="600036",
            current_value="002594",
        )
        == "600036"
    )


def test_dashboard_include_pending_symbol_prevents_handoff_fallback_to_first_symbol() -> (
    None
):
    assert _include_pending_symbol(["002594", "000338"], "600036") == [
        "600036",
        "002594",
        "000338",
    ]
    assert _include_pending_symbol(["600036", "002594"], "600036") == [
        "600036",
        "002594",
    ]


def test_dashboard_archive_conclusion_context_distinguishes_symbol_and_task_level() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
        DashboardDebateSummary,
    )

    class _TaskView:
        report_summary_lines = ("今日整体偏谨慎。",)
        runtime_lines = ("数据源: auto -> eastmoney",)
        next_day_focus_lines = ()
        market_environment = "震荡偏强"

    card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=71.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="关注承接质量",
        next_step="",
        review_meta="中优先级 / 收盘前",
        task_labels=("尾盘策略",),
        reasons=(),
        risks=(),
    )

    title, lines = _archive_conclusion_context(
        task_view=_TaskView(),
        selected_symbol="600519",
        selected_card=card,
        selected_spotlight=None,
    )
    assert title == "当前标的结论"
    assert any("候选摘要: 主链继续保留首选" == line for line in lines)
    assert any("下一步: 等待量能确认" == line for line in lines)

    spotlight_title, spotlight_lines = _archive_conclusion_context(
        task_view=_TaskView(),
        selected_symbol="002594",
        selected_card=None,
        selected_spotlight=spotlight,
    )
    assert spotlight_title == "当前标的结论"
    assert any("当前重点: 关注承接质量" == line for line in spotlight_lines)

    task_title, task_lines = _archive_conclusion_context(
        task_view=_TaskView(),
        selected_symbol="300750",
        selected_card=None,
        selected_spotlight=None,
    )
    assert task_title == "任务级归档结论"
    assert task_lines[0] == "今日整体偏谨慎。"

    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-only",
        rating="A",
        original_score=74.0,
        adjusted_score=77.0,
        adjustment_weight=0.2,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.42,
        consensus="维持观察但优先级上调",
        adjustment_reason="分歧收敛后更偏正面",
        bull_count=3,
        bear_count=1,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=("建议上调评分: 74.0 -> 77.0",),
        round_summaries=(),
        risk_warnings=("需确认银行板块承接",),
        opportunity_highlights=("权重股修复",),
        agent_views=(),
    )
    debate_title, debate_lines = _archive_conclusion_context(
        task_view=_TaskView(),
        selected_symbol="600036",
        selected_card=None,
        selected_spotlight=None,
        debate_summary=debate,
    )
    assert debate_title == "当前标的结论"
    assert any("辩论结论: 建议上调评分 / 分歧 0.42" == line for line in debate_lines)
    assert not any(line.startswith("投票分布:") for line in debate_lines)
    assert not any(line.startswith("当前阻塞:") for line in debate_lines)


def test_dashboard_archive_conclusion_context_avoids_repeating_blocker_as_summary() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    class _TaskView:
        report_summary_lines = ()
        runtime_lines = ()
        next_day_focus_lines = ()
        market_environment = ""

    blocked_card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="20日均成交额不足，流动性过滤",
        next_step="",
        blocker="",
        review_meta="",
        reasons=(),
        risks=("20日均成交额不足，流动性过滤", "MACD 动能走弱"),
        strategies=(),
        data_source="eastmoney",
    )

    title, lines = _archive_conclusion_context(
        task_view=_TaskView(),
        selected_symbol="000338",
        selected_card=blocked_card,
        selected_spotlight=None,
    )

    assert title == "当前标的结论"
    assert "当前阻塞: 20日均成交额不足，流动性过滤" in lines
    assert "下一步: 先确认复核条件，卡点解除后再决定是否恢复推进。" in lines
    assert not any(line == "候选摘要: 20日均成交额不足，流动性过滤" for line in lines)


def test_dashboard_archive_conclusion_context_prefers_existing_symbol_archive_lines() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    class _TaskView:
        report_summary_lines = (
            "600519 贵州茅台 | 下一步: 等待量能确认",
            "市场态势: 白酒权重延续修复",
        )
        runtime_lines = ()
        next_day_focus_lines = ()
        market_environment = ""

    card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    title, lines = _archive_conclusion_context(
        task_view=_TaskView(),
        selected_symbol="600519",
        selected_card=card,
        selected_spotlight=None,
    )

    assert title == "归档结论"
    assert lines.count("600519 贵州茅台 | 下一步: 等待量能确认") == 1
    assert lines.count("市场态势: 白酒权重延续修复") == 1
    assert "候选摘要: 主链继续保留首选" not in lines


def test_dashboard_archive_symbol_order_uses_full_review_cards() -> None:
    from aqsp.web.data_provider import DashboardCandidateCard

    class _TaskView:
        detail_cards = ()

    review_cards = tuple(
        DashboardCandidateCard(
            symbol=f"00000{index}",
            name=f"测试{index}",
            display_name=f"00000{index} 测试{index}",
            rank_label="备选",
            score=float(80 - index),
            action_label="观察候选",
            status_label="等待确认",
            decision_note="继续跟踪",
            next_step="",
            blocker="",
            review_meta="",
            reasons=(),
            risks=(),
            strategies=(),
            data_source="eastmoney",
        )
        for index in range(1, 10)
    )

    symbol_order = _archive_symbol_order(
        _TaskView(),
        review_cards,
        same_day_spotlights=(),
        same_day_debates=(),
        open_positions_frame=pd.DataFrame(),
        paper_events_frame=pd.DataFrame(),
        execution_frame=pd.DataFrame(),
    )

    assert len(symbol_order) == 9
    assert "000009" in symbol_order


def test_dashboard_archive_next_action_lines_hide_placeholder_review_meta() -> None:
    from aqsp.web.data_provider import DashboardCandidateCard

    class _TaskView:
        agenda_lines = ()
        review_lines = ()
        unlock_lines = ()

    card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="20日均成交额不足，流动性过滤",
        next_step="",
        blocker="20日均成交额不足，流动性过滤",
        review_meta="-",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    lines = _archive_next_action_lines(
        task_view=_TaskView(),
        selected_symbol="000338",
        selected_card=card,
    )

    assert lines == ("优先处理阻塞: 20日均成交额不足，流动性过滤",)
    assert not any("按节奏复核" in line for line in lines)


def test_dashboard_archive_followup_action_context_does_not_relabel_research_as_archive_action() -> (
    None
):
    assert _archive_followup_action_context(()) == (
        "待补归档动作",
        ("当前归档没有新增复盘动作，先看原文、研究链与纸面现实。",),
    )
    assert _archive_followup_action_context(("600519 | 复核分歧是否收敛",)) == (
        "接下来做什么",
        ("600519 | 复核分歧是否收敛",),
    )


def test_dashboard_archive_conclusion_title_distinguishes_research_from_report_archive_status() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
        DashboardDebateSummary,
    )

    class _EmptyTaskView:
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()
        next_day_focus_lines = ()

    class _ArchivedTaskView:
        report_markdown = "# report"
        report_summary_lines = ()
        runtime_lines = ()
        next_day_focus_lines = ()

    research_card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="20日均成交额不足，流动性过滤",
        next_step="",
        blocker="20日均成交额不足，流动性过滤",
        review_meta="-",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    debate_card = DashboardCandidateCard(
        symbol="600036",
        name="招商银行",
        display_name="600036 招商银行",
        rank_label="辩论主结论",
        score=7.5,
        action_label="建议维持评分",
        status_label="观点分化，保持原评级",
        decision_note="辩论补齐",
        next_step="",
        blocker="需关注大盘系统性风险",
        review_meta="辩论主结论 / 待复核",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=71.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="关注承接质量",
        next_step="",
        review_meta="中优先级 / 收盘前",
        task_labels=("尾盘策略",),
        reasons=(),
        risks=(),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=7.2,
        adjusted_score=7.5,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.48,
        consensus="观点分化，保持原评级",
        adjustment_reason="多头3票 vs 空头2票",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    assert (
        _archive_conclusion_title(
            task_view=_EmptyTaskView(),
            archive_title="当前标的结论",
            selected_card=debate_card,
            selected_spotlight=None,
            debate_summary=debate,
        )
        == "辩论补齐结论 / 未归档"
    )
    assert (
        _archive_conclusion_title(
            task_view=_EmptyTaskView(),
            archive_title="当前标的结论",
            selected_card=research_card,
            selected_spotlight=None,
            debate_summary=None,
        )
        == "研究候选结论 / 未归档"
    )
    assert (
        _archive_conclusion_title(
            task_view=_EmptyTaskView(),
            archive_title="当前标的结论",
            selected_card=None,
            selected_spotlight=spotlight,
            debate_summary=None,
        )
        == "同日联动结论 / 未归档"
    )
    assert (
        _archive_conclusion_title(
            task_view=_ArchivedTaskView(),
            archive_title="归档结论",
            selected_card=debate_card,
            selected_spotlight=None,
            debate_summary=debate,
        )
        == "已归档"
    )
    assert (
        _archive_conclusion_title(
            task_view=_ArchivedTaskView(),
            archive_title="当前标的结论",
            selected_card=research_card,
            selected_spotlight=None,
            debate_summary=None,
        )
        == "归档未命中该标的 / 候选补齐"
    )
    assert (
        _archive_conclusion_title(
            task_view=_ArchivedTaskView(),
            archive_title="当前标的结论",
            selected_card=None,
            selected_spotlight=spotlight,
            debate_summary=None,
        )
        == "归档未命中该标的 / 联动补齐"
    )
    assert (
        _archive_conclusion_title(
            task_view=_ArchivedTaskView(),
            archive_title="当前标的结论",
            selected_card=debate_card,
            selected_spotlight=None,
            debate_summary=debate,
        )
        == "归档未命中该标的 / 辩论补齐"
    )


def test_dashboard_review_to_archive_handoff_lines_keep_current_conclusion_and_focus() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    card = DashboardCandidateCard(
        symbol="600036",
        name="招商银行",
        display_name="600036 招商银行",
        rank_label="辩论主结论",
        score=7.5,
        action_label="建议维持评分",
        status_label="观点分化，保持原评级",
        decision_note="多头3票 vs 空头2票，辩论建议维持评分至 7.5",
        next_step="",
        blocker="需关注大盘系统性风险",
        review_meta="辩论主结论 / 待复核",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=7.2,
        adjusted_score=7.5,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.48,
        consensus="多Agent辩论后，3个看多，2个看空，3个中性，观点分化，保持原评级",
        adjustment_reason="多头3票 vs 空头2票，辩论建议维持评分至 7.5",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议维持评分: 7.2 -> 7.5",),
        round_summaries=(),
        risk_warnings=("需关注大盘系统性风险",),
        opportunity_highlights=("行业景气度提升",),
        agent_views=(),
    )

    lines = _review_to_archive_handoff_lines(
        selected_card=card,
        debate_summary=debate,
    )

    assert lines == (
        "当前标的: 600036 招商银行",
        "当前结论: 建议维持评分 / 分歧 0.48",
        "归档时重点看: 需关注大盘系统性风险",
    )


def test_dashboard_archive_to_review_handoff_lines_prefer_archive_next_action() -> None:
    from aqsp.web.data_provider import DashboardCandidateCard

    class _TaskView:
        agenda_lines = ("600036 招商银行 | 先核对分歧是否收敛",)
        review_lines = ()
        unlock_lines = ()
        report_markdown = "# report"

    card = DashboardCandidateCard(
        symbol="600036",
        name="招商银行",
        display_name="600036 招商银行",
        rank_label="辩论主结论",
        score=7.5,
        action_label="建议维持评分",
        status_label="观点分化，保持原评级",
        decision_note="多头3票 vs 空头2票，辩论建议维持评分至 7.5",
        next_step="",
        blocker="需关注大盘系统性风险",
        review_meta="辩论主结论 / 待复核",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )

    lines = _archive_to_review_handoff_lines(
        task_view=_TaskView(),
        selected_symbol="600036",
        selected_card=card,
    )

    assert lines == (
        "当前标的: 600036",
        "归档状态: 已归档",
        "回到复盘先看: 600036 招商银行 | 先核对分歧是否收敛",
    )


def test_dashboard_candidate_focus_spotlight_lines_omits_duplicate_focus_detail() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="继续观察流动性",
        next_step="先确认解锁条件，解除阻塞后再决定是否恢复推进。",
        blocker="20日均成交额不足，流动性过滤",
        review_meta="高优先级 / 午前复核",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="000338",
        display_name="000338 潍柴动力",
        score=58.0,
        action_label="降级观察",
        status_label="等待确认",
        blocker="20日均成交额不足，流动性过滤",
        next_step="",
        review_meta="统一走流动性复核",
        task_labels=("主链推荐", "收盘复盘"),
        reasons=(),
        risks=(),
    )

    lines = _candidate_focus_spotlight_lines(card, spotlight)

    assert lines == (
        "来源任务: 主链推荐、收盘复盘",
        "跨任务结论: 降级观察 / 等待确认",
        "统一复核: 统一走流动性复核",
    )


def test_dashboard_candidate_focus_spotlight_lines_keeps_non_duplicate_metadata() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    card = DashboardCandidateCard(
        symbol="300750",
        name="宁德时代",
        display_name="300750 宁德时代",
        rank_label="观察",
        score=83.0,
        action_label="维持原排序",
        status_label="等待确认",
        decision_note="继续跟踪承接",
        next_step="等待量价重新共振后再评估是否回到主推队列",
        blocker="",
        review_meta="中优先级 / 午后回看",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="300750",
        display_name="300750 宁德时代",
        score=83.0,
        action_label="降级观察",
        status_label="观察阻塞",
        blocker="高位波动放大，先等待分歧收敛",
        next_step="",
        review_meta="中优先级 / 午后回看",
        task_labels=("早盘策略", "尾盘策略"),
        reasons=(),
        risks=(),
    )

    lines = _candidate_focus_spotlight_lines(card, spotlight)

    assert lines == (
        "来源任务: 早盘策略、尾盘策略",
        "跨任务结论: 降级观察 / 观察阻塞",
        "重点关注: 高位波动放大，先等待分歧收敛",
        "统一复核: 中优先级 / 午后回看",
    )


def test_dashboard_candidate_review_path_lines_omit_duplicate_blocker_but_keep_cross_task_source() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="继续观察流动性",
        next_step="先确认解锁条件，解除阻塞后再决定是否恢复推进。",
        blocker="20日均成交额不足，流动性过滤",
        review_meta="高优先级 / 午前复核",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="000338",
        display_name="000338 潍柴动力",
        score=58.0,
        action_label="降级观察",
        status_label="等待确认",
        blocker="20日均成交额不足，流动性过滤",
        next_step="",
        review_meta="统一走流动性复核",
        task_labels=("主链推荐", "收盘复盘"),
        reasons=(),
        risks=(),
    )

    lines = _candidate_review_path_lines(
        selected_card=card,
        spotlight=spotlight,
        debate_summary=None,
    )

    assert lines == ("来源任务: 主链推荐、收盘复盘",)


def test_dashboard_candidate_review_path_lines_keep_debate_and_cross_task_delta() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
        DashboardDebateSummary,
    )

    card = DashboardCandidateCard(
        symbol="600036",
        name="招商银行",
        display_name="600036 招商银行",
        rank_label="同日联动",
        score=74.0,
        action_label="建议上调评分",
        status_label="维持观察但优先级上调",
        decision_note="辩论后偏正面",
        next_step="等待板块承接确认",
        blocker="需确认银行板块承接",
        review_meta="高优先级 / 午后复核",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="600036",
        display_name="600036 招商银行",
        score=74.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="等待权重股轮动确认",
        next_step="",
        review_meta="高优先级 / 午后复核",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=(),
        risks=(),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-only",
        rating="A",
        original_score=74.0,
        adjusted_score=77.0,
        adjustment_weight=0.2,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.42,
        consensus="维持观察但优先级上调",
        adjustment_reason="分歧收敛后更偏正面",
        bull_count=3,
        bear_count=1,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=("建议上调评分: 74.0 -> 77.0",),
        round_summaries=(),
        risk_warnings=("需确认银行板块承接",),
        opportunity_highlights=("权重股修复",),
        agent_views=(),
    )

    lines = _candidate_review_path_lines(
        selected_card=card,
        spotlight=spotlight,
        debate_summary=debate,
    )

    assert "投票分布: 看多 3 / 看空 1 / 中性 2" in lines
    assert "修正原因: 分歧收敛后更偏正面" in lines
    assert "来源任务: 主链推荐、尾盘策略" in lines
    assert "跨任务重点: 等待权重股轮动确认" in lines


def test_dashboard_candidate_review_path_lines_omit_duplicate_next_step() -> None:
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="600519",
        display_name="600519 贵州茅台",
        score=88.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="",
        next_step="等待量能确认",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "收盘复盘"),
        reasons=(),
        risks=(),
    )

    lines = _candidate_review_path_lines(
        selected_card=card,
        spotlight=spotlight,
        debate_summary=None,
    )

    assert lines == ("来源任务: 主链推荐、收盘复盘",)


def test_dashboard_execution_context_lines_absorb_debate_takeaways() -> None:
    from aqsp.web.data_provider import DashboardDebateSummary

    execution_focus = _DummyExecutionFocus(
        research_lines=(
            "研究动作: 维持原排序",
            "研究下一步: 等待量能确认",
            "复核节奏: 高优先级 / 开盘前后",
        ),
        readiness_lines=("执行准备: 等待开盘确认成交质量",),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-only",
        rating="A",
        original_score=74.0,
        adjusted_score=77.0,
        adjustment_weight=0.2,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.42,
        consensus="维持观察但优先级上调",
        adjustment_reason="分歧收敛后更偏正面",
        bull_count=3,
        bear_count=1,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=("建议上调评分: 74.0 -> 77.0",),
        round_summaries=(),
        risk_warnings=("需确认银行板块承接",),
        opportunity_highlights=("权重股修复",),
        agent_views=(),
    )

    research_lines = _execution_research_context_lines(
        selected_card=None,
        selected_spotlight=None,
        debate_summary=debate,
        execution_focus=execution_focus,
    )
    path_lines = _execution_path_context_lines(
        selected_card=None,
        selected_spotlight=None,
        debate_summary=debate,
        execution_focus=execution_focus,
    )

    assert any("辩论结论: 建议上调评分 / 分歧 0.42" == line for line in research_lines)
    assert any("辩论共识: 维持观察但优先级上调" == line for line in research_lines)
    assert any(
        "当前没有研究候选卡，研究链路已由辩论主结论补齐。" == line
        for line in research_lines
    )
    assert any("修正原因: 分歧收敛后更偏正面" == line for line in path_lines)
    assert any("当前阻塞: 需确认银行板块承接" == line for line in path_lines)


def test_dashboard_workspace_focus_helpers_use_review_fallback_for_debate_only_symbols() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    execution_focus = _DummyExecutionFocus(
        display_name="600519",
        research_status="研究链路缺席",
        research_lines=("该标的当前不在研究候选中，主要从纸面记录回看。",),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600519",
        display_name="600519 贵州茅台",
        debate_id="debate-only",
        rating="A+",
        original_score=80.0,
        adjusted_score=82.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.33,
        consensus="维持主推，但需要控制追高节奏",
        adjustment_reason="多头略占优",
        bull_count=4,
        bear_count=2,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=("建议上调评分: 80.0 -> 82.0",),
        round_summaries=(),
        risk_warnings=("高位波动放大",),
        opportunity_highlights=("主线延续",),
        agent_views=(),
    )
    _, _, _, review_card = _review_context_for_symbol(
        symbol="600519",
        cards=(),
        spotlights=(),
        debates=(debate,),
    )

    assert review_card is not None
    assert (
        _workspace_focus_title(
            selected_card=None,
            selected_spotlight=None,
            review_card=review_card,
            execution_focus=execution_focus,
        )
        == "600519 贵州茅台"
    )
    assert (
        _workspace_research_status(
            selected_card=None,
            selected_spotlight=None,
            review_card=review_card,
            execution_focus=execution_focus,
        )
        == "辩论主结论已补齐"
    )
    focus_lines = _workspace_focus_lines(
        selected_card=None,
        selected_spotlight=None,
        review_card=review_card,
        execution_focus=execution_focus,
    )
    assert focus_lines[:2] == (
        "动作 / 状态: 待独立验证 / 等待下一次任务确认",
        "辩论调整分（非选股评分）: 82.0",
    )
    assert focus_lines[2] == "验证动作: 等待下一次任务或纸面验证链路补充独立证据。"
    assert not any(line.startswith("复核节奏:") for line in focus_lines)
    assert not any(line.startswith("当前阻塞:") for line in focus_lines)


def test_dashboard_workspace_quick_symbol_label_prefers_available_context() -> None:
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
        DashboardDebateSummary,
    )

    card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=71.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="",
        next_step="观察承接",
        review_meta="中优先级 / 收盘前",
        task_labels=("尾盘策略",),
        reasons=(),
        risks=(),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-only",
        rating="A",
        original_score=74.0,
        adjusted_score=77.0,
        adjustment_weight=0.2,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.42,
        consensus="维持观察但优先级上调",
        adjustment_reason="分歧收敛后更偏正面",
        bull_count=3,
        bear_count=1,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=("建议上调评分: 74.0 -> 77.0",),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    assert _workspace_quick_symbol_label(
        symbol="600519",
        cards=(card,),
    ) == ("600519", "贵州茅台")
    assert _workspace_quick_symbol_label(
        symbol="002594",
        cards=(),
        spotlights=(spotlight,),
    ) == ("002594", "比亚迪")
    assert _workspace_quick_symbol_label(
        symbol="600036",
        cards=(),
        debates=(debate,),
    ) == ("600036", "招商银行")


def test_dashboard_candidate_symbol_order_keeps_selected_debate_symbol_available_for_quick_switch() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    cards = tuple(
        DashboardCandidateCard(
            symbol=f"00000{i}",
            name=f"测试{i}",
            display_name=f"00000{i} 测试{i}",
            rank_label="阻塞观察",
            score=60.0 + i,
            action_label="降级观察",
            status_label="等待确认",
            decision_note="继续跟踪",
            next_step="继续跟踪",
            blocker="",
            review_meta="中优先级 / 收盘前",
            reasons=(),
            risks=(),
            strategies=(),
            data_source="eastmoney",
        )
        for i in range(1, 7)
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600519",
        display_name="600519 贵州茅台",
        debate_id="debate-only",
        rating="A+",
        original_score=80.0,
        adjusted_score=82.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.33,
        consensus="维持主推，但需要控制追高节奏",
        adjustment_reason="多头略占优",
        bull_count=4,
        bear_count=2,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=("建议上调评分: 80.0 -> 82.0",),
        round_summaries=(),
        risk_warnings=("高位波动放大",),
        opportunity_highlights=("主线延续",),
        agent_views=(),
    )

    symbol_order = _candidate_symbol_order(cards, (), (debate,))
    quick_symbols = symbol_order[:6]
    if debate.symbol not in quick_symbols and quick_symbols:
        quick_symbols = [*quick_symbols[:-1], debate.symbol]

    assert quick_symbols[-1] == "600519"


def test_dashboard_quick_bar_symbols_prioritize_top_debate_focus_in_candidate_review() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    symbol_order = [
        "000001",
        "000002",
        "000003",
        "000004",
        "000005",
        "000006",
        "600036",
    ]
    debates = (
        DashboardDebateSummary(
            signal_date="2026-06-05",
            symbol="600036",
            display_name="600036 招商银行",
            debate_id="debate-focus",
            rating="A",
            original_score=74.0,
            adjusted_score=77.0,
            adjustment_weight=0.2,
            recommended_adjustment="raise",
            recommended_adjustment_label="建议上调评分",
            disagreement_score=0.42,
            consensus="维持观察但优先级上调",
            adjustment_reason="分歧收敛后更偏正面",
            bull_count=3,
            bear_count=1,
            neutral_count=2,
            round_count=2,
            regime="震荡偏强",
            data_source="multi",
            thresholds_version="v1.0.0",
            summary_lines=("建议上调评分: 74.0 -> 77.0",),
            round_summaries=(),
            risk_warnings=(),
            opportunity_highlights=(),
            agent_views=(),
        ),
    )

    quick_symbols = _quick_bar_symbols(
        workspace="候选复盘",
        symbol_order=symbol_order,
        selected_symbol="000001",
        debates=debates,
        limit=6,
    )

    assert quick_symbols == ["000001", "000002", "000003", "000004", "000005", "600036"]


def test_dashboard_review_phase_switch_rows_include_current_research_and_journey_tasks() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateJourneyStep,
        DashboardSameDayTaskRow,
    )

    same_day_rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先确认主推候选",
            status_label="待解锁",
            headline="盘前主链 headline",
            candidate_count=3,
            actionable_count=1,
            watch_count=1,
            blocked_count=1,
        ),
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="closing_review",
            task_label="收盘复盘",
            phase_order=4,
            phase_label="收盘复盘",
            phase_summary="核对结果",
            status_label="已复盘",
            headline="收盘复盘 headline",
            candidate_count=2,
            actionable_count=0,
            watch_count=0,
            blocked_count=2,
        ),
    )
    journey_steps = (
        DashboardCandidateJourneyStep(
            task_id="main_chain",
            task_label="主链推荐",
            phase_label="盘前主链",
            score=80.0,
            action_label="上调优先级",
            status_label="延续上升",
            blocker="",
            next_step="等待量能确认",
            review_meta="高优先级 / 开盘前后",
            reasons=(),
            risks=(),
        ),
    )

    rows = _review_phase_switch_rows(
        same_day_rows=same_day_rows,
        current_task_id="closing_review",
        journey_steps=journey_steps,
        research_task_id="main_chain",
    )

    assert tuple(row.task_id for row in rows) == ("main_chain", "closing_review")


def test_dashboard_phase_nav_label_includes_phase_task_and_status() -> None:
    from aqsp.web.data_provider import DashboardSameDayTaskRow

    row = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="closing_review",
        task_label="收盘复盘",
        phase_order=4,
        phase_label="收盘复盘",
        phase_summary="核对结果",
        status_label="已复盘",
        headline="收盘复盘 headline",
        candidate_count=2,
        actionable_count=0,
        watch_count=0,
        blocked_count=2,
    )

    assert _phase_nav_label(row) == "收盘复盘 · 收盘复盘 · 已复盘"


def test_dashboard_top_navigation_context_prefers_same_day_phase_summary() -> None:
    from aqsp.web.data_provider import DashboardSameDayTaskRow, DashboardTaskSnapshot

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先确认主推候选与执行顺位",
            status_label="有推荐",
            headline="000338 潍柴动力 | 先看流动性阻塞",
            candidate_count=3,
            actionable_count=0,
            watch_count=1,
            blocked_count=2,
        ),
    )
    snapshots = (
        DashboardTaskSnapshot(
            task_id="main_chain",
            task_label="主链推荐",
            latest_date="2026-06-05",
            status_label="有推荐",
            headline="snapshot headline",
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )

    title, lines = _top_navigation_context(
        selected_date="2026-06-05",
        selected_task_id="main_chain",
        same_day_rows=rows,
        snapshots=snapshots,
    )

    assert title == "2026-06-05 · 盘前主链"
    assert lines[0] == "主链推荐 / 有推荐"
    assert lines[1] == "队列: 待复核 0 / 观察 1 / 阻塞 2"
    assert lines[2] == "焦点: 000338 潍柴动力 | 先看流动性阻塞"


def test_dashboard_top_navigation_context_falls_back_to_snapshot_when_same_day_missing() -> (
    None
):
    from aqsp.web.data_provider import DashboardTaskSnapshot

    title, lines = _top_navigation_context(
        selected_date="2026-06-05",
        selected_task_id="briefing",
        same_day_rows=(),
        snapshots=(
            DashboardTaskSnapshot(
                task_id="briefing",
                task_label="盘前简报",
                latest_date="2026-06-05",
                status_label="需复核",
                headline="盘前简报提示关注成交额阈值",
                actionable_count=2,
                watch_count=1,
                blocked_count=0,
            ),
        ),
    )

    assert title == "2026-06-05 · 盘前简报"
    assert lines == (
        "盘前简报 / 需复核",
        "队列: 已落盘 2 / 待跟踪 1 / 待补档 0",
        "焦点: 盘前简报提示关注成交额阈值",
    )


def test_dashboard_workspace_context_brief_distinguishes_review_sources_and_execution_pressure() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=71.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="关注承接质量",
        next_step="",
        review_meta="中优先级 / 收盘前",
        task_labels=("尾盘策略",),
        reasons=(),
        risks=(),
    )

    assert _review_source_label(None) == "仅纸面记录"
    assert _review_source_label(card) == "研究候选卡"

    debate_card = card.__class__(
        **{**card.__dict__, "rank_label": "辩论主结论", "blocker": "需关注分歧"}
    )
    title, lines, tone = _workspace_context_brief(
        review_card=debate_card,
        selected_card=None,
        selected_spotlight=None,
        open_position_count=0,
        has_execution_activity=False,
    )
    assert title == "辩论主结论回看"
    assert lines == (
        "当前判断主要由辩论主结论补齐。",
        "先看辩论共识、修正原因和风险分歧。",
    )
    assert tone == "focus"

    title, lines, tone = _workspace_context_brief(
        review_card=card,
        selected_card=card,
        selected_spotlight=None,
        open_position_count=1,
        has_execution_activity=False,
    )
    assert title == "纸面持有优先"
    assert lines == ("当前仍有纸面持有假设。", "先看退出条件与约束。")
    assert tone == "pressure"

    title, lines, tone = _workspace_context_brief(
        review_card=card,
        selected_card=card,
        selected_spotlight=None,
        open_position_count=0,
        has_execution_activity=False,
        holding_status="纸面持有未绑定本日",
    )
    assert title == "纸面持有优先"
    assert lines == (
        "纸面 ledger 仍有未绑定本日的持有假设。",
        "先确认旧持有假设退出条件，再判断本日信号是否独立推进。",
    )
    assert tone == "pressure"
    assert (
        _holding_metric_label(_DummyExecutionFocus(holding_status="纸面持有未绑定本日"))
        == "本日绑定纸面持有"
    )

    title, lines, tone = _workspace_context_brief(
        review_card=_spotlight_as_candidate_card(spotlight),
        selected_card=None,
        selected_spotlight=spotlight,
        open_position_count=0,
        has_execution_activity=False,
    )
    assert title == "跨任务联动回看"
    assert lines == (
        "当前判断来自同日联动聚合。",
        "先核对跨任务结论，再回到单任务证据。",
    )
    assert tone == "archive"


def test_dashboard_workspace_context_brief_uses_blocked_review_language_when_card_has_primary_blocker() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    blocked_card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="20日均成交额不足，流动性过滤",
        next_step="",
        blocker="",
        review_meta="",
        reasons=(),
        risks=("20日均成交额不足，流动性过滤", "MACD 动能走弱"),
        strategies=(),
        data_source="eastmoney",
    )

    title, lines, tone = _workspace_context_brief(
        review_card=blocked_card,
        selected_card=blocked_card,
        selected_spotlight=None,
        open_position_count=0,
        has_execution_activity=False,
    )

    assert title == "阻塞卡点回看"
    assert lines == ("当前仍受研究阻塞影响。", "先核对卡点、复核条件和复核窗口。")
    assert tone == "blocked"


def test_dashboard_workspace_reality_lines_stay_compact_without_execution_body_duplication() -> (
    None
):
    lines = _workspace_reality_lines(
        selected_date="2026-06-01",
        research_status="研究结论已落盘",
        event_count=0,
        log_count=0,
        open_position_count=0,
    )

    assert lines == (
        "回看日期: 2026-06-01",
        "研究状态: 研究结论已落盘",
        "纸面记录: 事件 0 / 日志 0 / 纸面持有 0",
    )
    assert not any("研究已产出" in line or "纸面日志" in line for line in lines)
    assert (
        _workspace_reality_tone(
            execution_status="尚未进入执行",
            event_count=0,
            log_count=0,
            open_position_count=0,
        )
        == "archive"
    )
    assert (
        _workspace_reality_tone(
            execution_status="执行受阻",
            event_count=0,
            log_count=0,
            open_position_count=0,
        )
        == "blocked"
    )
    assert (
        _workspace_reality_tone(
            execution_status="已有纸面验证",
            event_count=1,
            log_count=0,
            open_position_count=0,
        )
        == "pressure"
    )


def test_dashboard_command_center_brief_lines_do_not_call_summaries_archive_when_unarchived() -> (
    None
):
    class _UnarchivedTaskView:
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()
        next_day_focus_lines = ()

    class _ArchivedTaskView:
        report_markdown = "# report"
        report_summary_lines = ("报告已归档。",)
        runtime_lines = ()
        next_day_focus_lines = ()

    assert _command_center_brief_lines(
        task_view=_UnarchivedTaskView(),
        summary_lines=("今日仍以研究阻塞为主。",),
    ) == ("研究摘要: 今日仍以研究阻塞为主。",)
    assert _command_center_brief_lines(
        task_view=_ArchivedTaskView(),
        summary_lines=("不应使用 fallback",),
    ) == ("历史报告摘要: 报告已归档。",)


def test_dashboard_command_center_neutralizes_archive_action_language() -> None:
    class _ArchivedTaskView:
        report_markdown = "# report"
        report_summary_lines = (
            "🎯 **首选**: 600036 招商银行，等待右侧确认",
            "❌ **移出候选**: 000001 平安银行",
        )
        runtime_lines = ()
        next_day_focus_lines = ()

    lines = _command_center_brief_lines(
        task_view=_ArchivedTaskView(),
        summary_lines=("不应使用 fallback",),
    )

    assert lines == (
        "历史报告摘要: 历史首选记录: 600036 招商银行，等待右侧确认",
        "历史报告摘要: 历史移出记录: 000001 平安银行",
    )
    assert not any("🎯" in line or "❌" in line or "**" in line for line in lines)


def test_dashboard_home_workspace_hint_prioritizes_execution_then_archive_then_review() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    class _TaskView:
        review_lines = ("先看候选复盘里的研究证据。",)
        recommendation_lines = ("先看主推候选。",)
        next_day_focus_lines = ("次日重点先看归档结论。",)
        report_summary_lines = ("归档已生成。",)
        report_markdown = "## report"

    class _Overview:
        archive_summary = "当日归档摘要"

    from aqsp.web.data_provider import DashboardPaperSummary

    execution_summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=0,
        pending_entries=1,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=("有纸面入场事件，先看纸面验证链。",),
    )
    assert _home_workspace_hint(_TaskView(), _Overview(), execution_summary) == (
        "先看虚拟盘跟踪",
        "有纸面入场事件，先看纸面验证链。",
        "pressure",
    )

    archive_summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(
            "2026-06-01 暂无虚拟盘纸面事件，当前以研究判断与纸面持有跟踪为主。",
        ),
    )
    assert _home_workspace_hint(_TaskView(), _Overview(), archive_summary) == (
        "先去归档回看",
        "次日重点先看归档结论。",
        "archive",
    )

    open_only_summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=1,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=("600519 持仓中",),
        event_lines=(),
        action_summary_lines=("当前以纸面持有跟踪为主，共 1 笔。",),
    )
    assert _home_workspace_hint(_TaskView(), _Overview(), open_only_summary) == (
        "先去归档回看",
        "次日重点先看归档结论。",
        "archive",
    )

    class _TaskViewNoArchive:
        review_lines = ("先看候选复盘里的研究证据。",)
        recommendation_lines = ("先看主推候选。",)
        next_day_focus_lines = ()
        report_summary_lines = ()
        runtime_lines = ()
        report_markdown = ""

    assert _home_workspace_hint(_TaskViewNoArchive(), _Overview(), archive_summary) == (
        "先去候选复盘",
        "先看候选复盘里的研究证据。",
        "focus",
    )

    class _TaskViewBlockedNoArchive:
        detail_cards = (
            DashboardCandidateCard(
                symbol="000338",
                name="潍柴动力",
                display_name="000338 潍柴动力",
                rank_label="阻塞观察",
                score=58.0,
                action_label="降级观察",
                status_label="降级观察",
                decision_note="20日均成交额不足，流动性过滤",
                next_step="",
                blocker="20日均成交额不足，流动性过滤",
                review_meta="",
                reasons=(),
                risks=("20日均成交额不足，流动性过滤",),
                strategies=(),
                data_source="eastmoney",
            ),
        )
        review_lines = ()
        recommendation_lines = ()
        next_day_focus_lines = ()
        report_summary_lines = ()
        runtime_lines = ()
        report_markdown = ""

    assert _home_workspace_hint(
        _TaskViewBlockedNoArchive(), _Overview(), archive_summary
    ) == (
        "先去候选复盘",
        "先复盘 000338 潍柴动力 的阻塞卡点“20日均成交额不足，流动性过滤”，再决定是否恢复推进。",
        "focus",
    )


def test_dashboard_home_action_rail_items_merge_same_day_spotlight_when_task_has_no_card() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    class _TaskView:
        detail_cards = ()
        agenda_lines = ()
        recommendation_lines = ()
        review_lines = ()
        watchlist_lines = ()
        blocker_lines = ()

    spotlights = (
        DashboardCandidateSpotlight(
            symbol="600519",
            display_name="600519 贵州茅台",
            score=88.0,
            action_label="上调优先级",
            status_label="延续上升",
            blocker="",
            next_step="等待量能确认",
            review_meta="高优先级 / 开盘前后",
            task_labels=("主链推荐", "收盘复盘"),
            reasons=(),
            risks=(),
        ),
    )

    recommend_item, watch_item, blocked_item = _home_action_rail_items(
        _TaskView(), spotlights
    )

    assert recommend_item.lane_label == "优先复核"
    assert recommend_item.card is not None
    assert recommend_item.card.rank_label == "同日联动"
    assert recommend_item.summary == "600519 贵州茅台"
    assert recommend_item.lines[0] == "研究入口: 同日联动"
    assert recommend_item.button_label == "去复盘"
    assert recommend_item.target_workspace == "候选复盘"
    assert watch_item.button_label == "归档回看"
    assert watch_item.target_workspace == "归档回看"
    assert watch_item.summary == "暂无观察项"
    assert blocked_item.summary == "暂无阻塞项"


def test_dashboard_home_action_rail_items_preserve_existing_card_over_same_symbol_spotlight() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    class _TaskView:
        detail_cards = (
            DashboardCandidateCard(
                symbol="600519",
                name="贵州茅台",
                display_name="600519 贵州茅台",
                rank_label="首选",
                score=88.0,
                action_label="上调优先级",
                status_label="延续上升",
                decision_note="主链继续保留首选",
                next_step="等待量能确认",
                blocker="",
                review_meta="高优先级 / 开盘前后",
                reasons=(),
                risks=(),
                strategies=(),
                data_source="eastmoney",
            ),
        )
        agenda_lines = ()
        recommendation_lines = ()
        review_lines = ()
        watchlist_lines = ()
        blocker_lines = ()

    same_symbol_spotlight = DashboardCandidateSpotlight(
        symbol="600519",
        display_name="600519 贵州茅台",
        score=88.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="等待板块承接",
        next_step="",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "收盘复盘"),
        reasons=(),
        risks=(),
    )

    recommend_item, _, _ = _home_action_rail_items(
        _TaskView(), (same_symbol_spotlight,)
    )

    assert recommend_item.card is not None
    assert recommend_item.card.rank_label == "首选"
    assert recommend_item.lines[0] == "研究入口: 研究候选卡"


def test_dashboard_home_action_rail_items_fall_back_to_task_briefs_when_lane_is_empty() -> (
    None
):
    class _TaskView:
        detail_cards = ()
        recommendation_lines = ()
        review_lines = ("000002 万科A | 中优先级 / 收盘前 | 等待回踩确认",)
        watchlist_lines = ()
        blocker_lines = ("000004 国华网安 | 量能不足，先别推进",)

    recommend_item, watch_item, blocked_item = _home_action_rail_items(_TaskView(), ())

    assert recommend_item.card is None
    assert recommend_item.lines == ("当前无 ready 候选。",)
    assert watch_item.card is None
    assert watch_item.lines == ("000002 万科A | 中优先级 / 收盘前 | 等待回踩确认",)
    assert blocked_item.card is None
    assert blocked_item.lines == ("000004 国华网安 | 量能不足，先别推进",)


def test_dashboard_home_action_rail_items_mark_empty_recommend_lane_hidden_when_no_actionable_context() -> (
    None
):
    class _TaskView:
        detail_cards = ()
        recommendation_lines = ()
        review_lines = ("000002 万科A | 中优先级 / 收盘前 | 等待回踩确认",)
        watchlist_lines = ()
        blocker_lines = ("000004 国华网安 | 量能不足，先别推进",)

    recommend_item, watch_item, blocked_item = _home_action_rail_items(_TaskView(), ())

    assert recommend_item.visible is False
    assert watch_item.visible is True
    assert blocked_item.visible is True


def test_dashboard_home_action_rail_items_use_blocked_focus_for_watch_empty_state_when_no_watch_lines() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    class _TaskView:
        detail_cards = (
            DashboardCandidateCard(
                symbol="000338",
                name="潍柴动力",
                display_name="000338 潍柴动力",
                rank_label="阻塞观察",
                score=68.0,
                action_label="降级观察",
                status_label="降级观察",
                decision_note="20日均成交额不足，流动性过滤",
                next_step="",
                blocker="20日均成交额不足，流动性过滤",
                review_meta="",
                reasons=(),
                risks=("20日均成交额不足，流动性过滤",),
                strategies=(),
                data_source="eastmoney",
            ),
        )
        recommendation_lines = ()
        review_lines = ()
        watchlist_lines = ()
        blocker_lines = ()

    _, watch_item, blocked_item = _home_action_rail_items(_TaskView(), ())

    assert watch_item.card is None
    assert watch_item.summary == "暂无观察项"
    assert watch_item.lines == ("当前没有独立观察对象。",)
    assert blocked_item.card is not None
    assert blocked_item.card.symbol == "000338"


def test_dashboard_home_action_rail_items_keep_recommend_lane_visible_when_same_day_spotlight_fills_it() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    class _TaskView:
        detail_cards = ()
        recommendation_lines = ()
        review_lines = ()
        watchlist_lines = ()
        blocker_lines = ()

    spotlights = (
        DashboardCandidateSpotlight(
            symbol="600519",
            display_name="600519 贵州茅台",
            score=88.0,
            action_label="上调优先级",
            status_label="延续上升",
            blocker="",
            next_step="等待量能确认",
            review_meta="高优先级 / 开盘前后",
            task_labels=("主链推荐", "收盘复盘"),
            reasons=(),
            risks=(),
        ),
    )

    recommend_item, watch_item, blocked_item = _home_action_rail_items(
        _TaskView(), spotlights
    )

    assert recommend_item.visible is True
    assert recommend_item.card is not None
    assert watch_item.visible is False
    assert blocked_item.visible is False


def test_dashboard_home_action_rail_items_prioritize_blocked_card_with_explicit_blocker() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    class _TaskView:
        recommendation_lines = ()
        review_lines = ()
        watchlist_lines = ()
        blocker_lines = ()
        detail_cards = (
            DashboardCandidateCard(
                symbol="000021",
                name="深科技",
                display_name="000021 深科技",
                rank_label="阻塞观察",
                score=70.0,
                action_label="降级观察",
                status_label="等待确认",
                decision_note="按当前顺位继续跟踪",
                next_step="",
                blocker="阻塞原因未记录，需补充风险说明或复核条件",
                review_meta="中优先级 / 收盘前",
                reasons=(),
                risks=(),
                strategies=(),
                data_source="eastmoney",
            ),
            DashboardCandidateCard(
                symbol="000338",
                name="潍柴动力",
                display_name="000338 潍柴动力",
                rank_label="阻塞观察",
                score=68.0,
                action_label="降级观察",
                status_label="等待确认",
                decision_note="等待条件解除",
                next_step="",
                blocker="20日均成交额不足，流动性过滤",
                review_meta="中优先级 / 收盘前",
                reasons=(),
                risks=(),
                strategies=(),
                data_source="eastmoney",
            ),
        )

    _, _, blocked_item = _home_action_rail_items(_TaskView(), ())

    assert blocked_item.card is not None
    assert blocked_item.card.symbol == "000338"
    assert blocked_item.lines[2] == "当前重点: 20日均成交额不足，流动性过滤"


def test_dashboard_home_primary_focus_card_prefers_explicit_blocker_when_only_blocked_candidates() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    missing_blocker_card = DashboardCandidateCard(
        symbol="000021",
        name="深科技",
        display_name="000021 深科技",
        rank_label="阻塞观察",
        score=70.0,
        action_label="降级观察",
        status_label="等待确认",
        decision_note="按当前顺位继续跟踪",
        next_step="",
        blocker="阻塞原因未记录，需补充风险说明或复核条件",
        review_meta="中优先级 / 收盘前",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    explicit_blocker_card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=68.0,
        action_label="降级观察",
        status_label="等待确认",
        decision_note="等待条件解除",
        next_step="",
        blocker="20日均成交额不足，流动性过滤",
        review_meta="中优先级 / 收盘前",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    ready_card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    assert (
        _home_primary_focus_card(
            recommend_cards=(),
            watch_cards=(),
            blocked_cards=(missing_blocker_card, explicit_blocker_card),
        )
        == explicit_blocker_card
    )
    assert (
        _home_primary_focus_card(
            recommend_cards=(ready_card,),
            watch_cards=(),
            blocked_cards=(explicit_blocker_card,),
        )
        == ready_card
    )


def test_dashboard_card_primary_blocker_falls_back_to_first_risk_for_blocked_card() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=68.0,
        action_label="降级观察",
        status_label="等待确认",
        decision_note="按当前顺位继续跟踪",
        next_step="",
        blocker="",
        review_meta="中优先级 / 收盘前",
        reasons=(),
        risks=("20日均成交额不足，流动性过滤", "MACD 动能走弱"),
        strategies=(),
        data_source="eastmoney",
    )

    assert _card_primary_blocker(card) == "20日均成交额不足，流动性过滤"


def test_dashboard_card_primary_blocker_ignores_missing_metadata_placeholder() -> None:
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="000021",
        name="深科技",
        display_name="000021 深科技",
        rank_label="阻塞观察",
        score=70.0,
        action_label="降级观察",
        status_label="等待确认",
        decision_note="按当前顺位继续跟踪",
        next_step="",
        blocker="阻塞原因未记录，需补充风险说明或复核条件",
        review_meta="中优先级 / 收盘前",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    assert _card_primary_blocker(card) == ""
    assert _card_emphasis(card) == "当前处于阻塞观察，先核对卡点条件。"


def test_dashboard_execution_path_context_lines_reframe_generic_readiness_for_blocked_candidate() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="20日均成交额不足，流动性过滤",
        next_step="",
        blocker="",
        review_meta="",
        reasons=(),
        risks=("20日均成交额不足，流动性过滤", "MACD 动能走弱"),
        strategies=(),
        data_source="eastmoney",
    )
    execution_focus = _DummyExecutionFocus(
        readiness_lines=("研究已产出，但尚未进入纸面入场或阻塞队列。",),
    )

    lines = _execution_path_context_lines(
        selected_card=card,
        selected_spotlight=None,
        debate_summary=None,
        execution_focus=execution_focus,
    )

    assert any(
        "下一步: 先确认复核条件，卡点解除后再决定是否恢复推进。" == line
        for line in lines
    )
    assert any(
            "研究已产出，但当前被20日均成交额不足，流动性过滤拦住，暂不进入纸面入场验证链路。"
            == line
            for line in lines
        )
    assert not any("当前阻塞: 20日均成交额不足，流动性过滤" == line for line in lines)


def test_dashboard_home_action_rail_items_insert_debate_lane_when_same_day_debate_exists() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    class _TaskView:
        detail_cards = ()
        recommendation_lines = ()
        review_lines = ()
        watchlist_lines = ()
        blocker_lines = ()

    debates = (
        DashboardDebateSummary(
            signal_date="2026-06-01",
            symbol="600519",
            display_name="600519 贵州茅台",
            debate_id="debate-1",
            rating="A",
            original_score=71.0,
            adjusted_score=74.0,
            adjustment_weight=0.2,
            recommended_adjustment="raise",
            recommended_adjustment_label="建议上调评分",
            disagreement_score=0.29,
            consensus="分歧可控",
            adjustment_reason="趋势延续，但需确认承接",
            bull_count=3,
            bear_count=2,
            neutral_count=3,
            round_count=2,
            regime="震荡偏强",
            data_source="multi",
            thresholds_version="v1",
            summary_lines=("建议上调评分: 71.0 -> 74.0",),
            round_summaries=(),
            risk_warnings=("追高回撤风险",),
            opportunity_highlights=("主线延续",),
            agent_views=(),
        ),
        DashboardDebateSummary(
            signal_date="2026-06-01",
            symbol="600036",
            display_name="600036 招商银行",
            debate_id="debate-2",
            rating="B",
            original_score=68.0,
            adjusted_score=68.0,
            adjustment_weight=0.0,
            recommended_adjustment="keep",
            recommended_adjustment_label="建议维持评分",
            disagreement_score=0.48,
            consensus="观点分化，保持原评级",
            adjustment_reason="多空分歧更大",
            bull_count=3,
            bear_count=2,
            neutral_count=3,
            round_count=2,
            regime="震荡偏强",
            data_source="multi",
            thresholds_version="v1",
            summary_lines=("建议维持评分: 68.0 -> 68.0",),
            round_summaries=(),
            risk_warnings=("分歧偏大",),
            opportunity_highlights=("防御属性",),
            agent_views=(),
        ),
    )

    items = _home_action_rail_items(_TaskView(), (), debates)

    assert len(items) == 4
    assert items[2].lane_id == "debate"
    assert items[2].lane_label == "分歧复核"
    assert items[2].card is not None
    assert items[2].card.symbol == "600036"
    assert items[2].button_label == "候选复盘"
    assert items[2].target_workspace == "候选复盘"
    assert items[2].tone == "pressure"
    assert items[2].lines[0] == "辩论结论: 建议维持评分 / 分歧 0.48"
    assert any("研究入口: 辩论主结论" == line for line in items[2].lines)


def test_dashboard_home_focus_spotlights_filters_out_single_task_blocked_cards() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    blocked_single = DashboardCandidateSpotlight(
        symbol="000338",
        display_name="000338 潍柴动力",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        blocker="20日均成交额不足，流动性过滤",
        next_step="",
        review_meta="",
        task_labels=("主链推荐",),
        reasons=("MA20 斜率向上",),
        risks=("20日均成交额不足，流动性过滤",),
    )
    cross_task = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=71.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="关注承接质量",
        next_step="",
        review_meta="中优先级 / 收盘前",
        task_labels=("尾盘策略", "收盘复盘"),
        reasons=("尾盘承接仍强",),
        risks=("高位波动放大",),
    )
    single_non_blocked = DashboardCandidateSpotlight(
        symbol="000021",
        display_name="000021 深科技",
        score=62.0,
        action_label="降级观察",
        status_label="降级观察",
        blocker="",
        next_step="继续跟踪",
        review_meta="",
        task_labels=("主链推荐",),
        reasons=("均线仍多头",),
        risks=(),
    )

    filtered = _home_focus_spotlights((blocked_single, cross_task, single_non_blocked))

    assert [item.symbol for item in filtered] == ["002594"]


def test_dashboard_home_focus_action_targets_start_with_candidate_review() -> None:
    assert _home_focus_action_targets() == (
        ("复盘", "候选复盘"),
        ("虚拟盘", "虚拟盘跟踪"),
        ("归档", "归档回看"),
    )

    review_state = _workspace_jump_state(_home_focus_action_targets()[0][1], "002594")
    assert review_state == {
        "dashboard_pending_workspace": "候选复盘",
        "dashboard_pending_review_symbol": "002594",
    }


def test_dashboard_home_spotlight_lines_keep_cross_task_summary_without_action_duplication() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    spotlight = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=71.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="关注承接质量",
        next_step="",
        review_meta="中优先级 / 收盘前",
        task_labels=("尾盘策略", "收盘复盘"),
        reasons=("尾盘承接仍强",),
        risks=("高位波动放大",),
    )

    lines = _home_spotlight_lines(spotlight)

    assert lines == (
        "覆盖任务: 尾盘策略、收盘复盘",
        "联动状态: 维持原排序 / 等待确认",
        "汇总理由: 尾盘承接仍强",
        "汇总风险: 高位波动放大",
    )


def test_dashboard_debate_vote_snapshot_lines_show_vote_distribution_and_rounds() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateAgentView, DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=68.0,
        adjusted_score=68.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.48,
        consensus="观点分化，保持原评级",
        adjustment_reason="多空分歧更大",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议维持评分: 68.0 -> 68.0",),
        round_summaries=(),
        risk_warnings=("分歧偏大",),
        opportunity_highlights=("防御属性",),
        agent_views=(
            DashboardDebateAgentView(
                role_id="sector_leader",
                role_label="板块轮动",
                stance="bullish",
                stance_label="看多",
                confidence=0.91,
                key_argument="板块轮动认为当前价格位置合理",
                key_risk="",
                key_opportunity="",
            ),
            DashboardDebateAgentView(
                role_id="bear",
                role_label="基本面空头",
                stance="bearish",
                stance_label="看空",
                confidence=0.70,
                key_argument="基本面空头认为当前价格位置偏高",
                key_risk="",
                key_opportunity="",
            ),
        ),
    )

    lines = _debate_vote_snapshot_lines(debate)

    assert lines[0] == "投票分布: 看多 3 / 看空 2 / 中性 3"
    assert lines[1] == "讨论轮次: 2"
    assert "板块轮动: 看多 / 置信 91%" in lines[2]
    assert "基本面空头: 看空 / 置信 70%" in lines[3]


def test_dashboard_debate_evidence_composition_line_shows_verifiable_inputs() -> None:
    from aqsp.web.data_provider import DashboardDebateAgentView, DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=68.0,
        adjusted_score=68.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.48,
        consensus="观点分化，保持原评级",
        adjustment_reason="多空分歧更大",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议维持评分: 68.0 -> 68.0",),
        round_summaries=(),
        risk_warnings=("分歧偏大",),
        opportunity_highlights=("防御属性",),
        agent_views=(
            DashboardDebateAgentView(
                role_id="sector_leader",
                role_label="板块轮动",
                stance="bullish",
                stance_label="看多",
                confidence=0.91,
                key_argument="板块轮动认为当前价格位置合理",
                key_risk="",
                key_opportunity="",
            ),
            DashboardDebateAgentView(
                role_id="bear",
                role_label="基本面空头",
                stance="bearish",
                stance_label="看空",
                confidence=0.70,
                key_argument="基本面空头认为当前价格位置偏高",
                key_risk="",
                key_opportunity="",
            ),
        ),
    )

    assert (
        _debate_evidence_composition_line(debate)
        == "证据构成: 2 轮讨论 / 2 个 agent 观点 / 数据源 multi / 阈值 v1"
    )


def test_dashboard_candidate_research_title_keeps_debate_consensus_out_of_header() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    card = DashboardCandidateCard(
        symbol="600036",
        name="招商银行",
        display_name="600036 招商银行",
        rank_label="辩论主结论",
        score=7.5,
        action_label="建议维持评分",
        status_label="多Agent辩论后，3个看多，2个看空，3个中性，观点分化，保持原评级",
        decision_note="多头3票 vs 空头2票，辩论建议维持评分至 7.5",
        next_step="",
        blocker="需关注大盘系统性风险",
        review_meta="辩论主结论 / 待复核",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=7.2,
        adjusted_score=7.5,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.48,
        consensus="多Agent辩论后，3个看多，2个看空，3个中性，观点分化，保持原评级",
        adjustment_reason="多头3票 vs 空头2票，辩论建议维持评分至 7.5",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议维持评分: 7.2 -> 7.5",),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    title = _candidate_research_title(
        selected_card=card,
        debate_summary=debate,
        compact_mode=True,
    )

    assert title == "建议维持评分 / 分歧 0.48"
    assert "多Agent辩论后" not in title


def test_dashboard_candidate_research_title_uses_unlock_title_for_blocked_card() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    blocked_card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="20日均成交额不足，流动性过滤",
        next_step="",
        blocker="",
        review_meta="",
        reasons=(),
        risks=("20日均成交额不足，流动性过滤",),
        strategies=(),
        data_source="eastmoney",
    )
    risk_card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="关注短线拥挤",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    assert (
        _candidate_research_title(
            selected_card=blocked_card,
            debate_summary=None,
            compact_mode=False,
        )
        == "阻塞待核对"
    )
    assert (
        _candidate_research_title(
            selected_card=risk_card,
            debate_summary=None,
            compact_mode=False,
        )
        == "上调优先级 / 延续上升"
    )


def test_dashboard_candidate_research_lines_do_not_repeat_action_plan() -> None:
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="20日均成交额不足，流动性过滤",
        next_step="等待量能恢复",
        blocker="20日均成交额不足，流动性过滤",
        review_meta="中优先级 / 收盘前",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    lines = _candidate_research_lines(
        selected_card=card,
        debate_summary=None,
        compact_mode=False,
    )

    assert lines == ("排队层级: 阻塞观察 / 评分 58.0",)
    assert not any(line.startswith("下一步:") for line in lines)
    assert not any(line.startswith("复核节奏:") for line in lines)


def test_dashboard_candidate_score_lines_label_debate_adjustment_as_non_picker_score() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    card = DashboardCandidateCard(
        symbol="600036",
        name="招商银行",
        display_name="600036 招商银行",
        rank_label="辩论主结论",
        score=82.0,
        action_label="待独立验证",
        status_label="等待下一次任务确认",
        decision_note="辩论补齐",
        next_step="",
        blocker="",
        review_meta="",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=80.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.31,
        consensus="分歧可控",
        adjustment_reason="多头略占优",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议上调评分: 80.0 -> 82.0",),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    assert _candidate_score_context_line(card) == "辩论调整分（非选股评分）: 82.0"
    assert (
        _candidate_score_metric_label(selected_card=card, debate_summary=debate)
        == "辩论调整分"
    )
    assert (
        _candidate_research_lines(
            selected_card=card,
            debate_summary=debate,
            compact_mode=True,
        )[0]
        == "辩论调整分（非选股评分）: 82.0"
    )


def test_dashboard_candidate_next_step_lines_prioritize_blocker_as_action_plan() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="20日均成交额不足，流动性过滤",
        next_step="等待量能恢复后再评估",
        blocker="20日均成交额不足，流动性过滤",
        review_meta="中优先级 / 收盘前",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    assert _candidate_action_plan_title(card) == "先核对卡点"
    assert _candidate_next_step_lines(card) == (
        "当前卡点: 20日均成交额不足，流动性过滤",
            "复核动作: 等待量能恢复后再评估",
        "复核节奏: 中优先级 / 收盘前",
    )


def test_dashboard_candidate_next_step_lines_do_not_treat_missing_blocker_as_action() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="000021",
        name="深科技",
        display_name="000021 深科技",
        rank_label="阻塞观察",
        score=69.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="按当前顺位继续跟踪",
        next_step="阻塞原因未记录，需补充风险说明或复核条件",
        blocker="",
        review_meta="",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    assert _candidate_next_step_lines(card) == (
        "待补证据: 阻塞原因未记录，需补充风险说明或复核条件",
    )


def test_dashboard_candidate_review_snapshot_uses_unlock_action_card_for_compact_blocked_state(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateCard

    rendered_cards: list[dict[str, object]] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def metric(self, *args, **kwargs):
            return None

    class _StubContainer(_StubColumn):
        pass

    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard.st, "container", lambda: _StubContainer())
    monkeypatch.setattr(dashboard.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="20日均成交额不足，流动性过滤",
        next_step="等待量能恢复后再评估",
        blocker="20日均成交额不足，流动性过滤",
        review_meta="中优先级 / 收盘前",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    dashboard._render_candidate_review_snapshot(
        card,
        spotlight=None,
        debate_summary=None,
        journey_steps=(),
        paper_frame=pd.DataFrame(),
        execution_frame=pd.DataFrame(),
    )

    research_card = next(
        item for item in rendered_cards if item["kicker"] == "研究判断"
    )
    action_card = next(item for item in rendered_cards if item["kicker"] == "推进计划")

    assert research_card["title"] == "阻塞待核对"
    assert not any(str(line).startswith("下一步:") for line in research_card["lines"])
    assert action_card["title"] == "先核对卡点"
    assert action_card["lines"] == (
        "当前卡点: 20日均成交额不足，流动性过滤",
            "复核动作: 等待量能恢复后再评估",
        "复核节奏: 中优先级 / 收盘前",
    )
    assert "当前仍处研究阻塞阶段，尚未进入执行动作" not in action_card["lines"]


def test_dashboard_archive_debate_evidence_lines_show_agent_rationale_and_quality() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateAgentView, DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=68.0,
        adjusted_score=68.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.48,
        consensus="观点分化，保持原评级",
        adjustment_reason="多空分歧更大",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议维持评分: 68.0 -> 68.0",),
        round_summaries=(),
        risk_warnings=("分歧偏大",),
        opportunity_highlights=("防御属性",),
        agent_views=(
            DashboardDebateAgentView(
                role_id="sector_leader",
                role_label="板块轮动",
                stance="bullish",
                stance_label="看多",
                confidence=0.91,
                key_argument="板块轮动认为当前价格位置合理",
                key_risk="",
                key_opportunity="",
            ),
            DashboardDebateAgentView(
                role_id="bear",
                role_label="基本面空头",
                stance="bearish",
                stance_label="看空",
                confidence=0.70,
                key_argument="基本面空头认为当前价格位置偏高",
                key_risk="",
                key_opportunity="",
            ),
        ),
    )

    lines = _archive_debate_evidence_lines(debate)

    assert lines[0] == "投票分布: 看多 3 / 看空 2 / 中性 3"
    assert lines[1] == "讨论轮次: 2"
    assert lines[2] == "板块轮动: 看多 / 置信 91% | 板块轮动认为当前价格位置合理"
    assert lines[3] == "基本面空头: 看空 / 置信 70% | 基本面空头认为当前价格位置偏高"
    assert lines[4] == "调整依据: 多空分歧更大"
    assert lines[5] == "证据构成: 2 轮讨论 / 2 个 agent 观点 / 数据源 multi / 阈值 v1"


def test_dashboard_candidate_linkage_context_distinguishes_spotlight_debate_and_single_task() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateSpotlight,
        DashboardDebateSummary,
    )

    spotlight = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=71.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="关注承接质量",
        next_step="",
        review_meta="中优先级 / 收盘前",
        task_labels=("尾盘策略", "收盘复盘"),
        reasons=("尾盘承接仍强",),
        risks=("高位波动放大",),
    )
    single_task_spotlight = DashboardCandidateSpotlight(
        symbol="000338",
        display_name="000338 潍柴动力",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        blocker="20日均成交额不足，流动性过滤",
        next_step="",
        review_meta="",
        task_labels=("主链推荐",),
        reasons=("MA20 斜率向上",),
        risks=("20日均成交额不足，流动性过滤",),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=68.0,
        adjusted_score=68.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.48,
        consensus="观点分化，保持原评级",
        adjustment_reason="多空分歧更大",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议维持评分: 68.0 -> 68.0",),
        round_summaries=(),
        risk_warnings=("分歧偏大",),
        opportunity_highlights=("防御属性",),
        agent_views=(),
    )

    title, lines, tone = _candidate_linkage_context(
        spotlight=spotlight,
        debate_summary=None,
        task_summary="尾盘策略、收盘复盘",
    )
    assert title == "跨任务视角"
    assert lines[0] == "任务覆盖: 尾盘策略、收盘复盘"
    assert any("汇总理由: 尾盘承接仍强" == line for line in lines)
    assert tone == "archive"

    title, lines, tone = _candidate_linkage_context(
        spotlight=None,
        debate_summary=debate,
        task_summary="仅当前任务",
    )
    assert title == "风险与机会"
    assert lines[0] == "主要机会: 防御属性"
    assert lines[1] == "主要风险: 分歧偏大"
    assert lines[2] == "当前覆盖: 仅当前任务"
    assert tone == "archive"

    title, lines, tone = _candidate_linkage_context(
        spotlight=single_task_spotlight,
        debate_summary=None,
        task_summary="主链推荐",
    )
    assert title == "单任务证据"
    assert lines[0] == "任务覆盖: 主链推荐"
    assert any("同日摘要: MA20 斜率向上" == line for line in lines)
    assert any("主要风险: 20日均成交额不足，流动性过滤" == line for line in lines)
    assert tone == "archive"

    title, lines, tone = _candidate_linkage_context(
        spotlight=None,
        debate_summary=None,
        task_summary="仅当前任务",
    )
    assert title == "单任务证据"
    assert lines[1] == "当前只在本任务中出现，没有额外同日联动上下文。"
    assert tone == "archive"


def test_dashboard_candidate_has_expanded_path_only_for_multistage_or_cross_task_or_debate() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateJourneyStep,
        DashboardCandidateSpotlight,
        DashboardDebateSummary,
    )

    single_step = (
        DashboardCandidateJourneyStep(
            task_id="main_chain",
            task_label="主链推荐",
            phase_label="盘前主链",
            score=69.0,
            action_label="降级观察",
            status_label="降级观察",
            blocker="",
            next_step="继续跟踪",
            review_meta="",
            reasons=(),
            risks=(),
        ),
    )
    multi_step = single_step + (
        DashboardCandidateJourneyStep(
            task_id="closing_review",
            task_label="收盘复盘",
            phase_label="收盘复盘",
            score=69.0,
            action_label="维持原排序",
            status_label="待复盘",
            blocker="",
            next_step="观察次日承接",
            review_meta="",
            reasons=(),
            risks=(),
        ),
    )
    cross_task_spotlight = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=71.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="关注承接质量",
        next_step="",
        review_meta="中优先级 / 收盘前",
        task_labels=("尾盘策略", "收盘复盘"),
        reasons=(),
        risks=(),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=68.0,
        adjusted_score=68.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.48,
        consensus="观点分化，保持原评级",
        adjustment_reason="多空分歧更大",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议维持评分: 68.0 -> 68.0",),
        round_summaries=(),
        risk_warnings=("分歧偏大",),
        opportunity_highlights=("防御属性",),
        agent_views=(),
    )

    assert (
        _candidate_has_expanded_path(
            spotlight=None,
            debate_summary=None,
            journey_steps=single_step,
        )
        is False
    )
    assert (
        _should_render_candidate_journey(
            spotlight=None,
            debate_summary=None,
            journey_steps=single_step,
        )
        is False
    )
    assert (
        _candidate_has_expanded_path(
            spotlight=None,
            debate_summary=None,
            journey_steps=multi_step,
        )
        is True
    )
    assert (
        _should_render_candidate_journey(
            spotlight=None,
            debate_summary=None,
            journey_steps=multi_step,
        )
        is True
    )
    assert (
        _candidate_has_expanded_path(
            spotlight=cross_task_spotlight,
            debate_summary=None,
            journey_steps=single_step,
        )
        is True
    )
    assert (
        _should_render_candidate_journey(
            spotlight=cross_task_spotlight,
            debate_summary=None,
            journey_steps=single_step,
        )
        is True
    )
    assert (
        _candidate_has_expanded_path(
            spotlight=None,
            debate_summary=debate,
            journey_steps=single_step,
        )
        is True
    )
    assert (
        _should_render_candidate_journey(
            spotlight=None,
            debate_summary=debate,
            journey_steps=single_step,
        )
        is True
    )
    assert (
        _should_render_candidate_journey(
            spotlight=None,
            debate_summary=None,
            journey_steps=(),
        )
        is True
    )


def test_dashboard_candidate_has_expanded_path_stays_compact_for_single_stage_blocked_card() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateJourneyStep

    blocked_single = (
        DashboardCandidateJourneyStep(
            task_id="main_chain",
            task_label="主链推荐",
            phase_label="盘前主链",
            score=58.0,
            action_label="降级观察",
            status_label="降级观察",
            blocker="20日均成交额不足，流动性过滤",
            next_step="",
            review_meta="",
            reasons=("MA20 斜率向上",),
            risks=("20日均成交额不足，流动性过滤",),
        ),
    )

    assert (
        _candidate_has_expanded_path(
            spotlight=None,
            debate_summary=None,
            journey_steps=blocked_single,
        )
        is False
    )


def test_dashboard_candidate_empty_journey_message_distinguishes_debate_spotlight_and_single_task() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
        DashboardDebateSummary,
    )

    debate_card = DashboardCandidateCard(
        symbol="600036",
        name="招商银行",
        display_name="600036 招商银行",
        rank_label="辩论主结论",
        score=7.5,
        action_label="建议维持评分",
        status_label="观点分化，保持原评级",
        decision_note="辩论补齐",
        next_step="",
        blocker="需关注大盘系统性风险",
        review_meta="辩论主结论 / 待复核",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    spotlight_card = DashboardCandidateCard(
        symbol="000027",
        name="深圳能源",
        display_name="000027 深圳能源",
        rank_label="同日联动",
        score=69.0,
        action_label="降级观察",
        status_label="等待确认",
        decision_note="同日联动补齐",
        next_step="",
        blocker="",
        review_meta="",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="同日联动",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="000027",
        display_name="000027 深圳能源",
        score=69.0,
        action_label="降级观察",
        status_label="等待确认",
        blocker="",
        next_step="",
        review_meta="",
        task_labels=("主链推荐",),
        reasons=(),
        risks=(),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=68.0,
        adjusted_score=68.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.48,
        consensus="观点分化，保持原评级",
        adjustment_reason="多空分歧更大",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议维持评分: 68.0 -> 68.0",),
        round_summaries=(),
        risk_warnings=("分歧偏大",),
        opportunity_highlights=("防御属性",),
        agent_views=(),
    )

    assert (
        _candidate_empty_journey_message(
            review_card=debate_card,
            spotlight=None,
            debate_summary=debate,
        )
        == "该标的在当前回看日没有独立候选路径，当前判断主要由同日多 Agent 讨论补齐。"
    )
    assert (
        _candidate_empty_journey_message(
            review_card=spotlight_card,
            spotlight=spotlight,
            debate_summary=None,
        )
        == "该标的在当前回看日没有独立候选路径，当前判断主要来自同日联动聚合。"
    )
    assert (
        _candidate_empty_journey_message(
            review_card=None,
            spotlight=None,
            debate_summary=None,
        )
        == "该标的在当前回看日只有单任务记录，暂无跨阶段路径。"
    )


def test_dashboard_home_execution_snapshot_context_prefers_compact_status_and_top_lines() -> (
    None
):
    from aqsp.web.data_provider import DashboardPaperSummary

    summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=1,
        pending_entries=1,
        not_executable=0,
        closed_trades=2,
        open_position_lines=("600519 纸面持有中", "000858 纸面持有中"),
        event_lines=("600519 纸面入场待核对", "000858 纸面关闭"),
        action_summary_lines=("优先处理纸面入场与纸面退出。",),
    )

    title, status_lines, holding_lines, event_lines, tone = (
        _home_execution_snapshot_context(summary)
    )

    assert title == "纸面事件待处理"
    assert (
        status_lines[0]
        == "纸面持有 1 / 入场待核对 1 / 不可成交 0 / 纸面关闭 2"
    )
    assert status_lines[1] == "优先处理纸面入场与纸面退出。"
    assert holding_lines == ("600519 纸面持有中", "000858 纸面持有中")
    assert event_lines == ("600519 纸面入场待核对", "000858 纸面关闭")
    assert tone == "pressure"


def test_dashboard_home_execution_snapshot_context_reframes_blocked_research_day() -> (
    None
):
    from aqsp.web.data_provider import DashboardPaperSummary

    summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )

    title, status_lines, holding_lines, event_lines, tone = (
        _home_execution_snapshot_context(
            summary,
            blocked_summary="阻塞 6 只，其中 5 只卡在：20日均成交额不足，流动性过滤",
        )
    )

    assert title == "研究阻塞待核对"
    assert status_lines[1] == "当前没有新的纸面事件，主要因为研究侧仍有阻塞未核对。"
    assert status_lines[2] == "阻塞 6 只，其中 5 只卡在：20日均成交额不足，流动性过滤"
    assert len(status_lines) == 3
    assert holding_lines == ("当前暂无纸面持有假设。",)
    assert event_lines == ("先回到候选复盘核对卡点与复核条件。",)
    assert tone == "blocked"


def test_dashboard_home_execution_blocked_summary_uses_aggregate_instead_of_first_symbol() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDateOverview

    class _TaskView:
        blocker_lines = ("000338 潍柴动力 | 20日均成交额不足，流动性过滤",)
        detail_cards = (
            DashboardCandidateCard(
                symbol="000338",
                name="潍柴动力",
                display_name="000338 潍柴动力",
                rank_label="阻塞观察",
                score=58.0,
                action_label="降级观察",
                status_label="降级观察",
                decision_note="继续观察流动性",
                next_step="",
                blocker="20日均成交额不足，流动性过滤",
                review_meta="",
                reasons=(),
                risks=(),
                strategies=(),
                data_source="eastmoney",
            ),
            DashboardCandidateCard(
                symbol="000400",
                name="许继电气",
                display_name="000400 许继电气",
                rank_label="阻塞观察",
                score=56.0,
                action_label="降级观察",
                status_label="降级观察",
                decision_note="继续观察流动性",
                next_step="",
                blocker="20日均成交额不足，流动性过滤",
                review_meta="",
                reasons=(),
                risks=(),
                strategies=(),
                data_source="eastmoney",
            ),
        )

    overview = DashboardDateOverview(
        signal_date="2026-06-01",
        task_count=2,
        actionable_total=0,
        watch_total=0,
        blocked_total=2,
        top_task_label="主链推荐",
        top_headline="无可执行标的",
        blocker_headline="000338 潍柴动力 | 20日均成交额不足，流动性过滤",
        focus_headline="000338 潍柴动力 | 20日均成交额不足，流动性过滤",
        workflow_summary="盘前主链 -> 收盘复盘",
        archive_summary="",
    )

    summary = _home_execution_blocked_summary(
        task_view=_TaskView(),
        overview=overview,
    )

    assert summary == "阻塞 2 只，当前都卡在：20日均成交额不足，流动性过滤"
    assert "000338" not in summary


def test_dashboard_candidate_research_context_lines_stay_research_focused_when_execution_is_empty() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=("主升结构保持", "量价配合稳定"),
        risks=("短线偏热",),
        strategies=("main_chain",),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="600519",
        display_name="600519 贵州茅台",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        blocker="",
        next_step="等待量能确认",
        review_meta="高优先级 / 开盘前后",
        task_labels=("盘前主链", "收盘复盘"),
        reasons=("主升结构保持",),
        risks=("短线偏热",),
    )
    empty_frame = pd.DataFrame()

    title, execution_lines, context_lines, tone = _candidate_research_context_lines(
        review_card=card,
        spotlight=spotlight,
        paper_frame=empty_frame,
        execution_frame=empty_frame,
    )

    assert title == "当前仍处研究阶段"
    assert execution_lines == (
        "当前回看日暂无虚拟盘纸面动作。",
        "纸面侧仍为空白，暂无持仓或日志可交叉验证。",
    )
    assert "研究入口: 研究候选卡" in context_lines
    assert "核心理由: 主升结构保持；量价配合稳定" in context_lines
    assert "风险提示: 短线偏热" in context_lines
    assert "同日联动: 盘前主链、收盘复盘" in context_lines
    assert tone == "archive"


def test_dashboard_candidate_research_context_lines_switch_to_execution_mode_when_frames_exist() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="002594",
        name="比亚迪",
        display_name="002594 比亚迪",
        rank_label="阻塞观察",
        score=71.0,
        action_label="维持原排序",
        status_label="等待确认",
        decision_note="等待承接确认",
        next_step="确认开盘量价",
        blocker="关注承接质量",
        review_meta="中优先级 / 收盘前",
        reasons=("趋势未坏",),
        risks=("承接仍需确认",),
        strategies=("closing_review",),
        data_source="multi",
    )
    paper_frame = pd.DataFrame([{"代码": "002594"}])
    execution_frame = pd.DataFrame([{"代码": "002594"}, {"代码": "002594"}])

    title, execution_lines, context_lines, tone = _candidate_research_context_lines(
        review_card=card,
        spotlight=None,
        paper_frame=paper_frame,
        execution_frame=execution_frame,
    )

    assert title == "纸面侧已联动"
    assert execution_lines[0] == "虚拟盘事件 1 条"
    assert execution_lines[1] == "纸面日志 2 条"
    assert execution_lines[2] == "当前已经进入纸面验证联动，可结合纸面记录核对研究结论。"
    assert "同日联动: 当前只在本任务中出现" in context_lines
    assert "重点关注" not in context_lines
    assert tone == "pressure"


def test_dashboard_candidate_research_context_lines_use_debate_summary_for_debate_only_review() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    card = DashboardCandidateCard(
        symbol="600036",
        name="招商银行",
        display_name="600036 招商银行",
        rank_label="辩论主结论",
        score=7.5,
        action_label="建议维持评分",
        status_label="观点分化，保持原评级",
        decision_note="多头3票 vs 空头2票，辩论建议维持评分至 7.5",
        next_step="",
        blocker="需关注大盘系统性风险",
        review_meta="辩论主结论 / 待复核",
        reasons=("行业景气度提升", "估值具有吸引力"),
        risks=("需关注大盘系统性风险", "业绩波动风险"),
        strategies=(),
        data_source="multi",
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-2",
        rating="B",
        original_score=7.2,
        adjusted_score=7.5,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.48,
        consensus="多Agent辩论后，3个看多，2个看空，3个中性，观点分化，保持原评级",
        adjustment_reason="多头3票 vs 空头2票，辩论建议维持评分至 7.5",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议维持评分: 7.2 -> 7.5",),
        round_summaries=(),
        risk_warnings=("需关注大盘系统性风险",),
        opportunity_highlights=("行业景气度提升",),
        agent_views=(),
    )
    empty_frame = pd.DataFrame()

    title, execution_lines, context_lines, tone = _candidate_research_context_lines(
        review_card=card,
        spotlight=None,
        debate_summary=debate,
        paper_frame=empty_frame,
        execution_frame=empty_frame,
    )

    assert title == "当前仍处研究阶段"
    assert execution_lines == (
        "当前回看日暂无虚拟盘纸面动作。",
        "纸面侧仍为空白，暂无持仓或日志可交叉验证。",
    )
    assert context_lines[0] == "监控焦点: 需关注大盘系统性风险"
    assert context_lines[1] == "验证动作: 等待下一次任务或纸面验证链路补充独立证据。"
    assert "复核节奏" not in " | ".join(context_lines)
    assert not any(line.startswith("核心理由:") for line in context_lines)
    assert not any(line.startswith("风险提示:") for line in context_lines)
    assert tone == "blocked"


def test_dashboard_execution_research_context_lines_prefer_research_evidence_over_header_summary() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    execution_focus = _DummyExecutionFocus(
        research_lines=(
            "研究动作: 维持原排序",
            "研究下一步: 等待量能确认",
            "复核节奏: 高优先级 / 开盘前后",
        ),
    )

    lines = _execution_research_context_lines(
        selected_card=card,
        selected_spotlight=None,
        debate_summary=None,
        execution_focus=execution_focus,
    )

    assert lines == (
        "研究动作: 维持原排序",
        "研究下一步: 等待量能确认",
        "复核节奏: 高优先级 / 开盘前后",
    )
    assert not any(line.startswith("动作 / 状态:") for line in lines)


def test_dashboard_renders_debate_modal_without_cross_symbol_leakage_and_keeps_consensus_summary() -> (
    None
):
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
        },
        "300750": {
            "symbol": "300750",
            "name": "宁德时代",
            "debate_date": "2026-06-02",
            "final_consensus": "等待右侧确认",
            "recommended_adjustment": "downgrade",
            "original_score": 69.0,
            "adjusted_score": 63.0,
            "adjustment_weight": -0.4,
            "disagreement_score": 0.61,
            "final_vote": {
                "bull": "neutral",
                "risk_control": "bearish",
            },
            "rounds": [
                {
                    "round_num": 2,
                    "summary": "第二只股票的讨论，不应串进 600519。",
                    "opinions": [
                        {
                            "role": "risk_control",
                            "stance": "bearish",
                            "confidence": 0.81,
                            "arguments": ["波动仍大"],
                            "counterarguments": [],
                            "risk_factors": ["板块拥挤"],
                            "opportunity_factors": [],
                        }
                    ],
                }
            ],
        },
    }

    html = render_dashboard(candidates, [], "辩论面板", debate_map=debate_map)

    assert "多Agent讨论摘要" in html
    assert "多头略占优，维持观察" in html
    assert "维持原评级" in html
    assert "25%" in html
    assert "第二只股票的讨论，不应串进 600519" not in html
    assert "等待右侧确认" not in html


def test_dashboard_report_archive_status_contract_distinguishes_full_report_summary_and_empty() -> (
    None
):
    class _TaskView:
        def __init__(
            self,
            *,
            report_markdown: str = "",
            report_summary_lines: tuple[str, ...] = (),
            runtime_lines: tuple[str, ...] = (),
            next_day_focus_lines: tuple[str, ...] = (),
        ) -> None:
            self.report_markdown = report_markdown
            self.report_summary_lines = report_summary_lines
            self.runtime_lines = runtime_lines
            self.next_day_focus_lines = next_day_focus_lines

    assert _report_archive_status(_TaskView(report_markdown="# report")) == "已归档"
    assert (
        _report_archive_status(
            _TaskView(
                report_summary_lines=("今日主链 1 只可执行。",),
                runtime_lines=("数据源: auto -> eastmoney",),
            )
        )
        == "有摘要"
    )
    assert _report_archive_status(_TaskView()) == "无归档"
