from __future__ import annotations

from aqsp.portfolio.diversification import SectorAllocation
from aqsp.reports.v2 import PortfolioReport, ReportGenerator


def test_reports_v2_markdown_uses_research_wording_and_timezone() -> None:
    generator = ReportGenerator()
    portfolio = PortfolioReport(
        symbols=["600519", "300750"],
        weights={"600519": 0.6, "300750": 0.4},
        sector_allocations=[
            SectorAllocation(sector="消费", weight=0.6, count=1),
            SectorAllocation(sector="新能源", weight=0.4, count=1),
        ],
        diversification_score=0.72,
    )

    report = generator.generate(strategies={}, portfolio=portfolio)
    markdown = generator.to_markdown(report)

    assert report.date.endswith("+08:00")
    assert "# AI量化选股研究日报" in markdown
    assert "仅供研究复核，不构成交易指令或投资建议" in markdown
    assert "## 📦 研究覆盖" in markdown
    assert "### 跟踪标的明细" in markdown
    assert "持仓组合" not in markdown
    assert "持仓明细" not in markdown
