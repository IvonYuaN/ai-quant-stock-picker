from __future__ import annotations

from aqsp.core.types import RunMetadata
from aqsp.models import PickResult
from aqsp.portfolio.manager import PortfolioDecision
from aqsp.report import to_markdown


def test_report_renders_run_metadata_when_provided() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=72,
        rating="strong_buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
    )
    metadata = RunMetadata(
        requested_source="auto",
        actual_source="tdx_vipdoc",
        source_freshness_tier="end_of_day",
        source_coverage_tier="history_core",
        source_local_status="present",
        source_health_label="healthy",
        source_health_message="tdx_vipdoc 健康；源成功/失败 3/0",
        fallback_used=False,
        explicit_symbol_count=0,
        resolved_symbol_count=100,
        fetched_frame_count=101,
        screened_count=8,
        final_count=1,
        min_price=1.0,
        max_price=1000.0,
        min_avg_amount=50_000_000,
        online_factors_enabled=False,
        thresholds_version="1.0.0",
        data_latest_trade_date="2026-05-29",
        data_lag_days=0,
        regime="stable_bull",
        max_universe=100,
    )

    markdown = to_markdown([pick], metadata=metadata)

    assert "## 运行参数" in markdown
    assert "- 数据源: auto -> tdx_vipdoc" in markdown
    assert (
        "- 数据层级: fresh=end_of_day / cover=history_core / local=present" in markdown
    )
    assert "- 数据时效: latest=2026-05-29 / lag=0d" in markdown
    assert "- 数据健康: healthy / tdx_vipdoc 健康；源成功/失败 3/0" in markdown
    assert "显式 0 / 解析 100 / 取数 101 / 筛选前 8 / 最终 1" in markdown
    assert "- thresholds.version: 1.0.0" in markdown


def test_report_renders_portfolio_manager_decision_when_provided() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=72,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
    )

    markdown = to_markdown(
        [pick],
        portfolio_decisions=[
            PortfolioDecision(
                symbol="600900",
                action="promote",
                score_delta=4.0,
                reasons=("多Agent辩论支持上调优先级",),
            )
        ],
    )

    assert "### Portfolio Manager" in markdown
    assert "- 最终动作: promote" in markdown
    assert "- 分数调整: +4.0" in markdown


def test_report_hides_noop_portfolio_manager_decision() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=72,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
    )

    markdown = to_markdown(
        [pick],
        portfolio_decisions=[
            PortfolioDecision(
                symbol="600900",
                action="keep",
                score_delta=0.0,
                reasons=("保持原排序",),
            )
        ],
    )

    assert "### Portfolio Manager" not in markdown


def test_report_renders_final_decision_board_first() -> None:
    pick = PickResult(
        symbol="600900",
        name="长江电力",
        date="2026-05-29",
        close=27.75,
        score=76,
        rating="buy_candidate",
        entry_type="relative_strength",
        ideal_buy=27.75,
        stop_loss=26.1,
        take_profit=31.0,
        position="30%-50%",
        reasons=("趋势保持", "量价配合"),
    )

    markdown = to_markdown(
        [pick],
        portfolio_decisions=[
            PortfolioDecision(
                symbol="600900",
                action="promote",
                score_delta=4.0,
                reasons=("多Agent辩论支持上调优先级",),
            )
        ],
    )

    assert "## 最终决策看板" in markdown
    assert "- Top 1: 600900 长江电力 | 观察候选 | 评分 76 | PM promote" in markdown
    assert markdown.index("## 最终决策看板") < markdown.index("## 1. 600900 长江电力")
