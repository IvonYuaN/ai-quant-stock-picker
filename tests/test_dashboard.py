from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from aqsp.core.time import now_shanghai
from aqsp.briefing.agent_roles import DEFAULT_RUNTIME_AGENT_ROLE_NAMES
from aqsp.research.summary import (
    ResearchActionItem,
    ResearchFamilySummary,
    ResearchPipelineSummary,
    ResearchPrereqItem,
    ResearchSummary,
)
from aqsp.web.dashboard import (
    _action_status_label,
    _archive_conclusion_title,
    _archive_followup_action_context,
    _archive_brief_cards,
    _archive_symbol_order,
    _archive_next_action_lines,
    _archive_conclusion_context,
    _candidate_research_context_lines,
    _candidate_discussion_snapshot_context,
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
    _candidate_debate_evidence_lines,
    _candidate_debate_detail_lines,
    _candidate_empty_journey_message,
    _render_candidate_evidence_drawers,
    _candidate_linkage_context,
    _candidate_symbol_order,
    _command_center_brief_lines,
    _day_replay_digest_lines,
    _day_replay_next_step_line,
    _archive_debate_evidence_lines,
    _debate_evidence_composition_line,
    _debate_brief_cards,
    _debate_lane_status_context,
    _debate_overview_lines,
    _debate_process_lines,
    _debate_priority_digest_lines,
    _debate_result_lines,
    _debate_signal_value_tier,
    _debate_vote_snapshot_lines,
    _ordered_home_debates,
    _salient_home_debates,
    _card_emphasis,
    _home_focus_spotlights,
    _home_focus_action_targets,
    _home_spotlight_lines,
    _home_debate_item_lines,
    _home_action_rail_items,
    _home_brief_cards,
    _home_evidence_entry_lines,
    _home_execution_blocked_summary,
    _home_primary_focus_card,
    _home_reading_order_lines,
    _runtime_boundary_card_context,
    _research_radar_card,
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
    _phase_nav_name,
    _provider_prioritized_debates,
    _prioritized_research_lines,
    _queue_item_meta,
    _quick_bar_symbols,
    _research_task_id_for_review_card,
    _render_execution_focus,
    _review_context_for_symbol,
    _review_source_label,
    _resolve_task_for_date,
    _resolve_task_for_date_with_reason,
    _report_archive_status,
    _raw_report_boundary_lines,
    _research_path_steps,
    _sanitize_raw_report_markdown,
    _resolve_workspace_symbol,
    _same_day_message_lines,
    _simple_candidate_card_lines,
    _ordered_same_day_message_rows,
    _signal_evidence_context,
    _should_render_candidate_journey,
    _source_lag_display,
    _source_status_verdict_line,
    _spotlight_as_candidate_card,
    _symbol_option_label,
    _task_message_summary,
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
    _workspace_handoff_notice_lines,
    _workspace_nav_items,
    _WorkspaceHandoff,
    _render_two_line_nav_label,
    _TwoLineNavLabel,
    _render_report_archive_center,
    _render_review_phase_bar,
    _render_symbol_quick_bar,
    _render_workspace_navigation,
    _render_top_navigation,
    _workspace_widget_state,
    _workspace_jump_state,
    _review_to_archive_handoff_lines,
    _execution_to_review_handoff_lines,
    _execution_to_archive_handoff_lines,
    _same_day_digest_conclusion_lines,
    _same_day_digest_decision_line,
    _same_day_summary_focus_context,
    _same_day_summary_handoff_lines,
    _queue_same_day_summary_handoff,
    _archive_to_review_handoff_lines,
    _review_to_execution_handoff_lines,
    _archive_to_execution_handoff_lines,
    _workspace_symbol_handoff_lines,
    _queue_workspace_symbol_handoff,
    _same_day_digest_snapshot_lines,
    _same_day_summary_card_lines,
    _same_day_spotlight_card_lines,
    _same_day_spotlight_card_tone,
    _render_same_day_candidate_spotlights,
    _timeline_debate_process_line,
    _timeline_debate_conclusion_lines,
    _queue_home_action_rail_handoff,
    _queue_home_spotlight_handoff,
    _queue_home_debate_handoff,
    _queue_home_selection_handoff,
    _render_same_day_phase_jump_bar,
    _simple_research_unlock_context,
)
from scripts.export_dashboard_db import export_db
from scripts.render_dashboard import (
    _gate_status_for_display,
    latest_candidate_date,
    read_candidates,
    read_debate_results,
    read_ledger_rows,
    read_preferred_candidates,
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
    assert "仅供研究复核 / 不连接券商 / 不触发真实委托" in html
    assert "600519" in html
    assert "贵州茅台" in html
    assert "阈值版本 1.0.0" in html
    assert "候选数据日" in html
    assert "纸面记录" in html
    assert "纸面持有跟踪" in html
    assert "等待入场数据" in html
    assert "等待 2026-05-29 次日开盘" in html
    assert "数据情况" in html
    assert "fallback" in html
    assert "通知级别 warning" in html
    assert "数据源 fallback" in html
    assert "通知" in html
    assert "已切换备用源" in html
    assert "已切换到备用数据源 eastmoney" in html
    assert "fallback 数据源生成" in html
    assert "主链状态总览" in html
    assert "主链复核" in html
    assert "候选分层" in html
    assert "/ 继续观察" in html
    assert "参考价" in html
    assert "最多亏到" in html
    assert "先看目标" in html
    assert "仓位参考" in html
    assert "买点" not in html
    assert "止盈" not in html
    assert "buy_candidate" not in html


def test_static_dashboard_candidate_cards_frontload_catalyst_chain() -> None:
    candidates = [
        {
            "symbol": "688256",
            "name": "寒武纪",
            "date": "2026-06-30",
            "score": "79",
            "rating": "buy_candidate",
            "reasons": "算力链量能改善",
            "risks": "高开回落风险",
            "news_catalyst_judgement": "supports",
            "news_catalyst_lead": "688256 寒武纪 偏多｜Physical AI 映射｜边缘算力需求升温",
            "cross_market_primary_theme": "海外物理AI叙事升温",
            "cross_market_action": "纸面复核",
            "cross_market_chain_summary": (
                "英伟达 Physical AI -> A股机器人/算力链映射｜确认 龙头放量"
            ),
            "cross_market_validation_signals": '["机器人龙头放量上攻"]',
            "cross_market_invalidation_signals": '["只有海外叙事但A股不共振"]',
        }
    ]

    html = render_dashboard(candidates, [], "测试面板")

    assert (
        "<b>催化</b>消息支持: 688256 寒武纪 偏多｜Physical AI 映射｜边缘算力需求升温"
        in html
    )
    assert "<b>跨市主线</b>海外物理AI叙事升温(纸面复核)" in html
    assert "<b>传导</b>英伟达 Physical AI -&gt; A股机器人/算力链映射" in html
    assert "<b>确认</b>机器人龙头放量上攻" in html
    assert "<b>失效</b>只有海外叙事但A股不共振" in html


def test_dashboard_renders_compact_daily_digest_panel() -> None:
    digest = """# 收盘总览-2026-06-02

## 结果
- 结论: 双门通过，今日保留纸面复核
- 数据: eastmoney fallback 已恢复
- 候选: 600519 贵州茅台进入纸面复核
- 讨论: 多 Agent 支持偏多但需确认承接
- 风险: 高开低走则失效
- 第六条: 不应该出现在首页压缩卡片

## 长文
这里是完整 briefing 正文，不应该直接铺在首页。
"""

    html = render_dashboard([], [], "测试面板", daily_digest_markdown=digest)

    assert "当天消息汇总" in html
    assert "结论: 双门通过，今日保留纸面复核" in html
    assert "风险: 高开低走则失效" in html
    assert "第六条: 不应该出现在首页压缩卡片" not in html
    assert "这里是完整 briefing 正文" not in html


def test_dashboard_daily_digest_prioritizes_agent_decision_points() -> None:
    digest = """# 收盘总览-2026-06-02

## 结果
- 流程: 5/5 成功 | 30.0s
- 数据: eastmoney fallback 已恢复
- 普通流水: 运行产物已写入
- 低价值记录: 只应该留在完整报告
- 结论: 今日保留纸面复核
- 跨市主线: 外盘算力链扩散
- 风险: 高开低走则失效
- 候选: 600519 贵州茅台进入纸面复核

## 讨论
- 讨论支持: 外盘算力链扩散
- 讨论待确认: 先确认开盘承接
- 讨论执行: 触发 竞价强于同板块均值
"""

    html = render_dashboard([], [], "测试面板", daily_digest_markdown=digest)

    assert "当天消息汇总" in html
    assert "结论: 今日保留纸面复核" in html
    assert "跨市主线: 外盘算力链扩散" in html
    assert "风险: 高开低走则失效" in html
    assert "候选: 600519 贵州茅台进入纸面复核" in html
    assert "讨论支持: 外盘算力链扩散" not in html
    assert "讨论待确认: 先确认开盘承接" not in html
    assert "讨论执行: 触发 竞价强于同板块均值" not in html
    assert "低价值记录: 只应该留在完整报告" not in html


def test_dashboard_synthesizes_digest_from_latest_run_event_when_daily_digest_missing() -> (
    None
):
    rows = [
        {
            "symbol": "__RUN__",
            "status": "blocked_by_circuit_breaker",
            "event_type": "blocked_by_circuit_breaker",
            "signal_date": "2026-07-07",
            "reason": "组合保护冷却期中，至 2026-07-12 解除",
            "run_task_id": "coldstart",
            "run_actual_source": "sqlite_db",
            "run_data_latest_trade_date": "2026-07-07",
            "run_data_lag_days": 0,
            "run_market_context_lines": [
                "北向资金: 偏强（5日 z=1.20），外资风险偏好改善。",
                "全局雷达: 全市场 偏多｜海外风险偏好回暖。",
            ],
            "run_fetched_frame_count": 3000,
            "run_screened_count": 0,
            "run_final_count": 0,
            "run_circuit_breaker_triggered": True,
            "run_circuit_breaker_reason": "组合保护冷却期中，至 2026-07-12 解除",
            "daily_pnl_pct": -11.4576,
            "monthly_pnl_pct": -19.5162,
        }
    ]

    html = render_dashboard([], rows, "测试面板")

    assert "当天消息汇总" in html
    assert "结论: 组合保护生效，暂停新增纸面复核" in html
    assert "风险/阻塞: 组合保护冷却期中，至 2026-07-12 解除" in html
    assert "运行状态: 任务 coldstart / 日期 2026-07-07" in html
    assert (
        "数据: 当前实际源 sqlite_db 只适合历史验证，盘中短线不可用（live_short=avoid） / 数据日 2026-07-07 / 延迟 0 天"
        in html
    )
    assert "市场上下文: 北向资金: 偏强（5日 z=1.20），外资风险偏好改善。" in html
    assert "市场上下文: 全局雷达: 全市场 偏多｜海外风险偏好回暖。" not in html
    assert "流程: 获取 3000 / 筛选 0 / 候选 0" not in html


def test_dashboard_keeps_explicit_daily_digest_over_runtime_fallback() -> None:
    rows = [
        {
            "symbol": "__RUN__",
            "status": "blocked_by_circuit_breaker",
            "signal_date": "2026-07-07",
            "run_circuit_breaker_triggered": True,
            "run_circuit_breaker_reason": "不应覆盖真实日报",
        }
    ]
    digest = "- 结论: 真实日报优先\n- 讨论结论: 已进入日报"

    html = render_dashboard([], rows, "测试面板", daily_digest_markdown=digest)

    assert "结论: 真实日报优先" in html
    assert "讨论结论: 已进入日报" in html
    assert "不应覆盖真实日报" not in html


def test_dashboard_places_candidates_before_supporting_system_details() -> None:
    candidates = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "date": "2026-06-02",
            "score": "71",
            "rating": "buy_candidate",
            "reasons": "趋势回踩",
            "risks": "RSI偏热",
        }
    ]
    digest = "- 结论: 今日先看候选卡片\n- 风险: 系统细节默认收起"

    html = render_dashboard(candidates, [], "测试面板", daily_digest_markdown=digest)

    assert "当天消息汇总" in html
    assert "短线决策看板" in html
    assert "aqsp-static-two-column" in html
    assert "研究候选已解锁" in html
    assert "生产 gate 未放行" in html
    assert 'href="#today-candidates"' in html
    assert 'href="#agent-discussion"' in html
    assert 'href="#daily-digest"' in html
    assert 'href="#supporting-status"' in html
    assert "今日候选卡片" in html
    assert "Agent讨论" in html
    assert "系统与历史辅助信息" in html
    assert "最近信号" in html
    assert "纸面记录" in html
    assert "card-quick-lines" in html
    assert "逻辑" in html
    assert "风险" in html
    assert 'id="supporting-status"' in html
    assert 'class="panel panel-fold supporting-panel"' in html
    assert html.index('id="today-candidates"') < html.index('id="agent-discussion"')
    assert html.index('id="agent-discussion"') < html.index('id="daily-digest"')
    assert 'href="agents.html"' not in html
    assert html.index('id="daily-digest"') < html.index('id="supporting-status"')
    assert html.index("系统与历史辅助信息") < html.index("最近信号")


def test_dashboard_static_frontdesk_surfaces_agent_result_and_process() -> None:
    candidates = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "date": "2026-06-02",
            "score": "71",
            "rating": "watch",
            "reasons": "趋势回踩",
            "risks": "高开低走则失效",
            "candidate_next_step": "确认开盘承接",
        }
    ]
    debate_map = {
        "600519::2026-06-02": {
            "symbol": "600519",
            "name": "贵州茅台",
            "signal_date": "2026-06-02",
            "debate_date": "2026-06-02",
            "final_consensus": "委员会维持观察，等待量能确认。",
            "recommended_adjustment": "keep",
            "support_points": ["白酒板块资金回流"],
            "watch_items": ["竞价不能弱于板块均值"],
            "cross_market_summary": "消费风险偏好修复",
            "final_vote": {
                "bull": "bullish",
                "risk_control": "neutral",
                "bear": "bearish",
            },
            "rounds": [
                {
                    "round_num": 2,
                    "summary": "多头认可修复，风控要求确认承接。",
                }
            ],
        }
    }

    html = render_dashboard(candidates, [], "辩论面板", debate_map=debate_map)

    assert "Agent讨论" in html
    assert "委员会维持观察，等待量能确认。" in html
    assert "跨市链: 消费风险偏好修复 | 先看 600519 贵州茅台" in html
    assert "投票: 看多 1 / 中性 1 / 看空 1" in html
    assert "过程: 第 2 轮摘要: 多头认可修复，风控要求确认承接。" in html
    assert html.index('id="today-candidates"') < html.index('id="agent-discussion"')


def test_dashboard_simple_research_unlock_context_separates_research_and_gate() -> None:
    provider = SimpleNamespace(
        runtime_overview=lambda _signal_date: SimpleNamespace(
            walkforward_runtime_line="生产 gate: 生产回测 资源不足阻塞 / 后续换更大机器或显式放行后再跑",
            gate_blocker_line="",
            coldstart_progress="35/30",
        )
    )
    overview = SimpleNamespace(
        actionable_total=1,
        watch_total=2,
        blocked_total=1,
    )

    title, lines, tone = _simple_research_unlock_context(
        provider=provider,
        signal_date="2026-07-09",
        overview=overview,
    )

    assert title == "研究候选已解锁"
    assert tone == "unlocked"
    assert any("今日 4 张候选卡片可看" in line for line in lines)
    assert any("冷启动样本 35/30" in line for line in lines)
    assert any("生产 gate 未放行" in line and "资源不足阻塞" in line for line in lines)


def test_dashboard_simple_research_unlock_context_prefers_visible_queue_counts() -> (
    None
):
    provider = SimpleNamespace(runtime_overview=lambda _signal_date: None)
    overview = SimpleNamespace(
        actionable_total=0,
        watch_total=3,
        blocked_total=7,
    )

    title, lines, tone = _simple_research_unlock_context(
        provider=provider,
        signal_date="2026-07-10",
        overview=overview,
        queue_counts=(1, 2, 7),
    )

    assert title == "研究候选已解锁"
    assert tone == "unlocked"
    assert any(
        "今日 10 张候选卡片可看：纸面 1 / 观察 2 / 阻塞 7。" in line for line in lines
    )


def test_static_dashboard_gate_display_rejects_string_boolean_sidecar() -> None:
    ok, detail = _gate_status_for_display(
        {
            "both_pass": "true",
            "pbo_valid": "true",
            "deflated_sharpe": 1.5,
            "pbo": 0.24,
            "n_periods": 12,
        }
    )

    assert ok is False
    assert "both_pass" in detail
    assert "PBO占位" in detail


def test_static_dashboard_gate_display_rejects_boolean_period_count() -> None:
    ok, detail = _gate_status_for_display(
        {
            "both_pass": True,
            "pbo_valid": True,
            "deflated_sharpe": 1.5,
            "pbo": 0.24,
            "n_periods": True,
        }
    )

    assert ok is False
    assert detail == "missing/invalid metrics"


def test_static_dashboard_gate_display_rejects_boolean_or_nan_metrics() -> None:
    ok, detail = _gate_status_for_display(
        {
            "both_pass": True,
            "pbo_valid": True,
            "deflated_sharpe": True,
            "pbo": "NaN",
            "n_periods": 12,
        }
    )

    assert ok is False
    assert detail == "missing/invalid metrics"


def test_static_dashboard_gate_display_rejects_string_numeric_metrics() -> None:
    ok, detail = _gate_status_for_display(
        {
            "both_pass": True,
            "pbo_valid": True,
            "deflated_sharpe": "1.5",
            "pbo": "0.24",
            "n_periods": 12,
        }
    )

    assert ok is False
    assert detail == "missing/invalid metrics"


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

    assert "研究进展" in html
    assert "113 findings" in html
    assert "mpquant/Ashare" in html
    assert "大盘择时 / 市场状态过滤" in html
    assert "门控中" in html


def test_dashboard_research_absorption_panel_labels_config_backed_queue() -> None:
    summary = ResearchSummary(
        generated_at="",
        total_findings=0,
        pipeline_summaries=(),
        absorbed_families=(
            ResearchFamilySummary(
                family_id="market_regime_timing_filter",
                name="大盘择时 / 市场状态过滤",
                status="research_absorbed",
                runtime_stage="report_only",
                absorbed_from_count=4,
                runtime_gate_count=4,
            ),
        ),
        source_candidates=(),
        next_actions=(),
        prereq_items=(),
        implemented_family_count=5,
        report_only_family_count=1,
        gated_family_count=0,
    )

    html = render_dashboard([], [], "研究面板", research_summary=summary)

    assert "config-backed" in html
    assert "未落盘（按配置吸收队列展示）" in html
    assert "0 findings" not in html


def test_dashboard_research_radar_summarizes_absorption_without_scoring_claims() -> (
    None
):
    summary = ResearchSummary(
        generated_at="",
        total_findings=113,
        pipeline_summaries=(
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
            ResearchFamilySummary(
                family_id="chan_theory_context",
                name="缠论结构语境",
                status="research_absorbed",
                runtime_stage="report_only",
                absorbed_from_count=2,
                runtime_gate_count=3,
            ),
        ),
        source_candidates=(),
        next_actions=(
            ResearchActionItem(
                kind="strategy",
                item_id="chan_theory_context",
                name="缠论结构语境",
                stage="report_only",
                priority="P1",
                blocker="只进报告，不直接入分",
                reference_hint="chanlun-pro",
            ),
        ),
        prereq_items=(),
        implemented_family_count=5,
        report_only_family_count=1,
        gated_family_count=1,
    )

    card = _research_radar_card(summary)
    rendered = "\n".join((card.title, *(line for line in card.lines)))

    assert "研究发现 113 条" in card.title
    assert ("已吸收", "2") in card.metrics
    assert ("只进报告", "1") in card.metrics
    assert ("门控中", "1") in card.metrics
    assert "研究结论不会直接改写评分" in rendered
    assert "缠论结构语境" in rendered
    assert "只进报告" in rendered
    assert "当前主链评分" not in rendered


def test_dashboard_research_radar_labels_config_backed_queue_when_findings_missing() -> (
    None
):
    summary = ResearchSummary(
        generated_at="",
        total_findings=0,
        pipeline_summaries=(),
        absorbed_families=(
            ResearchFamilySummary(
                family_id="market_regime_timing_filter",
                name="大盘择时 / 市场状态过滤",
                status="research_absorbed",
                runtime_stage="report_only",
                absorbed_from_count=4,
                runtime_gate_count=4,
            ),
        ),
        source_candidates=(),
        next_actions=(),
        prereq_items=(),
        implemented_family_count=5,
        report_only_family_count=1,
        gated_family_count=0,
    )

    card = _research_radar_card(summary)

    assert "研究发现 未落盘（按配置吸收队列展示）" in card.title
    assert ("研究发现", "配置队列") in card.metrics


def test_dashboard_research_radar_surfaces_prereq_blockers() -> None:
    summary = ResearchSummary(
        generated_at="",
        total_findings=0,
        pipeline_summaries=(),
        absorbed_families=(),
        source_candidates=(),
        next_actions=(),
        prereq_items=(
            ResearchPrereqItem(
                kind="data_source",
                item_id="tushare",
                name="Tushare Pro",
                status="needs_env",
                missing_env_vars=("TUSHARE_TOKEN",),
                fixture_hints=("tests/fixtures/tushare_trade_calendar.json",),
                user_action="在本地 .env 配置 TUSHARE_TOKEN。",
                code_action="补 PIT fixture。",
                registry_runtime_ready=False,
            ),
            ResearchPrereqItem(
                kind="strategy",
                item_id="market_regime_timing_filter",
                name="大盘择时 / 市场状态过滤",
                status="needs_fixture",
                missing_env_vars=(),
                fixture_hints=("tests/fixtures/regime_index_breadth.csv",),
                user_action="无需额外账号。",
                code_action="先做 regime detector v2，只改变过滤标签。",
                registry_runtime_ready=None,
            ),
        ),
        implemented_family_count=5,
        report_only_family_count=0,
        gated_family_count=0,
    )

    card = _research_radar_card(summary)
    prereq = "\n".join(card.prereq_lines)

    assert "Tushare Pro 缺 TUSHARE_TOKEN" in prereq
    assert "大盘择时 / 市场状态过滤" in prereq
    assert "在本地 .env 配置 TUSHARE_TOKEN" in prereq


def test_dashboard_research_radar_has_safe_empty_state_when_summary_missing() -> None:
    card = _research_radar_card(None)
    rendered = "\n".join((card.title, *(line for line in card.lines)))

    assert card.title == "研究进展未更新"
    assert ("研究发现", "-") in card.metrics
    assert "研究队列缺失不影响当前主链评分" in rendered
    assert "只放在报告里" in rendered


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


def test_dashboard_warns_when_latest_source_is_history_only_for_live_short() -> None:
    rows = [
        {
            "signal_date": "2026-07-07",
            "symbol": "__RUN__",
            "status": "blocked_by_circuit_breaker",
            "run_requested_source": "sqlite_db",
            "run_actual_source": "sqlite_db",
            "run_source_health_label": "healthy",
            "run_source_health_message": "sqlite_db 健康",
            "run_data_latest_trade_date": "2026-07-07",
            "run_data_lag_days": 0,
        }
    ]

    html = render_dashboard([], rows, "历史源面板")

    assert "实际源 sqlite_db 只适合历史验证，盘中短线不可用" in html
    assert "不要把本页当成实时短线信号质量样本" in html


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
            "cross_market_summary": "外盘风险偏好修复",
            "cross_market_validation_summary": "次日竞价高弹性方向明显强于防御方向",
            "cross_market_invalidation_summary": "外盘强但A股竞价无明显风险偏好跟随",
            "support_points": ["技术面强势且量价配合。"],
            "watch_items": ["先确认开盘承接是否持续。"],
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

    assert "多 Agent 结论摘要" in html
    assert '<details class="debate-round">' in html
    assert "查看讨论附录" in html
    assert "讨论附录只保留轮次摘要，不展示原始辩词。" in html
    assert "🐂 技术多头" not in html
    assert "🛡️ 风险控制" not in html
    assert "论点" not in html
    assert "反驳" not in html
    assert (
        "外盘风险偏好修复 | 先看 600519 贵州茅台 | 确认 次日竞价高弹性方向明显强于防御方向 | 失效 外盘强但A股竞价无明显风险偏好跟随"
        in html
    )
    assert "讨论支持: 技术面强势且量价配合。" in html
    assert "讨论待确认: 先确认开盘承接是否持续。" in html
    assert "趋势延续概率较高" not in html
    assert "高位波动放大" not in html


def test_dashboard_does_not_render_archived_debate_when_no_current_candidates() -> None:
    debate_map = {
        "600519": {
            "symbol": "600519",
            "name": "贵州茅台",
            "debate_date": "2026-06-01",
            "final_consensus": "历史辩论结论只作归档。",
            "recommended_adjustment": "raise",
            "original_score": 71.0,
            "adjusted_score": 82.0,
            "adjustment_weight": 0.15,
            "disagreement_score": 0.2,
        }
    }

    html = render_dashboard([], [], "空候选面板", debate_map=debate_map)

    assert "历史辩论结论只作归档。" not in html
    assert "历史多 Agent 归档" not in html
    assert "多 Agent 结论摘要" not in html


def test_dashboard_hides_stale_debate_when_candidate_has_no_matching_signal_date() -> (
    None
):
    candidates = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "date": "2026-06-10",
            "score": "71",
            "rating": "buy_candidate",
            "reasons": "趋势延续",
            "risks": "追高风险",
        }
    ]
    debate_map = {
        "600519": {
            "symbol": "600519",
            "name": "贵州茅台",
            "debate_date": "2026-06-01",
            "original_score": 71.0,
            "adjusted_score": 99.0,
            "adjustment_weight": 0.39,
            "recommended_adjustment": "raise",
            "final_consensus": "旧辩论结论仅作归档。",
        }
    }

    html = render_dashboard(candidates, [], "辩论面板", debate_map=debate_map)

    assert '<span class="score">71.00</span>' in html
    assert "历史多 Agent 归档" not in html
    assert "旧辩论结论仅作归档。" not in html
    assert "观点修正上调" not in html
    assert "原始 71.00 · 调整 99.00" not in html


def test_dashboard_keeps_current_debate_from_overriding_candidate_score() -> None:
    candidates = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "date": "2026-06-10",
            "score": "71",
            "rating": "buy_candidate",
            "reasons": "趋势延续",
            "risks": "追高风险",
        }
    ]
    debate_map = {
        "600519": {
            "symbol": "600519",
            "name": "贵州茅台",
            "related_signal_date": "2026-06-10",
            "debate_date": "2026-06-10",
            "original_score": 71.0,
            "adjusted_score": 99.0,
            "adjustment_weight": 0.39,
            "recommended_adjustment": "raise",
            "final_consensus": "只作附件参考。",
        }
    }

    html = render_dashboard(candidates, [], "辩论面板", debate_map=debate_map)

    assert '<span class="score">71.00</span>' in html
    assert '<span class="score">99.00</span>' not in html
    assert "系统评分 71.00 · 附件参考 99.00" in html
    assert "多 Agent 只提供附件参考，不改写系统筛选评分。" in html
    assert "原始 71.00 · 调整 99.00" not in html


def test_dashboard_matches_debate_by_symbol_and_related_signal_date_when_history_viewed(
    tmp_path: Path,
) -> None:
    debate_path = tmp_path / "debate_results.jsonl"
    debate_rows = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "related_signal_date": "2026-06-01",
            "debate_date": "2026-06-02",
            "final_consensus": "历史当天辩论结论。",
            "recommended_adjustment": "raise",
            "original_score": 71.0,
            "adjusted_score": 82.0,
            "adjustment_weight": 0.15,
            "disagreement_score": 0.2,
        },
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "related_signal_date": "2026-06-10",
            "debate_date": "2026-06-11",
            "final_consensus": "更新信号日辩论结论。",
            "recommended_adjustment": "lower",
            "original_score": 68.0,
            "adjusted_score": 60.0,
            "adjustment_weight": -0.2,
            "disagreement_score": 0.5,
        },
    ]
    debate_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in debate_rows) + "\n",
        encoding="utf-8",
    )
    candidates = [
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "date": "2026-06-01",
            "score": "71",
            "rating": "buy_candidate",
        }
    ]

    debate_map = read_debate_results(debate_path)
    html = render_dashboard(candidates, [], "历史辩论面板", debate_map=debate_map)

    assert set(debate_map) == {"600519::2026-06-01", "600519::2026-06-10"}
    assert "历史当天辩论结论。" in html
    assert "更新信号日辩论结论。" not in html
    assert '<span class="score">71.00</span>' in html
    assert '<span class="score">82.00</span>' not in html
    assert "系统评分 71.00 · 附件参考 82.00" in html
    assert "历史多 Agent 归档" not in html


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

    assert "/ 继续观察名单" in html
    assert ">风险: 追高风险<" in html
    assert "watch" not in html


def test_dashboard_surfaces_watch_candidate_lifecycle_details() -> None:
    candidates = [
        {
            "symbol": "688981",
            "name": "中芯国际",
            "date": "2026-06-05",
            "score": "-9",
            "rating": "buy_candidate",
            "reasons": "MA20 斜率向上",
            "risks": "收盘价低于 MA20",
            "candidate_status": "新晋",
            "candidate_next_step": "等待量价继续走强后，再评估是否转入重点跟踪名单",
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
            "candidate_next_step": "等待板块暴露回落后，再重新评估跟踪优先级",
            "candidate_review_window": "板块分化时",
            "candidate_review_priority": "medium",
        },
    ]

    html = render_dashboard(candidates, [], "主链观察面板")

    assert "主链状态总览" in html
    assert "今日纸面复核名单" in html
    assert "纸面复核与观察对象已按当前主链输出分层。" in html
    assert "重点跟踪与继续观察" not in html
    assert "主链复核" in html
    assert "阻塞" in html
    assert "明日复核" in html
    assert "/ 继续观察名单" in html
    assert "新晋" in html
    assert "下一步: 等待量价继续走强后，再评估是否转入重点跟踪名单" in html
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
    assert "不要把这个页面当作今日复核结论" in html


def test_dashboard_handles_missing_inputs() -> None:
    html = render_dashboard([], [], "空面板")

    assert "空面板" in html
    assert "本次没有候选股" in html
    assert "本次没有候选股。先确认宝塔 daily/intraday 是否成功跑完" in html
    assert "还没有信号记录。先等下一次主链跑批完成" in html
    assert "还没有纸面跟踪记录。出现候选后" in html


def test_dashboard_source_lag_display_uses_readable_missing_label() -> None:
    assert _source_lag_display("") == "未记录"
    assert _source_lag_display("-") == "未记录"
    assert _source_lag_display("未记录") == "未记录"
    assert _source_lag_display(0) == "0 天"
    assert _source_lag_display("2") == "2 天"


def test_read_candidates_handles_empty_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "latest.csv"
    csv_path.write_text("", encoding="utf-8")

    assert read_candidates(csv_path) == []


def test_read_preferred_candidates_uses_intraday_when_same_or_newer(
    tmp_path: Path,
) -> None:
    close_path = tmp_path / "latest.csv"
    intraday_path = tmp_path / "intraday_latest.csv"
    today = now_shanghai().date().isoformat()
    pd.DataFrame(
        [{"symbol": "600519", "name": "贵州茅台", "date": today, "score": "51"}]
    ).to_csv(close_path, index=False)
    pd.DataFrame(
        [{"symbol": "600900", "name": "长江电力", "date": today, "score": "55"}]
    ).to_csv(intraday_path, index=False)

    selected = read_preferred_candidates(
        close_path,
        intraday_csv_path=intraday_path,
    )

    assert selected.source_label == "盘中实时"
    assert selected.path == intraday_path
    assert [row["symbol"] for row in selected.candidates] == ["600900"]
    assert selected.candidates[0]["__candidate_source_label"] == "盘中实时"


def test_read_preferred_candidates_falls_back_when_intraday_is_stale(
    tmp_path: Path,
) -> None:
    close_path = tmp_path / "latest.csv"
    intraday_path = tmp_path / "intraday_latest.csv"
    pd.DataFrame([{"symbol": "600519", "date": "2026-07-10", "score": "51"}]).to_csv(
        close_path,
        index=False,
    )
    pd.DataFrame([{"symbol": "600900", "date": "2026-07-09", "score": "55"}]).to_csv(
        intraday_path,
        index=False,
    )

    selected = read_preferred_candidates(
        close_path,
        intraday_csv_path=intraday_path,
    )

    assert selected.source_label == "收盘主链"
    assert selected.path == close_path
    assert [row["symbol"] for row in selected.candidates] == ["600519"]


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


def test_dashboard_simple_candidate_card_lines_frontload_catalyst_chain() -> None:
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="688256",
        name="寒武纪",
        display_name="688256 寒武纪",
        rank_label="第一顺位",
        score=79.0,
        action_label="维持观察",
        status_label="待确认",
        decision_note="",
        next_step="等待盘口承接",
        blocker="",
        review_meta="",
        reasons=("算力链量能改善",),
        risks=("高开回落风险",),
        strategies=("relative_strength",),
        data_source="eastmoney",
        news_catalyst_summary="消息支持: 英伟达 Physical AI 平台发布",
        cross_market_summary="海外物理AI叙事升温(纸面复核)",
        cross_market_chain_summary="英伟达 Physical AI -> A股机器人/算力链映射｜确认 龙头放量",
        cross_market_validation_summary="机器人龙头放量上攻",
        cross_market_invalidation_summary="只有海外叙事但A股不共振",
    )

    assert _simple_candidate_card_lines(card)[:5] == (
        "催化: 消息支持: 英伟达 Physical AI 平台发布",
        "跨市主线: 海外物理AI叙事升温(纸面复核)",
        "传导: 英伟达 Physical AI -> A股机器人/算力链映射",
        "确认: 机器人龙头放量上攻",
        "失效: 只有海外叙事但A股不共振",
    )


def test_dashboard_simple_recommendation_panel_renders_compact_candidate_cards(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardCandidateSpotlight,
        DashboardDateOverview,
    )

    markdown_calls: list[str] = []

    class _Provider:
        @staticmethod
        def runtime_overview(signal_date: str):
            assert signal_date == "2026-07-09"
            return type(
                "_Runtime",
                (),
                {
                    "cooldown_until": "2026-07-12",
                    "risk_reason": "组合保护冷却期中",
                },
            )()

    task_view = type("_TaskView", (), {"detail_cards": ()})()
    spotlight = DashboardCandidateSpotlight(
        symbol="600900",
        display_name="600900 长江电力",
        score=55.38,
        action_label="结果不变",
        status_label="观察",
        blocker="",
        next_step="等待下一次实时刷新",
        review_meta="",
        task_labels=("盘中观察",),
        reasons=("MACD 动能改善", "RSI 位于健康强势区间"),
        risks=(),
        cross_market_summary="美股电力设备走强，关注A股映射",
    )
    overview = DashboardDateOverview(
        signal_date="2026-07-09",
        task_count=1,
        actionable_total=0,
        watch_total=1,
        blocked_total=0,
        top_task_label="盘中观察",
        top_headline="盘中观察 1 条",
        blocker_headline="",
        focus_headline="",
        workflow_summary="",
        archive_summary="",
    )
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_calls.append(str(body)),
    )

    dashboard._render_simple_recommendation_panel(
        provider=_Provider(),
        signal_date="2026-07-09",
        task_view=task_view,
        spotlights=(spotlight,),
        overview=overview,
    )

    html = "\n".join(markdown_calls)
    assert "今日候选已产生，组合保护中" in html
    assert "风控压制" in html
    assert "600900 长江电力" in html
    assert "跨市 美股电力设备走强，关注A股映射" in html
    assert "aqsp-simple-candidate-grid" in html
    assert "aqsp-simple-candidate-card watch" in html
    assert "aqsp-observation-row" not in html


def test_dashboard_simple_agent_panel_explains_cooldown_empty_state(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    rendered_cards: list[dict[str, object]] = []

    class _Provider:
        @staticmethod
        def runtime_overview(signal_date: str):
            assert signal_date == "2026-07-09"
            return type(
                "_Runtime",
                (),
                {
                    "cooldown_until": "2026-07-12",
                    "risk_reason": "组合保护冷却期中",
                },
            )()

    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    dashboard._render_simple_agent_panel(
        provider=_Provider(),
        signal_date="2026-07-09",
        debates=(),
    )

    assert rendered_cards[0]["title"] == "当天无 Agent 讨论结论"
    assert rendered_cards[0]["tone"] == "blocked"
    assert any(
        "组合保护解除日 2026-07-12" in line for line in rendered_cards[0]["lines"]
    )


def test_dashboard_simple_agent_panel_keeps_current_candidate_debate_first(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardDebateSummary

    rendered_cards: list[dict[str, object]] = []

    def _debate(
        symbol: str, name: str, disagreement_score: float
    ) -> DashboardDebateSummary:
        return DashboardDebateSummary(
            signal_date="2026-07-10",
            symbol=symbol,
            display_name=f"{symbol} {name}",
            debate_id=symbol,
            rating="buy_candidate",
            original_score=70.0,
            adjusted_score=70.0,
            adjustment_weight=0.0,
            recommended_adjustment="keep",
            recommended_adjustment_label="维持观察",
            disagreement_score=disagreement_score,
            consensus="等待确认",
            adjustment_reason="",
            bull_count=1,
            bear_count=1,
            neutral_count=1,
            round_count=2,
            regime="unknown",
            data_source="sina",
            thresholds_version="test",
            summary_lines=(),
            round_summaries=(),
            risk_warnings=(),
            opportunity_highlights=(),
            agent_views=(),
        )

    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    dashboard._render_simple_agent_panel(
        provider=object(),
        signal_date="2026-07-10",
        debates=(_debate("603893", "瑞芯微", 0.1), _debate("000938", "紫光股份", 0.9)),
    )

    assert rendered_cards[0]["kicker"] == "603893 瑞芯微"


def test_dashboard_simple_today_digest_hides_duplicate_committee_result(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardDateOverview

    rendered_cards: list[dict[str, object]] = []
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_same_day_digest_snapshot_lines",
        lambda *args, **kwargs: (
            "讨论结果: 603893 瑞芯微继续观察",
            "数据链路: 实时源 sina（live_short=fallback）",
            "主链推荐: 3 个待复核对象已落盘。",
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    dashboard._render_simple_today_digest(
        provider=object(),
        signal_date="2026-07-10",
        rows=(),
        task_view=object(),
        overview=DashboardDateOverview(
            signal_date="2026-07-10",
            task_count=1,
            actionable_total=3,
            watch_total=0,
            blocked_total=0,
            top_task_label="主链推荐",
            top_headline="今日速读",
            blocker_headline="",
            focus_headline="",
            workflow_summary="",
            archive_summary="",
        ),
        spotlights=(),
        debates=(),
    )

    assert rendered_cards[0]["lines"] == (
        "数据链路: 实时源 sina（live_short=fallback）",
        "主链推荐: 3 个待复核对象已落盘。",
    )


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
        "当前结论: 上调优先级 / 延续上升",
        "排队层级: 首选 / 评分 88.0",
        "下一步: 等待量能确认后决定是否保留主仓",
        "再看时间: 高优先级 / 开盘前后",
    )
    assert not any("不应回退" in line for line in lines)


def test_dashboard_focus_summary_lines_show_candidate_summary_when_cross_market_evidence_present() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="688981",
        name="中芯国际",
        display_name="688981 中芯国际",
        rank_label="第一顺位",
        score=79.0,
        action_label="维持原排序",
        status_label="等待确认",
        decision_note="跨市线索 海外物理AI叙事升温(纸面复核)｜同向 2 条｜反向 1 条；盘前主链: 倾向优先纸面复核",
        next_step="若龙头封单增强则优先复核",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=("volume_breakout",),
        data_source="eastmoney",
    )
    execution_focus = _DummyExecutionFocus()

    lines = _focus_summary_lines(
        selected_card=card,
        selected_spotlight=None,
        execution_focus=execution_focus,
    )

    assert (
        "候选摘要: 跨市线索 海外物理AI叙事升温(纸面复核)｜同向 2 条｜反向 1 条；盘前主链: 倾向优先纸面复核"
        in lines
    )


def test_dashboard_focus_summary_lines_prefer_same_day_spotlight_digest_when_available() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    card = DashboardCandidateCard(
        symbol="688981",
        name="中芯国际",
        display_name="688981 中芯国际",
        rank_label="第一顺位",
        score=79.0,
        action_label="维持原排序",
        status_label="等待确认",
        decision_note="主链继续保留复核优先级",
        next_step="若龙头封单增强则优先复核",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=("volume_breakout",),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="688981",
        display_name="688981 中芯国际",
        score=79.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="",
        next_step="若龙头封单增强则优先复核",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=(),
        risks=(),
        cross_market_summary="海外物理AI叙事升温(纸面复核)",
        cross_market_validation_summary="机器人龙头封单增强",
        cross_market_invalidation_summary="只有海外叙事但A股映射不跟",
    )
    execution_focus = _DummyExecutionFocus()

    lines = _focus_summary_lines(
        selected_card=card,
        selected_spotlight=spotlight,
        execution_focus=execution_focus,
    )

    assert (
        "跨市主线: 海外物理AI叙事升温(纸面复核) | 先看 688981 中芯国际 | 确认 机器人龙头封单增强 | 失效 只有海外叙事但A股映射不跟"
        in lines
    )
    assert not any(line.startswith("候选摘要:") for line in lines)


def test_dashboard_review_meta_helpers_hide_placeholder_values() -> None:
    assert _has_review_meta("高优先级 / 开盘前后") is True
    assert _has_review_meta("") is False
    assert _has_review_meta("-") is False
    assert _has_review_meta("暂无额外再看时间") is False

    assert (
        _review_meta_line("再看时间", "高优先级 / 开盘前后")
        == "再看时间: 高优先级 / 开盘前后"
    )
    assert _review_meta_line("再看时间", "-") == ""


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
        cross_market_summary="英伟达物理AI叙事升温(纸面复核)",
    )

    lines = _focus_summary_lines(
        selected_card=None,
        selected_spotlight=spotlight,
        execution_focus=execution_focus,
    )

    assert lines == (
        "跨市主线: 英伟达物理AI叙事升温(纸面复核) | 先看 300750 宁德时代",
        "涉及任务: 早盘策略、尾盘策略",
        "当前结论: 降级观察 / 观察阻塞",
        "当前重点: 高位波动放大，先等待分歧收敛",
        "统一复核: 中优先级 / 午后回看",
    )
    assert not any("主要从纸面记录回看" in line for line in lines)


def test_dashboard_focus_summary_lines_neutralize_action_words_in_candidate_and_spotlight() -> (
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
        rank_label="阻塞观察",
        score=88.0,
        action_label="上调优先级",
        status_label="观察阻塞",
        decision_note="立即买入后等待下单",
        next_step="新开仓失败，等待下单",
        blocker="买入条件不足，下单阻塞",
        review_meta="高优先级 / 开盘前后",
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
        blocker="立即买入条件不足",
        next_step="等待下单",
        review_meta="中优先级 / 午后回看",
        task_labels=("早盘策略",),
        reasons=(),
        risks=(),
    )
    execution_focus = _DummyExecutionFocus()

    rendered = "\n".join(
        (
            *_focus_summary_lines(
                selected_card=card,
                selected_spotlight=None,
                execution_focus=execution_focus,
            ),
            *_focus_summary_lines(
                selected_card=None,
                selected_spotlight=spotlight,
                execution_focus=execution_focus,
            ),
        )
    )

    for forbidden in ("立即买入", "下单", "新开仓", "买入条件"):
        assert forbidden not in rendered
    assert "纸面记录" in rendered


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


def test_dashboard_resolve_task_for_date_explains_fallback_when_current_task_missing() -> (
    None
):
    class _Row:
        def __init__(self, task_id: str, task_label: str) -> None:
            self.task_id = task_id
            self.task_label = task_label

    class _Snapshot:
        def __init__(self, task_id: str, task_label: str) -> None:
            self.task_id = task_id
            self.task_label = task_label

    class _Provider:
        def same_day_task_rows(self, signal_date: str):
            assert signal_date == "2026-06-04"
            return (
                _Row("main_chain", "主链推荐"),
                _Row("morning_breakout", "早盘策略"),
            )

        def preferred_task_for_date(self, signal_date: str) -> str:
            assert signal_date == "2026-06-04"
            return "main_chain"

        def task_snapshots(self, signal_date: str):
            assert signal_date == "2026-06-04"
            return (_Snapshot("briefing", "简报回看"),)

    resolved = _resolve_task_for_date_with_reason(
        provider=_Provider(),
        current_task_id="briefing",
        signal_date="2026-06-04",
    )

    assert resolved.task_id == "main_chain"
    assert resolved.reason == "该日无 简报回看，已到 主链推荐"


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
        cross_market_summary="英伟达物理AI叙事升温(纸面复核)",
        support_points=("映射链承接仍在延续。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日承接。",),
    )

    card = _spotlight_as_candidate_card(spotlight)

    assert card.symbol == "300750"
    assert card.name == "宁德时代"
    assert card.rank_label == "同日联动"
    assert card.data_source == "同日联动"
    assert card.reasons == ("量能放大",)
    assert (
        card.decision_note
        == "跨市线索 英伟达物理AI叙事升温(纸面复核)；讨论支持: 映射链承接仍在延续。；讨论反对: 高位分歧仍需压缩。；讨论待确认: 观察次日承接。"
    )


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
        research_verdict="倾向优先纸面复核，但先卡住 追高回撤风险",
        primary_risk_gate="追高回撤风险",
        next_trigger="先确认次日成交质量",
        bull_count=4,
        bear_count=2,
        neutral_count=2,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1.0.0",
        summary_lines=(
            "建议上调评分: 80.0 -> 82.0",
            "结论共识: 维持主推，但需要控制追高节奏",
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

    assert lines[0] == "结论: 建议上调评分 / 分歧 0.33"
    assert lines[1] == "共识: 维持主推，但需要控制追高节奏"
    assert lines[2] == "票数分布: 看多 4 / 看空 2 / 中性 2"
    assert lines[3] == "研究口径: 倾向优先纸面复核，但先卡住 追高回撤风险"
    assert lines[4] == "核心卡点: 追高回撤风险"
    assert lines[5] == "下一触发: 先确认次日成交质量"
    assert any("技术多头: 看多 / 置信 88%" in line for line in lines)
    assert any("风控: 中性 / 置信 72%" in line for line in lines)


def test_dashboard_debate_overview_lines_surface_missing_evidence() -> None:
    from aqsp.web.data_provider import DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-missing-evidence",
        rating="B",
        original_score=68.0,
        adjusted_score=68.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.0,
        consensus="",
        adjustment_reason="",
        bull_count=0,
        bear_count=0,
        neutral_count=0,
        round_count=0,
        regime="",
        data_source="",
        thresholds_version="",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    lines = _debate_overview_lines(debate)

    assert lines == (
        "结论: 建议维持评分 / 分歧 0.00",
        "共识: 暂未形成明确一致结论",
        "票数分布: 看多 0 / 看空 0 / 中性 0",
        "待补原因: 当前讨论未给出明确风险或机会，先回候选来龙去脉复核。",
    )


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
        research_verdict="倾向优先纸面复核，但先卡住 银行板块承接",
        primary_risk_gate="需确认银行板块承接",
        next_trigger="先确认次日成交质量",
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
    assert review_card.decision_note == "倾向优先纸面复核，但先卡住 银行板块承接"
    assert review_card.next_step == "先确认次日成交质量"
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
        symbol="600036",
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        focus_kind="debate",
        debate_id="debate-2",
        decision_source="debate",
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
        "dashboard_pending_handoff_symbol": "600036",
        "dashboard_pending_handoff_signal_date": "2026-06-05",
        "dashboard_pending_handoff_task_id": "main_chain",
        "dashboard_pending_handoff_task_label": "主链推荐",
        "dashboard_pending_handoff_focus_kind": "debate",
        "dashboard_pending_handoff_debate_id": "debate-2",
        "dashboard_pending_handoff_decision_source": "debate",
    }


def test_dashboard_workspace_handoff_notice_lines_surface_structured_focus() -> None:
    handoff = _WorkspaceHandoff(
        target_workspace="归档回看",
        source_workspace="候选复盘",
        title="带着当前判断去看归档",
        lines=("当前标的: 600036 招商银行",),
        symbol="600036",
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        focus_kind="debate",
        debate_id="debate-2",
        decision_source="card",
    )

    assert _workspace_handoff_notice_lines(handoff) == (
        "交接焦点: 2026-06-05 / 主链推荐 / 委员会补充结论",
        "当前采用口径: 研究候选卡",
        "讨论批次: debate-2",
        "当前标的: 600036 招商银行",
    )


def test_dashboard_queue_home_action_rail_handoff_preserves_signal_date_and_source(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    class _TaskView:
        task_id = "main_chain"
        task_label = "主链推荐"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
        detail_cards = ()
        agenda_lines = ()
        recommendation_lines = ()
        review_lines = ()
        watchlist_lines = ()
        blocker_lines = ()

    monkeypatch.setattr(dashboard.st, "session_state", {})
    spotlight = DashboardCandidateSpotlight(
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
    )

    item = _home_action_rail_items(_TaskView(), (spotlight,))[0]
    _queue_home_action_rail_handoff(item)

    assert dashboard.st.session_state["dashboard_pending_workspace"] == "候选复盘"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_signal_date"]
        == "2026-06-05"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_focus_kind"]
        == "spotlight"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_decision_source"]
        == "spotlight"
    )


def test_dashboard_queue_home_spotlight_handoff_uses_structured_cross_task_context(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    monkeypatch.setattr(dashboard.st, "session_state", {})
    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="等待量能扩散",
        next_step="先确认盘中龙头承接。",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "收盘复盘"),
        reasons=(),
        risks=(),
    )

    _queue_home_spotlight_handoff(
        workspace="归档回看",
        spotlight=spotlight,
        signal_date="2026-06-05",
    )

    assert dashboard.st.session_state["dashboard_pending_workspace"] == "归档回看"
    assert dashboard.st.session_state["dashboard_pending_handoff_source"] == "决策首页"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_focus_kind"]
        == "spotlight"
    )


def test_dashboard_queue_home_debate_handoff_keeps_debate_batch_and_source(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardDebateSummary

    monkeypatch.setattr(dashboard.st, "session_state", {})
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-7",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.25,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    _queue_home_debate_handoff(
        debate_summary=debate,
        title="带着多 Agent 讨论结果去看候选复盘",
        lines=("当前结论: 建议上调评分 / 分歧可控",),
    )

    assert dashboard.st.session_state["dashboard_pending_workspace"] == "候选复盘"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_debate_id"] == "debate-7"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_decision_source"]
        == "debate"
    )


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


def test_dashboard_workspace_nav_items_keep_two_line_fast_switch_labels() -> None:
    items = _workspace_nav_items()

    assert tuple(item.code for item in items) == (
        "首页",
        "候选",
        "纸面",
        "归档",
    )
    assert tuple(item.name for item in items) == (
        "决策首页",
        "候选复盘",
        "虚拟盘跟踪",
        "归档回看",
    )


def test_dashboard_workspace_navigation_renders_code_buttons_and_separate_names(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    button_labels: list[str] = []
    markdown_blocks: list[str] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubSessionState(dict):
        pass

    session_state = _StubSessionState()

    monkeypatch.setattr(dashboard.st, "session_state", session_state)
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(
        dashboard.st,
        "button",
        lambda label, *args, **kwargs: button_labels.append(label) and False,
    )
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_blocks.append(str(body)),
    )

    workspace = _render_workspace_navigation(pending_workspace="候选复盘")

    assert workspace == "候选复盘"
    assert button_labels == ["首页", "候选", "纸面", "归档"]
    assert "决策首页" not in button_labels
    assert any(
        "aqsp-nav-name" in block and "决策首页" in block for block in markdown_blocks
    )
    assert not any("切到" in block or "当前" in block for block in markdown_blocks)


def test_dashboard_symbol_quick_bar_renders_code_buttons_and_name_rows(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateCard

    button_labels: list[str] = []
    markdown_blocks: list[str] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    cards = (
        DashboardCandidateCard(
            symbol="600519",
            name="贵州茅台",
            display_name="600519 贵州茅台",
            rank_label="纸面重点",
            score=82.0,
            action_label="重点跟踪",
            status_label="延续上升",
            decision_note="趋势延续",
            next_step="等待量价确认",
            blocker="",
            review_meta="高优先级 / 开盘前后",
            reasons=(),
            risks=(),
            strategies=(),
            data_source="eastmoney",
        ),
        DashboardCandidateCard(
            symbol="000338",
            name="潍柴动力",
            display_name="000338 潍柴动力",
            rank_label="观察",
            score=58.0,
            action_label="观察",
            status_label="观察",
            decision_note="等待确认",
            next_step="等待量能恢复",
            blocker="",
            review_meta="中优先级 / 收盘前",
            reasons=(),
            risks=(),
            strategies=(),
            data_source="eastmoney",
        ),
    )

    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(
        dashboard.st,
        "button",
        lambda label, *args, **kwargs: button_labels.append(label) and False,
    )
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_blocks.append(str(body)),
    )

    _render_symbol_quick_bar(
        title="",
        workspace="候选复盘",
        symbol_order=["600519", "000338"],
        selected_symbol="600519",
        cards=cards,
    )

    assert button_labels == ["600519", "000338"]
    assert "贵州茅台" not in button_labels
    assert any(
        "aqsp-quick-symbol-name active" in block and "贵州茅台" in block
        for block in markdown_blocks
    )
    assert any(
        "aqsp-quick-symbol-name" in block and "潍柴动力" in block
        for block in markdown_blocks
    )


def test_dashboard_workspace_symbol_handoff_lines_keep_workspace_specific_focus() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    cards = (
        DashboardCandidateCard(
            symbol="600036",
            name="招商银行",
            display_name="600036 招商银行",
            rank_label="辩论主结论",
            score=7.5,
            action_label="建议维持评分",
            status_label="观点分化，保持原评级",
            decision_note="多头3票 vs 空头2票",
            next_step="等待分歧收敛",
            blocker="需关注大盘系统性风险",
            review_meta="辩论主结论 / 待复核",
            reasons=(),
            risks=(),
            strategies=(),
            data_source="multi",
        ),
    )
    debates = (
        DashboardDebateSummary(
            signal_date="2026-06-05",
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
            adjustment_reason="多空分歧更大",
            bull_count=3,
            bear_count=2,
            neutral_count=3,
            round_count=2,
            regime="震荡偏强",
            data_source="multi",
            thresholds_version="v1",
            summary_lines=(),
            round_summaries=(),
            risk_warnings=("需关注大盘系统性风险",),
            opportunity_highlights=(),
            agent_views=(),
        ),
    )

    assert _workspace_symbol_handoff_lines(
        workspace="归档回看",
        symbol="600036",
        cards=cards,
        debates=debates,
    ) == (
        "当前标的: 600036 招商银行",
        "当前采用口径: 委员会补充结论；当前没有独立候选卡，委员会结论只作解释，不改写评分。",
        "切到归档先看: 需关注大盘系统性风险",
    )


def test_dashboard_queue_workspace_symbol_handoff_preserves_workspace_context(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateCard

    monkeypatch.setattr(dashboard.st, "session_state", {})
    cards = (
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

    _queue_workspace_symbol_handoff(
        workspace="候选复盘",
        symbol="600519",
        cards=cards,
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
    )

    assert dashboard.st.session_state["dashboard_pending_workspace"] == "候选复盘"
    assert dashboard.st.session_state["dashboard_pending_handoff_source"] == "候选复盘"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_signal_date"]
        == "2026-06-05"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_task_id"] == "main_chain"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_decision_source"]
        == "card"
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
        cross_market_summary="英伟达物理AI叙事升温(纸面复核)",
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
    assert any(
        "跨市传导: 英伟达物理AI叙事升温(纸面复核)" == line for line in spotlight_lines
    )
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
    assert any("当前结论: 维持观察但优先级上调" == line for line in debate_lines)
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
    assert "当前限制: 20日均成交额不足，流动性过滤" in lines
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


def test_dashboard_archive_conclusion_context_neutralizes_raw_archive_action_words() -> (
    None
):
    class _TaskView:
        report_summary_lines = ("今日建议: 立即买入 600519",)
        runtime_lines = ("真实持仓等待下单回写",)
        next_day_focus_lines = ("重点跟踪名单: 600519 等待下单",)
        market_environment = ""

    title, lines = _archive_conclusion_context(
        task_view=_TaskView(),
        selected_symbol="000001",
        selected_card=None,
        selected_spotlight=None,
    )

    rendered = "\n".join((title, *lines))
    for forbidden in ("今日建议", "立即买入", "重点跟踪名单", "下单", "真实持仓"):
        assert forbidden not in rendered
    assert "历史回看" in rendered
    assert "历史复核名单" in rendered


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
            action_label="继续观察",
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
        ("当前归档没有新增复盘动作，先看原文、研究链和纸面记录。",),
    )
    assert _archive_followup_action_context(("600519 | 复核分歧是否收敛",)) == (
        "接下来做什么",
        ("600519 | 复核分歧是否收敛",),
    )


def test_dashboard_archive_brief_cards_summarize_archive_without_action_hype() -> None:
    from aqsp.web.data_provider import DashboardDebateSummary

    class _TaskView:
        blocker_lines = ("600036 招商银行 | 需确认分歧是否收敛",)
        report_markdown = "# report"
        report_summary_lines = ("报告已归档。",)
        runtime_lines = ()
        next_day_focus_lines = ()

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

    cards = _archive_brief_cards(
        task_view=_TaskView(),
        archive_lines=("600036 招商银行 | 回看分歧是否收敛",),
        conclusion_title="已归档",
        action_title="接下来做什么",
        action_lines=("600036 招商银行 | 复核分歧是否收敛",),
        debate_summary=debate,
        has_execution_activity=True,
        has_holding_activity=False,
    )

    assert tuple(card.kicker for card in cards) == (
        "归档结论",
        "接下来做什么",
        "纸面记录",
        "委员会怎么看",
    )
    assert cards[0].title == "已归档"
    assert cards[1].tone == "pressure"
    assert cards[2].title == "有纸面联动"
    assert cards[2].tone == "pressure"
    assert cards[3].title == "建议维持评分 / 分歧 0.48"
    assert cards[3].lines == (
        "委员会结论: 观点分化，保持原评级",
        "修正原因: 多空分歧更大",
    )
    rendered_text = "\n".join(
        line for card in cards for line in (card.title, *card.lines)
    )
    assert "立即买入" not in rendered_text
    assert "下单" not in rendered_text


def test_dashboard_archive_debate_summary_lines_prioritize_cross_market_chain_before_followup() -> (
    None
):
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300308",
        display_name="300308 中际旭创",
        debate_id="debate-archive-cross-market",
        rating="A",
        original_score=74.0,
        adjusted_score=74.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.28,
        consensus="分歧可控",
        adjustment_reason="海外链条仍在映射",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认映射链承接",
        next_trigger="若龙头放量延续则优先复核",
        cross_market_summary="海外算力风险偏好修复",
        cross_market_validation_summary="龙头放量上攻且光模块同步走强",
        cross_market_invalidation_summary="美股走强但A股映射链不共振",
        historical_context_note="强证据样本 4/5 命中",
        support_points=("海外叙事仍在扩散。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日承接。",),
    )

    assert dashboard._archive_debate_summary_lines(debate) == (
        "研究口径: 倾向优先纸面复核；卡点 先确认映射链承接",
        "跨市主线: 海外算力风险偏好修复 | 先看 300308 中际旭创 | 确认 龙头放量上攻且光模块同步走强 | 失效 美股走强但A股映射链不共振",
        "下一触发: 若龙头放量延续则优先复核",
        "修正原因: 海外链条仍在映射",
        "历史校验: 强证据样本 4/5 命中",
    )


def test_dashboard_raw_report_markdown_is_wrapped_as_historical_evidence() -> None:
    class _TaskView:
        task_label = "主链推荐"
        selected_date = "2026-06-05"
        latest_date = "2026-06-06"

    lines = _raw_report_boundary_lines(_TaskView())

    assert lines == (
        "历史原文: 主链推荐 / 2026-06-05",
        "以下内容只用于回看当时研究语境，不是今日动作、不是交易指令。",
        "原文中的行动词已在展示层中性化为研究口径，原始文件未被改写。",
    )


def test_dashboard_sanitizes_raw_archive_action_words_without_rewriting_source() -> (
    None
):
    raw_markdown = (
        "## 今日建议\n"
        "- 今日重点名单: 复核执行顺序\n"
        "- 重点跟踪对象: 600519\n"
        "- 首选标的: 000858，首选观察。\n"
        "- 配仓建议: 默认轻仓，仓位建议 10%。\n"
        "- 新开仓: 等待参考买点，止损 1420，止盈 1680\n"
        "- 重点跟踪名单: 禁止下单演示\n"
        "- 纸面回写: 600519 | BUY 100 @ 1500 / SELL 100 @ 1520\n"
        "- 买入计划后再卖出，开仓和平仓都只可回看\n"
    )

    sanitized = _sanitize_raw_report_markdown(raw_markdown)

    assert raw_markdown != sanitized
    for forbidden in (
        "重点跟踪对象",
        "今日重点名单",
        "首选标的",
        "首选观察",
        "配仓建议",
        "仓位建议",
        "新开仓",
        "参考买点",
        "买点",
        "止损",
        "止盈",
        "重点跟踪名单",
        "执行顺序",
        "下单",
        "今日建议",
        "BUY",
        "SELL",
        "买入计划",
        "买入",
        "卖出",
        "开仓",
        "平仓",
    ):
        assert forbidden not in sanitized
    assert "历史回看" in sanitized
    assert "历史重点记录" in sanitized
    assert "历史复核对象" in sanitized
    assert "历史复核对象" in sanitized
    assert "历史重点对象" in sanitized
    assert "重点观察" in sanitized
    assert "历史比例参考" in sanitized
    assert "历史比例参考 10%" in sanitized
    assert "纸面新建观察" in sanitized
    assert "参考价" in sanitized
    assert "历史最多亏到" in sanitized
    assert "历史先看目标" in sanitized
    assert "历史复核名单" in sanitized
    assert "历史再看顺序" in sanitized


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
        cross_market_summary="英伟达物理AI叙事升温(纸面复核)",
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
        consensus="多视角讨论后，3个看多，2个看空，3个中性，观点分化，保持原评级",
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
        next_trigger="放量站稳再复核",
        support_points=("行业景气度提升",),
        opposition_points=("需关注大盘系统性风险",),
        watch_items=("等待下一次任务确认",),
    )

    lines = _review_to_archive_handoff_lines(
        selected_card=card,
        debate_summary=debate,
    )

    assert lines == (
        "当前标的: 600036 招商银行",
        "当前采用口径: 委员会补充结论；当前没有独立候选卡，委员会结论只作解释，不改写评分。",
        "当前结论: 建议维持评分 / 分歧 0.48",
        "归档时重点看: 需关注大盘系统性风险",
    )


def test_dashboard_execution_to_review_handoff_lines_keep_paper_context_and_review_focus() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    selected_card = DashboardCandidateCard(
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
    review_card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="等待开盘量能确认",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    execution_focus = _DummyExecutionFocus(
        execution_status="已有纸面入场假设，等待盘中验证",
    )

    lines = _execution_to_review_handoff_lines(
        selected_symbol="600519",
        selected_card=selected_card,
        selected_spotlight=None,
        debate_summary=None,
        review_card=review_card,
        execution_focus=execution_focus,
    )

    assert lines == (
        "当前标的: 600519 贵州茅台",
        "当前采用口径: 研究候选卡；当前判断以本任务研究结论为主。",
        "当前纸面: 已有纸面入场假设，等待盘中验证",
        "回到复盘先看: 等待开盘量能确认",
    )


def test_dashboard_execution_to_archive_handoff_lines_keep_paper_validation_context() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="等待量能扩散",
        next_step="先确认盘中龙头承接。",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "收盘复盘"),
        reasons=(),
        risks=(),
    )
    execution_focus = _DummyExecutionFocus(
        execution_status="纸面侧暂无入场，先核对龙头承接",
    )

    lines = _execution_to_archive_handoff_lines(
        selected_symbol="688256",
        selected_card=None,
        selected_spotlight=spotlight,
        debate_summary=None,
        execution_focus=execution_focus,
    )

    assert lines == (
        "当前标的: 688256",
        "当前采用口径: 同日跨任务联动；先核对跨任务结论，再回到单任务原始记录。",
        "当前纸面: 纸面侧暂无入场，先核对龙头承接",
        "归档先看: 纸面验证是否支持当前研究结论与后续回看重点。",
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


def test_dashboard_review_to_execution_handoff_lines_keep_current_research_focus() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="300750",
        name="宁德时代",
        display_name="300750 宁德时代",
        rank_label="首选",
        score=82.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="主线延续",
        next_step="等待放量延续",
        blocker="先确认承接",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    lines = _review_to_execution_handoff_lines(
        selected_card=card,
        spotlight=None,
        debate_summary=None,
    )

    assert lines == (
        "当前标的: 300750 宁德时代",
        "当前采用口径: 研究候选卡；当前判断以本任务研究结论为主。",
        "纸面先看: 等待放量延续",
        "当前限制: 先确认承接",
    )


def test_dashboard_archive_to_execution_handoff_lines_include_archive_status_and_source() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    class _TaskView:
        agenda_lines = ()
        review_lines = ("300750 宁德时代 | 先核对承接是否继续",)
        unlock_lines = ()
        report_markdown = "# report"

    card = DashboardCandidateCard(
        symbol="300750",
        name="宁德时代",
        display_name="300750 宁德时代",
        rank_label="辩论主结论",
        score=82.0,
        action_label="建议上调评分",
        status_label="分歧可控",
        decision_note="倾向优先纸面复核",
        next_step="若放量延续则优先复核",
        blocker="先确认承接",
        review_meta="补充结论 / 待复核",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-0",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.25,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    lines = _archive_to_execution_handoff_lines(
        task_view=_TaskView(),
        selected_symbol="300750",
        selected_card=card,
        spotlight=None,
        debate_summary=debate,
        review_card=card,
    )

    assert lines == (
        "当前标的: 300750",
        "当前采用口径: 委员会补充结论；当前没有独立候选卡，委员会结论只作解释，不改写评分。",
        "归档状态: 已归档",
        "纸面先看: 300750 宁德时代 | 先核对承接是否继续",
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
        "涉及任务: 主链推荐、收盘复盘",
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
        cross_market_summary="英伟达物理AI叙事升温(纸面复核)",
    )

    lines = _candidate_focus_spotlight_lines(card, spotlight)

    assert lines == (
        "跨市主线: 英伟达物理AI叙事升温(纸面复核) | 先看 300750 宁德时代",
        "涉及任务: 早盘策略、尾盘策略",
        "跨任务结论: 降级观察 / 观察阻塞",
        "重点复核: 高位波动放大，先等待分歧收敛",
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

    assert lines == (
        "当前采用口径: 研究候选卡；跨任务联动只作一起参考，不替代当前任务结论。",
        "涉及任务: 主链推荐、收盘复盘",
    )


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
        cross_market_summary="美股风险偏好修复(纸面复核)",
        support_points=("外盘风险偏好改善，对银行权重形成支撑。",),
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

    assert (
        "当前采用口径: 研究候选卡；主链卡点优先，委员会结论只补充分歧与下一触发。"
        in lines
    )
    assert "投票分布: 看多 3 / 看空 1 / 中性 2" in lines
    assert "修正原因: 分歧收敛后更偏正面" in lines
    assert (
        "候选摘要: 跨市线索 美股风险偏好修复(纸面复核)；讨论支持: 外盘风险偏好改善，对银行权重形成支撑。"
        in lines
    )
    assert "涉及任务: 主链推荐、尾盘策略" in lines
    assert "跨任务重点: 等待权重股轮动确认" in lines


def test_dashboard_candidate_review_path_lines_prioritize_cross_market_chain_before_vote_snapshot() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    card = DashboardCandidateCard(
        symbol="300308",
        name="中际旭创",
        display_name="300308 中际旭创",
        rank_label="主链首位",
        score=74.0,
        action_label="维持原排序",
        status_label="等待确认",
        decision_note="主链继续保留首位",
        next_step="等待映射链确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300308",
        display_name="300308 中际旭创",
        debate_id="debate-cross-market-path",
        rating="A",
        original_score=74.0,
        adjusted_score=74.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.28,
        consensus="分歧可控",
        adjustment_reason="海外链条仍在映射",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认映射链承接",
        next_trigger="若龙头放量延续则优先复核",
        cross_market_summary="海外算力风险偏好修复",
        cross_market_validation_summary="龙头放量上攻且光模块同步走强",
        cross_market_invalidation_summary="美股走强但A股映射链不共振",
        support_points=("海外叙事仍在扩散。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日承接。",),
    )

    lines = _candidate_review_path_lines(
        selected_card=card,
        spotlight=None,
        debate_summary=debate,
    )

    assert lines[:5] == (
        "当前采用口径: 研究候选卡；委员会结论只作补充，不替代评分。",
        "跨市主线: 海外算力风险偏好修复 | 确认 龙头放量上攻且光模块同步走强 | 失效 美股走强但A股映射链不共振",
        "讨论待确认: 观察次日承接。",
        "投票分布: 看多 3 / 看空 1 / 中性 1",
        "讨论轮次: 2",
    )
    assert "修正原因: 海外链条仍在映射" in lines


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

    assert lines == (
        "当前采用口径: 研究候选卡；跨任务联动只作一起参考，不替代当前任务结论。",
        "涉及任务: 主链推荐、收盘复盘",
    )


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

    assert any("当前结论: 维持观察但优先级上调" == line for line in research_lines)
    assert any("结论共识: 维持观察但优先级上调" == line for line in research_lines)
    assert any(
        "当前没有研究候选卡，当前判断主要由同日多方讨论补齐。" == line
        for line in research_lines
    )
    assert any("修正原因: 分歧收敛后更偏正面" == line for line in path_lines)
    assert any("当前限制: 需确认银行板块承接" == line for line in path_lines)


def test_dashboard_execution_context_lines_surface_cross_market_digest_when_same_day_spotlight_exists() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    card = DashboardCandidateCard(
        symbol="002594",
        name="比亚迪",
        display_name="002594 比亚迪",
        rank_label="首选",
        score=81.0,
        action_label="维持原排序",
        status_label="等待确认",
        decision_note="主链继续保留首选",
        next_step="等待量能确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="002594",
        display_name="002594 比亚迪",
        score=81.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="",
        next_step="等待量能确认",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=(),
        risks=(),
        cross_market_summary="美股风险偏好修复(纸面复核)",
        cross_market_validation_summary="权重股放量共振",
        cross_market_invalidation_summary="美股走强但A股权重不承接",
    )
    execution_focus = _DummyExecutionFocus(
        research_lines=(
            "研究动作: 维持原排序",
            "研究下一步: 等待量能确认",
            "复核节奏: 高优先级 / 开盘前后",
        ),
        readiness_lines=("执行准备: 等待开盘确认成交质量",),
    )

    research_lines = _execution_research_context_lines(
        selected_card=card,
        selected_spotlight=spotlight,
        debate_summary=None,
        execution_focus=execution_focus,
    )
    path_lines = _execution_path_context_lines(
        selected_card=card,
        selected_spotlight=spotlight,
        debate_summary=None,
        execution_focus=execution_focus,
    )

    assert research_lines[0] == (
        "跨市主线: 美股风险偏好修复(纸面复核) | 先看 002594 比亚迪 | 确认 权重股放量共振 | 失效 美股走强但A股权重不承接"
    )
    assert any(
        line
        == "跨市主线: 美股风险偏好修复(纸面复核) | 先看 002594 比亚迪 | 确认 权重股放量共振 | 失效 美股走强但A股权重不承接"
        for line in path_lines
    )
    assert any("研究下一步: 等待量能确认" == line for line in research_lines)


def test_dashboard_workspace_focus_helpers_use_review_fallback_for_debate_only_symbols() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    execution_focus = _DummyExecutionFocus(
        display_name="600519",
        research_status="缺少研究结论",
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
        == "委员会补充结论已补齐"
    )
    focus_lines = _workspace_focus_lines(
        selected_card=None,
        selected_spotlight=None,
        review_card=review_card,
        execution_focus=execution_focus,
    )
    assert focus_lines[:2] == (
        "复核状态: 待独立验证 / 等待下一次任务确认",
        "辩论调整分（非选股评分）: 82.0",
    )
    assert focus_lines[2] == "验证动作: 等待下一次任务或纸面验证记录补充独立依据。"
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
        cross_market_summary="英伟达物理AI叙事升温(纸面复核)",
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


def test_dashboard_quick_bar_symbols_skip_low_value_debate_when_stronger_chain_exists() -> (
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
        "300750",
        "601398",
    ]
    debates = (
        DashboardDebateSummary(
            signal_date="2026-06-05",
            symbol="300750",
            display_name="300750 宁德时代",
            debate_id="debate-strong",
            rating="A",
            original_score=80.0,
            adjusted_score=81.0,
            adjustment_weight=0.1,
            recommended_adjustment="raise",
            recommended_adjustment_label="建议上调评分",
            disagreement_score=0.31,
            consensus="先看承接",
            adjustment_reason="主线延续",
            bull_count=3,
            bear_count=1,
            neutral_count=1,
            round_count=2,
            regime="强势",
            data_source="multi",
            thresholds_version="v1",
            summary_lines=(),
            round_summaries=(),
            risk_warnings=(),
            opportunity_highlights=(),
            agent_views=(),
            research_verdict="倾向优先纸面复核",
            primary_risk_gate="先确认量价延续",
            next_trigger="若放量延续则优先复核",
            historical_context_note="历史校验: 强证据样本 4/5 命中",
            role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
            support_points=("主线仍在扩散。",),
            opposition_points=(),
            watch_items=("观察次日承接。",),
        ),
        DashboardDebateSummary(
            signal_date="2026-06-05",
            symbol="601398",
            display_name="601398 工商银行",
            debate_id="debate-low",
            rating="B",
            original_score=60.0,
            adjusted_score=60.0,
            adjustment_weight=0.0,
            recommended_adjustment="keep",
            recommended_adjustment_label="建议维持评分",
            disagreement_score=0.12,
            consensus="",
            adjustment_reason="",
            bull_count=1,
            bear_count=1,
            neutral_count=1,
            round_count=1,
            regime="震荡偏强",
            data_source="multi",
            thresholds_version="v1",
            summary_lines=(),
            round_summaries=(),
            risk_warnings=(),
            opportunity_highlights=(),
            agent_views=(),
            research_verdict="",
            primary_risk_gate="",
            next_trigger="",
            historical_context_note="",
            role_reliability_lines=(),
            support_points=(),
            opposition_points=(),
            watch_items=(),
        ),
    )

    quick_symbols = _quick_bar_symbols(
        workspace="候选复盘",
        symbol_order=symbol_order,
        selected_symbol="000001",
        debates=debates,
        limit=6,
    )

    assert quick_symbols == ["000001", "000002", "000003", "000004", "000005", "300750"]


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


def test_dashboard_render_review_phase_bar_queues_same_workspace_handoff_with_task_context(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardSameDayTaskRow

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

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

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: label == "盘前主链",
    )
    reruns: list[bool] = []
    monkeypatch.setattr(dashboard.st, "rerun", lambda: reruns.append(True))

    _render_review_phase_bar(
        signal_date="2026-06-05",
        current_task_id="closing_review",
        selected_symbol="600519",
        same_day_rows=same_day_rows,
        journey_steps=(),
        research_task_id="main_chain",
        selected_card=card,
        selected_spotlight=None,
        debate_summary=None,
    )

    assert reruns == [True]
    assert dashboard.st.session_state["dashboard_pending_workspace"] == "候选复盘"
    assert dashboard.st.session_state["dashboard_pending_task_id"] == "main_chain"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_task_label"] == "主链推荐"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_decision_source"]
        == "card"
    )


def test_dashboard_phase_nav_label_keeps_quick_switch_two_line_text_compact() -> None:
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

    assert _phase_nav_label(row) == "收盘复盘"
    assert _phase_nav_name(row) == "收盘复盘"
    assert "已复盘" not in _phase_nav_label(row)


def test_dashboard_two_line_nav_label_renders_code_and_name_without_action_noise(
    monkeypatch,
) -> None:
    rendered: list[str] = []

    monkeypatch.setattr(
        "aqsp.web.dashboard.st.markdown",
        lambda html, unsafe_allow_html=False: rendered.append(html),
    )

    _render_two_line_nav_label(
        _TwoLineNavLabel(code="盘前主链", name="主链推荐"),
        active=True,
    )

    html = "".join(rendered)
    assert "aqsp-nav-code active" in html
    assert "aqsp-nav-name active" in html
    assert "盘前主链" in html
    assert "主链推荐" in html
    assert "已复盘" not in html
    assert "切到" not in html
    assert "当前" not in html


def test_dashboard_top_navigation_uses_beginner_friendly_labels(monkeypatch) -> None:
    import aqsp.web.dashboard as dashboard

    selectbox_labels: list[str] = []
    markdown_blocks: list[str] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubProvider:
        @staticmethod
        def dashboard_dates() -> list[str]:
            return ["2026-06-05"]

        @staticmethod
        def default_task_id() -> str:
            return "main_chain"

        @staticmethod
        def same_day_task_rows(signal_date: str):
            from aqsp.web.data_provider import DashboardSameDayTaskRow

            return (
                DashboardSameDayTaskRow(
                    signal_date=signal_date,
                    task_id="main_chain",
                    task_label="主链推荐",
                    phase_order=1,
                    phase_label="盘前主链",
                    phase_summary="先看主链推荐",
                    status_label="有推荐",
                    headline="先看主链推荐",
                    candidate_count=1,
                    actionable_count=1,
                    watch_count=0,
                    blocked_count=0,
                ),
            )

        @staticmethod
        def preferred_task_for_date(signal_date: str) -> str:
            return "main_chain"

    class _Opt:
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_blocks.append(str(body)),
    )
    monkeypatch.setattr(
        dashboard.st, "columns", lambda spec: [_StubColumn() for _ in range(len(spec))]
    )

    def _fake_selectbox(label, options, **kwargs):
        selectbox_labels.append(label)
        return options[0]

    monkeypatch.setattr(dashboard.st, "selectbox", _fake_selectbox)
    monkeypatch.setattr(dashboard, "_render_date_jump_bar", lambda **kwargs: None)
    monkeypatch.setattr(
        dashboard, "_render_same_day_phase_jump_bar", lambda **kwargs: None
    )
    monkeypatch.setattr(
        dashboard, "_render_top_navigation_banner", lambda **kwargs: None
    )

    selected_task_id, selected_date = _render_top_navigation(
        options=(_Opt("main_chain"),),
        snapshots=(),
        provider=_StubProvider(),
    )

    assert selected_task_id == "main_chain"
    assert selected_date == "2026-06-05"
    assert selectbox_labels == ["看哪一天", "看哪一段"]
    assert any(
        "先看当天总控，再按日期和阶段展开。" in block for block in markdown_blocks
    )


def test_dashboard_top_navigation_can_resolve_state_without_rendering_legacy_controls(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    class _Provider:
        @staticmethod
        def dashboard_dates() -> tuple[str, ...]:
            return ("2026-07-14",)

        @staticmethod
        def same_day_task_rows(signal_date: str):
            del signal_date
            return ()

        @staticmethod
        def preferred_task_for_date(signal_date: str) -> str:
            assert signal_date == "2026-07-14"
            return "intraday"

    monkeypatch.setattr(
        dashboard.st,
        "session_state",
        {"dashboard_selected_date": "2026-07-14"},
    )
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy navigation must not render")
        ),
    )
    monkeypatch.setattr(
        dashboard.st,
        "selectbox",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy navigation must not render")
        ),
    )

    selected_task_id, selected_date = dashboard._render_top_navigation(
        options=(),
        snapshots=(),
        provider=_Provider(),
        render_controls=False,
    )

    assert (selected_task_id, selected_date) == ("intraday", "2026-07-14")


def test_dashboard_top_navigation_queue_home_handoff_when_selection_changes(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubProvider:
        @staticmethod
        def dashboard_dates() -> list[str]:
            return ["2026-06-05", "2026-06-04"]

        @staticmethod
        def default_task_id() -> str:
            return "main_chain"

        @staticmethod
        def same_day_task_rows(signal_date: str):
            from aqsp.web.data_provider import DashboardSameDayTaskRow

            if signal_date == "2026-06-04":
                return (
                    DashboardSameDayTaskRow(
                        signal_date=signal_date,
                        task_id="closing_premium",
                        task_label="尾盘策略",
                        phase_order=4,
                        phase_label="尾盘确认",
                        phase_summary="先确认尾盘承接",
                        status_label="有推荐",
                        headline="尾盘有 1 个待复核对象",
                        candidate_count=1,
                        actionable_count=1,
                        watch_count=0,
                        blocked_count=0,
                    ),
                )
            return (
                DashboardSameDayTaskRow(
                    signal_date=signal_date,
                    task_id="main_chain",
                    task_label="主链推荐",
                    phase_order=1,
                    phase_label="盘前主链",
                    phase_summary="先看主链推荐",
                    status_label="有推荐",
                    headline="先看主链推荐",
                    candidate_count=1,
                    actionable_count=1,
                    watch_count=0,
                    blocked_count=0,
                ),
            )

        @staticmethod
        def preferred_task_for_date(signal_date: str) -> str:
            return "closing_premium" if signal_date == "2026-06-04" else "main_chain"

    class _Opt:
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id

    monkeypatch.setattr(
        dashboard.st,
        "session_state",
        {
            "dashboard_selected_date": "最新",
            "dashboard_task_id": "main_chain",
        },
    )
    monkeypatch.setattr(
        dashboard.st, "columns", lambda spec: [_StubColumn() for _ in range(len(spec))]
    )
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)

    def _fake_selectbox(label, options, **kwargs):
        if label == "看哪一天":
            return "2026-06-04"
        if label == "看哪一段":
            return "closing_premium"
        return options[0]

    monkeypatch.setattr(dashboard.st, "selectbox", _fake_selectbox)
    monkeypatch.setattr(dashboard, "_render_date_jump_bar", lambda **kwargs: None)
    monkeypatch.setattr(
        dashboard, "_render_same_day_phase_jump_bar", lambda **kwargs: None
    )
    monkeypatch.setattr(
        dashboard, "_render_top_navigation_banner", lambda **kwargs: None
    )

    selected_task_id, selected_date = _render_top_navigation(
        options=(_Opt("main_chain"), _Opt("closing_premium")),
        snapshots=(),
        provider=_StubProvider(),
    )

    assert selected_task_id == "closing_premium"
    assert selected_date == "2026-06-04"
    assert dashboard.st.session_state["dashboard_pending_workspace"] == "决策首页"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_signal_date"]
        == "2026-06-04"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_task_id"]
        == "closing_premium"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_title"]
        == "切到 尾盘确认 看这段结论"
    )


def test_dashboard_same_day_buttons_use_short_beginner_labels(monkeypatch) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardDateOverview, DashboardSameDayTaskRow

    button_labels: list[str] = []
    subheaders: list[str] = []
    markdown_blocks: list[str] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "subheader", lambda text: subheaders.append(text))
    monkeypatch.setattr(
        dashboard.st,
        "button",
        lambda label, *args, **kwargs: button_labels.append(label) and False,
    )
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_blocks.append(str(body)),
    )

    row = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        phase_order=1,
        phase_label="盘前主链",
        phase_summary="先看主链推荐",
        status_label="有推荐",
        headline="先看主链推荐",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
    )
    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=1,
        actionable_total=1,
        watch_total=0,
        blocked_total=0,
        top_task_label="主链推荐",
        top_headline="先看主链推荐",
        blocker_headline="",
        focus_headline="",
        workflow_summary="当日流程: 盘前主链",
        archive_summary="",
    )

    monkeypatch.setattr(dashboard, "_set_dashboard_selection", lambda **kwargs: None)
    monkeypatch.setattr(dashboard.st, "rerun", lambda: None)
    dashboard._render_date_jump_bar(
        all_dates=("2026-06-05",),
        selected_date="2026-06-05",
        provider=type(
            "_Provider",
            (),
            {
                "dashboard_date_overviews": staticmethod(lambda: (overview,)),
                "default_task_id": staticmethod(lambda: "main_chain"),
                "same_day_task_rows": staticmethod(lambda signal_date: (row,)),
            },
        )(),
        current_task_id="main_chain",
    )
    dashboard._render_same_day_task_matrix((row,), "other_task")

    assert "当天各段" in subheaders
    assert "2026-06-05" in button_labels
    assert "切到主链推荐" in button_labels
    assert any("2026-06-05" in block for block in markdown_blocks)


def test_dashboard_date_jump_bar_queue_home_handoff_on_date_switch(monkeypatch) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardDateOverview, DashboardSameDayTaskRow

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubExpander:
        def __init__(self, label: str, expanded: bool) -> None:
            self.label = label
            self.expanded = expanded

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    row = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        phase_order=1,
        phase_label="盘前主链",
        phase_summary="先看主链推荐",
        status_label="有推荐",
        headline="主链有 1 个待复核对象",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
    )
    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=1,
        actionable_total=1,
        watch_total=0,
        blocked_total=0,
        top_task_label="主链推荐",
        top_headline="主链有 1 个待复核对象",
        blocker_headline="",
        focus_headline="",
        workflow_summary="当日流程: 盘前主链",
        archive_summary="",
    )

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard.st, "expander", _StubExpander)
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: label == "2026-06-05",
    )
    reruns: list[bool] = []
    monkeypatch.setattr(dashboard.st, "rerun", lambda: reruns.append(True))

    dashboard._render_date_jump_bar(
        all_dates=("2026-06-05",),
        selected_date="2026-06-04",
        provider=type(
            "_Provider",
            (),
            {
                "dashboard_date_overviews": staticmethod(lambda: (overview,)),
                "default_task_id": staticmethod(lambda: "main_chain"),
                "same_day_task_rows": staticmethod(lambda signal_date: (row,)),
            },
        )(),
        current_task_id="main_chain",
    )

    assert reruns == [True]
    assert dashboard.st.session_state["dashboard_pending_workspace"] == "决策首页"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_signal_date"]
        == "2026-06-05"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_task_id"] == "main_chain"
    )


def test_dashboard_queue_home_selection_handoff_keeps_home_workspace_context(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    monkeypatch.setattr(dashboard.st, "session_state", {})

    _queue_home_selection_handoff(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        title="切到 盘前主链 看这段结论",
        lines=("切到这段先看: 主链有 1 个待复核对象",),
    )

    assert dashboard.st.session_state["dashboard_pending_workspace"] == "决策首页"
    assert dashboard.st.session_state["dashboard_pending_handoff_target"] == "决策首页"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_signal_date"]
        == "2026-06-05"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_task_id"] == "main_chain"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_task_label"] == "主链推荐"
    )


def test_dashboard_task_workbench_open_task_queues_home_handoff(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardTaskSnapshot

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubExpander:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard.st, "info", lambda text: None)
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "expander", _StubExpander)
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: kwargs.get("key") == "task-switch-main_chain",
    )
    reruns: list[bool] = []
    monkeypatch.setattr(dashboard.st, "rerun", lambda: reruns.append(True))

    dashboard._render_task_workbench(
        snapshots=(
            DashboardTaskSnapshot(
                task_id="main_chain",
                task_label="主链推荐",
                latest_date="2026-06-05",
                status_label="有推荐",
                headline="先看主链里最强的 2 个候选",
                actionable_count=2,
                watch_count=1,
                blocked_count=0,
            ),
        ),
        signal_date="2026-06-05",
    )

    assert reruns == [True]
    assert dashboard.st.session_state["dashboard_pending_workspace"] == "决策首页"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_task_id"] == "main_chain"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_task_label"] == "主链推荐"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_title"]
        == "切到 主链推荐 看任务快照"
    )
    assert dashboard.st.session_state["dashboard_pending_handoff_lines"] == (
        "切过去先看: 先看主链里最强的 2 个候选",
        "当前状态: 有推荐",
    )


def test_dashboard_task_workbench_hidden_snapshot_open_task_keeps_context_lines(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardTaskSnapshot

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubExpander:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard.st, "info", lambda text: None)
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "expander", _StubExpander)
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: (
            kwargs.get("key") == "task-switch-hidden-briefing-2026-06-05"
        ),
    )
    reruns: list[bool] = []
    monkeypatch.setattr(dashboard.st, "rerun", lambda: reruns.append(True))

    dashboard._render_task_workbench(
        snapshots=(
            DashboardTaskSnapshot(
                task_id="main_chain",
                task_label="主链推荐",
                latest_date="2026-06-05",
                status_label="有推荐",
                headline="主链仍是当天主入口",
                actionable_count=2,
                watch_count=1,
                blocked_count=0,
            ),
            DashboardTaskSnapshot(
                task_id="briefing",
                task_label="简报回看",
                latest_date="2026-06-04",
                status_label="未产出",
                headline="次日预案沿用昨日简报",
                actionable_count=0,
                watch_count=1,
                blocked_count=0,
            ),
        ),
        signal_date="2026-06-05",
    )

    assert reruns == [True]
    assert dashboard.st.session_state["dashboard_pending_workspace"] == "决策首页"
    assert dashboard.st.session_state["dashboard_pending_handoff_task_id"] == "briefing"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_title"]
        == "切到 简报回看 看任务快照"
    )
    assert dashboard.st.session_state["dashboard_pending_handoff_lines"] == (
        "切过去先看: 次日预案沿用昨日简报",
        "当前状态: 未产出",
        "最近独立结果日: 2026-06-04",
    )


def test_dashboard_same_day_task_matrix_queue_home_handoff_on_task_switch(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardSameDayTaskRow

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    row = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        phase_order=1,
        phase_label="盘前主链",
        phase_summary="先看主链推荐",
        status_label="有推荐",
        headline="主链有 1 个待复核对象",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
    )

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: label == "切到主链推荐",
    )
    reruns: list[bool] = []
    monkeypatch.setattr(dashboard.st, "rerun", lambda: reruns.append(True))

    dashboard._render_same_day_task_matrix((row,), "other_task")

    assert reruns == [True]
    assert dashboard.st.session_state["dashboard_pending_workspace"] == "决策首页"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_task_id"] == "main_chain"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_title"]
        == "切到 盘前主链 看这段结论"
    )


def test_dashboard_same_day_phase_jump_bar_queue_home_handoff_on_phase_switch(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardSameDayTaskRow

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    row = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        phase_order=1,
        phase_label="盘前主链",
        phase_summary="先看主链推荐",
        status_label="有推荐",
        headline="主链有 1 个待复核对象",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
    )

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: label == "盘前主链",
    )
    reruns: list[bool] = []
    monkeypatch.setattr(dashboard.st, "rerun", lambda: reruns.append(True))

    _render_same_day_phase_jump_bar(
        signal_date="2026-06-05",
        rows=(row,),
        current_task_id="other_task",
    )

    assert reruns == [True]
    assert dashboard.st.session_state["dashboard_pending_workspace"] == "决策首页"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_task_id"] == "main_chain"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_title"]
        == "切到 盘前主链 看这段结论"
    )


def test_dashboard_task_message_summary_falls_back_from_report_to_summary_to_headline() -> (
    None
):
    class _WithReport:
        report_summary_lines = ("今日建议: 立即买入后等待下单。",)
        summary_lines = ("备用摘要",)
        headline = "headline"

    class _WithSummary:
        report_summary_lines = ()
        summary_lines = ("第二层摘要",)
        headline = "headline"

    class _WithHeadline:
        report_summary_lines = ()
        summary_lines = ()
        headline = "只剩 headline"

    assert "立即买入" not in _task_message_summary(_WithReport())
    assert "纸面" in _task_message_summary(_WithReport())
    assert _task_message_summary(_WithSummary()) == "第二层摘要"
    assert _task_message_summary(_WithHeadline()) == "只剩 headline"


def test_dashboard_same_day_message_lines_explain_what_why_and_next() -> None:
    from aqsp.web.data_provider import DashboardSameDayTaskRow

    class _TaskView:
        report_summary_lines = ("主链结论已落盘，先复核量能承接。",)
        summary_lines = ()
        headline = "主链摘要"
        review_lines = ()
        recommendation_lines = ()
        agenda_lines = ()
        watchlist_lines = ()
        blocker_lines = ()
        next_day_focus_lines = ()

    row = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        phase_order=1,
        phase_label="盘前主链",
        phase_summary="先确认主推候选与量能承接。",
        status_label="有推荐",
        headline="主链有 1 个待复核对象",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
    )

    assert _same_day_message_lines(_TaskView(), row) == (
        "这一段新增: 先确认主推候选与量能承接。",
        "本段焦点: 有 1 个待复核对象已落盘。",
        "切到这段看: 主链有 1 个待复核对象",
    )


def test_dashboard_same_day_message_lines_prioritize_structured_judgment_when_available() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardSameDayTaskRow

    class _TaskView:
        report_summary_lines = ("尾盘结论已落盘，先看承接是否延续。",)
        summary_lines = ()
        headline = "尾盘摘要"
        review_lines = ("300750 宁德时代 | 高优先级 / 开盘前后 | 观察承接是否继续。",)
        recommendation_lines = (
            "300750 宁德时代 | 延续上升 | 评分 73.0 | 观察承接是否继续。",
        )
        agenda_lines = ("先看推荐: 300750 宁德时代 | 延续上升 | 评分 73.0",)
        watchlist_lines = ()
        blocker_lines = ()
        next_day_focus_lines = ()
        detail_cards = (
            DashboardCandidateCard(
                symbol="300750",
                name="宁德时代",
                display_name="300750 宁德时代",
                rank_label="同日聚合",
                score=73.0,
                action_label="延续上升",
                status_label="等待确认",
                decision_note=(
                    "跨市线索 英伟达物理AI 叙事继续扩散｜同向 2 条｜反向 1 条；"
                    "盘前主链: 倾向优先纸面复核"
                ),
                next_step="等待承接确认",
                blocker="",
                review_meta="高优先级 / 开盘前后",
                reasons=(),
                risks=(),
                strategies=("closing_premium",),
                data_source="multi",
            ),
        )

    row = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="closing_premium",
        task_label="尾盘策略",
        phase_order=4,
        phase_label="尾盘确认",
        phase_summary="先确认尾盘承接与次日成交质量。",
        status_label="有推荐",
        headline="尾盘有 1 个待复核对象",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
    )

    assert _same_day_message_lines(_TaskView(), row) == (
        "这一段新增: 300750 宁德时代 | 高优先级 / 开盘前后 | 跨市线索 英伟达物理AI 叙事继续扩散｜同向 2 条｜反向 1 条；盘前主链: 倾向优先纸面复核",
        "本段焦点: 300750 宁德时代 | 高优先级 / 开盘前后 | 观察承接是否继续。",
        "切到这段看: 尾盘有 1 个待复核对象",
    )


def test_dashboard_ordered_same_day_message_rows_prioritize_structured_context_over_phase_order() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardSameDayTaskRow

    main_chain = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        phase_order=1,
        phase_label="盘前主链",
        phase_summary="先确认主推候选与量能承接。",
        status_label="有推荐",
        headline="主链有 1 个待复核对象",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
    )
    closing_premium = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="closing_premium",
        task_label="尾盘策略",
        phase_order=4,
        phase_label="尾盘确认",
        phase_summary="先确认尾盘承接与次日成交质量。",
        status_label="有推荐",
        headline="尾盘有 1 个待复核对象",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
    )

    class _Provider:
        def build_task_view(self, task_id: str, signal_date: str):
            assert signal_date == "2026-06-05"
            detail_cards = ()
            if task_id == "closing_premium":
                detail_cards = (
                    DashboardCandidateCard(
                        symbol="300750",
                        name="宁德时代",
                        display_name="300750 宁德时代",
                        rank_label="同日聚合",
                        score=73.0,
                        action_label="延续上升",
                        status_label="等待确认",
                        decision_note=(
                            "跨市线索 英伟达物理AI 叙事继续扩散｜同向 2 条｜反向 1 条；"
                            "盘前主链: 倾向优先纸面复核"
                        ),
                        next_step="等待承接确认",
                        blocker="",
                        review_meta="高优先级 / 开盘前后",
                        reasons=(),
                        risks=(),
                        strategies=("closing_premium",),
                        data_source="multi",
                    ),
                )
            return type(
                "_TaskView",
                (),
                {
                    "detail_cards": detail_cards,
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                },
            )()

    ordered = _ordered_same_day_message_rows(
        _Provider(),
        "2026-06-05",
        (main_chain, closing_premium),
    )

    assert [row.task_id for row, _ in ordered] == ["closing_premium", "main_chain"]


def test_dashboard_ordered_same_day_message_rows_use_newer_task_when_priority_tied() -> (
    None
):
    from aqsp.web.data_provider import DashboardSameDayTaskRow

    earlier_phase = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        phase_order=1,
        phase_label="盘前主链",
        phase_summary="先确认主推候选。",
        status_label="有推荐",
        headline="主链有 1 个待复核对象",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
        created_at="2026-06-05T10:00:00+08:00",
    )
    later_phase = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="closing_premium",
        task_label="尾盘策略",
        phase_order=4,
        phase_label="尾盘确认",
        phase_summary="先确认尾盘承接。",
        status_label="有推荐",
        headline="尾盘有 1 个待复核对象",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
        created_at="2026-06-05T14:55:00+08:00",
    )

    class _Provider:
        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            assert signal_date == "2026-06-05"
            return type(
                "_TaskView",
                (),
                {
                    "detail_cards": (),
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                },
            )()

    ordered = _ordered_same_day_message_rows(
        _Provider(),
        "2026-06-05",
        (earlier_phase, later_phase),
    )

    assert [row.task_id for row, _ in ordered] == ["closing_premium", "main_chain"]


def test_dashboard_same_day_digest_snapshot_lines_merge_task_messages_with_top_debate() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardDebateAgentView,
        DashboardDebateSummary,
        DashboardSameDayTaskRow,
    )

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先看主推候选与量能承接。",
            status_label="有推荐",
            headline="主链有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="closing_premium",
            task_label="尾盘策略",
            phase_order=4,
            phase_label="尾盘确认",
            phase_summary="先确认尾盘承接与次日成交质量。",
            status_label="有推荐",
            headline="尾盘有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )

    class _Provider:
        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            assert signal_date == "2026-06-05"
            return type(
                "_TaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": (f"{task_id} 的摘要",),
                    "headline": f"{task_id} 的 headline",
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                    "detail_cards": (),
                    "source_status": {},
                },
            )()

    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-0",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.25,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=("先讨论海外主线传导",),
        risk_warnings=(),
        opportunity_highlights=(),
        created_at="2026-06-05T21:00:00+08:00",
        agent_views=(
            DashboardDebateAgentView(
                role_id="cross_market",
                role_label="跨市传导",
                stance="bullish",
                stance_label="看多",
                confidence=0.82,
                key_argument="海外主线仍在扩散。",
                key_risk="",
                key_opportunity="",
            ),
        ),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
        historical_context_note="强证据样本 4/5 命中",
        role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
        support_points=("海外主线仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )

    lines = _same_day_digest_snapshot_lines(_Provider(), "2026-06-05", rows, (debate,))

    assert lines == (
        "当前采用口径: 当天任务落盘；当天以各任务已落盘结论为主，委员会只补充分歧、风险与触发。",
        "讨论结果: 21:00 更新 | 300750 宁德时代: 倾向优先纸面复核；卡点 先确认承接 | 跨市主线: 海外主线仍在扩散。 | 先看 300750 宁德时代 | 触发 若放量延续则优先复核",
        "主链推荐: 有 1 个待复核对象已落盘。",
        "尾盘策略: 有 1 个待复核对象已落盘。",
    )
    assert not any(line.startswith("讨论过程:") for line in lines)


def test_dashboard_same_day_digest_snapshot_lines_surface_cross_market_digest_before_tasks() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateSpotlight,
        DashboardSameDayTaskRow,
    )

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先看主推候选与量能承接。",
            status_label="有推荐",
            headline="主链有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )

    class _Provider:
        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            assert task_id == "main_chain"
            assert signal_date == "2026-06-05"
            return type(
                "_TaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": ("main_chain 的摘要",),
                    "headline": "main_chain 的 headline",
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                    "detail_cards": (),
                    "source_status": {},
                },
            )()

    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="等待量能扩散",
        next_step="先确认盘中龙头承接。",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐",),
        reasons=(),
        risks=(),
        cross_market_summary="海外物理AI叙事升温(纸面复核)",
        cross_market_chain_summary="英伟达物理AI -> 算力链映射 -> A股弹性标的扩散",
        cross_market_validation_summary="龙头封单增强",
        cross_market_invalidation_summary="高开低走且量能背离",
        support_points=(),
        opposition_points=(),
        watch_items=(),
    )

    lines = _same_day_digest_snapshot_lines(
        _Provider(),
        "2026-06-05",
        rows,
        (),
        spotlights=(spotlight,),
    )

    assert lines == (
        "跨市主线: 海外物理AI叙事升温(纸面复核) | 先看 688256 寒武纪 | 确认 龙头封单增强 | 失效 高开低走且量能背离",
        "当前采用口径: 同日跨任务联动；先看跨任务共振，再回到各任务落盘。",
        "主链推荐: 有 1 个待复核对象已落盘。",
    )


def test_dashboard_same_day_digest_snapshot_lines_fallback_to_debate_cross_market_digest_when_no_spotlight() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardDebateAgentView,
        DashboardDebateSummary,
        DashboardSameDayTaskRow,
    )

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="closing_premium",
            task_label="尾盘策略",
            phase_order=4,
            phase_label="尾盘确认",
            phase_summary="先确认尾盘承接与次日成交质量。",
            status_label="有推荐",
            headline="尾盘有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )

    class _Provider:
        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            assert task_id == "closing_premium"
            assert signal_date == "2026-06-05"
            return type(
                "_TaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": ("closing_premium 的摘要",),
                    "headline": "closing_premium 的 headline",
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                    "detail_cards": (),
                    "source_status": {},
                },
            )()

    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-1",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.25,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=("先讨论海外主线传导",),
        risk_warnings=(),
        opportunity_highlights=(),
        created_at="2026-06-05T21:00:00+08:00",
        agent_views=(
            DashboardDebateAgentView(
                role_id="cross_market",
                role_label="跨市传导",
                stance="bullish",
                stance_label="看多",
                confidence=0.82,
                key_argument="海外主线仍在扩散。",
                key_risk="",
                key_opportunity="",
            ),
        ),
        cross_market_summary="海外物理AI叙事升温(纸面复核)",
        cross_market_chain_summary="英伟达物理AI -> 算力链映射 -> A股弹性标的扩散",
        cross_market_validation_summary="机器人龙头放量上攻",
        cross_market_invalidation_summary="只有海外叙事但A股板块不共振",
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
        historical_context_note="强证据样本 4/5 命中",
        role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
        support_points=("海外主线仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )

    lines = _same_day_digest_snapshot_lines(
        _Provider(),
        "2026-06-05",
        rows,
        (debate,),
    )

    assert lines[0] == (
        "跨市主线: 海外主线仍在扩散。 | 先看 300750 宁德时代 | "
        "确认 机器人龙头放量上攻 | 失效 只有海外叙事但A股板块不共振"
    )
    assert lines[1:] == (
        "当前采用口径: 当天任务落盘；当天以各任务已落盘结论为主，委员会只补充分歧、风险与触发。",
        "讨论结果: 21:00 更新 | 300750 宁德时代: 倾向优先纸面复核；卡点 先确认承接 | 跨市主线: 海外主线仍在扩散。 | 先看 300750 宁德时代 | 确认 机器人龙头放量上攻 | 失效 只有海外叙事但A股板块不共振 | 触发 若放量延续则优先复核",
        "尾盘策略: 有 1 个待复核对象已落盘。",
    )
    assert not any(line.startswith("讨论过程:") for line in lines)


def test_dashboard_same_day_digest_snapshot_lines_use_newer_debate_when_priority_tied() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary, DashboardSameDayTaskRow

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先看主推候选与量能承接。",
            status_label="有推荐",
            headline="主链有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )

    class _Provider:
        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            assert task_id == "main_chain"
            assert signal_date == "2026-06-05"
            return type(
                "_TaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": ("main_chain 的摘要",),
                    "headline": "main_chain 的 headline",
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                    "detail_cards": (),
                    "source_status": {},
                },
            )()

    older = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-older",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.25,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=("先讨论海外主线传导",),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        created_at="2026-06-05T21:00:00+08:00",
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
    )
    newer = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-newer",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.25,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=("先讨论海外主线传导",),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        created_at="2026-06-05T21:10:00+08:00",
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
    )

    lines = _same_day_digest_snapshot_lines(
        _Provider(),
        "2026-06-05",
        rows,
        (older, newer),
    )

    assert lines[1] == (
        "讨论结果: 21:10 更新 | 300750 宁德时代: 倾向优先纸面复核；卡点 先确认承接 | 触发 若放量延续则优先复核"
    )
    assert not any(line.startswith("讨论过程:") for line in lines)


def test_dashboard_same_day_digest_snapshot_lines_place_newer_task_before_older_debate() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary, DashboardSameDayTaskRow

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先看主推候选与量能承接。",
            status_label="有推荐",
            headline="主链有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
            created_at="2026-06-05T21:05:00+08:00",
        ),
    )

    class _Provider:
        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            assert task_id == "main_chain"
            assert signal_date == "2026-06-05"
            return type(
                "_TaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": ("main_chain 的摘要",),
                    "headline": "main_chain 的 headline",
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                    "detail_cards": (),
                    "source_status": {},
                },
            )()

    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-older",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.25,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=("先讨论海外主线传导",),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        created_at="2026-06-05T21:00:00+08:00",
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
    )

    lines = _same_day_digest_snapshot_lines(_Provider(), "2026-06-05", rows, (debate,))

    task_index = next(
        index for index, line in enumerate(lines) if line.startswith("主链推荐: ")
    )
    debate_index = next(
        index for index, line in enumerate(lines) if line.startswith("讨论结果: ")
    )

    assert "21:05 更新 | 有 1 个待复核对象已落盘。" in lines[task_index]
    assert task_index < debate_index


def test_dashboard_same_day_digest_snapshot_lines_surface_realtime_source_status_before_cross_market() -> (
    None
):
    from aqsp.web.data_provider import DashboardSameDayTaskRow

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先看主推候选与量能承接。",
            status_label="有推荐",
            headline="主链有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )

    class _Provider:
        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            assert task_id == "main_chain"
            assert signal_date == "2026-06-05"
            return type(
                "_TaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": ("main_chain 的摘要",),
                    "headline": "main_chain 的 headline",
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                    "detail_cards": (),
                    "source_status": {
                        "actual_source": "eastmoney",
                        "health_label": "fallback",
                        "lag_days": "0",
                    },
                },
            )()

    lines = _same_day_digest_snapshot_lines(
        _Provider(),
        "2026-06-05",
        rows,
        (),
        source_task_view=_Provider.build_task_view("main_chain", "2026-06-05"),
    )

    assert lines == (
        "数据链路: 备用实时源 eastmoney（live_short=primary）；滞后 0 天",
        "当前采用口径: 当天任务落盘；当天以各任务已落盘结论为主。",
        "主链推荐: 有 1 个待复核对象已落盘。",
    )


def test_dashboard_same_day_digest_decision_line_prefers_spotlight_then_tasks_then_debate() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateSpotlight,
        DashboardDebateSummary,
    )

    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="",
        next_step="",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "收盘复盘"),
        reasons=(),
        risks=(),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-1",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.25,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    assert (
        _same_day_digest_decision_line(
            rows=(), spotlights=(spotlight,), debates=(debate,)
        )
        == "当前采用口径: 同日跨任务联动；先看跨任务共振，再回到各任务落盘，委员会只补充分歧与风险。"
    )
    assert (
        _same_day_digest_decision_line(
            rows=(object(),), spotlights=(), debates=(debate,)
        )
        == "当前采用口径: 当天任务落盘；当天以各任务已落盘结论为主，委员会只补充分歧、风险与触发。"
    )
    assert (
        _same_day_digest_decision_line(rows=(), spotlights=(), debates=(debate,))
        == "当前采用口径: 委员会补充结论；当天没有独立任务结论，委员会只作解释，不改写评分。"
    )


def test_dashboard_source_status_verdict_line_marks_history_only_source() -> None:
    assert (
        _source_status_verdict_line(
            {
                "actual_source": "sqlite_db",
                "health_label": "healthy",
                "lag_days": "1",
            }
        )
        == "数据链路: 当前实际源 sqlite_db 只适合历史验证，盘中短线不可用（live_short=avoid）；滞后 1 天"
    )


def test_dashboard_same_day_digest_conclusion_lines_drop_discussion_sections() -> None:
    lines = (
        "跨市主线: 海外主线仍在扩散。 | 先看 300750 宁德时代 | 确认 机器人龙头放量上攻",
        "主链推荐: 有 1 个待复核对象已落盘。",
        "讨论结果: 300750 宁德时代: 倾向优先纸面复核；卡点 先确认承接 | 触发 若放量延续则优先复核",
        "讨论过程: 300750 宁德时代: 倾向优先纸面复核；卡点 先确认承接 | 过程主线 第 1 轮 先讨论海外主线传导 | 关键支持: 跨市传导 看多 / 置信 82% | 海外主线仍在扩散。",
    )

    assert _same_day_digest_conclusion_lines(lines) == (
        "跨市主线: 海外主线仍在扩散。 | 先看 300750 宁德时代 | 确认 机器人龙头放量上攻",
        "主链推荐: 有 1 个待复核对象已落盘。",
    )


def test_dashboard_same_day_summary_card_lines_frontload_conclusion_gate_and_trigger() -> (
    None
):
    from aqsp.web.data_provider import DashboardDateOverview, DashboardDebateSummary

    class _TaskView:
        headline = "主链有 1 个待复核对象"

    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=2,
        actionable_total=1,
        watch_total=0,
        blocked_total=1,
        top_task_label="主链推荐",
        top_headline="主链有 1 个待复核对象",
        blocker_headline="等待板块承接确认",
        focus_headline="300750 宁德时代 | 海外物理AI叙事升温",
        workflow_summary="",
        archive_summary="",
    )
    digest_lines = (
        "数据链路: 备用实时源 eastmoney（live_short=primary）；滞后 0 天",
        "跨市主线: 海外物理AI叙事升温。 | 先看 300750 宁德时代 | 确认 机器人龙头放量上攻",
        "主链推荐: 有 1 个待复核对象已落盘。",
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-0",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.25,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        created_at="2026-06-05T21:00:00+08:00",
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
    )

    assert _same_day_summary_card_lines(
        overview=overview,
        digest_lines=digest_lines,
        debates=(debate,),
        task_view=_TaskView(),
    ) == (
        "21:00 更新 | 委员会结论: 倾向优先纸面复核；卡点 先确认承接",
        "跨市主线: 海外物理AI叙事升温。 | 先看 300750 宁德时代 | 确认 机器人龙头放量上攻",
        "当前卡点: 先确认承接",
        "下一触发: 若放量延续则优先复核",
    )


def test_dashboard_same_day_summary_card_lines_do_not_leak_debate_process() -> None:
    from aqsp.web.data_provider import DashboardDateOverview, DashboardDebateSummary

    class _TaskView:
        headline = "主链有 1 个待复核对象"

    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-0",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.25,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=("过程主线 第 1 轮 先讨论海外映射",),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        created_at="2026-06-05T21:00:00+08:00",
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
        support_points=("关键支持: 海外主线仍在扩散。",),
        opposition_points=("角色分工: 风控继续压降高波动。",),
        watch_items=("观察次日承接。",),
    )

    lines = _same_day_summary_card_lines(
        overview=DashboardDateOverview(
            signal_date="2026-06-05",
            task_count=2,
            actionable_total=1,
            watch_total=0,
            blocked_total=0,
            top_task_label="主链推荐",
            top_headline="主链有 1 个待复核对象",
            blocker_headline="",
            focus_headline="300750 宁德时代",
            workflow_summary="",
            archive_summary="",
        ),
        digest_lines=("主链推荐: 有 1 个待复核对象已落盘。",),
        debates=(debate,),
        task_view=_TaskView(),
    )

    rendered = "\n".join(lines)
    assert "过程主线" not in rendered
    assert "关键支持" not in rendered
    assert "角色分工" not in rendered
    assert "委员会结论" in rendered
    assert "当前卡点" in rendered
    assert "下一触发" in rendered


def test_dashboard_same_day_summary_card_lines_keeps_source_warning_when_no_debate() -> (
    None
):
    from aqsp.web.data_provider import DashboardDateOverview

    class _TaskView:
        headline = "简报已落盘"

    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=1,
        actionable_total=0,
        watch_total=0,
        blocked_total=0,
        top_task_label="简报回看",
        top_headline="简报已落盘",
        blocker_headline="",
        focus_headline="",
        workflow_summary="",
        archive_summary="",
    )
    digest_lines = (
        "数据链路: 当前实际源 sqlite_db 只适合历史验证，盘中短线不可用（live_short=avoid）；滞后 1 天",
        "主链推荐: 当前无待复核对象。",
        "尾盘策略: 当前无待复核对象。",
    )

    assert _same_day_summary_card_lines(
        overview=overview,
        digest_lines=digest_lines,
        debates=(),
        task_view=_TaskView(),
    ) == (
        "数据链路: 当前实际源 sqlite_db 只适合历史验证，盘中短线不可用（live_short=avoid）；滞后 1 天",
        "主链推荐: 当前无待复核对象。",
        "尾盘策略: 当前无待复核对象。",
    )


def test_dashboard_same_day_summary_focus_context_prefers_spotlight_when_no_task_card() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    class _TaskView:
        detail_cards = ()

    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="优先级上调",
        status_label="等待确认",
        blocker="先确认算力链量能扩散",
        next_step="若龙头封单增强则优先复核",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
    )

    selected_card, selected_spotlight, debate_summary = _same_day_summary_focus_context(
        task_view=_TaskView(),
        spotlights=(spotlight,),
        debates=(),
    )

    assert selected_card is None
    assert selected_spotlight == spotlight
    assert debate_summary is None


def test_dashboard_same_day_summary_handoff_lines_keep_current_scope_and_next_focus() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="优先级上调",
        status_label="等待确认",
        blocker="先确认算力链量能扩散",
        next_step="若龙头封单增强则优先复核",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
    )

    assert _same_day_summary_handoff_lines(
        workspace="候选复盘",
        selected_card=None,
        selected_spotlight=spotlight,
        debate_summary=None,
    ) == (
        "当前标的: 688256 寒武纪",
        "当前采用口径: 同日跨任务联动；先核对跨任务结论，再回到单任务原始记录。",
        "切到复盘先看: 先确认算力链量能扩散",
    )


def test_dashboard_render_source_status_frontloads_live_short_verdict(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    subheaders: list[str] = []
    markdown_calls: list[str] = []

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: subheaders.append(text))
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, **kwargs: markdown_calls.append(str(body)),
    )

    dashboard._render_source_status(
        {
            "requested_source": "auto",
            "actual_source": "eastmoney",
            "health_label": "fallback",
            "health_message": "fallback 到 eastmoney；plan成功/失败 5/1，源成功/失败 5/0",
            "data_latest_trade_date": "2026-06-05",
            "lag_days": "0",
        }
    )

    assert subheaders == ["数据源状态"]
    rendered = markdown_calls[0]
    assert (
        "- 链路结论: 数据链路: 备用实时源 eastmoney（live_short=primary）；滞后 0 天"
        in rendered
    )
    assert "- 原始链路: `auto` -> `eastmoney`" in rendered
    assert "- 健康度: `fallback`" in rendered


def test_dashboard_queue_same_day_summary_handoff_preserves_focus_context(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    class _TaskView:
        task_id = "main_chain"
        task_label = "主链推荐"
        detail_cards = ()

    monkeypatch.setattr(dashboard.st, "session_state", {})
    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="优先级上调",
        status_label="等待确认",
        blocker="先确认算力链量能扩散",
        next_step="若龙头封单增强则优先复核",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
    )

    _queue_same_day_summary_handoff(
        workspace="候选复盘",
        signal_date="2026-06-05",
        task_view=_TaskView(),
        spotlights=(spotlight,),
        debates=(),
    )

    assert dashboard.st.session_state["dashboard_pending_workspace"] == "候选复盘"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_signal_date"]
        == "2026-06-05"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_focus_kind"]
        == "spotlight"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_decision_source"]
        == "spotlight"
    )


def test_dashboard_same_day_ordering_uses_digest_task_view() -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardSameDayTaskRow

    calls: list[tuple[str, str]] = []

    class _Provider:
        @staticmethod
        def build_task_digest_view(task_id: str, signal_date: str = ""):
            calls.append((task_id, signal_date))
            return SimpleNamespace(
                detail_cards=(),
                review_lines=(f"{task_id} 轻量摘要",),
                recommendation_lines=(),
                agenda_lines=(),
                watchlist_lines=(),
                blocker_lines=(),
                next_day_focus_lines=(),
            )

        @staticmethod
        def build_task_view(task_id: str, signal_date: str = ""):
            raise AssertionError("首页同日摘要不应构建完整 task view")

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="briefing",
            task_label="多 Agent 简报",
            phase_order=30,
            phase_label="简报",
            phase_summary="",
            status_label="待跟踪",
            headline="多 Agent 已汇总",
            candidate_count=0,
            actionable_count=0,
            watch_count=1,
            blocked_count=0,
        ),
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=10,
            phase_label="收盘",
            phase_summary="",
            status_label="有推荐",
            headline="主链已落盘",
            candidate_count=2,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )

    ordered = dashboard._ordered_same_day_message_rows(
        _Provider(),
        "2026-06-05",
        rows,
    )

    assert calls == [("briefing", "2026-06-05"), ("main_chain", "2026-06-05")]
    assert tuple(row.task_id for row, _view in ordered) == ("main_chain", "briefing")


def test_dashboard_same_day_message_detail_is_lazy_loaded(monkeypatch) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardDateOverview,
        DashboardPaperSummary,
        DashboardSameDayTaskRow,
    )

    rendered_cards: list[dict[str, object]] = []
    captions: list[str] = []

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "subheader", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: captions.append(text))
    monkeypatch.setattr(dashboard.st, "info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )
    monkeypatch.setattr(dashboard, "_render_home_reading_order", lambda **_kwargs: None)
    monkeypatch.setattr(dashboard, "_stretch_button", lambda label, **_kwargs: False)
    monkeypatch.setattr(
        dashboard,
        "_ordered_same_day_message_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("同日明细默认不应构建")
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "_same_day_digest_snapshot_lines",
        lambda *_args, **_kwargs: ("结论: 今日先看主链",),
    )

    row = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        phase_order=10,
        phase_label="收盘",
        phase_summary="",
        status_label="有推荐",
        headline="主链已落盘",
        candidate_count=2,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
    )
    dashboard._render_same_day_message_digest(
        provider=SimpleNamespace(),
        signal_date="2026-06-05",
        rows=(row,),
        task_view=SimpleNamespace(),
        overview=DashboardDateOverview(
            signal_date="2026-06-05",
            task_count=1,
            actionable_total=1,
            watch_total=0,
            blocked_total=0,
            top_task_label="主链推荐",
            top_headline="主链已落盘",
            blocker_headline="",
            focus_headline="今日先看主链",
            workflow_summary="",
            archive_summary="",
        ),
        paper_summary=DashboardPaperSummary(
            signal_date="2026-06-05",
            open_positions=0,
            pending_entries=0,
            not_executable=0,
            closed_trades=0,
            open_position_lines=(),
            event_lines=(),
            action_summary_lines=(),
        ),
        spotlights=(),
        debates=(),
    )

    assert rendered_cards[0]["kicker"] == "当天总控"
    assert any("同日任务明细按需加载" in caption for caption in captions)


def test_dashboard_same_day_digest_snapshot_does_not_build_task_digest_before_detail_click(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardDateOverview,
        DashboardPaperSummary,
        DashboardSameDayTaskRow,
    )

    rendered_cards: list[dict[str, object]] = []
    captions: list[str] = []

    class _Provider:
        @staticmethod
        def build_task_digest_view(task_id: str, signal_date: str = ""):
            raise AssertionError(
                f"首屏摘要不应构建同日任务明细: {task_id}/{signal_date}"
            )

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "subheader", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: captions.append(text))
    monkeypatch.setattr(dashboard.st, "info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )
    monkeypatch.setattr(dashboard, "_render_home_reading_order", lambda **_kwargs: None)
    monkeypatch.setattr(dashboard, "_stretch_button", lambda label, **_kwargs: False)

    row = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        phase_order=1,
        phase_label="盘前主链",
        phase_summary="先确认主推候选与量能承接。",
        status_label="有推荐",
        headline="主链有 1 个待复核对象",
        candidate_count=1,
        actionable_count=1,
        watch_count=0,
        blocked_count=0,
        created_at="2026-06-05T10:00:00+08:00",
    )

    dashboard._render_same_day_message_digest(
        provider=_Provider(),
        signal_date="2026-06-05",
        rows=(row,),
        task_view=SimpleNamespace(
            detail_cards=(),
            headline="主链有 1 个待复核对象",
            source_status={},
        ),
        overview=DashboardDateOverview(
            signal_date="2026-06-05",
            task_count=1,
            actionable_total=1,
            watch_total=0,
            blocked_total=0,
            top_task_label="主链推荐",
            top_headline="主链已落盘",
            blocker_headline="",
            focus_headline="今日先看主链",
            workflow_summary="",
            archive_summary="",
        ),
        paper_summary=DashboardPaperSummary(
            signal_date="2026-06-05",
            open_positions=0,
            pending_entries=0,
            not_executable=0,
            closed_trades=0,
            open_position_lines=(),
            event_lines=(),
            action_summary_lines=(),
        ),
        spotlights=(),
        debates=(),
    )

    assert rendered_cards[0]["kicker"] == "当天总控"
    assert rendered_cards[0]["lines"] == (
        "当前采用口径: 当天任务落盘；当天以各任务已落盘结论为主。",
        "主链推荐: 10:00 更新 | 有 1 个待复核对象已落盘。",
    )
    assert any("同日任务明细按需加载" in caption for caption in captions)


def test_dashboard_date_timeline_cards_render_expandable_date_digest_without_cross_day_leak(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardDebateAgentView,
        DashboardDebateSummary,
        DashboardSameDayTaskRow,
    )

    subheaders: list[str] = []
    captions: list[str] = []
    markdown_calls: list[str] = []
    info_calls: list[str] = []
    rendered_cards: list[dict[str, object]] = []
    button_labels: list[str] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: subheaders.append(text))
    monkeypatch.setattr(dashboard.st, "caption", lambda text: captions.append(text))
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_calls.append(str(body)),
    )
    monkeypatch.setattr(dashboard.st, "info", lambda text: info_calls.append(text))
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: button_labels.append(label) and False,
    )
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    class _Provider:
        @staticmethod
        def timeline_rows(limit: int = 8):
            return (
                type(
                    "_Timeline",
                    (),
                    {
                        "signal_date": "2026-06-05",
                        "task_labels": ("主链推荐", "早盘策略"),
                        "actionable_total": 1,
                        "watch_total": 0,
                        "blocked_total": 1,
                        "headline": "主链有推荐，早盘有阻塞。",
                    },
                )(),
            )

        @staticmethod
        def date_overview(signal_date: str):
            return type(
                "_Overview",
                (),
                {
                    "signal_date": signal_date,
                    "task_count": 2,
                    "actionable_total": 1,
                    "watch_total": 0,
                    "blocked_total": 1,
                    "top_task_label": "主链推荐",
                    "top_headline": "主链有推荐，早盘有阻塞。",
                    "blocker_headline": "需要确认量能承接",
                    "focus_headline": "300750 宁德时代 | 倾向优先纸面复核 | 若放量延续则优先复核",
                    "workflow_summary": "",
                    "archive_summary": "",
                },
            )()

        @staticmethod
        def same_day_task_rows(signal_date: str):
            return (
                DashboardSameDayTaskRow(
                    signal_date=signal_date,
                    task_id="main_chain",
                    task_label="主链推荐",
                    phase_order=1,
                    phase_label="盘前主链",
                    phase_summary="先看主链",
                    status_label="有推荐",
                    headline=f"{signal_date} 主链摘要",
                    candidate_count=1,
                    actionable_count=1,
                    watch_count=0,
                    blocked_count=0,
                ),
            )

        @staticmethod
        def preferred_task_for_date(signal_date: str) -> str:
            return "main_chain"

        @staticmethod
        def debate_summaries(signal_date: str):
            if signal_date != "2026-06-05":
                return ()
            return (
                DashboardDebateSummary(
                    signal_date=signal_date,
                    symbol="300750",
                    display_name="300750 宁德时代",
                    debate_id="debate-0",
                    rating="A",
                    original_score=82.0,
                    adjusted_score=82.0,
                    adjustment_weight=0.0,
                    recommended_adjustment="raise",
                    recommended_adjustment_label="建议上调评分",
                    disagreement_score=0.25,
                    consensus="分歧可控",
                    adjustment_reason="主线延续",
                    bull_count=3,
                    bear_count=1,
                    neutral_count=1,
                    round_count=1,
                    regime="强势",
                    data_source="multi",
                    thresholds_version="v1",
                    summary_lines=(),
                    round_summaries=("先讨论海外主线传导",),
                    risk_warnings=(),
                    opportunity_highlights=(),
                    agent_views=(
                        DashboardDebateAgentView(
                            role_id="cross_market",
                            role_label="跨市传导",
                            stance="bullish",
                            stance_label="看多",
                            confidence=0.82,
                            key_argument="海外主线仍在扩散。",
                            key_risk="",
                            key_opportunity="",
                        ),
                    ),
                    research_verdict="倾向优先纸面复核",
                    primary_risk_gate="先确认承接",
                    next_trigger="若放量延续则优先复核",
                    historical_context_note="强证据样本 4/5 命中",
                    role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
                    support_points=("海外主线仍在扩散。",),
                    opposition_points=(),
                    watch_items=("观察次日承接。",),
                ),
                DashboardDebateSummary(
                    signal_date=signal_date,
                    symbol="600519",
                    display_name="600519 贵州茅台",
                    debate_id="debate-1",
                    rating="A",
                    original_score=80.0,
                    adjusted_score=80.0,
                    adjustment_weight=0.0,
                    recommended_adjustment="keep",
                    recommended_adjustment_label="建议维持评分",
                    disagreement_score=0.41,
                    consensus="分歧较大，暂不改排序",
                    adjustment_reason="需要确认量能承接",
                    bull_count=2,
                    bear_count=1,
                    neutral_count=1,
                    round_count=2,
                    regime="震荡偏强",
                    data_source="multi",
                    thresholds_version="v1",
                    summary_lines=(),
                    round_summaries=("先讨论量能承接",),
                    risk_warnings=(),
                    opportunity_highlights=(),
                    agent_views=(
                        DashboardDebateAgentView(
                            role_id="sector_leader",
                            role_label="板块轮动",
                            stance="neutral",
                            stance_label="中性",
                            confidence=0.61,
                            key_argument="量能承接待确认。",
                            key_risk="",
                            key_opportunity="",
                        ),
                    ),
                    research_verdict="先保留评分",
                    primary_risk_gate="量能承接待确认",
                    next_trigger="放量站稳再看",
                ),
            )

        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            return type(
                "_TaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": (f"{signal_date} 的消息摘要",),
                    "headline": f"{signal_date} 的 headline",
                    "detail_cards": (),
                },
            )()

    dashboard._render_date_timeline_cards(_Provider(), "2026-06-05", "briefing")

    assert subheaders == ["按日期展开"]
    assert rendered_cards == [
        {
            "kicker": "2026-06-05",
            "title": "300750 宁德时代 | 倾向优先纸面复核 | 若放量延续则优先复核",
            "lines": (
                "涉及任务: 主链推荐、早盘策略",
                "当前采用口径: 当天任务落盘；当天以各任务已落盘结论为主，委员会只补充分歧、风险与触发。",
            ),
            "tone": "blocked",
        },
        {
            "kicker": "多 Agent",
            "title": "300750 宁德时代",
            "lines": (
                "倾向优先纸面复核；卡点 先确认承接 | 跨市主线: 海外主线仍在扩散。 | 触发 若放量延续则优先复核",
                "支持方: 跨市传导 看多 / 置信 82%",
                "过程主线 第 1 轮 先讨论海外主线传导 | 讨论待确认: 观察次日承接。",
            ),
            "tone": "focus",
        },
    ]
    assert not any("**任务进展**" == call for call in markdown_calls)
    assert not any("**当日结论**" == call for call in markdown_calls)
    assert not any("**多 Agent 结论**" == call for call in markdown_calls)
    assert not any("**多 Agent 过程**" == call for call in markdown_calls)
    assert not any(call.startswith("**涉及任务**:") for call in markdown_calls)
    assert not any(call.startswith("**当天摘要**:") for call in markdown_calls)
    assert any("该日无 briefing，已到 主链推荐" == text for text in captions)
    assert not info_calls
    assert button_labels == ["切到这天"]


def test_dashboard_date_timeline_cards_queue_home_handoff_on_action(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardSameDayTaskRow

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Provider:
        @staticmethod
        def timeline_rows(limit: int = 8):
            return (
                type(
                    "_Timeline",
                    (),
                    {
                        "signal_date": "2026-06-05",
                        "task_labels": ("主链推荐", "早盘策略"),
                        "actionable_total": 1,
                        "watch_total": 0,
                        "blocked_total": 0,
                        "headline": "主链有推荐。",
                    },
                )(),
            )

        @staticmethod
        def same_day_task_rows(signal_date: str):
            return (
                DashboardSameDayTaskRow(
                    signal_date=signal_date,
                    task_id="main_chain",
                    task_label="主链推荐",
                    phase_order=1,
                    phase_label="盘前主链",
                    phase_summary="先看主链",
                    status_label="有推荐",
                    headline=f"{signal_date} 主链摘要",
                    candidate_count=1,
                    actionable_count=1,
                    watch_count=0,
                    blocked_count=0,
                ),
            )

        @staticmethod
        def preferred_task_for_date(signal_date: str) -> str:
            return "main_chain"

        @staticmethod
        def debate_summaries(signal_date: str):
            return ()

        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            return type(
                "_TaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": (f"{signal_date} 的消息摘要",),
                    "headline": f"{signal_date} 的 headline",
                    "detail_cards": (),
                },
            )()

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard.st, "info", lambda text: None)
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard, "_render_cockpit_card", lambda **kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: label == "切到这天",
    )
    reruns: list[bool] = []
    monkeypatch.setattr(dashboard.st, "rerun", lambda: reruns.append(True))

    dashboard._render_date_timeline_cards(_Provider(), "2026-06-05", "briefing")

    assert reruns == [True]
    assert dashboard.st.session_state["dashboard_pending_workspace"] == "决策首页"
    assert dashboard.st.session_state["dashboard_pending_selected_date"] == "2026-06-05"
    assert dashboard.st.session_state["dashboard_pending_task_id"] == "main_chain"


def test_dashboard_render_same_day_message_digest_exposes_summary_actions(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardCandidateSpotlight,
        DashboardSameDayTaskRow,
    )

    subheaders: list[str] = []
    captions: list[str] = []
    rendered_cards: list[dict[str, object]] = []
    button_labels: list[str] = []
    expanders: list[tuple[str, bool]] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubExpander:
        def __init__(self, label: str, expanded: bool) -> None:
            self.label = label
            self.expanded = expanded
            expanders.append((label, expanded))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _TaskView:
        task_id = "main_chain"
        task_label = "主链推荐"
        detail_cards = ()
        headline = "主链有 1 个待复核对象"

    class _Provider:
        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            return type(
                "_RowTaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": ("main_chain 的摘要",),
                    "headline": "main_chain 的 headline",
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                    "detail_cards": (),
                    "source_status": {},
                },
            )()

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先看主推候选与量能承接。",
            status_label="有推荐",
            headline="主链有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )
    overview = type(
        "_Overview",
        (),
        {
            "blocked_total": 0,
            "actionable_total": 1,
            "focus_headline": "688256 寒武纪 | 海外物理AI叙事升温",
            "top_headline": "主链有 1 个待复核对象",
        },
    )()
    paper_summary = type(
        "_PaperSummary",
        (),
        {"pending_entries": 0, "not_executable": 0, "open_positions": 0},
    )()
    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="优先级上调",
        status_label="等待确认",
        blocker="先确认算力链量能扩散",
        next_step="若龙头封单增强则优先复核",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
        cross_market_summary="海外物理AI叙事升温(纸面复核)",
    )

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: subheaders.append(text))
    monkeypatch.setattr(dashboard.st, "caption", lambda text: captions.append(text))
    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "expander", _StubExpander)
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_home_reading_order",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: button_labels.append(label) and False,
    )

    dashboard._render_same_day_message_digest(
        provider=_Provider(),
        signal_date="2026-06-05",
        rows=rows,
        task_view=_TaskView(),
        overview=overview,
        paper_summary=paper_summary,
        spotlights=(spotlight,),
        debates=(),
    )

    assert subheaders == ["同日速读"]
    assert captions[0] == "只保留今天最重要的结论、卡点和少量阶段证据。"
    assert any("同日任务明细按需加载" in caption for caption in captions)
    assert rendered_cards[0]["kicker"] == "当天总控"
    assert len(rendered_cards) == 2
    assert rendered_cards[1]["kicker"] == "盘前主链 · 主链推荐"
    assert "数量: 待复核 1 / 观察 0 / 阻塞 0" in rendered_cards[1]["lines"]
    assert expanders == []
    assert button_labels[:5] == ["复盘", "虚拟盘", "归档", "看这段", "加载同日任务明细"]


def test_dashboard_render_same_day_message_digest_caps_lite_phase_cards(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardDateOverview,
        DashboardPaperSummary,
        DashboardSameDayTaskRow,
    )

    rendered_cards: list[dict[str, object]] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    rows = tuple(
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id=f"task_{index}",
            task_label=f"任务{index}",
            phase_order=index,
            phase_label=f"阶段{index}",
            phase_summary="",
            status_label="已落盘",
            headline=f"任务{index} 已落盘",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        )
        for index in range(1, 4)
    )

    monkeypatch.setattr(dashboard.st, "subheader", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        dashboard.st,
        "columns",
        lambda count: [_StubColumn() for _ in range(count)],
    )
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )
    monkeypatch.setattr(dashboard, "_render_home_reading_order", lambda **_kwargs: None)
    monkeypatch.setattr(dashboard, "_stretch_button", lambda *_, **__: False)
    monkeypatch.setattr(
        dashboard,
        "_same_day_digest_snapshot_lines",
        lambda *_args, **_kwargs: ("结论: 今日只看最关键阶段",),
    )
    monkeypatch.setattr(
        dashboard,
        "_ordered_same_day_message_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("同日明细默认不应构建")
        ),
    )

    dashboard._render_same_day_message_digest(
        provider=SimpleNamespace(),
        signal_date="2026-06-05",
        rows=rows,
        task_view=SimpleNamespace(
            detail_cards=(), headline="主链摘要", source_status={}
        ),
        overview=DashboardDateOverview(
            signal_date="2026-06-05",
            task_count=3,
            actionable_total=3,
            watch_total=0,
            blocked_total=0,
            top_task_label="任务1",
            top_headline="任务1 已落盘",
            blocker_headline="",
            focus_headline="今日只看最关键阶段",
            workflow_summary="",
            archive_summary="",
        ),
        paper_summary=DashboardPaperSummary(
            signal_date="2026-06-05",
            open_positions=0,
            pending_entries=0,
            not_executable=0,
            closed_trades=0,
            open_position_lines=(),
            event_lines=(),
            action_summary_lines=(),
        ),
        spotlights=(),
        debates=(),
    )

    lite_cards = [
        card for card in rendered_cards if str(card["kicker"]).startswith("阶段")
    ]
    assert len(lite_cards) == 2


def test_dashboard_render_same_day_message_digest_uses_runtime_fallback_when_no_rows(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    rendered_cards: list[dict[str, object]] = []
    infos: list[str] = []

    class _Provider:
        @staticmethod
        def runtime_fallback_digest_lines(signal_date: str):
            assert signal_date == "2026-07-07"
            return (
                "结论: 组合保护生效，暂停新增纸面复核",
                "运行状态: 任务 coldstart / 日期 2026-07-07",
                "风险/阻塞: 组合保护冷却期中，至 2026-07-12 解除",
            )

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard.st, "info", lambda text: infos.append(text))
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    dashboard._render_same_day_message_digest(
        provider=_Provider(),
        signal_date="2026-07-07",
        rows=(),
        task_view=object(),
        overview=object(),
        paper_summary=object(),
        spotlights=(),
        debates=(),
    )

    assert infos == []
    assert rendered_cards == [
        {
            "kicker": "运行状态",
            "title": "组合保护生效，暂停新增纸面复核",
            "lines": (
                "运行状态: 任务 coldstart / 日期 2026-07-07",
                "风险/阻塞: 组合保护冷却期中，至 2026-07-12 解除",
            ),
            "tone": "blocked",
        }
    ]


def test_dashboard_render_same_day_message_digest_marks_history_source_as_blocked(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    rendered_cards: list[dict[str, object]] = []

    class _Provider:
        @staticmethod
        def runtime_fallback_digest_lines(signal_date: str):
            assert signal_date == "2026-07-07"
            return (
                "结论: 最近运行已落盘，等待完整收盘摘要",
                "数据: 当前实际源 sqlite_db 只适合历史验证，盘中短线不可用（live_short=avoid） / 数据日 2026-07-07 / 延迟 0 天",
            )

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard.st, "info", lambda text: None)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    dashboard._render_same_day_message_digest(
        provider=_Provider(),
        signal_date="2026-07-07",
        rows=(),
        task_view=object(),
        overview=object(),
        paper_summary=object(),
        spotlights=(),
        debates=(),
    )

    assert rendered_cards[0]["tone"] == "blocked"


def test_dashboard_render_home_runtime_truth_explains_coldstart_block(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    rendered_cards: list[dict[str, object]] = []

    class _Run:
        action = "coldstart"
        task_label = "冷启动"
        status_label = "风控阻塞"
        headline = "冷启动筛选被组合保护正常阻塞；历史库已更新，本次不追加新增候选"
        detail_lines = ("冷启动: 34/30",)

    class _Provider:
        @staticmethod
        def runtime_overview(signal_date: str):
            assert signal_date == "2026-07-07"
            return type(
                "_RuntimeOverview",
                (),
                {
                    "conclusion": "冷启动样本已达标，等待组合保护冷却",
                    "task_label": "冷启动",
                    "signal_date": "2026-07-07",
                    "run_status": "风控阻塞",
                    "effective_source": "sqlite_db",
                    "requested_source": "sqlite_db",
                    "source_reason": "当前实际源 sqlite_db 只适合历史验证，盘中短线不可用（live_short=avoid）",
                    "data_latest_trade_date": "2026-07-07",
                    "lag_days": "0",
                    "risk_reason": "组合保护冷却期中，至 2026-07-12 解除",
                    "cooldown_until": "2026-07-12",
                    "coldstart_progress": "34/30",
                    "gate_blocker_line": "双门 gate: DSR 未过门 / PBO 未过门",
                    "market_context_runtime_line": "跨市规则: 9 条在线 | 商业航天 / 物理 AI / 地缘冲突 等 | 边界: 确定性上下文优先级增强",
                    "walkforward_runtime_line": "生产 gate: DSR/PBO 未过门 / 生产回测 超时 / 更新 2026-06-30 / 覆盖 5209 标的 / gate 数据至 2024-12-31 / 后续需重跑生产 walk-forward",
                },
            )()

        @staticmethod
        def runtime_fallback_digest_lines(signal_date: str):
            assert signal_date == "2026-07-07"
            return (
                "结论: 组合保护生效，暂停新增纸面复核",
                "运行状态: 任务 coldstart / 日期 2026-07-07",
                "数据: 当前实际源 sqlite_db 只适合历史验证，盘中短线不可用（live_short=avoid） / 数据日 2026-07-07 / 延迟 0 天",
                "风险/阻塞: 组合保护冷却期中，至 2026-07-12 解除",
            )

        @staticmethod
        def runtime_task_runs(signal_date: str):
            raise AssertionError(
                "runtime truth must not read task logs when overview exists"
            )

    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    dashboard._render_home_runtime_truth(_Provider(), signal_date="2026-07-07")

    assert rendered_cards == [
        {
            "kicker": "运行真相",
            "title": "冷启动样本已达标，等待组合保护冷却",
            "lines": (
                "运行状态: 冷启动 / 2026-07-07 / 风控阻塞",
                "风险/阻塞: 组合保护冷却期中，至 2026-07-12 解除",
                "冷启动: 34/30，样本门已达标；双门 gate: DSR 未过门 / PBO 未过门；不再追加冷启动样本。",
                "生产 gate: DSR/PBO 未过门 / 生产回测 超时 / 更新 2026-06-30 / 覆盖 5209 标的 / gate 数据至 2024-12-31 / 后续需重跑生产 walk-forward",
                "跨市规则: 9 条在线 | 商业航天 / 物理 AI / 地缘冲突 等 | 边界: 确定性上下文优先级增强",
            ),
            "tone": "blocked",
        }
    ]


def test_dashboard_render_home_runtime_truth_marks_history_source_as_blocked(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    rendered_cards: list[dict[str, object]] = []

    class _Provider:
        @staticmethod
        def runtime_overview(signal_date: str):
            assert signal_date == "2026-07-07"
            return type(
                "_RuntimeOverview",
                (),
                {
                    "conclusion": "最近运行已落盘，等待完整收盘摘要",
                    "task_label": "收盘主链",
                    "signal_date": "2026-07-07",
                    "run_status": "完成",
                    "effective_source": "sqlite_db",
                    "requested_source": "sqlite_db",
                    "source_reason": "当前实际源 sqlite_db 只适合历史验证，盘中短线不可用（live_short=avoid）",
                    "data_latest_trade_date": "2026-07-07",
                    "lag_days": "0",
                    "risk_reason": "",
                    "cooldown_until": "",
                    "coldstart_progress": "",
                    "gate_blocker_line": "",
                    "market_context_runtime_line": "",
                    "walkforward_runtime_line": "",
                },
            )()

        @staticmethod
        def runtime_fallback_digest_lines(signal_date: str):
            return ()

    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    dashboard._render_home_runtime_truth(_Provider(), signal_date="2026-07-07")

    assert rendered_cards[0]["tone"] == "blocked"


def test_dashboard_render_runtime_task_runs_passes_limit_to_provider() -> None:
    import aqsp.web.dashboard as dashboard

    calls: list[tuple[str, int | None]] = []

    class _Provider:
        @staticmethod
        def runtime_task_runs(signal_date: str, limit: int | None = None):
            calls.append((signal_date, limit))
            return ()

    dashboard._render_runtime_task_runs(
        _Provider(),  # type: ignore[arg-type]
        log_date="2026-07-08",
        limit=2,
    )

    assert calls == [("2026-07-08", 2)]


def test_dashboard_render_same_day_message_digest_queue_summary_handoff_on_action(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardCandidateSpotlight,
        DashboardSameDayTaskRow,
    )

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubExpander:
        def __init__(self, label: str, expanded: bool) -> None:
            self.label = label
            self.expanded = expanded

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _TaskView:
        task_id = "main_chain"
        task_label = "主链推荐"
        detail_cards = ()
        headline = "主链有 1 个待复核对象"

    class _Provider:
        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            return type(
                "_RowTaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": ("main_chain 的摘要",),
                    "headline": "main_chain 的 headline",
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                    "detail_cards": (),
                    "source_status": {},
                },
            )()

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先看主推候选与量能承接。",
            status_label="有推荐",
            headline="主链有 1 个待复核对象",
            candidate_count=1,
            actionable_count=1,
            watch_count=0,
            blocked_count=0,
        ),
    )
    overview = type(
        "_Overview",
        (),
        {
            "blocked_total": 0,
            "actionable_total": 1,
            "focus_headline": "688256 寒武纪 | 海外物理AI叙事升温",
            "top_headline": "主链有 1 个待复核对象",
        },
    )()
    paper_summary = type(
        "_PaperSummary",
        (),
        {"pending_entries": 0, "not_executable": 0, "open_positions": 0},
    )()
    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="优先级上调",
        status_label="等待确认",
        blocker="先确认算力链量能扩散",
        next_step="若龙头封单增强则优先复核",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
        cross_market_summary="海外物理AI叙事升温(纸面复核)",
    )

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard.st, "expander", _StubExpander)
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard, "_render_cockpit_card", lambda **kwargs: None)
    monkeypatch.setattr(dashboard, "_render_home_reading_order", lambda **kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: label == "复盘",
    )
    reruns: list[bool] = []
    monkeypatch.setattr(dashboard.st, "rerun", lambda: reruns.append(True))

    dashboard._render_same_day_message_digest(
        provider=_Provider(),
        signal_date="2026-06-05",
        rows=rows,
        task_view=_TaskView(),
        overview=overview,
        paper_summary=paper_summary,
        spotlights=(spotlight,),
        debates=(),
    )

    assert reruns == [True]
    assert dashboard.st.session_state["dashboard_pending_workspace"] == "候选复盘"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_signal_date"]
        == "2026-06-05"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_focus_kind"]
        == "spotlight"
    )


def test_dashboard_timeline_debate_conclusion_lines_merge_support_and_bear_views() -> (
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
        support_points=("量能承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
        research_verdict="先保留评分",
        primary_risk_gate="量能承接待确认",
        next_trigger="放量站稳再看",
    )

    assert _timeline_debate_conclusion_lines(debate) == (
        "600036 招商银行: 先保留评分；卡点 量能承接待确认 | 触发 放量站稳再看",
        "讨论站位: 支持 板块轮动 看多 / 置信 91% | 板块轮动认为当前价格位置合理 | 反对 基本面空头 看空 / 置信 70% | 基本面空头认为当前价格位置偏高",
        "讨论待确认: 观察次日承接是否继续。",
    )


def test_dashboard_timeline_debate_process_line_merges_support_and_bear_views() -> None:
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
        round_summaries=("先讨论银行权重承接。",),
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
        support_points=("量能承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
        research_verdict="先保留评分",
        primary_risk_gate="量能承接待确认",
        next_trigger="放量站稳再看",
    )

    assert _timeline_debate_process_line(debate) == (
        "- 600036 招商银行: 过程主线 第 1 轮 先讨论银行权重承接。"
        " | 过程对照: 支持 板块轮动 看多 / 置信 91% | 板块轮动认为当前价格位置合理"
        " | 反对 基本面空头 看空 / 置信 70% | 基本面空头认为当前价格位置偏高"
        " | 讨论待确认: 观察次日承接是否继续。"
    )


def test_dashboard_home_task_board_uses_direct_reading_hint(monkeypatch) -> None:
    import aqsp.web.dashboard as dashboard

    subheaders: list[str] = []
    captions: list[str] = []
    markdown_calls: list[str] = []

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: subheaders.append(text))
    monkeypatch.setattr(dashboard.st, "caption", lambda text: captions.append(text))
    monkeypatch.setattr(
        dashboard, "_render_daily_workflow", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(dashboard.st, "columns", lambda spec: (object(), object()))
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_calls.append(str(body)),
    )
    monkeypatch.setattr(
        dashboard, "_render_home_action_rail", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        dashboard, "_render_home_execution_snapshot", lambda *args, **kwargs: None
    )

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        dashboard.st, "columns", lambda spec: (_StubColumn(), _StubColumn())
    )

    dashboard._render_home_task_board(
        rows=(),
        current_task_id="main_chain",
        task_view=type("_TaskView", (), {})(),
        spotlights=(),
        debates=(),
        paper_summary=type("_PaperSummary", (), {})(),
        overview=type("_Overview", (), {})(),
    )

    assert subheaders == ["今天先看什么"]
    assert captions == ["先看今天走到哪一步，再看先看这些和纸面记录。"]
    assert "**先看这些**" in markdown_calls
    assert "**纸面记录**" in markdown_calls


def test_dashboard_main_homepage_keeps_simple_card_order(monkeypatch) -> None:
    import aqsp.web.dashboard as dashboard

    call_order: list[str] = []
    markdown_calls: list[str] = []
    button_labels: list[str] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubExpander:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Now:
        def strftime(self, fmt: str) -> str:
            return "2026-07-02 09:30:00 +0800"

    class _Provider:
        def task_snapshots(self, signal_date=None):
            raise AssertionError("homepage must not build task snapshots")

        def task_options(self):
            raise AssertionError("homepage must not load task navigation options")

        def dashboard_dates(self):
            return ("2026-06-05",)

        def preferred_task_for_date(self, signal_date):
            assert signal_date == "2026-06-05"
            return "main_chain"

        def build_task_view(self, task_id, signal_date=None):
            raise AssertionError("homepage must not build full task view")

        def build_task_digest_view(self, task_id, signal_date=None):
            call_order.append("digest")
            return type(
                "_TaskView",
                (),
                {
                    "task_id": "main_chain",
                    "selected_date": "2026-06-05",
                    "latest_date": "2026-06-05",
                    "summary_lines": (),
                    "headline": "主链摘要",
                    "agenda_lines": (),
                    "blocker_lines": (),
                    "review_lines": (),
                    "source_status": {},
                    "detail_cards": (),
                    "report_markdown": "",
                    "report_summary_lines": (),
                    "runtime_lines": (),
                    "next_day_focus_lines": (),
                    "market_environment": (),
                    "strategy_breakdown_lines": (),
                    "lesson_lines": (),
                    "improvement_lines": (),
                },
            )()

        def same_day_task_rows(self, signal_date):
            return ()

        def same_day_candidate_spotlights(self, signal_date, limit=8):
            call_order.append(f"spotlights:{limit}")
            return ()

        def date_overview(self, signal_date):
            return type(
                "_Overview",
                (),
                {
                    "signal_date": signal_date,
                    "task_count": 0,
                    "actionable_total": 0,
                    "watch_total": 0,
                    "blocked_total": 0,
                    "top_task_label": "",
                    "top_headline": "",
                    "blocker_headline": "",
                    "focus_headline": "",
                    "workflow_summary": "",
                    "archive_summary": "",
                },
            )()

        def paper_summary(self, signal_date):
            from aqsp.web.data_provider import DashboardPaperSummary

            return DashboardPaperSummary(
                signal_date=signal_date,
                open_positions=0,
                pending_entries=0,
                not_executable=0,
                closed_trades=0,
                open_position_lines=(),
                event_lines=(),
                action_summary_lines=(),
            )

    monkeypatch.setattr(dashboard, "get_provider", lambda: _Provider())
    monkeypatch.setattr(dashboard, "now_shanghai", lambda: _Now())
    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard, "_inject_dashboard_styles", lambda: None)
    monkeypatch.setattr(
        dashboard,
        "_render_top_navigation",
        lambda **kwargs: ("main_chain", "2026-06-05"),
    )
    monkeypatch.setattr(dashboard, "_render_workspace_navigation", lambda: "决策首页")
    monkeypatch.setattr(
        dashboard, "_provider_prioritized_debates", lambda *args, **kwargs: ()
    )
    monkeypatch.setattr(dashboard.st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_calls.append(str(body)),
    )
    monkeypatch.setattr(
        dashboard.st,
        "button",
        lambda label, *args, **kwargs: button_labels.append(str(label)) and False,
    )
    monkeypatch.setattr(dashboard.st, "divider", lambda: call_order.append("divider"))
    monkeypatch.setattr(
        dashboard,
        "_render_home_runtime_truth",
        lambda *args, **kwargs: call_order.append("truth"),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_same_day_message_digest",
        lambda **kwargs: call_order.append("messages"),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_runtime_task_runs",
        lambda *args, **kwargs: call_order.append(f"runtime:{kwargs.get('limit')}"),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_home_debate_process",
        lambda *args, **kwargs: call_order.append("process"),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_home_debate_results",
        lambda *args, **kwargs: call_order.append("results"),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_date_timeline_cards",
        lambda *args, **kwargs: call_order.append("timeline"),
    )
    monkeypatch.setattr(dashboard.st, "subheader", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard.st, "info", lambda *args, **kwargs: None)
    expander_calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        dashboard.st,
        "expander",
        lambda label, expanded=False: (
            expander_calls.append((str(label), bool(expanded))) or _StubExpander()
        ),
    )
    monkeypatch.setattr(
        dashboard.st,
        "columns",
        lambda *args, **kwargs: (_StubColumn(), _StubColumn()),
    )

    dashboard.main()

    assert call_order == ["digest"]
    assert expander_calls == []
    assert button_labels == [
        "2026-06-05",
        "全部  0",
        "推荐  0",
        "观察  0",
        "阻塞  0",
    ]
    assert any("2026-06-05" in block for block in markdown_calls)
    assert any("短线决策看板" in block for block in markdown_calls)


def test_dashboard_main_homepage_uses_digest_view_and_caps_spotlights(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardDateOverview,
        DashboardPaperSummary,
        DashboardSameDayTaskRow,
    )

    seen: dict[str, object] = {}

    class _Now:
        def strftime(self, fmt: str) -> str:
            return "2026-07-02 09:30:00 +0800"

    class _Provider:
        def task_snapshots(self, signal_date=None):
            return ()

        def task_options(self):
            return ()

        def dashboard_dates(self):
            return ("2026-06-05",)

        def preferred_task_for_date(self, signal_date):
            assert signal_date == "2026-06-05"
            return "main_chain"

        def build_task_view(self, task_id, signal_date=None):
            raise AssertionError("homepage must not build full task view")

        def build_task_digest_view(self, task_id, signal_date=None):
            seen["digest"] = (task_id, signal_date)
            return type(
                "_TaskView",
                (),
                {
                    "task_id": "main_chain",
                    "selected_date": "2026-06-05",
                    "latest_date": "2026-06-05",
                    "detail_cards": (),
                    "headline": "主链摘要",
                    "source_status": {},
                },
            )()

        def same_day_task_rows(self, signal_date):
            return (
                DashboardSameDayTaskRow(
                    signal_date=signal_date,
                    task_id="main_chain",
                    task_label="主链推荐",
                    phase_order=1,
                    phase_label="盘前主链",
                    phase_summary="先看主链承接",
                    status_label="有推荐",
                    headline="主链有 1 个待复核对象",
                    candidate_count=1,
                    actionable_count=1,
                    watch_count=0,
                    blocked_count=0,
                ),
            )

        def same_day_candidate_spotlights(self, signal_date, limit=8):
            seen["spotlights"] = (signal_date, limit)
            return ()

        def prioritized_debate_summaries(
            self,
            signal_date,
            *,
            limit=8,
            salient_only=False,
        ):
            seen["debates"] = (signal_date, limit, salient_only)
            return ()

        def date_overview(self, signal_date, *, spotlights=None, debates=None):
            seen["overview_context"] = (signal_date, spotlights, debates)
            return DashboardDateOverview(
                signal_date=signal_date,
                task_count=1,
                actionable_total=1,
                watch_total=0,
                blocked_total=0,
                top_task_label="主链推荐",
                top_headline="主链有 1 个待复核对象",
                blocker_headline="",
                focus_headline="今日先看主链",
                workflow_summary="",
                archive_summary="",
            )

        def paper_summary(self, signal_date):
            return DashboardPaperSummary(
                signal_date=signal_date,
                open_positions=0,
                pending_entries=0,
                not_executable=0,
                closed_trades=0,
                open_position_lines=(),
                event_lines=(),
                action_summary_lines=(),
            )

    monkeypatch.setattr(dashboard, "get_provider", lambda: _Provider())
    monkeypatch.setattr(dashboard, "now_shanghai", lambda: _Now())
    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard, "_inject_dashboard_styles", lambda: None)
    monkeypatch.setattr(
        dashboard,
        "_render_top_navigation",
        lambda **kwargs: ("main_chain", "2026-06-05"),
    )
    monkeypatch.setattr(dashboard, "_render_workspace_navigation", lambda: "决策首页")
    monkeypatch.setattr(dashboard.st, "title", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        dashboard.st,
        "pills",
        lambda label, options, **kwargs: options[0],
    )
    monkeypatch.setattr(dashboard, "_render_workspace_handoff_notice", lambda **_: None)
    monkeypatch.setattr(dashboard, "_render_home_runtime_truth", lambda *_, **__: None)
    monkeypatch.setattr(dashboard, "_render_runtime_task_runs", lambda *_, **__: None)
    monkeypatch.setattr(
        dashboard,
        "_render_same_day_message_digest",
        lambda **kwargs: seen.setdefault("digest_rows", len(kwargs["rows"])),
    )

    dashboard.main()

    assert seen["digest"] == ("main_chain", "2026-06-05")
    assert seen["spotlights"] == ("2026-06-05", 3)
    assert seen["debates"] == ("2026-06-05", 3, True)
    assert seen["overview_context"] == ("2026-06-05", (), ())
    assert "digest_rows" not in seen


def test_dashboard_simple_home_board_keeps_detail_process_off_homepage() -> None:
    import inspect
    import aqsp.web.dashboard as dashboard

    source = inspect.getsource(dashboard._render_simple_home_board)
    agent_source = inspect.getsource(dashboard._render_simple_agent_panel)

    assert 'with st.expander("多 Agent 过程和结果", expanded=False):' not in source
    assert 'with st.expander("更多切换与回看", expanded=False):' not in source
    assert "_render_home_debate_process" not in source
    assert "_render_simple_agent_panel" in source
    assert "_render_simple_agent_process_panel" not in source
    assert "_render_same_day_message_digest" not in source
    assert "_render_simple_today_digest" in source
    assert "_render_home_debate_results" not in source
    assert "_render_home_debate_results" not in agent_source
    assert 'button_label="加载多 Agent 讨论过程"' not in source
    assert "_SIMPLE_HOME_PANELS" not in source
    assert "aqsp_simple_home_panel" not in source


def test_dashboard_simple_home_board_exposes_agent_panel_when_debates_exist(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardDateOverview,
        DashboardDebateSummary,
        DashboardPaperSummary,
    )

    calls: list[str] = []
    button_labels: list[str] = []

    class _StubContainer:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    debate = DashboardDebateSummary(
        signal_date="2026-06-05",
        symbol="600519",
        display_name="600519 贵州茅台",
        debate_id="debate-1",
        rating="buy_candidate",
        original_score=70.0,
        adjusted_score=70.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="维持观察",
        disagreement_score=0.2,
        consensus="先观察承接",
        adjustment_reason="",
        bull_count=1,
        bear_count=0,
        neutral_count=1,
        round_count=1,
        regime="unknown",
        data_source="multi",
        thresholds_version="test",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
    )

    class _Provider:
        def dashboard_dates(self):
            return ("2026-06-05",)

        def preferred_task_for_date(self, signal_date):
            return "main_chain"

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "container", lambda **kwargs: _StubContainer())
    monkeypatch.setattr(
        dashboard.st,
        "columns",
        lambda *args, **kwargs: (_StubContainer(), _StubContainer()),
    )
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        dashboard.st,
        "button",
        lambda label, *args, **kwargs: button_labels.append(str(label)) and False,
    )
    monkeypatch.setattr(
        dashboard,
        "_render_simple_recommendation_panel",
        lambda **kwargs: calls.append("recommendations"),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_simple_agent_panel",
        lambda **kwargs: calls.append("agents"),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_simple_today_digest",
        lambda **kwargs: calls.append("today_digest"),
    )

    dashboard._render_simple_home_board(
        provider=_Provider(),
        signal_date="2026-06-05",
        task_view=object(),
        same_day_rows=(),
        same_day_spotlights=(),
        same_day_debates=(debate,),
        overview=DashboardDateOverview(
            signal_date="2026-06-05",
            task_count=1,
            actionable_total=0,
            watch_total=1,
            blocked_total=0,
            top_task_label="",
            top_headline="",
            blocker_headline="",
            focus_headline="",
            workflow_summary="",
            archive_summary="",
        ),
        paper_summary=DashboardPaperSummary(
            signal_date="2026-06-05",
            open_positions=0,
            pending_entries=0,
            not_executable=0,
            closed_trades=0,
            open_position_lines=(),
            event_lines=(),
            action_summary_lines=(),
        ),
    )

    assert not any(label.startswith("Agent讨论") for label in button_labels)
    assert calls == ["recommendations", "agents", "today_digest"]


def test_dashboard_simple_recommendation_panel_keeps_candidates_under_cooldown(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDateOverview

    markdown_blocks: list[str] = []
    cockpit_cards: list[dict[str, object]] = []

    class _Provider:
        @staticmethod
        def runtime_overview(signal_date: str):
            return SimpleNamespace(
                cooldown_until="2026-07-15",
                risk_reason="组合保护冷却期中",
            )

    task_view = SimpleNamespace(
        detail_cards=(
            DashboardCandidateCard(
                symbol="002025",
                name="航天电器",
                display_name="002025 航天电器",
                rank_label="第一顺位",
                score=59.15,
                action_label="继续观察",
                status_label="",
                decision_note="",
                next_step="看开盘承接",
                blocker="",
                review_meta="",
                reasons=("MA多头排列", "相对强势"),
                risks=(),
                strategies=(),
                data_source="intraday",
            ),
        )
    )

    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda text, *args, **kwargs: markdown_blocks.append(str(text)),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: cockpit_cards.append(kwargs),
    )

    dashboard._render_simple_recommendation_panel(
        provider=_Provider(),
        signal_date="2026-07-10",
        task_view=task_view,
        spotlights=(),
        overview=DashboardDateOverview(
            signal_date="2026-07-10",
            task_count=1,
            actionable_total=1,
            watch_total=0,
            blocked_total=0,
            top_task_label="",
            top_headline="",
            blocker_headline="",
            focus_headline="",
            workflow_summary="",
            archive_summary="",
        ),
    )

    rendered = "\n".join(markdown_blocks)
    assert "今日候选已产生，组合保护中" in rendered
    assert "今日无可纸面复核推荐" not in rendered
    assert "002025 航天电器" in rendered
    assert cockpit_cards == []


def test_dashboard_simple_recommendation_panel_hides_duplicate_observation_rows(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDateOverview

    markdown_blocks: list[str] = []

    class _Provider:
        @staticmethod
        def runtime_overview(signal_date: str):
            return SimpleNamespace(cooldown_until="", risk_reason="")

    task_view = SimpleNamespace(
        detail_cards=(
            DashboardCandidateCard(
                symbol="603019",
                name="中科曙光",
                display_name="603019 中科曙光",
                rank_label="第一顺位",
                score=72.0,
                action_label="纸面复核",
                status_label="新晋",
                decision_note="",
                next_step="等待量价确认",
                blocker="",
                review_meta="",
                reasons=("强势延续",),
                risks=(),
                strategies=(),
                data_source="intraday",
            ),
            DashboardCandidateCard(
                symbol="600879",
                name="航天电子",
                display_name="600879 航天电子",
                rank_label="观察",
                score=61.0,
                action_label="继续观察",
                status_label="等待确认",
                decision_note="等待量能",
                next_step="等待下一次刷新",
                blocker="",
                review_meta="",
                reasons=(),
                risks=(),
                strategies=(),
                data_source="intraday",
            ),
        )
    )

    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda text, *args, **kwargs: markdown_blocks.append(str(text)),
    )
    monkeypatch.setattr(dashboard, "_render_cockpit_card", lambda **kwargs: None)

    dashboard._render_simple_recommendation_panel(
        provider=_Provider(),
        signal_date="2026-07-10",
        task_view=task_view,
        spotlights=(),
        overview=DashboardDateOverview(
            signal_date="2026-07-10",
            task_count=1,
            actionable_total=1,
            watch_total=1,
            blocked_total=0,
            top_task_label="",
            top_headline="",
            blocker_headline="",
            focus_headline="",
            workflow_summary="",
            archive_summary="",
        ),
    )

    assert "aqsp-observation-table" not in "\n".join(markdown_blocks)
    assert "aqsp-simple-candidate-grid" in "\n".join(markdown_blocks)
    assert "603019 中科曙光" in "\n".join(markdown_blocks)
    assert "600879 航天电子" in "\n".join(markdown_blocks)
    assert '\n            <div class="aqsp-simple-candidate-card' not in "\n".join(
        markdown_blocks
    )


def test_dashboard_global_styles_hide_streamlit_chrome() -> None:
    import inspect
    import aqsp.web.dashboard as dashboard

    source = inspect.getsource(dashboard._inject_dashboard_styles)

    assert "#MainMenu" in source
    assert '[data-testid="stToolbar"]' in source
    assert '[data-testid="stHeader"]' in source
    assert '[data-testid="stActionButton"]' in source
    assert '[data-testid="baseButton-header"]' in source
    assert '[data-testid="stMainMenu"]' in source
    assert '[data-testid="stDecoration"]' in source
    assert '[data-testid="stStatusWidget"]' in source
    assert "Made with Streamlit" not in source
    assert "transition: all" not in source


def test_dashboard_date_timeline_cards_only_expand_selected_day_when_multiple_dates(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardSameDayTaskRow

    rendered_cards: list[dict[str, object]] = []
    button_labels: list[str] = []
    captions: list[str] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: captions.append(text))
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: button_labels.append(label) and False,
    )
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    class _Provider:
        @staticmethod
        def timeline_rows(limit: int = 8):
            return (
                type(
                    "_Timeline",
                    (),
                    {
                        "signal_date": "2026-06-05",
                        "task_labels": ("主链推荐",),
                        "actionable_total": 1,
                        "watch_total": 0,
                        "blocked_total": 0,
                        "headline": "2026-06-05 主链摘要",
                    },
                )(),
                type(
                    "_Timeline",
                    (),
                    {
                        "signal_date": "2026-06-04",
                        "task_labels": ("收盘复盘",),
                        "actionable_total": 0,
                        "watch_total": 1,
                        "blocked_total": 0,
                        "headline": "2026-06-04 复盘摘要",
                    },
                )(),
            )

        @staticmethod
        def date_overview(signal_date: str):
            return type(
                "_Overview",
                (),
                {
                    "signal_date": signal_date,
                    "task_count": 1,
                    "actionable_total": 1 if signal_date == "2026-06-05" else 0,
                    "watch_total": 0 if signal_date == "2026-06-05" else 1,
                    "blocked_total": 0,
                    "top_task_label": "主链推荐",
                    "top_headline": "",
                    "blocker_headline": "",
                    "focus_headline": f"{signal_date} 只展开这一天",
                    "workflow_summary": "",
                    "archive_summary": "",
                },
            )()

        @staticmethod
        def same_day_task_rows(signal_date: str):
            return (
                DashboardSameDayTaskRow(
                    signal_date=signal_date,
                    task_id="main_chain",
                    task_label="主链推荐",
                    phase_order=1,
                    phase_label="盘前主链",
                    phase_summary="先看主链",
                    status_label="有推荐" if signal_date == "2026-06-05" else "观察中",
                    headline=f"{signal_date} 主链摘要",
                    candidate_count=1,
                    actionable_count=1 if signal_date == "2026-06-05" else 0,
                    watch_count=0 if signal_date == "2026-06-05" else 1,
                    blocked_count=0,
                ),
            )

        @staticmethod
        def preferred_task_for_date(signal_date: str) -> str:
            return "main_chain"

        @staticmethod
        def build_task_view(task_id: str, signal_date: str):
            return type(
                "_TaskView",
                (),
                {
                    "report_summary_lines": (),
                    "summary_lines": (f"{signal_date} 的消息摘要",),
                    "headline": f"{signal_date} 的 headline",
                    "detail_cards": (),
                    "source_status": {},
                    "review_lines": (),
                    "recommendation_lines": (),
                    "agenda_lines": (),
                    "watchlist_lines": (),
                    "blocker_lines": (),
                    "next_day_focus_lines": (),
                },
            )()

    monkeypatch.setattr(
        dashboard, "_provider_prioritized_debates", lambda *args, **kwargs: ()
    )

    dashboard._render_date_timeline_cards(_Provider(), "2026-06-04", "main_chain")

    assert button_labels == ["2026-06-05", "2026-06-04", "切到这天"]
    assert rendered_cards[0]["kicker"] == "2026-06-04"
    assert rendered_cards[0]["title"] == "2026-06-04 只展开这一天"
    assert len(rendered_cards) == 2
    assert captions[1] == "点击日期切换，只展开当天详情。"


def test_dashboard_top_navigation_context_prefers_same_day_phase_summary() -> None:
    from aqsp.web.data_provider import DashboardSameDayTaskRow, DashboardTaskSnapshot

    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先确认主推候选与跟踪优先级",
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
    assert lines[0] == "当前位置: 主链推荐 / 有推荐"
    assert lines[1] == "阅读顺序: 先看当天总控，再展开 盘前主链"
    assert lines[2] == "当前焦点: 000338 潍柴动力 | 先看流动性阻塞"


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
        "当前位置: 盘前简报 / 需复核",
        "阅读顺序: 先看当天总控，再展开 盘前简报",
        "当前焦点: 盘前简报提示关注成交额阈值",
    )


def test_dashboard_day_replay_digest_compresses_same_day_flow_for_humans() -> None:
    from aqsp.web.data_provider import (
        DashboardDateOverview,
        DashboardPaperSummary,
        DashboardSameDayTaskRow,
    )

    class _TaskView:
        task_label = "主链推荐"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
        next_day_focus_lines = ("历史回看: 先确认量能延续。",)
        review_lines = ("安排复核: 600519 贵州茅台",)
        report_markdown = "# archived"
        report_summary_lines = ("历史报告摘要: 主线偏谨慎。",)
        runtime_lines = ()

    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=3,
        actionable_total=2,
        watch_total=1,
        blocked_total=0,
        top_task_label="主链推荐",
        top_headline="主链有 2 个待复核",
        blocker_headline="",
        focus_headline="600519 贵州茅台 | 复核量能",
        workflow_summary="当日流程: 盘前主链 -> 早盘观察 -> 尾盘确认",
        archive_summary="本日共覆盖 3 个阶段；主焦点在主链推荐；全链路无明显阻塞。",
    )
    rows = (
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="main_chain",
            task_label="主链推荐",
            phase_order=1,
            phase_label="盘前主链",
            phase_summary="先确认主推候选",
            status_label="有推荐",
            headline="主链有 2 个待复核",
            candidate_count=2,
            actionable_count=2,
            watch_count=0,
            blocked_count=0,
        ),
        DashboardSameDayTaskRow(
            signal_date="2026-06-05",
            task_id="closing_premium",
            task_label="尾盘策略",
            phase_order=3,
            phase_label="尾盘确认",
            phase_summary="确认隔夜价值",
            status_label="观察中",
            headline="尾盘继续观察",
            candidate_count=1,
            actionable_count=0,
            watch_count=1,
            blocked_count=0,
        ),
    )
    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-05",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )

    lines = _day_replay_digest_lines(
        task_view=_TaskView(),
        overview=overview,
        paper_summary=paper_summary,
        same_day_rows=rows,
    )

    assert lines[0] == "📍 当日结论 | 2 待复核 | 1 观察 | 0 阻塞"
    assert lines[1] == "🧩 任务回放 | 盘前主链 → 尾盘确认 | 主链推荐"
    assert lines[2] == "📚 归档回看: 原报告下一交易日重点，历史回看: 先确认量能延续。"
    assert lines[3].startswith("🗂 全日覆盖 | 已归档")
    assert len(lines) == 4


def test_dashboard_day_replay_digest_prioritizes_paper_reality() -> None:
    from aqsp.web.data_provider import DashboardDateOverview, DashboardPaperSummary

    class _TaskView:
        task_label = "主链推荐"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
        next_day_focus_lines = ("历史回看: 稍后看。",)
        review_lines = ()
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()

    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=2,
        actionable_total=0,
        watch_total=1,
        blocked_total=1,
        top_task_label="主链推荐",
        top_headline="当前无主推",
        blocker_headline="000338 潍柴动力 | 流动性不足",
        focus_headline="000338 潍柴动力 | 流动性不足",
        workflow_summary="当日流程: 盘前主链 -> 收盘复盘",
        archive_summary="本日共覆盖 2 个阶段；主焦点在主链推荐；主要卡在主链推荐。",
    )
    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-05",
        open_positions=0,
        pending_entries=1,
        not_executable=1,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=("600519 贵州茅台 | 等待纸面入场确认",),
    )

    assert (
        _day_replay_next_step_line(
            task_view=_TaskView(),
            overview=overview,
            paper_summary=paper_summary,
        )
        == "🧪 复核提示: 纸面验证记录待核对，600519 贵州茅台 | 等待纸面入场确认"
    )

    lines = _day_replay_digest_lines(
        task_view=_TaskView(),
        overview=overview,
        paper_summary=paper_summary,
        same_day_rows=(),
    )

    assert (
        lines[2]
        == "🧪 复核提示: 纸面验证记录待核对，600519 贵州茅台 | 等待纸面入场确认"
    )


def test_dashboard_day_replay_digest_neutralizes_raw_paper_writeback() -> None:
    from aqsp.web.data_provider import DashboardDateOverview, DashboardPaperSummary

    class _TaskView:
        task_label = "纸面验证"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
        next_day_focus_lines = ()
        review_lines = ()
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()

    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=1,
        actionable_total=1,
        watch_total=0,
        blocked_total=0,
        top_task_label="纸面验证",
        top_headline="纸面事件待复核",
        blocker_headline="",
        focus_headline="600519 贵州茅台 | 纸面验证",
        workflow_summary="当日流程: 纸面验证",
        archive_summary="本日共覆盖 1 个阶段。",
    )
    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-05",
        open_positions=0,
        pending_entries=1,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(
            "最近纸面回写: 600519 贵州茅台 | BUY 100 @ 1500",
            "纸面入场待核对 1 笔，等待下一交易日开盘价。",
        ),
    )

    line = _day_replay_next_step_line(
        task_view=_TaskView(),
        overview=overview,
        paper_summary=paper_summary,
    )

    assert (
        line
        == "🧪 复核提示: 纸面验证记录待核对，纸面入场待核对 1 笔，等待下一交易日开盘价。"
    )
    assert "BUY" not in line
    assert "SELL" not in line
    assert "下单" not in line


def test_dashboard_day_replay_digest_neutralizes_archived_focus_words() -> None:
    from aqsp.web.data_provider import DashboardDateOverview, DashboardPaperSummary

    class _TaskView:
        task_label = "归档回看"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
        next_day_focus_lines = ("今日建议: 立即买入/下单，按跟踪优先级新开仓",)
        review_lines = ()
        report_markdown = "# archived"
        report_summary_lines = ("历史报告摘要: 主线偏谨慎。",)
        runtime_lines = ()

    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=1,
        actionable_total=0,
        watch_total=1,
        blocked_total=0,
        top_task_label="归档回看",
        top_headline="历史归档",
        blocker_headline="",
        focus_headline="",
        workflow_summary="当日流程: 收盘复盘",
        archive_summary="本日共覆盖 1 个阶段。",
    )
    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-05",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )

    line = _day_replay_next_step_line(
        task_view=_TaskView(),
        overview=overview,
        paper_summary=paper_summary,
    )

    assert line.startswith("📚 归档回看:")
    for forbidden in (
        "📚 下一步",
        "今日建议",
        "立即买入",
        "下单",
        "跟踪优先级",
        "新开仓",
    ):
        assert forbidden not in line


def test_dashboard_day_replay_digest_neutralizes_archive_summary_words() -> None:
    from aqsp.web.data_provider import DashboardDateOverview, DashboardPaperSummary

    class _TaskView:
        task_label = "归档回看"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
        next_day_focus_lines = ()
        review_lines = ()
        report_markdown = "# archived"
        report_summary_lines = ()
        runtime_lines = ()

    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=1,
        actionable_total=0,
        watch_total=1,
        blocked_total=0,
        top_task_label="归档回看",
        top_headline="历史归档",
        blocker_headline="",
        focus_headline="",
        workflow_summary="当日流程: 收盘复盘",
        archive_summary="今日建议: 立即买入，重点跟踪名单等待下单。",
    )
    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-05",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )

    lines = _day_replay_digest_lines(
        task_view=_TaskView(),
        overview=overview,
        paper_summary=paper_summary,
        same_day_rows=(),
    )

    assert lines[3].startswith("🗂 全日覆盖 | 已归档")
    for forbidden in ("今日建议", "立即买入", "重点跟踪名单", "下单"):
        assert forbidden not in lines[3]


def test_dashboard_day_replay_digest_prioritizes_blockers_before_archive() -> None:
    from aqsp.web.data_provider import DashboardDateOverview, DashboardPaperSummary

    class _TaskView:
        task_label = "主链推荐"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
        next_day_focus_lines = ("历史回看: 先确认量能延续。",)
        review_lines = ("安排复核: 600519 贵州茅台",)
        report_markdown = "# archived"
        report_summary_lines = ("历史报告摘要: 主线偏谨慎。",)
        runtime_lines = ()

    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=2,
        actionable_total=0,
        watch_total=1,
        blocked_total=1,
        top_task_label="主链推荐",
        top_headline="当前无主推",
        blocker_headline="000338 潍柴动力 | 流动性不足",
        focus_headline="000338 潍柴动力 | 流动性不足",
        workflow_summary="当日流程: 盘前主链 -> 收盘复盘",
        archive_summary="本日共覆盖 2 个阶段；主焦点在主链推荐；主要卡在主链推荐。",
    )
    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-05",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )

    assert (
        _day_replay_next_step_line(
            task_view=_TaskView(),
            overview=overview,
            paper_summary=paper_summary,
        )
        == "⚠️ 阻塞提示: 待核对卡点，000338 潍柴动力 | 流动性不足"
    )


def test_dashboard_day_replay_digest_neutralizes_review_action_words() -> None:
    from aqsp.web.data_provider import DashboardDateOverview, DashboardPaperSummary

    class _TaskView:
        task_label = "主链推荐"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
        next_day_focus_lines = ()
        review_lines = ("跟踪优先级: 新开仓/下单/买入 600519",)
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()

    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=1,
        actionable_total=1,
        watch_total=0,
        blocked_total=0,
        top_task_label="主链推荐",
        top_headline="待复核 1 个",
        blocker_headline="",
        focus_headline="600519 贵州茅台 | 待复核",
        workflow_summary="当日流程: 主链推荐",
        archive_summary="本日共覆盖 1 个阶段。",
    )
    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-05",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )

    line = _day_replay_next_step_line(
        task_view=_TaskView(),
        overview=overview,
        paper_summary=paper_summary,
    )

    assert line == "🧭 复核线索: 复核顺位: 纸面新建观察/纸面记录/纸面入场记录 600519"
    assert "跟踪优先级" not in line
    assert "新开仓" not in line
    assert "下单" not in line
    assert "买入" not in line


def test_dashboard_day_replay_digest_falls_back_when_same_day_rows_are_empty() -> None:
    from aqsp.web.data_provider import DashboardDateOverview, DashboardPaperSummary

    class _TaskView:
        task_label = "简报回看"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
        next_day_focus_lines = ()
        review_lines = ()
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()

    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-05",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )
    overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=1,
        actionable_total=0,
        watch_total=0,
        blocked_total=0,
        top_task_label="简报回看",
        top_headline="简报已落盘",
        blocker_headline="",
        focus_headline="",
        workflow_summary="当日流程: 收盘复盘 -> 次日预案",
        archive_summary="",
    )

    lines = _day_replay_digest_lines(
        task_view=_TaskView(),
        overview=overview,
        paper_summary=paper_summary,
        same_day_rows=(),
    )
    assert lines[1] == "🧩 任务回放 | 收盘复盘 -> 次日预案 | 简报回看"

    empty_overview = DashboardDateOverview(
        signal_date="2026-06-05",
        task_count=0,
        actionable_total=0,
        watch_total=0,
        blocked_total=0,
        top_task_label="",
        top_headline="",
        blocker_headline="",
        focus_headline="",
        workflow_summary="",
        archive_summary="",
    )
    fallback_lines = _day_replay_digest_lines(
        task_view=_TaskView(),
        overview=empty_overview,
        paper_summary=paper_summary,
        same_day_rows=(),
    )
    assert fallback_lines[1] == "🧩 任务回放 | 简报回看"


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
        cross_market_summary="英伟达物理AI叙事升温(纸面复核)",
    )

    assert _review_source_label(None) == "纸面记录"
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
    assert title == "委员会补充结论回看"
    assert lines == (
        "当前判断主要由委员会补充结论补齐。",
        "先看委员会结论、修正原因和风险分歧。",
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
        "当前判断主要来自同日一起出现的信息。",
        "跨市传导: 英伟达物理AI叙事升温(纸面复核)",
        "先核对跨任务结论，再回到单任务原始记录。",
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


def test_dashboard_research_path_steps_connect_review_paper_and_archive_safely() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard

    class _TaskView:
        report_markdown = "# archived"
        report_summary_lines = ("今日建议: 立即买入 000338", "今日无重点跟踪对象")
        next_day_focus_lines = ("重点跟踪名单: 000338 等待下单",)
        runtime_lines = ()

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
        review_meta="高优先级 / 午前复核",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    execution_focus = _DummyExecutionFocus(
        execution_status="纸面阻塞待核对",
        holding_status="尚未形成纸面持有",
        readiness_lines=("研究已产出，但被流动性过滤拦住。",),
        execution_lines=("纸面事件: 不可成交待核对",),
    )

    steps = _research_path_steps(
        task_view=_TaskView(),
        selected_symbol="000338",
        review_card=card,
        selected_card=card,
        selected_spotlight=None,
        debate_summary=None,
        execution_focus=execution_focus,
        event_count=1,
        log_count=0,
        open_position_count=0,
        archive_status="已归档",
    )

    assert tuple(step.title for step in steps) == ("研究结论", "纸面记录", "归档结果")
    assert steps[0].tone == "blocked"
    assert steps[0].headline == "000338 潍柴动力 · 降级观察"
    assert "排队层级: 阻塞观察 / 评分 58.0" in steps[0].lines
    assert steps[1].headline == "事件 1 / 日志 0 / 纸面持有 0"
    assert steps[1].lines[0] == "纸面事件: 不可成交待核对"
    assert steps[2].headline == "已归档"
    rendered_text = "\n".join(
        line for step in steps for line in (step.headline, *step.lines)
    )
    for forbidden in (
        "今日建议",
        "立即买入",
        "执行名单",
        "下单",
        "真实持仓",
        "可执行标的",
    ):
        assert forbidden not in rendered_text
    assert "历史回看" in rendered_text


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
        "当前阶段: 研究结论已落盘",
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


def test_dashboard_research_path_steps_surface_spotlight_candidate_summary() -> None:
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    class _TaskView:
        report_summary_lines = ()
        next_day_focus_lines = ()
        runtime_lines = ()
        market_environment = "震荡偏强"

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
        cross_market_summary="英伟达物理AI叙事升温(纸面复核)",
        support_points=("映射链承接仍在延续。",),
    )
    execution_focus = _DummyExecutionFocus(
        execution_status="尚未进入执行",
        holding_status="尚未形成纸面持有",
    )

    steps = _research_path_steps(
        task_view=_TaskView(),
        selected_symbol="300750",
        review_card=_spotlight_as_candidate_card(spotlight),
        selected_card=None,
        selected_spotlight=spotlight,
        debate_summary=None,
        execution_focus=execution_focus,
        event_count=0,
        log_count=0,
        open_position_count=0,
        archive_status="无归档",
    )

    assert steps[0].title == "研究结论"
    assert (
        "候选摘要: 跨市线索 英伟达物理AI叙事升温(纸面复核)；讨论支持: 映射链承接仍在延续。"
        in steps[0].lines
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
        action_summary_lines=(
            "最近纸面回写: 600519 贵州茅台 | BUY 100 @ 1500",
            "纸面入场待核对 1 笔，先看纸面验证链。",
        ),
    )
    assert _home_workspace_hint(_TaskView(), _Overview(), execution_summary) == (
        "先看虚拟盘跟踪",
        "纸面入场待核对 1 笔，先看纸面验证链。",
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


def test_dashboard_home_brief_cards_prioritize_discussion_summary_when_present() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
        DashboardPaperSummary,
    )

    class _TaskView:
        task_id = "main_chain"
        task_label = "主链推荐"
        headline = "今日主链聚焦贵州茅台。"
        detail_cards = (
            DashboardCandidateCard(
                symbol="600519",
                name="贵州茅台",
                display_name="600519 贵州茅台",
                rank_label="第一顺位",
                score=88.0,
                action_label="上调优先级",
                status_label="延续上升",
                decision_note="主链继续保留首选",
                next_step="观察量能是否继续扩张，再决定是否维持主推",
                blocker="",
                review_meta="高优先级 / 开盘前后",
                reasons=("量价齐升",),
                risks=("追高波动",),
                strategies=("volume_breakout",),
                data_source="eastmoney",
            ),
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
                review_meta="中优先级 / 收盘前",
                reasons=(),
                risks=("流动性不足",),
                strategies=(),
                data_source="eastmoney",
            ),
        )

    class _Overview:
        signal_date = "2026-06-05"
        focus_headline = "待复核 1，只看主链首选。"
        top_headline = "主链有推荐。"
        blocker_headline = "流动性阻塞待核对。"

    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-05",
        open_positions=0,
        pending_entries=1,
        not_executable=1,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(
            "BUY 100 @ 1500 后等待下单。",
            "SELL 100 @ 1450 后等待真实持仓回写。",
        ),
    )
    research_summary = ResearchSummary(
        generated_at="",
        total_findings=0,
        pipeline_summaries=(
            ResearchPipelineSummary(
                pipeline="strategy",
                total=22,
                p1=10,
                top_repo="sngyai/Sequoia-X",
            ),
        ),
        absorbed_families=(
            ResearchFamilySummary(
                family_id="chan_theory",
                name="缠论结构识别",
                status="research_absorbed",
                runtime_stage="report_only",
                absorbed_from_count=3,
                runtime_gate_count=2,
            ),
        ),
        source_candidates=(),
        next_actions=(
            ResearchActionItem(
                kind="strategy",
                item_id="chan_theory",
                name="缠论结构识别",
                stage="report_only",
                priority="P1",
                blocker="先做 fixture 验证",
                reference_hint="",
            ),
        ),
        prereq_items=(),
        implemented_family_count=5,
        report_only_family_count=1,
        gated_family_count=0,
    )
    spotlights = (
        DashboardCandidateSpotlight(
            symbol="600519",
            display_name="600519 贵州茅台",
            score=88.0,
            action_label="上调优先级",
            status_label="延续上升",
            blocker="",
            next_step="观察量能是否继续扩张，再决定是否维持主推",
            review_meta="高优先级 / 开盘前后",
            task_labels=("主链推荐", "尾盘策略"),
            reasons=("量价齐升",),
            risks=("追高波动",),
            cross_market_summary="美股风险偏好修复(重点跟踪)",
            support_points=("量能承接仍在延续。",),
            opposition_points=("高位分歧依然偏大。",),
            watch_items=("观察次日承接是否继续。",),
        ),
    )

    cards = _home_brief_cards(
        task_view=_TaskView(),
        overview=_Overview(),
        paper_summary=paper_summary,
        research_summary=research_summary,
        spotlights=spotlights,
        debates=(),
    )

    assert tuple(card.kicker for card in cards) == (
        "01 先看什么",
        "02 纸面记录",
        "03 风险卡点",
        "04 研究进化",
    )
    assert cards[0].title == "600519 贵州茅台"
    assert cards[0].lines == (
        "跨市主线: 美股风险偏好修复(重点跟踪) | 先看 600519 贵州茅台",
        "讨论支持: 量能承接仍在延续。",
        "讨论反对: 高位分歧依然偏大。",
    )
    assert cards[1].title == "纸面事件待核对"
    assert cards[1].tone == "pressure"
    assert cards[2].title == "000338 潍柴动力"
    assert cards[2].tone == "blocked"
    assert cards[3].tone == "research"
    assert any("研究结论不会直接改写评分" in line for line in cards[3].lines)
    rendered_text = "\n".join(
        line for card in cards for line in (card.title, *card.lines)
    )
    assert "真实持仓" not in rendered_text
    assert "立即买入" not in rendered_text
    assert "下单" not in rendered_text
    assert "BUY" not in rendered_text
    assert "SELL" not in rendered_text


def test_dashboard_home_brief_cards_prioritize_cross_market_line_when_only_debate_present() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardDebateAgentView,
        DashboardDebateSummary,
        DashboardPaperSummary,
    )

    class _TaskView:
        detail_cards = ()
        headline = "当前无显著候选"
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()
        next_day_focus_lines = ()

    class _Overview:
        focus_headline = ""
        top_headline = "当前无显著候选"
        blocker_headline = ""

    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="688981",
        display_name="688981 中芯国际",
        debate_id="debate-cross-market",
        rating="A",
        original_score=79.0,
        adjusted_score=79.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.32,
        consensus="分歧可控",
        adjustment_reason="海外叙事仍在传导",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=("先讨论海外主线传导",),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(
            DashboardDebateAgentView(
                role_id="cross_market",
                role_label="跨市传导",
                stance="bullish",
                stance_label="看多",
                confidence=0.81,
                key_argument="海外物理AI叙事升温，先看A股映射链条承接。",
                key_risk="",
                key_opportunity="",
            ),
        ),
        cross_market_chain_summary=(
            "产业映射｜领先窗 隔夜-3日｜先看 机器人整机｜"
            "确认 机器人龙头放量上攻且核心零部件同步走强｜"
            "失效 只有海外叙事但A股机器人板块不共振"
        ),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认映射链承接",
        next_trigger="若龙头放量延续则优先复核",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=("海外叙事仍在扩散。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日承接。",),
    )

    cards = _home_brief_cards(
        task_view=_TaskView(),
        overview=_Overview(),
        paper_summary=paper_summary,
        research_summary=None,
        spotlights=(),
        debates=(debate,),
    )

    assert cards[0].title == "688981 中芯国际"
    assert cards[0].lines == (
        "研究口径: 倾向优先纸面复核；卡点 先确认映射链承接",
        "跨市主线: 海外物理AI叙事升温，先看A股映射链条承接。 | 先看 688981 中芯国际 | 确认 机器人龙头放量上攻且核心零部件同步走强 | 失效 只有海外叙事但A股机器人板块不共振",
        "下一触发: 若龙头放量延续则优先复核",
    )


def test_dashboard_home_brief_cards_keep_cross_market_line_when_spotlight_and_debate_coexist() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateSpotlight,
        DashboardDebateAgentView,
        DashboardDebateSummary,
        DashboardPaperSummary,
    )

    class _TaskView:
        detail_cards = ()
        headline = "当前无显著候选"
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()
        next_day_focus_lines = ()

    class _Overview:
        focus_headline = ""
        top_headline = "当前无显著候选"
        blocker_headline = ""

    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="688981",
        display_name="688981 中芯国际",
        score=79.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="",
        next_step="观察映射链承接。",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=("海外叙事扩散。",),
        risks=("高位波动。",),
        cross_market_summary="海外物理AI叙事升温(重点跟踪)",
        cross_market_chain_summary=(
            "产业映射｜领先窗 隔夜-3日｜先看 机器人整机｜"
            "确认 机器人龙头放量上攻且核心零部件同步走强｜"
            "失效 只有海外叙事但A股机器人板块不共振"
        ),
        support_points=("映射链承接仍在延续。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日承接。",),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="688981",
        display_name="688981 中芯国际",
        debate_id="debate-cross-market-spotlight",
        rating="A",
        original_score=79.0,
        adjusted_score=79.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.32,
        consensus="分歧可控",
        adjustment_reason="海外叙事仍在传导",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=("先讨论海外主线传导",),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(
            DashboardDebateAgentView(
                role_id="cross_market",
                role_label="跨市传导",
                stance="bullish",
                stance_label="看多",
                confidence=0.81,
                key_argument="海外物理AI叙事升温，先看A股映射链条承接。",
                key_risk="",
                key_opportunity="",
            ),
        ),
        cross_market_chain_summary=(
            "产业映射｜领先窗 隔夜-3日｜先看 机器人整机｜"
            "确认 机器人龙头放量上攻且核心零部件同步走强｜"
            "失效 只有海外叙事但A股机器人板块不共振"
        ),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认映射链承接",
        next_trigger="若龙头放量延续则优先复核",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=("海外叙事仍在扩散。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日承接。",),
    )

    cards = _home_brief_cards(
        task_view=_TaskView(),
        overview=_Overview(),
        paper_summary=paper_summary,
        research_summary=None,
        spotlights=(spotlight,),
        debates=(debate,),
    )

    assert cards[0].title == "688981 中芯国际"
    assert cards[0].lines == (
        "研究口径: 倾向优先纸面复核；卡点 先确认映射链承接",
        "跨市主线: 海外物理AI叙事升温，先看A股映射链条承接。 | 先看 688981 中芯国际 | 确认 机器人龙头放量上攻且核心零部件同步走强 | 失效 只有海外叙事但A股机器人板块不共振",
        "下一触发: 若龙头放量延续则优先复核",
    )


def test_dashboard_queue_item_meta_neutralizes_decision_note_action_words() -> None:
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="首选",
        score=88.0,
        action_label="上调优先级",
        status_label="延续上升",
        decision_note="立即买入后等待下单",
        next_step="",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=("执行名单进入后买入",),
        risks=("真实持仓暴露过高",),
        strategies=("volume_breakout",),
        data_source="eastmoney",
    )

    html = _queue_item_meta(card, "等待量能确认")

    for forbidden in ("立即买入", "下单", "执行名单", "买入", "真实持仓"):
        assert forbidden not in html
    assert "纸面记录" in html
    assert "纸面持有" in html


def test_dashboard_home_evidence_entry_lines_keep_first_screen_compact_and_safe() -> (
    None
):
    from aqsp.web.data_provider import DashboardPaperSummary

    class _TaskView:
        task_label = "主链推荐"
        report_markdown = "# archived"
        report_summary_lines = ()
        runtime_lines = ()

    class _Overview:
        actionable_total = 1
        watch_total = 2
        blocked_total = 1
        archive_summary = "今日建议: 立即买入，重点跟踪名单等待下单。"

    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-05",
        open_positions=1,
        pending_entries=1,
        not_executable=1,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )

    lines = _home_evidence_entry_lines(
        task_view=_TaskView(),
        overview=_Overview(),
        paper_summary=paper_summary,
        research_summary=None,
    )

    assert lines == (
        ("🧪 纸面", "纸面: 持有 1 / 待核对 1 / 阻塞 1"),
        ("🧭 候选", "候选 · 1 复核 · 2 观察 · 1 阻塞"),
        (
            "🗂 归档",
            "归档 · 已归档 · 历史回看: 历史记录: 纸面观察，历史复核名单等待历史纸面记录。",
        ),
    )
    rendered_text = "\n".join(line for _, line in lines)
    for forbidden in ("今日建议", "立即买入", "重点跟踪名单", "下单", "真实持仓"):
        assert forbidden not in rendered_text


def test_dashboard_home_action_rail_items_merge_same_day_spotlight_when_task_has_no_card() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    class _TaskView:
        task_id = "main_chain"
        task_label = "主链推荐"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
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

    assert recommend_item.lane_label == "今日先看"
    assert recommend_item.card is not None
    assert recommend_item.card.rank_label == "同日联动"
    assert recommend_item.summary == "600519 贵州茅台"
    assert recommend_item.lines[0] == "当前结论: 上调优先级 / 延续上升"
    assert "下一步: 等待量能确认" in recommend_item.lines
    assert recommend_item.button_label == "去复盘"
    assert recommend_item.target_workspace == "候选复盘"
    assert recommend_item.signal_date == "2026-06-05"
    assert recommend_item.focus_kind == "spotlight"
    assert recommend_item.decision_source == "spotlight"
    assert recommend_item.task_id == ""
    assert watch_item.button_label == "归档回看"
    assert watch_item.target_workspace == "归档回看"
    assert watch_item.summary == "无需硬找方向"
    assert blocked_item.summary == "当前没有明显卡点"


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
    assert recommend_item.lines[0] == "当前结论: 上调优先级 / 延续上升"


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
    assert recommend_item.lines == ("当前没有纸面复核候选，先等下一轮主链信号。",)
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
    assert watch_item.summary == "无需硬找方向"
    assert watch_item.lines == ("当前没有需要单独观察的对象，不用为了凑名单硬找方向。",)
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
    assert blocked_item.lines[1] == "当前卡点: 20日均成交额不足，流动性过滤"
    assert any(
        line == "下一步: 先确认复核条件，卡点解除后再决定是否恢复推进。"
        for line in blocked_item.lines
    )


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
        "研究已产出，但当前被20日均成交额不足，流动性过滤拦住，暂不进入纸面入场验证。"
        == line
        for line in lines
    )
    assert not any("当前阻塞: 20日均成交额不足，流动性过滤" == line for line in lines)


def test_dashboard_home_action_rail_items_insert_debate_lane_when_same_day_debate_exists() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    class _TaskView:
        task_id = "main_chain"
        task_label = "主链推荐"
        selected_date = "2026-06-01"
        latest_date = "2026-06-01"
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
    assert items[2].lane_label == "委员会分歧"
    assert items[2].card is not None
    assert items[2].card.symbol == "600036"
    assert items[2].button_label == "候选复盘"
    assert items[2].target_workspace == "候选复盘"
    assert items[2].tone == "pressure"
    assert items[2].signal_date == "2026-06-01"
    assert items[2].focus_kind == "debate"
    assert items[2].debate_id == "debate-2"
    assert items[2].decision_source == "debate"
    assert items[2].lines[0] == "当前结论: 观点分化，保持原评级"
    assert any("分歧焦点: 多空分歧更大" == line for line in items[2].lines)


def test_dashboard_home_action_rail_items_do_not_duplicate_debate_lane_when_candidate_lane_exists() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    class _TaskView:
        task_id = "main_chain"
        task_label = "主链推荐"
        selected_date = "2026-06-01"
        latest_date = "2026-06-01"
        detail_cards = (
            DashboardCandidateCard(
                symbol="600519",
                name="贵州茅台",
                display_name="600519 贵州茅台",
                rank_label="第一顺位",
                score=82.0,
                action_label="维持原排序",
                status_label="等待确认",
                decision_note="主链仍在候选池",
                next_step="先确认承接质量",
                blocker="",
                review_meta="高优先级 / 开盘前后",
                reasons=(),
                risks=(),
                strategies=(),
                data_source="eastmoney",
            ),
        )
        recommendation_lines = ()
        review_lines = ()
        watchlist_lines = ()
        blocker_lines = ()

    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600519",
        display_name="600519 贵州茅台",
        debate_id="debate-1",
        rating="A",
        original_score=82.0,
        adjusted_score=82.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.42,
        consensus="分歧可控",
        adjustment_reason="海外链条仍在映射",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
    )

    items = _home_action_rail_items(_TaskView(), (), (debate,))

    assert [item.lane_id for item in items] == ["recommend", "watch", "blocked"]
    assert items[0].visible is True
    assert all(item.lane_id != "debate" for item in items)


def test_dashboard_home_debate_item_lines_prioritize_agent_views_when_available() -> (
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
        support_points=("量能承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
        research_verdict="先保留评分",
        primary_risk_gate="量能承接待确认",
        next_trigger="放量站稳再看",
    )

    assert _home_debate_item_lines(debate) == (
        "研究口径: 先保留评分；卡点 量能承接待确认",
        "支持方: 板块轮动 看多 / 置信 91% | 板块轮动认为当前价格位置合理",
        "反对方: 基本面空头 看空 / 置信 70% | 基本面空头认为当前价格位置偏高",
    )


def test_dashboard_home_debate_item_lines_prioritize_cross_market_chain_when_available() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300308",
        display_name="300308 中际旭创",
        debate_id="debate-cross-market",
        rating="A",
        original_score=74.0,
        adjusted_score=74.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.28,
        consensus="分歧可控",
        adjustment_reason="海外链条仍在映射",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认映射链承接",
        next_trigger="若龙头放量延续则优先复核",
        cross_market_summary="海外算力风险偏好修复",
        cross_market_validation_summary="龙头放量上攻且光模块同步走强",
        cross_market_invalidation_summary="美股走强但A股映射链不共振",
        support_points=("海外叙事仍在扩散。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日承接。",),
    )

    assert _home_debate_item_lines(debate) == (
        "研究口径: 倾向优先纸面复核；卡点 先确认映射链承接",
        "跨市主线: 海外算力风险偏好修复 | 确认 龙头放量上攻且光模块同步走强 | 失效 美股走强但A股映射链不共振",
        "待确认 观察次日承接。",
    )


def test_dashboard_home_reading_order_prioritizes_paper_events_before_candidates() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardPaperSummary

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
        headline = "主链候选已产出"
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()
        next_day_focus_lines = ()

    class _Overview:
        focus_headline = "主链候选已产出"
        top_headline = "主链候选已产出"
        archive_summary = ""

    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=0,
        pending_entries=1,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(
            "最近纸面回写: 600519 贵州茅台 | BUY 100 @ 1500",
            "纸面入场待核对 1 笔，等待下一交易日开盘价。",
        ),
    )

    lines = _home_reading_order_lines(
        task_view=_TaskView(),
        overview=_Overview(),
        paper_summary=paper_summary,
        spotlights=(),
        debates=(),
    )

    assert lines[0].startswith("🧪 先看纸面验证")
    assert "下一交易日开盘价" in lines[0]
    assert "BUY" not in lines[0]
    assert "@" not in lines[0]
    assert "🎯 主看候选 | 600519 贵州茅台" in lines[1]
    assert "等待量能确认" in lines[1]
    assert lines[2].startswith("📚 收盘后补归档")


def test_dashboard_home_reading_order_surfaces_blocked_card_as_final_gate() -> None:
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardPaperSummary

    class _TaskView:
        detail_cards = (
            DashboardCandidateCard(
                symbol="000338",
                name="潍柴动力",
                display_name="000338 潍柴动力",
                rank_label="阻塞观察",
                score=58.0,
                action_label="降级观察",
                status_label="观察阻塞",
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
        headline = "当前候选被过滤"
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()
        next_day_focus_lines = ()

    class _Overview:
        focus_headline = "当前候选被过滤"
        top_headline = "当前候选被过滤"
        archive_summary = ""

    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )

    lines = _home_reading_order_lines(
        task_view=_TaskView(),
        overview=_Overview(),
        paper_summary=paper_summary,
        spotlights=(),
        debates=(),
    )

    assert lines[0] == "纸面验证: 暂无新的纸面入场或不可成交事件。"
    assert "🎯 主看候选 | 000338 潍柴动力" in lines[1]
    assert (
        lines[2] == "🔒 最后核对卡点 | 000338 潍柴动力 | 20日均成交额不足，流动性过滤"
    )


def test_dashboard_home_reading_order_uses_archive_when_no_blockers() -> None:
    from aqsp.web.data_provider import DashboardPaperSummary

    class _TaskView:
        detail_cards = ()
        headline = "当前无显著候选"
        report_markdown = "# 已归档"
        report_summary_lines = ("历史报告摘要: 今日没有可推进候选。",)
        runtime_lines = ()
        next_day_focus_lines = ("明日继续观察量能恢复。",)

    class _Overview:
        focus_headline = ""
        top_headline = "当前无显著候选"
        archive_summary = "本日已归档。"

    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )

    lines = _home_reading_order_lines(
        task_view=_TaskView(),
        overview=_Overview(),
        paper_summary=paper_summary,
        spotlights=(),
        debates=(),
    )

    assert lines[1] == "🎯 主看研究结论 | 当前无显著候选"
    assert lines[2] == "📚 收盘后回看归档 | 明日继续观察量能恢复。"


def test_dashboard_home_reading_order_prioritizes_cross_market_chain_for_debate_only_focus() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary, DashboardPaperSummary

    class _TaskView:
        detail_cards = ()
        headline = "当前没有独立候选"
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()
        next_day_focus_lines = ()

    class _Overview:
        focus_headline = "当前没有独立候选"
        top_headline = "当前没有独立候选"
        archive_summary = ""

    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300308",
        display_name="300308 中际旭创",
        debate_id="debate-only",
        rating="A",
        original_score=74.0,
        adjusted_score=74.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.28,
        consensus="分歧可控",
        adjustment_reason="海外链条仍在映射",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认映射链承接",
        next_trigger="若龙头放量延续则优先复核",
        cross_market_summary="海外算力风险偏好修复",
        cross_market_validation_summary="龙头放量上攻且光模块同步走强",
        cross_market_invalidation_summary="美股走强但A股映射链不共振",
        support_points=("海外叙事仍在扩散。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日承接。",),
    )

    lines = _home_reading_order_lines(
        task_view=_TaskView(),
        overview=_Overview(),
        paper_summary=paper_summary,
        spotlights=(),
        debates=(debate,),
    )

    assert lines[0] == "纸面验证: 暂无新的纸面入场或不可成交事件。"
    assert lines[1] == "🎯 主看分歧 | 300308 中际旭创 | 先确认映射链承接"
    assert lines[2].startswith("📚 收盘后补归档")


def test_dashboard_runtime_boundary_card_context_defaults_to_enabled_realtime_history_split() -> (
    None
):
    class _TaskView:
        task_id = "intraday"

    title, lines, tone = _runtime_boundary_card_context(_TaskView())

    assert title == "实时优先 / 研究增强"
    assert tone == "focus"
    assert (
        "实时链路: 盘中任务优先实时数据；历史链路只做回测、验证和阈值冻结。" == lines[0]
    )
    assert lines[1] == "运行开关: 守卫 开 / 回退链 开 / 国内情报 开 / 海外情报 开。"
    assert f"讨论层: 已启用 {len(DEFAULT_RUNTIME_AGENT_ROLE_NAMES)} 个角色" in lines[2]
    assert "结论仅供复核，不改写候选排序。" in lines[2]
    assert "优化层: 只产出 proposal，不直接写回运行参数。" == lines[3]
    assert lines[4] == "推进主线: P0 历史验证边界；P0 实时数据守卫；P1 信息融合。"


def test_dashboard_runtime_boundary_card_context_marks_relaxed_local_experiment(
    monkeypatch, tmp_path
) -> None:
    goal_switch_path = tmp_path / "goal_switches.yaml"
    goal_switch_path.write_text(
        """
version: "test"
mode: short_term_realtime
switches:
  live_short_runtime:
    enabled: true
    purpose: live short
  historical_validation_only:
    enabled: true
    purpose: history only
  enforce_live_vs_history_boundary:
    enabled: false
    purpose: relaxed guard
  domestic_market_intelligence:
    enabled: false
    purpose: disable cn intelligence
  global_market_intelligence:
    enabled: false
    purpose: disable global intelligence
  multi_agent_advisory_layer:
    enabled: true
    purpose: debate
  auto_optimization_proposals:
    enabled: false
    purpose: disable proposals
  auto_optimization_apply_runtime:
    enabled: false
    purpose: no apply
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AQSP_GOAL_SWITCHES", str(goal_switch_path))
    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
    monkeypatch.setenv("AQSP_DEBATE_FOCUS_ROLES", "cross_market,risk_control")
    monkeypatch.setenv("AQSP_DEBATE_DISABLED_ROLES", "northbound")

    class _TaskView:
        task_id = "briefing"

    title, lines, tone = _runtime_boundary_card_context(_TaskView())

    assert title == "本地实验边界"
    assert tone == "pressure"
    assert (
        lines[1]
        == "运行开关: 守卫 关(仅本地实验) / 回退链 开 / 国内情报 关 / 海外情报 关。"
    )
    assert "讨论层: 已启用 6 个角色" in lines[2]
    assert "结论仅供复核，不改写候选排序。" in lines[2]
    assert lines[3] == "轨道裁剪: 聚焦 跨市传导、风控 / 停用 北向资金。"
    assert "优化层: 当前关闭自动优化提案" in lines[4]


def test_dashboard_render_home_reading_order_includes_runtime_boundary_card(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardPaperSummary

    markdown_calls: list[str] = []
    cockpit_calls: list[dict[str, object]] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _TaskView:
        task_id = "intraday"
        detail_cards = ()
        headline = "当前无显著候选"
        report_markdown = ""
        report_summary_lines = ()
        runtime_lines = ()
        next_day_focus_lines = ()

    class _Overview:
        focus_headline = "当前无显著候选"
        top_headline = "当前无显著候选"
        archive_summary = ""

    paper_summary = DashboardPaperSummary(
        signal_date="2026-06-01",
        open_positions=0,
        pending_entries=0,
        not_executable=0,
        closed_trades=0,
        open_position_lines=(),
        event_lines=(),
        action_summary_lines=(),
    )

    monkeypatch.setattr(
        dashboard.st, "columns", lambda spec: [_StubColumn(), _StubColumn()]
    )
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_calls.append(str(body)),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: cockpit_calls.append(kwargs),
    )

    dashboard._render_home_reading_order(
        task_view=_TaskView(),
        overview=_Overview(),
        paper_summary=paper_summary,
        spotlights=(),
        debates=(),
    )

    assert any("先看顺序" in body for body in markdown_calls)
    assert len(cockpit_calls) == 1
    assert cockpit_calls[0]["kicker"] == "当前运行边界"
    assert "实时链路: 盘中任务优先实时数据" in "\n".join(cockpit_calls[0]["lines"])


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
        cross_market_summary="英伟达物理AI叙事升温(纸面复核)",
        cross_market_validation_summary="龙头封单增强",
        cross_market_invalidation_summary="高开低走且量能背离",
        support_points=("映射链承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
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
        cross_market_summary="英伟达物理AI叙事升温(重点跟踪)",
        cross_market_validation_summary="机器人龙头放量上攻",
        cross_market_invalidation_summary="只有海外叙事但A股板块不共振",
        support_points=("量能承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
    )

    lines = _home_spotlight_lines(spotlight)

    assert lines == (
        "当前结论: 维持原排序 / 等待确认",
        "跨市主线: 英伟达物理AI叙事升温(重点跟踪)",
        "当前卡点: 关注承接质量",
        "涉及任务: 尾盘策略、收盘复盘 / 复核: 中优先级 / 收盘前",
    )
    assert len(lines) <= 4


def test_dashboard_home_spotlight_lines_include_cross_market_chain_when_available() -> (
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
        cross_market_summary="英伟达物理AI叙事升温(重点跟踪)",
        cross_market_chain_summary=(
            "产业映射｜领先窗 隔夜-3日｜先看 机器人整机｜"
            "确认 机器人龙头放量上攻且核心零部件同步走强｜"
            "失效 只有海外叙事但A股机器人板块不共振｜同向 2 条｜反向 1 条"
        ),
        cross_market_validation_summary="机器人龙头放量上攻且核心零部件同步走强",
        cross_market_invalidation_summary="只有海外叙事但A股机器人板块不共振",
        support_points=("量能承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
    )

    lines = _home_spotlight_lines(spotlight)

    assert lines == (
        "当前结论: 维持原排序 / 等待确认",
        "跨市主线: 英伟达物理AI叙事升温(重点跟踪)",
        "当前卡点: 关注承接质量",
        "涉及任务: 尾盘策略、收盘复盘 / 复核: 中优先级 / 收盘前",
    )
    assert len(lines) <= 4


def test_dashboard_home_spotlight_lines_show_validation_and_invalidation_from_spotlight_fallback_fields() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="等待量能扩散",
        next_step="",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐",),
        reasons=(),
        risks=(),
        cross_market_summary="海外物理AI叙事升温(纸面复核)",
        cross_market_chain_summary=(
            "英伟达物理AI -> 算力链映射 -> A股弹性标的扩散｜"
            "确认 龙头封单增强｜失效 高开低走且量能背离｜同向 2 条｜反向 1 条"
        ),
        cross_market_validation_summary="龙头封单增强",
        cross_market_invalidation_summary="高开低走且量能背离",
        support_points=("映射链承接仍在延续。",),
        opposition_points=(),
        watch_items=(),
    )

    lines = _home_spotlight_lines(spotlight)

    assert lines == (
        "当前结论: 维持原排序 / 等待确认",
        "跨市主线: 海外物理AI叙事升温(纸面复核)",
        "当前卡点: 等待量能扩散",
        "涉及任务: 主链推荐 / 复核: 高优先级 / 开盘前后",
    )
    assert len(lines) <= 4


def test_dashboard_same_day_spotlight_card_lines_compress_to_conclusion_mainline_and_gate() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="优先级上调",
        status_label="等待确认",
        blocker="先确认算力链量能扩散",
        next_step="若龙头封单增强则优先复核",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
        cross_market_summary="海外物理AI叙事升温(纸面复核)",
        support_points=("映射链承接仍在延续。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日龙头封单质量。",),
    )

    assert _same_day_spotlight_card_lines(spotlight) == (
        "当前结论: 优先级上调 / 等待确认",
        "跨市主线: 海外物理AI叙事升温(纸面复核)",
        "当前卡点: 先确认算力链量能扩散",
        "涉及任务: 主链推荐、尾盘策略 / 复核: 高优先级 / 开盘前后",
    )
    assert _same_day_spotlight_card_tone(spotlight) == "blocked"


def test_dashboard_render_same_day_candidate_spotlights_uses_compact_cockpit_cards(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    subheaders: list[str] = []
    captions: list[str] = []
    rendered_cards: list[dict[str, object]] = []
    action_labels: list[str] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: subheaders.append(text))
    monkeypatch.setattr(dashboard.st, "caption", lambda text: captions.append(text))
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: action_labels.append(label) and False,
    )
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="优先级上调",
        status_label="等待确认",
        blocker="先确认算力链量能扩散",
        next_step="若龙头封单增强则优先复核",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
        cross_market_summary="海外物理AI叙事升温(纸面复核)",
        support_points=("映射链承接仍在延续。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日龙头封单质量。",),
    )

    _render_same_day_candidate_spotlights((spotlight,), signal_date="2026-06-05")

    assert subheaders == ["同日联动焦点"]
    assert captions == ["这里只保留跨任务共振、当前卡点和下一步，不再重复铺开长摘要。"]
    assert action_labels == ["复盘", "虚拟盘", "归档"]
    assert rendered_cards == [
        {
            "kicker": "同日联动 · 79.0分",
            "title": "688256 寒武纪",
            "lines": (
                "当前结论: 优先级上调 / 等待确认",
                "跨市主线: 海外物理AI叙事升温(纸面复核)",
                "当前卡点: 先确认算力链量能扩散",
                "涉及任务: 主链推荐、尾盘策略 / 复核: 高优先级 / 开盘前后",
            ),
            "tone": "blocked",
        }
    ]


def test_dashboard_render_same_day_candidate_spotlights_queue_structured_handoff_on_action(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateSpotlight

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    spotlight = DashboardCandidateSpotlight(
        symbol="688256",
        display_name="688256 寒武纪",
        score=79.0,
        action_label="优先级上调",
        status_label="等待确认",
        blocker="先确认算力链量能扩散",
        next_step="若龙头封单增强则优先复核",
        review_meta="高优先级 / 开盘前后",
        task_labels=("主链推荐", "尾盘策略"),
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
        cross_market_summary="海外物理AI叙事升温(纸面复核)",
        support_points=("映射链承接仍在延续。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日龙头封单质量。",),
    )

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard, "_render_cockpit_card", lambda **kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: label == "复盘",
    )
    reruns: list[bool] = []
    monkeypatch.setattr(dashboard.st, "rerun", lambda: reruns.append(True))

    _render_same_day_candidate_spotlights((spotlight,), signal_date="2026-06-05")

    assert reruns == [True]
    assert dashboard.st.session_state["dashboard_pending_workspace"] == "候选复盘"
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_signal_date"]
        == "2026-06-05"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_focus_kind"]
        == "spotlight"
    )
    assert (
        dashboard.st.session_state["dashboard_pending_handoff_decision_source"]
        == "spotlight"
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


def test_dashboard_debate_vote_snapshot_lines_sort_by_confidence_and_cover_both_sides_when_available() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateAgentView, DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-3",
        rating="A",
        original_score=72.0,
        adjusted_score=75.0,
        adjustment_weight=0.08,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.41,
        consensus="先保留主线，但分歧需继续核对",
        adjustment_reason="跨市与风控分歧仍在",
        bull_count=4,
        bear_count=2,
        neutral_count=1,
        round_count=3,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(
            DashboardDebateAgentView(
                role_id="northbound",
                role_label="北向资金",
                stance="neutral",
                stance_label="中性",
                confidence=0.95,
                key_argument="北向尚未明显回流",
                key_risk="",
                key_opportunity="",
            ),
            DashboardDebateAgentView(
                role_id="cross_market",
                role_label="跨市传导",
                stance="bullish",
                stance_label="看多",
                confidence=0.84,
                key_argument="海外主线仍在扩散",
                key_risk="",
                key_opportunity="",
            ),
            DashboardDebateAgentView(
                role_id="risk_control",
                role_label="风控",
                stance="bearish",
                stance_label="看空",
                confidence=0.76,
                key_argument="高开回撤风险仍高",
                key_risk="",
                key_opportunity="",
            ),
        ),
    )

    lines = _debate_vote_snapshot_lines(debate)

    assert lines[2] == "跨市传导: 看多 / 置信 84%"
    assert lines[3] == "风控: 看空 / 置信 76%"


def test_dashboard_debate_process_lines_preserve_round_order_and_agent_arguments() -> (
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
        summary_lines=(),
        round_summaries=("先讨论量能承接", "再讨论防御属性"),
        risk_warnings=("分歧偏大",),
        opportunity_highlights=("防御属性",),
        agent_views=(
            DashboardDebateAgentView(
                role_id="sector_leader",
                role_label="板块轮动",
                stance="bullish",
                stance_label="看多",
                confidence=0.91,
                key_argument="量能承接尚可",
                key_risk="",
                key_opportunity="",
            ),
        ),
        research_verdict="",
        primary_risk_gate="",
        next_trigger="",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=("量能承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
    )

    lines = _debate_process_lines(debate)

    assert lines[0] == "当前结论: 观点分化，保持原评级"
    assert lines[1] == "过程主线: 第 1 轮 先讨论量能承接 → 第 2 轮 再讨论防御属性"
    assert "关键支持: 板块轮动 看多 / 置信 91% | 量能承接尚可" == lines[2]
    assert "讨论待确认: 观察次日承接是否继续。" == lines[3]
    assert "讨论轮次: 2" not in lines
    assert "讨论视角: 板块轮动" not in lines
    assert "支持观点: 量能承接仍在延续。" not in lines
    assert "反对观点: 高位分歧依然偏大。" not in lines
    assert "板块轮动: 看多 | 量能承接尚可" not in lines


def test_dashboard_debate_lane_status_context_shows_enabled_runtime_and_waiting_roles() -> (
    None
):
    class _TaskView:
        task_id = "intraday"

    title, lines, tone = _debate_lane_status_context(_TaskView(), ())

    assert title == f"{len(DEFAULT_RUNTIME_AGENT_ROLE_NAMES)} 轨道待命 / 0 已出场"
    assert tone == "archive"
    assert lines[0].startswith("默认轨道: 技术多头、基本面空头、风控")
    assert lines[1] == "今日讨论: 当前还没触发同日讨论，角色轨道已待命。"
    assert lines[2] == "实际出场: 当前还没有角色发言记录。"
    assert lines[3].startswith("待补轨道: 技术多头、基本面空头、风控、板块轮动")
    assert f"等 {len(DEFAULT_RUNTIME_AGENT_ROLE_NAMES)} 个角色" in lines[3]


def test_dashboard_debate_lane_status_context_summarizes_active_and_missing_tracks(
    monkeypatch,
) -> None:
    from aqsp.web.data_provider import DashboardDebateAgentView, DashboardDebateSummary

    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")

    class _TaskView:
        task_id = "briefing"

    debates = (
        DashboardDebateSummary(
            signal_date="2026-06-01",
            symbol="300750",
            display_name="300750 宁德时代",
            debate_id="debate-1",
            rating="A",
            original_score=80.0,
            adjusted_score=81.0,
            adjustment_weight=0.1,
            recommended_adjustment="raise",
            recommended_adjustment_label="建议上调评分",
            disagreement_score=0.42,
            consensus="先看承接",
            adjustment_reason="主线延续但分歧偏大",
            bull_count=3,
            bear_count=1,
            neutral_count=1,
            round_count=2,
            regime="强势",
            data_source="multi",
            thresholds_version="v1",
            summary_lines=(),
            round_summaries=("先讨论海外主线传导",),
            risk_warnings=(),
            opportunity_highlights=(),
            agent_views=(
                DashboardDebateAgentView(
                    role_id="bull",
                    role_label="技术多头",
                    stance="bullish",
                    stance_label="看多",
                    confidence=0.8,
                    key_argument="趋势延续",
                    key_risk="",
                    key_opportunity="",
                ),
                DashboardDebateAgentView(
                    role_id="cross_market",
                    role_label="跨市传导",
                    stance="bullish",
                    stance_label="看多",
                    confidence=0.74,
                    key_argument="海外主线仍在扩散。",
                    key_risk="",
                    key_opportunity="",
                ),
            ),
            research_verdict="倾向优先纸面复核",
            primary_risk_gate="先确认量价延续",
            next_trigger="若放量延续则优先复核",
            historical_context_note="",
            role_reliability_lines=(),
            support_points=("海外主线仍在扩散。",),
            opposition_points=(),
            watch_items=("观察次日承接。",),
        ),
    )

    title, lines, tone = _debate_lane_status_context(_TaskView(), debates)

    assert title == "7 轨道待命 / 2 已出场"
    assert tone == "pressure"
    assert lines[1] == "今日讨论: 1 场 / 覆盖 1 个标的 / 已出场 2 个角色"
    assert lines[2] == "实际出场: 技术多头、跨市传导"
    assert lines[3] == "价值分层: 高价值 1 / 背景 0"


def test_dashboard_debate_lane_status_context_surfaces_track_tuning_before_activity(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AQSP_ENABLE_DEBATE", "true")
    monkeypatch.setenv("AQSP_DEBATE_FOCUS_ROLES", "cross_market,risk_control")
    monkeypatch.setenv("AQSP_DEBATE_DISABLED_ROLES", "northbound")

    class _TaskView:
        task_id = "briefing"

    title, lines, tone = _debate_lane_status_context(_TaskView(), ())

    assert title == "6 轨道待命 / 0 已出场"
    assert tone == "archive"
    assert lines[0].startswith("默认轨道: 跨市传导、风控、技术多头")
    assert lines[1] == "轨道裁剪: 聚焦 跨市传导、风控 / 停用 北向资金。"
    assert lines[2] == "今日讨论: 当前还没触发同日讨论，角色轨道已待命。"
    assert lines[3] == "实际出场: 当前还没有角色发言记录。"


def test_dashboard_salient_home_debates_demote_low_signal_noise_when_stronger_chain_exists() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    strong = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-strong",
        rating="A",
        original_score=80.0,
        adjusted_score=81.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.31,
        consensus="先看承接",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认量价延续",
        next_trigger="若放量延续则优先复核",
        historical_context_note="历史校验: 强证据样本 4/5 命中",
        role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
        cross_market_summary="海外电池链情绪修复",
        cross_market_validation_summary="龙头放量上攻且产业链同步走强",
        cross_market_invalidation_summary="只有高开冲动但午后承接消失",
        support_points=("主线仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )
    noisy = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-noisy",
        rating="B",
        original_score=68.0,
        adjusted_score=69.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.39,
        consensus="观点分化",
        adjustment_reason="多空分歧更大",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=1,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="",
        primary_risk_gate="",
        next_trigger="",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=(),
        opposition_points=(),
        watch_items=(),
    )

    assert _debate_signal_value_tier(strong) == "high"
    assert _debate_signal_value_tier(noisy) == "medium"
    assert [item.symbol for item in _salient_home_debates((noisy, strong))] == [
        "300750",
        "600036",
    ]


def test_dashboard_provider_prioritized_debates_falls_back_to_salient_filter_when_only_raw_debates() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    class _RawOnlyProvider:
        @staticmethod
        def debate_summaries(signal_date: str):
            assert signal_date == "2026-06-01"
            return (low, strong)

    strong = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-strong",
        rating="A",
        original_score=80.0,
        adjusted_score=81.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.31,
        consensus="先看承接",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认量价延续",
        next_trigger="若放量延续则优先复核",
        historical_context_note="历史校验: 强证据样本 4/5 命中",
        role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
        cross_market_summary="海外电池链情绪修复",
        cross_market_validation_summary="龙头放量上攻且产业链同步走强",
        cross_market_invalidation_summary="只有高开冲动但午后承接消失",
        support_points=("主线仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )
    low = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-low",
        rating="B",
        original_score=68.0,
        adjusted_score=69.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.22,
        consensus="信息不足",
        adjustment_reason="缺少结构化证据",
        bull_count=1,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="",
        primary_risk_gate="",
        next_trigger="",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=(),
        opposition_points=(),
        watch_items=(),
    )

    prioritized = _provider_prioritized_debates(_RawOnlyProvider(), "2026-06-01")

    assert [item.symbol for item in prioritized] == ["300750"]


def test_dashboard_render_execution_focus_uses_prioritized_debates_for_symbol_order(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardDebateSummary

    class _StopRender(Exception):
        pass

    class _Provider:
        @staticmethod
        def same_day_candidate_spotlights(signal_date: str):
            assert signal_date == "2026-06-01"
            return ()

        @staticmethod
        def prioritized_debate_summaries(
            signal_date: str,
            *,
            salient_only: bool = False,
        ):
            assert signal_date == "2026-06-01"
            assert salient_only is True
            return (strong,)

        @staticmethod
        def debate_summaries(signal_date: str):
            assert signal_date == "2026-06-01"
            return (low, strong)

        @staticmethod
        def same_day_task_rows(signal_date: str):
            assert signal_date == "2026-06-01"
            return ()

        @staticmethod
        def open_positions_frame(signal_date: str):
            assert signal_date == "2026-06-01"
            return pd.DataFrame()

        @staticmethod
        def paper_events_frame(*, limit: int, signal_date: str):
            assert limit == 50
            assert signal_date == "2026-06-01"
            return pd.DataFrame()

        @staticmethod
        def recent_execution_frame(*, limit: int, signal_date: str):
            assert limit == 50
            assert signal_date == "2026-06-01"
            return pd.DataFrame()

    class _TaskView:
        selected_date = "2026-06-01"
        latest_date = "2026-06-01"
        task_id = "main_chain"
        detail_cards = ()

    strong = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-strong",
        rating="A",
        original_score=80.0,
        adjusted_score=81.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.31,
        consensus="先看承接",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认量价延续",
        next_trigger="若放量延续则优先复核",
        historical_context_note="历史校验: 强证据样本 4/5 命中",
        role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
        cross_market_summary="海外电池链情绪修复",
        cross_market_validation_summary="龙头放量上攻且产业链同步走强",
        cross_market_invalidation_summary="只有高开冲动但午后承接消失",
        support_points=("主线仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )
    low = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600036",
        display_name="600036 招商银行",
        debate_id="debate-low",
        rating="B",
        original_score=68.0,
        adjusted_score=69.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.22,
        consensus="信息不足",
        adjustment_reason="缺少结构化证据",
        bull_count=1,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="",
        primary_risk_gate="",
        next_trigger="",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=(),
        opposition_points=(),
        watch_items=(),
    )
    captured_symbol_order: list[str] = []

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(
        dashboard,
        "_render_workspace_symbol_selector",
        lambda **kwargs: (
            captured_symbol_order.extend(kwargs["symbol_order"])
            or (_ for _ in ()).throw(_StopRender())
        ),
    )

    try:
        _render_execution_focus(provider=_Provider(), task_view=_TaskView())
    except _StopRender:
        pass

    assert captured_symbol_order == ["300750"]


def test_dashboard_home_debate_results_hide_low_value_noise_when_better_chain_exists(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardDebateSummary

    captured_cards: list[dict[str, object]] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard, "_stretch_button", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: captured_cards.append(kwargs),
    )

    strong = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-strong",
        rating="A",
        original_score=80.0,
        adjusted_score=81.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.31,
        consensus="先看承接",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认量价延续",
        next_trigger="若放量延续则优先复核",
        historical_context_note="历史校验: 强证据样本 4/5 命中",
        role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
        cross_market_summary="海外电池链情绪修复",
        cross_market_validation_summary="龙头放量上攻且产业链同步走强",
        cross_market_invalidation_summary="只有高开冲动但午后承接消失",
        support_points=("主线仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )
    low = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="601398",
        display_name="601398 工商银行",
        debate_id="debate-low",
        rating="B",
        original_score=60.0,
        adjusted_score=60.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.12,
        consensus="",
        adjustment_reason="",
        bull_count=1,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="",
        primary_risk_gate="",
        next_trigger="",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=(),
        opposition_points=(),
        watch_items=(),
    )

    dashboard._render_home_debate_results((strong, low))

    assert captured_cards[0]["kicker"] == "先看顺序"
    assert captured_cards[0]["title"] == "300750 宁德时代"
    assert captured_cards[0]["lines"] == (
        "1. 300750 宁德时代: 倾向优先纸面复核 | 跨市主线: 海外电池链情绪修复 | 确认 龙头放量上攻且产业链同步走强",
    )
    assert captured_cards[1]["kicker"] == "300750 宁德时代"
    assert captured_cards[1]["title"] == "建议上调评分"
    assert captured_cards[1]["lines"] == (
        "委员会结论: 倾向优先纸面复核；卡点 先确认量价延续",
        "当前卡点: 先确认量价延续",
        "下一触发: 若放量延续则优先复核",
    )
    assert len(captured_cards) == 2
    assert len(captured_cards[1]["lines"]) <= 3
    assert not any("讨论支持:" in line for line in captured_cards[1]["lines"])
    assert not any("讨论反对:" in line for line in captured_cards[1]["lines"])


def test_dashboard_home_debate_result_card_lines_fallback_to_votes_when_shell_already_carries_action() -> (
    None
):
    from aqsp.web.dashboard import _home_debate_result_card_lines
    from aqsp.web.data_provider import DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="601398",
        display_name="601398 工商银行",
        debate_id="debate-low",
        rating="B",
        original_score=60.0,
        adjusted_score=60.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.12,
        consensus="",
        adjustment_reason="",
        bull_count=1,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="",
        primary_risk_gate="",
        next_trigger="",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=(),
        opposition_points=(),
        watch_items=(),
    )

    assert _home_debate_result_card_lines(debate) == ("投票: 看多 1 / 看空 1 / 中性 1",)


def test_dashboard_render_home_debate_process_renders_lane_status_before_empty_state(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    captured_cards: list[dict[str, object]] = []
    info_calls: list[str] = []

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard.st, "info", lambda text: info_calls.append(str(text)))
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: captured_cards.append(kwargs),
    )

    class _TaskView:
        task_id = "intraday"

    dashboard._render_home_debate_process(_TaskView(), ())

    assert len(captured_cards) == 1
    assert captured_cards[0]["kicker"] == "当前轨道"
    assert info_calls == ["当天没有多 Agent 讨论过程。"]


def test_dashboard_render_home_debate_process_uses_focus_title_and_compact_lines(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardDebateAgentView, DashboardDebateSummary

    captured_cards: list[dict[str, object]] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard.st, "info", lambda text: None)
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard, "_stretch_button", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: captured_cards.append(kwargs),
    )

    class _TaskView:
        task_id = "briefing"

    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-strong",
        rating="A",
        original_score=80.0,
        adjusted_score=81.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.31,
        consensus="先看承接",
        adjustment_reason="主线延续但分歧偏大",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=("先讨论海外主线传导", "再讨论量价承接"),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(
            DashboardDebateAgentView(
                role_id="cross_market",
                role_label="跨市传导",
                stance="bullish",
                stance_label="看多",
                confidence=0.82,
                key_argument="海外主线仍在扩散。",
                key_risk="",
                key_opportunity="",
            ),
        ),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认量价延续",
        next_trigger="若放量延续则优先复核",
        historical_context_note="",
        role_selection_summary="因海外传导、政策催化，本轮先看 跨市传导、板块轮动、政策分析。",
        role_selection_plan="跨市传导看海外催化到A股映射；板块轮动看板块共振和龙头扩散；政策分析看政策催化和兑现节奏。",
        role_reliability_lines=(),
        support_points=("海外主线仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )

    dashboard._render_home_debate_process(_TaskView(), (debate,))

    assert captured_cards[0]["kicker"] == "当前轨道"
    assert captured_cards[1]["kicker"] == "300750 宁德时代 · 2 轮讨论"
    assert captured_cards[1]["title"] == "主线延续但分歧偏大"
    assert captured_cards[1]["lines"] == (
        "过程主线: 第 1 轮 先讨论海外主线传导 → 第 2 轮 再讨论量价承接",
        "关键支持: 跨市传导 看多 / 置信 82% | 海外主线仍在扩散。",
        "角色分工: 跨市传导看海外催化到A股映射；板块轮动看板块共振和龙头扩散；政策分析看政策催化和兑现节奏。",
    )


def test_dashboard_debate_result_lines_include_reliability_and_result_fields() -> None:
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
        summary_lines=("角色可信度: 技术多头 近21天 7/10 (70%)",),
        round_summaries=(),
        risk_warnings=("分歧偏大",),
        opportunity_highlights=("防御属性",),
        agent_views=(
            DashboardDebateAgentView(
                role_id="cross_market",
                role_label="跨市传导",
                stance="bullish",
                stance_label="看多",
                confidence=0.74,
                key_argument="美股风险偏好回升，A股映射链条先看承接。",
                key_risk="",
                key_opportunity="",
            ),
        ),
        research_verdict="先保留评分",
        primary_risk_gate="量能承接待确认",
        next_trigger="放量站稳再看",
        historical_context_note="强证据样本 4/5 命中，冲突样本 1/3。",
        role_selection_summary="因多空分歧，本轮先看 板块轮动、基本面空头。",
        role_selection_plan="板块轮动看权重承接；基本面空头看高位分歧是否继续放大。",
        role_reliability_lines=("技术多头 近21天 7/10 (70%)",),
        support_points=("量能承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
    )

    lines = _debate_result_lines(debate)

    assert lines[0] == "结论: 建议维持评分"
    assert "研究口径: 先保留评分；卡点 量能承接待确认" in lines
    assert "核心卡点: 量能承接待确认" not in lines
    assert "下一触发: 放量站稳再看" in lines
    assert "讨论视角: 跨市传导" in lines
    assert "跨市传导: 美股风险偏好回升，A股映射链条先看承接。" in lines
    assert "讨论支持: 量能承接仍在延续。" in lines
    assert "讨论反对: 高位分歧依然偏大。" in lines
    assert "讨论待确认: 观察次日承接是否继续。" in lines
    assert "历史校验: 强证据样本 4/5 命中，冲突样本 1/3。" in lines
    assert "角色可信度: 技术多头 近21天 7/10 (70%)" in lines


def test_dashboard_debate_result_lines_fallback_to_structured_cross_market_chain_when_agent_view_missing() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300750",
        display_name="300750 宁德时代",
        debate_id="debate-3",
        rating="A",
        original_score=80.0,
        adjusted_score=81.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.29,
        consensus="先看承接",
        adjustment_reason="海外主线传导仍在继续",
        bull_count=2,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        cross_market_summary="海外物理AI叙事升温(优先复核)｜同向 2 条｜反向 1 条",
        cross_market_chain_summary=(
            "产业映射｜领先窗 隔夜-3日｜先看 机器人整机｜"
            "确认 机器人龙头放量上攻且核心零部件同步走强｜"
            "失效 只有海外叙事但A股机器人板块不共振｜同向 2 条｜反向 1 条"
        ),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认量价延续",
        next_trigger="若放量延续则优先复核",
        historical_context_note="强证据样本 4/5 命中",
        role_reliability_lines=(),
        support_points=("海外叙事仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )

    lines = _debate_result_lines(debate)

    assert "跨市传导: 海外物理AI叙事升温(优先复核)｜同向 2 条｜反向 1 条" in lines
    assert (
        "传导链: 产业映射｜领先窗 隔夜-3日｜先看 机器人整机｜确认 机器人龙头放量上攻且核心零部件同步走强｜失效 只有海外叙事但A股机器人板块不共振｜同向 2 条｜反向 1 条；触发 若放量延续则优先复核"
        in lines
    )


def test_dashboard_home_debate_results_prioritize_high_disagreement_first(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardDebateSummary

    captured_titles: list[str] = []
    captured_kickers: list[str] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(dashboard.st, "caption", lambda text: None)
    monkeypatch.setattr(dashboard, "_stretch_button", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: (
            captured_kickers.append(kwargs["kicker"])
            or captured_titles.append(kwargs["title"])
        ),
    )

    low = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600519",
        display_name="600519 贵州茅台",
        debate_id="debate-1",
        rating="A",
        original_score=80.0,
        adjusted_score=82.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.21,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
        historical_context_note="强证据样本 4/5 命中",
        role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
        support_points=("海外主线仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )
    high = DashboardDebateSummary(
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
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="",
        primary_risk_gate="",
        next_trigger="",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=(),
        opposition_points=(),
        watch_items=(),
    )

    dashboard._render_home_debate_results((low, high))

    assert captured_kickers == ["先看顺序", "600519 贵州茅台"]
    assert captured_titles == ["600519 贵州茅台", "建议上调评分"]


def test_dashboard_ordered_home_debates_prioritize_structured_verdict_over_raw_disagreement() -> (
    None
):
    from aqsp.web.data_provider import DashboardDebateSummary

    structured = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="600519",
        display_name="600519 贵州茅台",
        debate_id="debate-1",
        rating="A",
        original_score=80.0,
        adjusted_score=82.0,
        adjustment_weight=0.1,
        recommended_adjustment="raise",
        recommended_adjustment_label="建议上调评分",
        disagreement_score=0.21,
        consensus="分歧可控",
        adjustment_reason="主线延续",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=1,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认承接",
        next_trigger="若放量延续则优先复核",
        historical_context_note="强证据样本 4/5 命中",
        role_reliability_lines=("跨市场 近21天 7/10 (70%)",),
        cross_market_summary="海外消费链情绪修复",
        cross_market_validation_summary="白酒龙头放量上攻且消费权重同步走强",
        cross_market_invalidation_summary="外盘修复但消费权重全天无承接",
        support_points=("海外主线仍在扩散。",),
        opposition_points=(),
        watch_items=("观察次日承接。",),
    )
    noisy = DashboardDebateSummary(
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
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="",
        primary_risk_gate="",
        next_trigger="",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=(),
        opposition_points=(),
        watch_items=(),
    )

    ordered = _ordered_home_debates((noisy, structured))
    lines = _debate_priority_digest_lines(ordered)

    assert ordered[0].display_name == "600519 贵州茅台"
    assert (
        lines[0]
        == "1. 600519 贵州茅台: 倾向优先纸面复核 | 跨市主线: 海外消费链情绪修复 | 确认 白酒龙头放量上攻且消费权重同步走强"
    )


def test_dashboard_debate_brief_cards_surface_human_summary_with_score_boundary() -> (
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
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认银行板块承接",
        next_trigger="若放量站回 20 日线再复核",
        support_points=("防御属性与北向回流形成支撑。",),
        opposition_points=("若只是单日脉冲，次日承接可能不足。",),
        watch_items=("观察北向强弱是否在次日延续。",),
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

    cards = _debate_brief_cards(debate)

    assert tuple(card.kicker for card in cards) == (
        "辩论结论",
        "票型结构",
        "接下来做什么",
    )
    assert cards[0].title == "建议维持评分 / 分歧 0.48"
    assert cards[0].tone == "pressure"
    assert "边界: 这是解释层，不替代选股评分。" in cards[0].lines
    assert cards[1].title == "看多 3 / 看空 2 / 中性 3"
    assert "板块轮动: 看多 / 置信 91%" in cards[1].lines
    assert cards[2].title == "核对触发与失效"
    assert cards[2].tone == "pressure"
    assert cards[2].lines[:4] == (
        "下一触发: 若放量站回 20 日线再复核",
        "讨论待确认: 观察北向强弱是否在次日延续。",
        "讨论反对: 若只是单日脉冲，次日承接可能不足。",
        "核心卡点: 先确认银行板块承接",
    )
    assert "讨论支持: 防御属性与北向回流形成支撑。" in cards[2].lines
    assert "先核对风险: 分歧偏大" in cards[2].lines
    assert cards[2].lines.index("讨论反对: 若只是单日脉冲，次日承接可能不足。") < cards[
        2
    ].lines.index("先核对风险: 分歧偏大")
    rendered_text = "\n".join(
        line for card in cards for line in (card.title, *card.lines)
    )
    assert "替代选股评分" in rendered_text
    assert "立即买入" not in rendered_text
    assert "下单" not in rendered_text


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
        == "证据构成: 2 轮讨论 / 2 个观点 / 数据源 multi / 阈值 v1"
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
        status_label="多视角讨论后，3个看多，2个看空，3个中性，观点分化，保持原评级",
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
        consensus="多视角讨论后，3个看多，2个看空，3个中性，观点分化，保持原评级",
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
    assert "多视角讨论后" not in title


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


def test_dashboard_candidate_research_lines_compact_cross_market_logic_when_debate_present() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    card = DashboardCandidateCard(
        symbol="688981",
        name="中芯国际",
        display_name="688981 中芯国际",
        rank_label="主链首位",
        score=79.0,
        action_label="维持原排序",
        status_label="等待确认",
        decision_note="主链继续保留首位",
        next_step="",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="688981",
        display_name="688981 中芯国际",
        debate_id="debate-cross-market-card",
        rating="A",
        original_score=79.0,
        adjusted_score=79.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.32,
        consensus="分歧可控",
        adjustment_reason="海外叙事仍在传导",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        cross_market_chain_summary=(
            "产业映射｜领先窗 隔夜-3日｜先看 机器人整机｜"
            "确认 机器人龙头放量上攻且核心零部件同步走强｜"
            "失效 只有海外叙事但A股机器人板块不共振"
        ),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认映射链承接",
        next_trigger="若龙头放量延续则优先复核",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=("海外叙事仍在扩散。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日承接。",),
    )

    lines = _candidate_research_lines(
        selected_card=card,
        debate_summary=debate,
        compact_mode=False,
    )

    assert lines == (
        "排队层级: 主链首位 / 评分 79.0",
        "研究口径: 倾向优先纸面复核；卡点 先确认映射链承接",
        "跨市主线: 产业映射｜领先窗 隔夜-3日｜先看 机器人整机 | 先看 688981 中芯国际 | 确认 机器人龙头放量上攻且核心零部件同步走强 | 失效 只有海外叙事但A股机器人板块不共振",
        "下一触发: 若龙头放量延续则优先复核",
    )


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
        "当前限制: 20日均成交额不足，流动性过滤",
        "再看动作: 等待量能恢复后再评估",
        "再看时间: 中优先级 / 收盘前",
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


def test_dashboard_candidate_next_step_lines_neutralize_blocker_action_words() -> None:
    from aqsp.web.data_provider import DashboardCandidateCard

    card = DashboardCandidateCard(
        symbol="000338",
        name="潍柴动力",
        display_name="000338 潍柴动力",
        rank_label="阻塞观察",
        score=58.0,
        action_label="降级观察",
        status_label="降级观察",
        decision_note="立即买入后等待下单",
        next_step="新开仓失败后等待下单",
        blocker="买入条件不足，下单阻塞",
        review_meta="中优先级 / 收盘前",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )

    rendered = "\n".join(_candidate_next_step_lines(card))

    for forbidden in ("立即买入", "下单", "新开仓", "买入条件"):
        assert forbidden not in rendered
    assert "当前限制:" in rendered
    assert "纸面记录阻塞" in rendered


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
        item for item in rendered_cards if item["kicker"] == "研究结论"
    )
    action_card = next(
        item for item in rendered_cards if item["kicker"] == "接下来怎么看"
    )

    assert research_card["title"] == "阻塞待核对"
    assert not any(str(line).startswith("下一步:") for line in research_card["lines"])
    assert action_card["title"] == "先核对卡点"
    assert action_card["lines"] == (
        "当前限制: 20日均成交额不足，流动性过滤",
        "再看动作: 等待量能恢复后再评估",
        "再看时间: 中优先级 / 收盘前",
    )
    assert "当前仍处研究阻塞阶段，尚未进入执行动作" not in action_card["lines"]


def test_dashboard_candidate_review_snapshot_uses_compact_discussion_card_for_debate_only_symbol(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardDebateAgentView,
        DashboardDebateSummary,
    )

    rendered_cards: list[dict[str, object]] = []
    debate_brief_calls: list[object] = []
    markdown_calls: list[str] = []

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
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_calls.append(str(body)),
    )
    monkeypatch.setattr(dashboard.st, "container", lambda: _StubContainer())
    monkeypatch.setattr(dashboard.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_debate_brief",
        lambda debate_summary: debate_brief_calls.append(debate_summary),
    )

    card = DashboardCandidateCard(
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
        adjustment_reason="多空分歧更大",
        bull_count=3,
        bear_count=2,
        neutral_count=3,
        round_count=2,
        regime="震荡偏强",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=("建议维持评分: 7.2 -> 7.5",),
        round_summaries=("先讨论银行权重承接。",),
        risk_warnings=("需关注大盘系统性风险",),
        opportunity_highlights=("行业景气度提升",),
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
        ),
        research_verdict="先保留评分",
        primary_risk_gate="量能承接待确认",
        next_trigger="放量站稳再看",
        historical_context_note="",
        role_reliability_lines=(),
        support_points=(),
        opposition_points=(),
        watch_items=(),
    )

    dashboard._render_candidate_review_snapshot(
        card,
        spotlight=None,
        debate_summary=debate,
        journey_steps=(),
        paper_frame=pd.DataFrame(),
        execution_frame=pd.DataFrame(),
    )

    summary_card = next(
        item for item in rendered_cards if item["kicker"] == "多 Agent 摘要"
    )
    action_card = next(
        item for item in rendered_cards if item["kicker"] == "下一步怎么核"
    )

    assert debate_brief_calls == []
    assert summary_card["title"] == "建议维持评分 / 分歧 0.48"
    assert summary_card["lines"] == (
        "委员会结论: 先保留评分；卡点 量能承接待确认",
        "当前采用口径: 委员会补充结论；当前没有独立候选卡，委员会结论只作解释，不改写评分。",
        "下一触发: 放量站稳再看",
    )
    assert action_card["kicker"] == "下一步怎么核"
    assert action_card["title"] == "先等独立依据"
    assert action_card["lines"] == (
        "验证动作: 等待下一次任务或纸面验证记录补充独立依据。",
        "复核节奏: 辩论主结论 / 待复核",
        "当前限制: 需关注大盘系统性风险",
        "当前状态: 尚未进入纸面动作，先等独立验证。",
    )
    rendered_markdown = "\n".join(markdown_calls)
    assert "- 当前定位: 委员会补充结论" in rendered_markdown
    assert "- 使用边界: 辩论调整分，非主选股评分" in rendered_markdown
    assert "- 下一步: 等待独立候选路径或纸面记录补足依据。" in rendered_markdown


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
    assert lines[4] == "调整原因: 多空分歧更大"
    assert lines[5] == "证据构成: 2 轮讨论 / 2 个观点 / 数据源 multi / 阈值 v1"


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
        cross_market_summary="英伟达物理AI叙事升温(纸面复核)",
        cross_market_validation_summary="龙头封单增强",
        cross_market_invalidation_summary="高开低走且量能背离",
        support_points=("映射链承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
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
    structured_single_task_spotlight = DashboardCandidateSpotlight(
        symbol="300308",
        display_name="300308 中际旭创",
        score=74.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="等待龙头继续承接",
        next_step="",
        review_meta="",
        task_labels=("主链推荐",),
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
        cross_market_summary="美股算力主线继续走强(纸面复核)",
        support_points=("映射链承接仍在延续。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日龙头封单质量。",),
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
    assert lines[0] == "涉及任务: 尾盘策略、收盘复盘"
    assert lines[1] == "跨市传导: 英伟达物理AI叙事升温(纸面复核)"
    assert any("确认信号: 龙头封单增强" == line for line in lines)
    assert any("失效信号: 高开低走且量能背离" == line for line in lines)
    assert any("讨论支持: 映射链承接仍在延续。" == line for line in lines)
    assert any("讨论反对: 高位分歧依然偏大。" == line for line in lines)
    assert tone == "archive"

    title, lines, tone = _candidate_linkage_context(
        spotlight=None,
        debate_summary=debate,
        task_summary="仅当前任务",
    )
    assert title == "风险与机会"
    assert lines[0] == "主要机会: 防御属性"
    assert lines[1] == "主要风险: 分歧偏大"
    assert lines[2] == "涉及任务: 仅当前任务"
    assert tone == "archive"

    title, lines, tone = _candidate_linkage_context(
        spotlight=single_task_spotlight,
        debate_summary=None,
        task_summary="主链推荐",
    )
    assert title == "单任务证据"
    assert lines[0] == "涉及任务: 主链推荐"
    assert any("同日摘要: MA20 斜率向上" == line for line in lines)
    assert any("主要风险: 20日均成交额不足，流动性过滤" == line for line in lines)
    assert tone == "archive"

    title, lines, tone = _candidate_linkage_context(
        spotlight=structured_single_task_spotlight,
        debate_summary=None,
        task_summary="主链推荐",
    )
    assert title == "单任务证据"
    assert lines[0] == "涉及任务: 主链推荐"
    assert lines[1] == "跨市传导: 美股算力主线继续走强(纸面复核)"
    assert any("讨论支持: 映射链承接仍在延续。" == line for line in lines)
    assert any("讨论反对: 高位分歧仍需压缩。" == line for line in lines)
    assert any("讨论待确认: 观察次日龙头封单质量。" == line for line in lines)
    assert not any("同日摘要:" in line for line in lines)
    assert not any("主要风险:" in line for line in lines)
    assert tone == "archive"

    title, lines, tone = _candidate_linkage_context(
        spotlight=None,
        debate_summary=None,
        task_summary="仅当前任务",
    )
    assert title == "单任务证据"
    assert lines[1] == "当前只在本任务中出现，没有额外同日参考信息。"
    assert tone == "archive"


def test_dashboard_candidate_discussion_snapshot_context_prioritizes_result_process_and_roles() -> (
    None
):
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardDebateAgentView,
        DashboardDebateSummary,
    )

    card = DashboardCandidateCard(
        symbol="600036",
        name="招商银行",
        display_name="600036 招商银行",
        rank_label="同日联动",
        score=68.0,
        action_label="建议维持评分",
        status_label="等待确认",
        decision_note="研究候选卡仍保留原排序",
        next_step="等待板块承接确认",
        blocker="量能承接待确认",
        review_meta="高优先级 / 午后复核",
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
        round_summaries=("先讨论银行权重承接。",),
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
        research_verdict="先保留评分",
        primary_risk_gate="量能承接待确认",
        next_trigger="放量站稳再看",
        historical_context_note="强证据样本 4/5 命中，冲突样本 1/3。",
        role_selection_summary="因多空分歧，本轮先看 板块轮动、基本面空头。",
        role_selection_plan="板块轮动看权重承接；基本面空头看高位分歧是否继续放大。",
        role_reliability_lines=("技术多头 近21天 7/10 (70%)",),
        support_points=("量能承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
    )

    title, lines, tone = _candidate_discussion_snapshot_context(card, debate)

    assert title == "建议维持评分 / 分歧 0.48"
    assert lines == (
        "委员会结论: 先保留评分；卡点 量能承接待确认",
        "当前采用口径: 研究候选卡；主链卡点优先，委员会结论只补充分歧与下一触发。",
        "下一触发: 放量站稳再看",
        "讨论待确认: 观察次日承接是否继续。",
    )
    assert not any(
        line.startswith(("支持方:", "反对方:", "选角理由:", "角色分工:"))
        for line in lines
    )
    assert tone == "pressure"


def test_dashboard_candidate_discussion_snapshot_context_prioritizes_cross_market_chain_before_role_meta() -> (
    None
):
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    card = DashboardCandidateCard(
        symbol="300308",
        name="中际旭创",
        display_name="300308 中际旭创",
        rank_label="主链首位",
        score=74.0,
        action_label="维持原排序",
        status_label="等待确认",
        decision_note="主链继续保留首位",
        next_step="",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="multi",
    )
    debate = DashboardDebateSummary(
        signal_date="2026-06-01",
        symbol="300308",
        display_name="300308 中际旭创",
        debate_id="debate-cross-market-snapshot",
        rating="A",
        original_score=74.0,
        adjusted_score=74.0,
        adjustment_weight=0.0,
        recommended_adjustment="keep",
        recommended_adjustment_label="建议维持评分",
        disagreement_score=0.28,
        consensus="分歧可控",
        adjustment_reason="海外链条仍在映射",
        bull_count=3,
        bear_count=1,
        neutral_count=1,
        round_count=2,
        regime="强势",
        data_source="multi",
        thresholds_version="v1",
        summary_lines=(),
        round_summaries=(),
        risk_warnings=(),
        opportunity_highlights=(),
        agent_views=(),
        research_verdict="倾向优先纸面复核",
        primary_risk_gate="先确认映射链承接",
        next_trigger="若龙头放量延续则优先复核",
        cross_market_summary="海外算力风险偏好修复",
        cross_market_validation_summary="龙头放量上攻且光模块同步走强",
        cross_market_invalidation_summary="美股走强但A股映射链不共振",
        role_selection_summary="因多空分歧，本轮先看 跨市传导、板块轮动。",
        role_selection_plan="跨市传导看海外催化；板块轮动看A股映射扩散。",
        role_reliability_lines=("跨市传导 近21天 8/10 (80%)",),
        support_points=("海外叙事仍在扩散。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日承接。",),
    )

    title, lines, tone = _candidate_discussion_snapshot_context(card, debate)

    assert title == "建议维持评分 / 分歧 0.28"
    assert lines == (
        "委员会结论: 倾向优先纸面复核；卡点 先确认映射链承接",
        "当前采用口径: 研究候选卡；委员会结论只作补充，不替代评分。",
        "跨市主线: 海外算力风险偏好修复 | 确认 龙头放量上攻且光模块同步走强 | 失效 美股走强但A股映射链不共振",
        "下一触发: 若龙头放量延续则优先复核",
        "讨论待确认: 观察次日承接。",
    )
    assert not any(
        line.startswith(("主导视角:", "选角理由:", "角色分工:", "角色可信度:"))
        for line in lines
    )
    assert tone == "focus"


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


def test_dashboard_candidate_evidence_drawers_keep_journey_and_evidence_collapsed(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateJourneyStep,
    )

    expanders: list[tuple[str, bool]] = []
    rendered: list[str] = []

    class _StubExpander:
        def __init__(self, label: str, expanded: bool) -> None:
            self.label = label
            self.expanded = expanded

        def __enter__(self):
            expanders.append((self.label, self.expanded))
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    card = DashboardCandidateCard(
        symbol="600519",
        name="贵州茅台",
        display_name="600519 贵州茅台",
        rank_label="纸面重点",
        score=82.0,
        action_label="重点跟踪",
        status_label="延续上升",
        decision_note="趋势延续",
        next_step="等待量价确认",
        blocker="",
        review_meta="高优先级 / 开盘前后",
        reasons=(),
        risks=(),
        strategies=(),
        data_source="eastmoney",
    )
    journey_steps = (
        DashboardCandidateJourneyStep(
            task_id="main_chain",
            task_label="主链推荐",
            phase_label="盘前主链",
            score=82.0,
            action_label="重点跟踪",
            status_label="延续上升",
            blocker="",
            next_step="等待量价确认",
            review_meta="高优先级 / 开盘前后",
            reasons=(),
            risks=(),
        ),
        DashboardCandidateJourneyStep(
            task_id="closing_review",
            task_label="收盘复盘",
            phase_label="收盘复盘",
            score=80.0,
            action_label="维持重点跟踪",
            status_label="等待验证",
            blocker="",
            next_step="复核次日承接",
            review_meta="收盘后",
            reasons=(),
            risks=(),
        ),
    )

    monkeypatch.setattr(
        dashboard.st,
        "expander",
        lambda label, expanded=False: _StubExpander(label, expanded),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_candidate_journey",
        lambda *args, **kwargs: rendered.append("journey"),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_candidate_research_stream",
        lambda **kwargs: rendered.append("evidence"),
    )

    _render_candidate_evidence_drawers(
        review_card=card,
        spotlight=None,
        debate_summary=None,
        journey_steps=journey_steps,
        signal_frame=pd.DataFrame(),
        task_frame=pd.DataFrame(),
        paper_frame=pd.DataFrame(),
        execution_frame=pd.DataFrame(),
        evidence_title="同日研究证据",
    )

    assert expanders == [("当日怎么走到这里", False), ("原始记录", False)]
    assert rendered == ["journey", "evidence"]


def test_dashboard_candidate_evidence_drawers_add_debate_section_before_raw_records(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    expanders: list[tuple[str, bool]] = []
    rendered: list[str] = []

    class _StubExpander:
        def __init__(self, label: str, expanded: bool) -> None:
            self.label = label
            self.expanded = expanded

        def __enter__(self):
            expanders.append((self.label, self.expanded))
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    card = DashboardCandidateCard(
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
        adjustment_reason="多空分歧更大",
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
        next_trigger="放量站稳再复核",
        support_points=("行业景气度提升",),
        opposition_points=("需关注大盘系统性风险",),
        watch_items=("等待下一次任务确认",),
    )

    monkeypatch.setattr(
        dashboard.st,
        "expander",
        lambda label, expanded=False: _StubExpander(label, expanded),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_line_block",
        lambda title, lines, empty_text: rendered.append(f"{title}:{lines[0]}"),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_candidate_research_stream",
        lambda **kwargs: rendered.append("evidence"),
    )

    _render_candidate_evidence_drawers(
        review_card=card,
        spotlight=None,
        debate_summary=debate,
        journey_steps=(),
        signal_frame=pd.DataFrame(),
        task_frame=pd.DataFrame(),
        paper_frame=pd.DataFrame(),
        execution_frame=pd.DataFrame(),
        evidence_title="同日研究证据",
    )

    assert expanders == [("多 Agent 摘要与证据", False), ("原始记录", False)]
    assert rendered[0] == "委员会摘要:摘要标题: 建议维持评分 / 分歧 0.48"
    assert rendered[1].startswith("过程细节:")
    assert rendered[2] == "evidence"


def test_dashboard_candidate_research_stream_prefers_structured_spotlight_summary(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import (
        DashboardCandidateCard,
        DashboardCandidateSpotlight,
    )

    markdown_blocks: list[str] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    card = DashboardCandidateCard(
        symbol="300308",
        name="中际旭创",
        display_name="300308 中际旭创",
        rank_label="研究重点",
        score=74.0,
        action_label="维持原排序",
        status_label="等待确认",
        decision_note="",
        next_step="等待龙头继续承接",
        blocker="",
        review_meta="高优先级 / 收盘前",
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
        strategies=(),
        data_source="eastmoney",
    )
    spotlight = DashboardCandidateSpotlight(
        symbol="300308",
        display_name="300308 中际旭创",
        score=74.0,
        action_label="维持原排序",
        status_label="等待确认",
        blocker="等待龙头继续承接",
        next_step="",
        review_meta="",
        task_labels=("主链推荐", "收盘复盘"),
        reasons=("量价仍在延续",),
        risks=("高位波动扩大",),
        cross_market_summary="美股算力主线继续走强(纸面复核)",
        support_points=("映射链承接仍在延续。",),
        opposition_points=("高位分歧仍需压缩。",),
        watch_items=("观察次日龙头封单质量。",),
    )

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_blocks.append(str(body)),
    )
    monkeypatch.setattr(dashboard, "_render_cockpit_card", lambda **kwargs: None)

    dashboard._render_candidate_research_stream(
        review_card=card,
        spotlight=spotlight,
        debate_summary=None,
        signal_frame=pd.DataFrame(),
        task_frame=pd.DataFrame(),
        paper_frame=pd.DataFrame(),
        execution_frame=pd.DataFrame(),
        evidence_title="同日研究证据",
    )

    rendered = "\n".join(markdown_blocks)
    assert "#### 同日全局视角" in rendered
    assert "- 涉及任务: 主链推荐、收盘复盘" in rendered
    assert "- 跨市传导: 美股算力主线继续走强(纸面复核)" in rendered
    assert "- 讨论支持: 映射链承接仍在延续。" in rendered
    assert "- 讨论反对: 高位分歧仍需压缩。" in rendered
    assert "- 讨论待确认: 观察次日龙头封单质量。" in rendered
    assert "- 汇总理由:" not in rendered
    assert "- 汇总风险:" not in rendered


def test_dashboard_candidate_debate_evidence_lines_put_summary_before_raw_detail() -> (
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
        round_summaries=("先讨论银行权重承接。",),
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
        ),
        research_verdict="先保留评分",
        primary_risk_gate="量能承接待确认",
        next_trigger="放量站稳再看",
        historical_context_note="强证据样本 4/5 命中，冲突样本 1/3。",
        role_reliability_lines=("技术多头 近21天 7/10 (70%)",),
        support_points=("量能承接仍在延续。",),
        opposition_points=("高位分歧依然偏大。",),
        watch_items=("观察次日承接是否继续。",),
    )

    lines = _candidate_debate_evidence_lines(debate)

    assert lines[:5] == (
        "摘要标题: 建议维持评分 / 分歧 0.48",
        "委员会结论: 先保留评分；卡点 量能承接待确认",
        "当前采用口径: 委员会补充结论；当前没有独立候选卡，委员会结论只作解释，不改写评分。",
        "下一触发: 放量站稳再看",
        "讨论待确认: 观察次日承接是否继续。",
    )
    assert "讨论待确认: 观察次日承接是否继续。" in lines
    assert not any(line.startswith("讨论过程:") for line in lines)

    detail_lines = _candidate_debate_detail_lines(debate)
    assert (
        "讨论过程: 过程主线 第 1 轮 先讨论银行权重承接。 | 关键支持: 板块轮动 看多 / 置信 91% | 板块轮动认为当前价格位置合理 | 讨论待确认: 观察次日承接是否继续。"
        in detail_lines
    )
    assert "轮次摘要: 第1轮 先讨论银行权重承接。" in detail_lines
    assert "角色可信度: 技术多头 近21天 7/10 (70%)" in detail_lines
    assert "讨论支持: 量能承接仍在延续。" in detail_lines
    assert "讨论反对: 高位分歧依然偏大。" in detail_lines
    assert "讨论待确认: 观察次日承接是否继续。" in detail_lines
    assert "证据构成: 2 轮讨论 / 1 个观点 / 数据源 multi / 阈值 v1" in detail_lines


def test_dashboard_candidate_research_stream_routes_debate_only_detail_to_separate_drawer(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.data_provider import DashboardCandidateCard, DashboardDebateSummary

    rendered_cards: list[dict[str, object]] = []

    class _StubColumn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    card = DashboardCandidateCard(
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
        adjustment_reason="多空分歧更大",
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

    monkeypatch.setattr(dashboard.st, "subheader", lambda text: None)
    monkeypatch.setattr(
        dashboard.st, "columns", lambda count: [_StubColumn() for _ in range(count)]
    )
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered_cards.append(kwargs),
    )

    dashboard._render_candidate_research_stream(
        review_card=card,
        spotlight=None,
        debate_summary=debate,
        signal_frame=pd.DataFrame(),
        task_frame=pd.DataFrame(),
        paper_frame=pd.DataFrame(),
        execution_frame=pd.DataFrame(),
        evidence_title="同日研究证据",
    )

    assert rendered_cards[0]["kicker"] == "研究证据状态"
    assert rendered_cards[0]["title"] == "当前没有独立任务信号表"
    assert rendered_cards[0]["lines"] == (
        "当前标的主要依赖同日多 Agent 讨论补齐；原始讨论已单独放在上方抽屉。",
        "如果后续补到任务信号或纸面记录，再回到这里交叉验证。",
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
        == "该标的当前只有讨论结论可参考，先等下一次行情刷新给出独立候选证据。"
    )
    assert (
        _candidate_empty_journey_message(
            review_card=spotlight_card,
            spotlight=spotlight,
            debate_summary=None,
        )
        == "该标的当前只有同日观察线索，先等下一次刷新确认是否进入复核。"
    )
    assert (
        _candidate_empty_journey_message(
            review_card=None,
            spotlight=None,
            debate_summary=None,
        )
        == "该标的在当前回看日只有单任务记录，暂无跨阶段来龙去脉。"
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
    assert status_lines[0] == "纸面持有 1 / 入场待核对 1 / 不可成交 0 / 纸面关闭 2"
    assert status_lines[1] == "优先处理纸面入场与纸面退出。"
    assert holding_lines == ("600519 纸面持有中", "000858 纸面持有中")
    assert event_lines == ("600519 纸面入场待核对", "000858 纸面关闭")
    assert tone == "pressure"


def test_dashboard_review_source_label_uses_plain_paper_record_wording() -> None:
    assert _review_source_label(None) == "纸面记录"


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
    assert holding_lines == ("当前没有纸面持有假设。说明暂时没有需要跟踪的纸面仓位。",)
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
        top_headline="无重点跟踪对象",
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
        cross_market_summary="美股风险偏好修复(纸面复核)",
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
    assert "当前来源: 研究候选卡" in context_lines
    assert (
        "跨市主线: 美股风险偏好修复(纸面复核) | 先看 600519 贵州茅台" in context_lines
    )
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
    assert (
        execution_lines[2] == "当前已经进入纸面验证联动，可结合纸面记录核对研究结论。"
    )
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
        consensus="多视角讨论后，3个看多，2个看空，3个中性，观点分化，保持原评级",
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
        next_trigger="放量站稳再复核",
        support_points=("行业景气度提升",),
        opposition_points=("需关注大盘系统性风险",),
        watch_items=("等待下一次任务确认",),
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
    assert context_lines[0] == "委员会结论: 建议维持评分 / 观点分化，保持原评级"
    assert context_lines[1] == "监控焦点: 需关注大盘系统性风险"
    assert context_lines[2] == "下一触发: 放量站稳再复核"
    assert "讨论支持: 行业景气度提升" in context_lines
    assert "讨论反对: 需关注大盘系统性风险" in context_lines
    assert "讨论待确认: 等待下一次任务确认" in context_lines
    assert context_lines[-1] == "验证动作: 等待下一次任务或纸面验证记录补充独立依据。"
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


def test_dashboard_prioritized_research_lines_include_discussion_guidance_after_core_steps() -> (
    None
):
    lines = _prioritized_research_lines(
        (
            "研究动作: 优先级上调 / 延续上升",
            "评分 88.0",
            "研究下一步: 等待量能确认",
            "跨市逻辑: 海外物理AI叙事升温(优先复核) | 映射 产业映射｜领先窗 隔夜-3日",
            "确认信号: 龙头封单增强",
            "失效信号: 高开低走且量能背离",
            "支持观点: 量能承接仍在延续。",
            "反对观点: 高位分歧依然偏大。",
            "待确认: 观察次日承接是否继续。",
            "角色可信度: 技术多头: 近21天 7/10 (70%)",
        )
    )

    assert lines == (
        "研究动作: 优先级上调 / 延续上升",
        "研究下一步: 等待量能确认",
        "跨市逻辑: 海外物理AI叙事升温(优先复核) | 映射 产业映射｜领先窗 隔夜-3日",
        "确认信号: 龙头封单增强",
        "失效信号: 高开低走且量能背离",
        "支持观点: 量能承接仍在延续。",
        "反对观点: 高位分歧依然偏大。",
        "待确认: 观察次日承接是否继续。",
        "角色可信度: 技术多头: 近21天 7/10 (70%)",
    )


def test_dashboard_execution_context_lines_prioritize_cross_market_logic_from_execution_focus_when_no_same_day_context() -> (
    None
):
    execution_focus = _DummyExecutionFocus(
        research_lines=(
            "研究动作: 维持原排序",
            "评分 72.0",
            "研究下一步: 等待承接确认",
            "跨市逻辑: 美股风险偏好修复(重点跟踪) | 映射 风险偏好映射｜领先窗 次日竞价-1日",
            "确认信号: 权重股放量共振",
            "失效信号: 美股走强但A股权重不承接",
            "支持观点: 权重链条开始跟随。",
        ),
    )

    lines = _execution_research_context_lines(
        selected_card=None,
        selected_spotlight=None,
        debate_summary=None,
        execution_focus=execution_focus,
    )

    assert lines == (
        "研究动作: 维持原排序",
        "研究下一步: 等待承接确认",
        "跨市逻辑: 美股风险偏好修复(重点跟踪) | 映射 风险偏好映射｜领先窗 次日竞价-1日",
        "确认信号: 权重股放量共振",
        "失效信号: 美股走强但A股权重不承接",
        "支持观点: 权重链条开始跟随。",
    )


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

    assert "多 Agent 结论摘要" in html
    assert "多头略占优，维持观察" in html
    assert "观点修正维持" in html
    assert "25%" in html
    assert 'class="score-value "' in html
    assert 'class="score-value {"' not in html
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


def test_dashboard_report_archive_center_sanitizes_historical_action_words(
    monkeypatch,
) -> None:
    from aqsp.web.data_provider import DashboardSameDayTaskRow

    class _TaskView:
        task_id = "main_chain"
        task_label = "主链推荐"
        selected_date = "2026-06-05"
        latest_date = "2026-06-05"
        headline = "历史回看: 先复核流动性。"
        report_markdown = "# archived"
        report_summary_lines = ("今日建议: 立即买入 600519，等待下单",)
        next_day_focus_lines = ("重点跟踪名单: 600519 执行开仓",)
        runtime_lines = ()

    class _Provider:
        def build_task_view(self, task_id: str, signal_date: str):
            assert task_id == "main_chain"
            assert signal_date == "2026-06-05"
            return _TaskView()

    row = DashboardSameDayTaskRow(
        signal_date="2026-06-05",
        task_id="main_chain",
        task_label="主链推荐",
        phase_order=1,
        phase_label="盘前主链",
        phase_summary="",
        status_label="已归档",
        headline="历史回看",
        candidate_count=1,
        actionable_count=0,
        watch_count=1,
        blocked_count=0,
    )
    rendered: list[str] = []

    monkeypatch.setattr(
        "aqsp.web.dashboard.st.markdown",
        lambda text, unsafe_allow_html=False: rendered.append(str(text)),
    )
    monkeypatch.setattr(
        "aqsp.web.dashboard.st.subheader",
        lambda text: rendered.append(str(text)),
    )

    _render_report_archive_center(
        provider=_Provider(),
        review_date="2026-06-05",
        same_day_rows=(row,),
        current_task_id="main_chain",
    )

    text = "\n".join(rendered)
    assert "历史摘要" in text
    assert "历史下一日重点" in text
    for forbidden in ("今日建议", "立即买入", "重点跟踪名单", "下单", "执行开仓"):
        assert forbidden not in text


def test_dashboard_main_reads_only_home_snapshot_when_valid(
    monkeypatch, tmp_path
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.home_snapshot import (
        HOME_SNAPSHOT_SCHEMA_VERSION,
        HomeDashboardSnapshot,
        HomeSnapshotCandidate,
        HomeSnapshotColdstart,
        HomeSnapshotDebate,
        HomeSnapshotSource,
        write_home_dashboard_snapshot,
    )

    snapshot = HomeDashboardSnapshot(
        schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
        generated_at="2026-07-11T09:30:00+08:00",
        selected_date="2026-07-11",
        available_dates=("2026-07-11", "2026-07-10"),
        candidates=(
            HomeSnapshotCandidate(
                symbol="600000",
                display_name="600000 浦发银行",
                score=72.0,
                research_status="纸面复核",
                next_step="观察量能",
                context="金融权重承接",
            ),
        ),
        debate=HomeSnapshotDebate(
            symbol="600000",
            display_name="600000 浦发银行",
            conclusion="等待量能确认",
            primary_risk_gate="高开回落",
            next_trigger="站稳早盘高点",
            active_roles=("bull", "risk_control"),
        ),
        summaries=("市场风险偏好稳定",),
        source=HomeSnapshotSource(
            effective="sina",
            latest_trade_date="2026-07-11",
            lag_days=0,
            status="fresh",
        ),
        coldstart=HomeSnapshotColdstart(status="积累中", detail="样本 12/30"),
    )
    path = tmp_path / "home.json"
    write_home_dashboard_snapshot(path, snapshot)
    rendered: list[HomeDashboardSnapshot] = []

    class _Now:
        def strftime(self, fmt: str) -> str:
            return "2026-07-11 09:30:00 +0800"

    monkeypatch.setenv("AQSP_HOME_SNAPSHOT_PATH", str(path))
    monkeypatch.setattr(dashboard, "now_shanghai", lambda: _Now())
    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard, "_inject_dashboard_styles", lambda: None)
    monkeypatch.setattr(dashboard, "_render_simple_app_header", lambda **_: None)
    workspace_calls: list[bool] = []
    monkeypatch.setattr(
        dashboard,
        "_render_workspace_navigation",
        lambda: workspace_calls.append(True) or "决策首页",
    )
    monkeypatch.setattr(
        dashboard,
        "get_provider",
        lambda: (_ for _ in ()).throw(
            AssertionError("valid home snapshot must not construct a data provider")
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_snapshot_home_board",
        lambda value: rendered.append(value),
    )

    dashboard.main()

    assert rendered == [snapshot]
    assert workspace_calls == [True]


def test_dashboard_compacts_long_snapshot_copy() -> None:
    import aqsp.web.dashboard as dashboard

    text = "这是一个很长的生成式研究结论，" * 10

    compacted = dashboard._compact_snapshot_text(text)

    assert len(compacted) <= 72
    assert compacted.endswith("…")


def test_dashboard_home_snapshot_path_uses_default_and_environment_override(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard

    monkeypatch.delenv("AQSP_HOME_SNAPSHOT_PATH", raising=False)
    assert (
        dashboard._home_snapshot_path() == "data/runtime/home_dashboard_snapshot.json"
    )

    monkeypatch.setenv("AQSP_HOME_SNAPSHOT_PATH", "/tmp/aqsp-home.json")
    assert dashboard._home_snapshot_path() == "/tmp/aqsp-home.json"


def test_dashboard_snapshot_home_board_renders_bounded_card_layout(monkeypatch) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.home_snapshot import (
        HOME_SNAPSHOT_SCHEMA_VERSION,
        HomeDashboardSnapshot,
        HomeSnapshotCandidate,
        HomeSnapshotColdstart,
        HomeSnapshotDebate,
        HomeSnapshotMessage,
        HomeSnapshotSource,
    )

    class _Column:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    snapshot = HomeDashboardSnapshot(
        schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
        generated_at="2026-07-11T09:30:00+08:00",
        selected_date="2026-07-11",
        available_dates=("2026-07-11",),
        candidates=tuple(
            HomeSnapshotCandidate(
                symbol=f"60000{index}",
                display_name=f"候选 {index}",
                score=70.0 + index,
                research_status="纸面复核",
                next_step="观察承接",
                context="实时信号",
            )
            for index in range(3)
        ),
        debate=HomeSnapshotDebate(
            symbol="600000",
            display_name="候选 0",
            conclusion="等待确认",
            primary_risk_gate="量能不足",
            next_trigger="放量站稳",
            active_roles=("bull",),
            process_summary="实时讨论过程: 第 2 轮完成",
        ),
        summaries=("摘要一", "摘要二", "摘要三"),
        source=HomeSnapshotSource(
            effective="sina",
            latest_trade_date="2026-07-11",
            lag_days=0,
            status="fresh",
        ),
        coldstart=HomeSnapshotColdstart(status="积累中", detail="样本 12/30"),
        message_status="ok",
        messages=(
            HomeSnapshotMessage(
                title="当前消息标题",
                summary="当前消息摘要",
                impact="利好",
                category="行业",
                source="当前消息源",
                published_at="2026-07-11T09:00:00+08:00",
            ),
        ),
    )
    markdown_blocks: list[str] = []
    cockpit_cards: list[dict[str, object]] = []
    column_specs: list[object] = []

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(
        dashboard.st,
        "columns",
        lambda spec, **kwargs: (
            column_specs.append(spec) or tuple(_Column() for _ in range(2))
        ),
    )
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_blocks.append(str(body)),
    )
    monkeypatch.setattr(dashboard.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        dashboard, "_render_cockpit_card", lambda **kwargs: cockpit_cards.append(kwargs)
    )

    dashboard._render_snapshot_home_board(snapshot)

    rendered = "\n".join(markdown_blocks)
    assert rendered.count("aqsp-simple-candidate-card") == 3
    assert column_specs == [(0.28, 0.72), 2]
    assert "\n            <article" not in rendered
    assert "消息汇总" in rendered
    assert "Agent 讨论结果" in rendered
    assert 'aqsp-simple-nav-title">阶段' not in rendered
    assert 'aqsp-simple-nav-title">工作台' not in rendered
    assert "当日摘要" not in rendered
    assert len(cockpit_cards) == 3
    assert cockpit_cards[0]["title"] == "摘要一"
    assert cockpit_cards[1]["title"] == "当天消息汇总"
    assert "当前消息标题" in " ".join(cockpit_cards[1]["lines"])
    assert cockpit_cards[2]["title"] == "1 个候选完成多 Agent 讨论"
    assert "候选 0" in "\n".join(cockpit_cards[2]["lines"])
    assert "实时讨论过程: 第 2 轮完成" in "\n".join(cockpit_cards[2]["lines"])


def test_dashboard_snapshot_candidate_grid_hides_observation_and_blocked_cards() -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.home_snapshot import HomeSnapshotCandidate

    candidates = (
        HomeSnapshotCandidate(
            symbol="600001",
            display_name="600001 推荐",
            score=80.0,
            research_status="纸面复核",
            next_step="确认量能",
            context="实时信号",
            deterministic_reasons=("量价突破",),
        ),
        HomeSnapshotCandidate(
            symbol="600002",
            display_name="600002 观察",
            score=79.0,
            research_status="观察",
            next_step="等待确认",
            context="仅作观察",
        ),
        HomeSnapshotCandidate(
            symbol="600003",
            display_name="600003 阻塞",
            score=78.0,
            research_status="阻塞观察",
            next_step="解除卡点",
            context="流动性阻塞",
        ),
    )

    class _Snapshot:
        pass

    snapshot = _Snapshot()
    snapshot.candidates = candidates

    html = dashboard._snapshot_candidate_grid(snapshot)

    assert "600001 推荐" in html
    assert "600002 观察" not in html
    assert "600003 阻塞" not in html


def test_dashboard_snapshot_market_context_lines_surface_transmission_before_news() -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.home_snapshot import (
        HomeDashboardSnapshot,
        HomeSnapshotCandidate,
        HomeSnapshotColdstart,
        HomeSnapshotCrossMarket,
        HomeSnapshotMarketContext,
        HomeSnapshotSource,
    )

    snapshot = HomeDashboardSnapshot(
        schema_version="v1",
        generated_at="2026-07-13T09:35:00+08:00",
        selected_date="2026-07-13",
        available_dates=("2026-07-13",),
        candidates=(
            HomeSnapshotCandidate(
                symbol="600879",
                display_name="商业航天",
                score=80.0,
                research_status="观察",
                next_step="确认板块扩散",
                context="实时行情",
            ),
        ),
        debate=None,
        summaries=(),
        source=HomeSnapshotSource(
            effective="sina",
            latest_trade_date="2026-07-13",
            lag_days=0,
            status="实时",
        ),
        coldstart=HomeSnapshotColdstart(status="完成", detail=""),
        stale_after="2026-07-14T09:35:00+08:00",
        market_context=HomeSnapshotMarketContext(
            status="可用",
            overview="海外题材先行，A股等待共振",
            summary_lines=(),
            cross_market=(
                HomeSnapshotCrossMarket(
                    rule_id="commercial_space",
                    theme="海外商业航天催化",
                    strength="强",
                    action="纸面复核",
                    source_title="SpaceX IPO",
                    source_region="international",
                    source_published_at="2026-07-13T08:00:00+08:00",
                    affected_sectors=("商业航天",),
                    transmission_path=("海外风险偏好 -> A股题材",),
                    validation_signals=("龙头承接",),
                    invalidation_signals=("高开低走",),
                    summary="先看A股板块是否扩散",
                ),
            ),
        ),
    )

    lines = dashboard._snapshot_market_context_lines(snapshot)

    assert lines[0] == "跨市综述: 海外题材先行，A股等待共振"
    assert "海外商业航天催化" in lines[1]
    assert "来源: international" in lines[1]


def test_dashboard_snapshot_observation_grid_shows_non_recommendations_separately() -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.home_snapshot import HomeSnapshotCandidate

    class _Snapshot:
        candidates = (
            HomeSnapshotCandidate(
                symbol="600002",
                display_name="600002 观察",
                score=79.0,
                research_status="观察",
                next_step="等待确认",
                context="仅作观察",
            ),
            HomeSnapshotCandidate(
                symbol="600003",
                display_name="600003 阻塞",
                score=78.0,
                research_status="阻塞观察",
                next_step="解除卡点",
                context="流动性阻塞",
            ),
        )

    html = dashboard._snapshot_observation_grid(_Snapshot())

    assert "600002 观察" in html
    assert "600003 阻塞" in html
    assert "下一步: 等待确认" in html


def test_dashboard_snapshot_date_buttons_update_selected_date(monkeypatch) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.home_snapshot import (
        HOME_SNAPSHOT_SCHEMA_VERSION,
        HomeDashboardSnapshot,
        HomeSnapshotColdstart,
        HomeSnapshotSource,
    )

    snapshot = HomeDashboardSnapshot(
        schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
        generated_at="2026-07-11T09:30:00+08:00",
        selected_date="2026-07-11",
        available_dates=("2026-07-11", "2026-07-10"),
        candidates=(),
        debate=None,
        summaries=(),
        source=HomeSnapshotSource(
            effective="sina",
            latest_trade_date="2026-07-11",
            lag_days=0,
            status="fresh",
        ),
        coldstart=HomeSnapshotColdstart(status="积累中", detail="样本 12/30"),
    )
    reruns: list[bool] = []

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard.st, "rerun", lambda: reruns.append(True))
    monkeypatch.setattr(
        dashboard,
        "_render_simple_workspace_shortcuts",
        lambda: None,
    )
    monkeypatch.setattr(
        dashboard,
        "_stretch_button",
        lambda label, **kwargs: label == "2026-07-10",
    )

    dashboard._render_snapshot_home_rail(snapshot)

    assert dashboard.st.session_state["dashboard_snapshot_selected_date"] == "2026-07-10"
    assert dashboard.st.session_state["dashboard_selected_date"] == "2026-07-10"
    assert reruns == [True]


def test_dashboard_expired_snapshot_stops_without_historical_fallback(monkeypatch) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.home_snapshot import (
        HOME_SNAPSHOT_SCHEMA_VERSION,
        HomeDashboardSnapshot,
        HomeSnapshotColdstart,
        HomeSnapshotSource,
    )

    snapshot = HomeDashboardSnapshot(
        schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
        generated_at="2026-07-11T09:30:00+08:00",
        selected_date="2026-07-11",
        available_dates=("2026-07-11",),
        candidates=(),
        debate=None,
        summaries=("不应展示为当前结论",),
        source=HomeSnapshotSource(
            effective="sina",
            latest_trade_date="2026-07-09",
            lag_days=2,
            status="stale",
        ),
        coldstart=HomeSnapshotColdstart(status="积累中", detail="不应展示冷启动长文"),
    )
    rendered: list[dict[str, object]] = []

    class _Column:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(dashboard.st, "session_state", {})
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        dashboard.st,
        "columns",
        lambda *args, **kwargs: (_Column(), _Column()),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_snapshot_home_rail",
        lambda value, **kwargs: None,
    )
    monkeypatch.setattr(
        dashboard,
        "_render_simple_panel_header",
        lambda: None,
    )
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: rendered.append(kwargs),
    )

    dashboard._render_snapshot_home_board(snapshot)

    assert rendered[0]["title"] == "数据快照已过期/等待刷新"
    assert "不应展示为当前结论" not in str(rendered)
    assert "不应展示冷启动长文" not in str(rendered)


def test_dashboard_main_uses_exact_snapshot_from_index_for_selected_date(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.home_snapshot import (
        HOME_SNAPSHOT_INDEX_SCHEMA_VERSION,
        HOME_SNAPSHOT_SCHEMA_VERSION,
        HomeDashboardSnapshot,
        HomeSnapshotColdstart,
        HomeSnapshotDay,
        HomeSnapshotIndex,
        HomeSnapshotSource,
        write_home_snapshot_index,
    )

    def _snapshot(signal_date: str, label: str) -> HomeDashboardSnapshot:
        return HomeDashboardSnapshot(
            schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
            generated_at="2026-07-11T09:30:00+08:00",
            selected_date=signal_date,
            available_dates=(signal_date,),
            candidates=(),
            debate=None,
            summaries=(label,),
            source=HomeSnapshotSource(
                effective="sina",
                latest_trade_date=signal_date,
                lag_days=0,
                status="fresh",
            ),
            coldstart=HomeSnapshotColdstart(status="完成", detail=""),
            stale_after="2099-07-11T09:30:00+08:00",
        )

    first = _snapshot("2026-07-11", "当天快照")
    second = _snapshot("2026-07-10", "历史日快照")
    index_path = tmp_path / "home_dashboard_snapshot_index.json"
    write_home_snapshot_index(
        index_path,
        HomeSnapshotIndex(
            schema_version=HOME_SNAPSHOT_INDEX_SCHEMA_VERSION,
            generated_at="2026-07-11T09:30:00+08:00",
            stale_after="2099-07-11T09:30:00+08:00",
            selected_date=first.selected_date,
            days=(
                HomeSnapshotDay(date=first.selected_date, snapshot=first),
                HomeSnapshotDay(date=second.selected_date, snapshot=second),
            ),
        ),
    )
    rendered: list[tuple[HomeDashboardSnapshot, tuple[str, ...] | None]] = []

    monkeypatch.setenv("AQSP_HOME_SNAPSHOT_PATH", str(tmp_path / "home.json"))
    monkeypatch.setenv("AQSP_HOME_SNAPSHOT_INDEX_PATH", str(index_path))
    monkeypatch.setattr(
        dashboard.st,
        "session_state",
        {"dashboard_snapshot_selected_date": "2026-07-10"},
    )
    monkeypatch.setattr(dashboard, "_inject_dashboard_styles", lambda: None)
    monkeypatch.setattr(dashboard, "_render_simple_app_header", lambda **_: None)
    monkeypatch.setattr(
        dashboard,
        "_workspace_widget_state",
        lambda **kwargs: "决策首页",
    )
    monkeypatch.setattr(
        dashboard,
        "load_home_dashboard_snapshot",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("index should be loaded before single snapshot")
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "_render_snapshot_home_board",
        lambda value, **kwargs: rendered.append(
            (value, kwargs.get("available_dates"))
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "get_provider",
        lambda: (_ for _ in ()).throw(
            AssertionError("indexed homepage must not construct a provider")
        ),
    )

    dashboard.main()

    assert rendered == [(second, ("2026-07-11", "2026-07-10"))]


def test_dashboard_main_resets_unknown_snapshot_date_to_index_selection(
    monkeypatch, tmp_path: Path
) -> None:
    import aqsp.web.dashboard as dashboard

    rendered: list[tuple[str, tuple[str, ...]]] = []
    first_snapshot = object()
    index = type(
        "_Index",
        (),
        {
            "days": (first_snapshot,),
            "available_dates": ("2026-07-11",),
            "selected_date": "2026-07-11",
            "snapshot_for_date": lambda self, selected_date: (
                first_snapshot if selected_date == "2026-07-11" else None
            ),
        },
    )()

    monkeypatch.setenv("AQSP_HOME_SNAPSHOT_PATH", str(tmp_path / "home.json"))
    monkeypatch.setenv(
        "AQSP_HOME_SNAPSHOT_INDEX_PATH", str(tmp_path / "home-index.json")
    )
    monkeypatch.setattr(
        dashboard.st,
        "session_state",
        {"dashboard_snapshot_selected_date": "2026-07-10"},
    )
    monkeypatch.setattr(dashboard, "_inject_dashboard_styles", lambda: None)
    monkeypatch.setattr(dashboard, "_render_simple_app_header", lambda **_: None)
    monkeypatch.setattr(
        dashboard,
        "_workspace_widget_state",
        lambda **kwargs: "决策首页",
    )
    monkeypatch.setattr(dashboard, "load_home_snapshot_index", lambda _: index)
    monkeypatch.setattr(
        dashboard,
        "_render_snapshot_home_board",
        lambda value, **kwargs: rendered.append(
            (str(value), tuple(kwargs.get("available_dates", ())))
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "get_provider",
        lambda: (_ for _ in ()).throw(
            AssertionError("unknown indexed date must not construct a provider")
        ),
    )

    dashboard.main()

    assert rendered == [(str(first_snapshot), ("2026-07-11",))]
    assert (
        dashboard.st.session_state["dashboard_snapshot_selected_date"]
        == "2026-07-11"
    )


def test_dashboard_historical_snapshot_remains_reviewable_after_live_ttl_expires(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.home_snapshot import (
        HOME_SNAPSHOT_SCHEMA_VERSION,
        HomeDashboardSnapshot,
        HomeSnapshotCandidate,
        HomeSnapshotColdstart,
        HomeSnapshotMessage,
        HomeSnapshotSource,
    )

    class _Column:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    snapshot = HomeDashboardSnapshot(
        schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
        generated_at="2026-07-10T09:30:00+08:00",
        selected_date="2026-07-10",
        available_dates=("2026-07-11", "2026-07-10"),
        candidates=(
            HomeSnapshotCandidate(
                symbol="600001",
                display_name="历史候选",
                score=81.0,
                research_status="纸面复核",
                next_step="确认承接",
                context="历史快照证据",
                deterministic_reasons=("量价确认",),
            ),
        ),
        summaries=("历史日结论",),
        source=HomeSnapshotSource(
            effective="sina",
            latest_trade_date="2026-07-10",
            lag_days=1,
            status="stale",
        ),
        coldstart=HomeSnapshotColdstart(status="完成", detail=""),
        stale_after="2026-07-11T09:30:00+08:00",
        message_status="ok",
        messages=(
            HomeSnapshotMessage(
                title="历史消息",
                summary="历史消息摘要",
                impact="中性",
                category="市场",
                source="测试源",
                published_at="2026-07-10T09:00:00+08:00",
            ),
        ),
    )
    cards: list[dict[str, object]] = []

    monkeypatch.setattr(
        dashboard.st,
        "session_state",
        {"dashboard_snapshot_selected_date": "2026-07-10"},
    )
    monkeypatch.setattr(
        dashboard.st,
        "columns",
        lambda spec, **kwargs: tuple(_Column() for _ in range(2)),
    )
    monkeypatch.setattr(dashboard.st, "markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: cards.append(kwargs),
    )

    dashboard._render_snapshot_home_board(
        snapshot,
        available_dates=("2026-07-11", "2026-07-10"),
    )

    assert cards[0]["title"] == "历史日结论"
    assert cards[1]["title"] == "历史消息汇总"
    assert "历史消息摘要" in " ".join(cards[1]["lines"])
    assert cards[0]["lines"][1].startswith("数据: 历史快照")


def test_dashboard_snapshot_home_marks_historical_sections_and_keeps_empty_state_clear(
    monkeypatch,
) -> None:
    import aqsp.web.dashboard as dashboard
    from aqsp.web.home_snapshot import (
        HOME_SNAPSHOT_SCHEMA_VERSION,
        HomeDashboardSnapshot,
        HomeSnapshotCandidate,
        HomeSnapshotColdstart,
        HomeSnapshotMessage,
        HomeSnapshotSource,
    )

    class _Column:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    snapshot = HomeDashboardSnapshot(
        schema_version=HOME_SNAPSHOT_SCHEMA_VERSION,
        generated_at="2026-07-11T09:30:00+08:00",
        selected_date="2026-07-10",
        available_dates=("2026-07-10",),
        candidates=(
            HomeSnapshotCandidate(
                symbol="600001",
                display_name="600001 观察",
                score=61.0,
                research_status="观察",
                next_step="等待确认",
                context="历史观察记录",
            ),
        ),
        debate=None,
        summaries=(),
        source=HomeSnapshotSource(
            effective="sina",
            latest_trade_date="2026-07-10",
            lag_days=0,
            status="fresh",
        ),
        coldstart=HomeSnapshotColdstart(status="完成", detail=""),
        stale_after="2099-07-11T09:30:00+08:00",
        message_status="ok",
        messages=(
            HomeSnapshotMessage(
                title="历史消息",
                summary="历史摘要",
                impact="中性",
                category="市场",
                source="测试源",
                published_at="2026-07-10T09:00:00+08:00",
            ),
        ),
    )
    markdown_blocks: list[str] = []
    cockpit_cards: list[dict[str, object]] = []

    monkeypatch.setattr(
        dashboard.st,
        "session_state",
        {"dashboard_snapshot_selected_date": "2026-07-10"},
    )
    monkeypatch.setattr(
        dashboard.st,
        "columns",
        lambda spec, **kwargs: tuple(_Column() for _ in range(2)),
    )
    monkeypatch.setattr(
        dashboard.st,
        "markdown",
        lambda body, *args, **kwargs: markdown_blocks.append(str(body)),
    )
    monkeypatch.setattr(dashboard.st, "button", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        dashboard,
        "_render_cockpit_card",
        lambda **kwargs: cockpit_cards.append(kwargs),
    )

    dashboard._render_snapshot_home_board(
        snapshot,
        available_dates=("2026-07-11", "2026-07-10"),
    )

    rendered = "\n".join(markdown_blocks)
    assert "历史回看" in rendered
    assert "历史观察记录" in rendered
    assert cockpit_cards[0]["title"] == "当前无实时推荐，保留观察对象"
    assert cockpit_cards[1]["title"] == "历史消息汇总"
    assert cockpit_cards[-1]["title"] == "历史无 Agent 讨论记录"
    assert cockpit_cards[-1]["kicker"] == "Agent 讨论"


def test_dashboard_snapshot_without_stale_after_is_expired() -> None:
    import aqsp.web.dashboard as dashboard

    snapshot = SimpleNamespace(stale_after="")

    assert dashboard._snapshot_is_expired(snapshot) is True
    assert dashboard._snapshot_is_expired(snapshot, historical=True) is False
