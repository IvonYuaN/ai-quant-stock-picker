from __future__ import annotations

from aqsp.portfolio.correlation import CorrelationResult
from aqsp.portfolio.optimizer import PortfolioAllocation
from aqsp.portfolio.risk_summary import summarize_portfolio_risk
from aqsp.portfolio.sector_check import ConcentrationResult


def test_summarize_portfolio_risk_reports_concentration_and_correlation() -> None:
    result = summarize_portfolio_risk(
        (
            PortfolioAllocation("300750", "宁德时代", 0.2, ()),
            PortfolioAllocation("688981", "中芯国际", 0.15, ()),
        ),
        cash_reserve=0.65,
        concentration=ConcentrationResult(
            total_candidates=2,
            sector_count=1,
            max_concentration=1.0,
            warnings=("集中",),
            sectors=(),
        ),
        correlation_result=CorrelationResult(
            matrix={},
            high_corr_pairs=[("300750", "688981", 0.8)],
            avg_correlation=0.62,
        ),
    )

    assert result.max_weight == 0.2
    assert result.hhi == 0.0625
    assert result.effective_positions == 16.0
    assert result.avg_correlation == 0.62
    assert "组合集中度 HHI 0.062" in result.lines[0]
    assert any("高相关候选对" in line for line in result.lines)
