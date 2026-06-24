from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from aqsp.core.time import now_shanghai
from aqsp.strategies.thresholds import load_thresholds
from aqsp.ledger.learner import StrategyPerformance
from aqsp.portfolio.diversification import SectorAllocation
from aqsp.regime.detector import MarketRegime
from aqsp.risk.circuit_breaker import BreakerStatus


@dataclass(frozen=True)
class ReportSection:
    title: str
    content: str
    status: str = "info"


@dataclass(frozen=True)
class StrategyReport:
    strategy_name: str
    win_rate: float
    avg_return: float
    max_drawdown: float
    sharpe_ratio: float
    weight: float


@dataclass(frozen=True)
class PortfolioReport:
    symbols: List[str]
    weights: Dict[str, float]
    sector_allocations: List[SectorAllocation]
    diversification_score: float

    @property
    def sector_allocation_dict(self) -> Dict[str, float]:
        return {a.sector: a.weight for a in self.sector_allocations}


@dataclass(frozen=True)
class DailyReport:
    date: str
    version: str
    market_regime: str
    regime_confidence: float
    breaker_status: str
    strategies: List[StrategyReport]
    portfolio: PortfolioReport
    summary: str


class ReportGenerator:
    def __init__(self):
        self.thresholds = load_thresholds()

    def generate(
        self,
        strategies: Dict[str, StrategyPerformance],
        portfolio: PortfolioReport,
        regime: Optional[MarketRegime] = None,
        breaker: Optional[BreakerStatus] = None,
    ) -> DailyReport:
        self.thresholds = load_thresholds()

        strategy_reports = []
        for name, perf in strategies.items():
            strategy_reports.append(
                StrategyReport(
                    strategy_name=name,
                    win_rate=perf.recent_performance.win_rate,
                    avg_return=perf.recent_performance.avg_return,
                    max_drawdown=perf.recent_performance.max_drawdown,
                    sharpe_ratio=perf.recent_performance.sharpe_ratio,
                    weight=perf.weights.get("base", 1.0),
                )
            )

        regime_name = regime.name if regime else "unknown"
        regime_confidence = regime.confidence if regime else 0.0
        breaker_status = breaker.reason if breaker and breaker.triggered else "正常"

        summary = self._generate_summary(strategy_reports, portfolio, regime, breaker)

        return DailyReport(
            date=now_shanghai().isoformat(timespec="seconds"),
            version=self.thresholds.version,
            market_regime=regime_name,
            regime_confidence=regime_confidence,
            breaker_status=breaker_status,
            strategies=strategy_reports,
            portfolio=portfolio,
            summary=summary,
        )

    def _generate_summary(
        self,
        strategies: List[StrategyReport],
        portfolio: PortfolioReport,
        regime: Optional[MarketRegime],
        breaker: Optional[BreakerStatus],
    ) -> str:

        parts = []

        if breaker and breaker.triggered:
            parts.append(f"⚠️ 熔断状态: {breaker.reason}")

        if regime:
            parts.append(
                f"📊 市场状态: {regime.description} (置信度: {regime.confidence:.1%})"
            )

        strong_strategies = [s for s in strategies if s.win_rate >= 0.5]
        weak_strategies = [s for s in strategies if s.win_rate < 0.4]

        if strong_strategies:
            parts.append(
                f"✅ 表现优异策略: {', '.join(s.strategy_name for s in strong_strategies)}"
            )

        if weak_strategies:
            parts.append(
                f"⚠️ 表现较弱策略: {', '.join(s.strategy_name for s in weak_strategies)}"
            )

        parts.append(f"📌 跟踪标的数: {len(portfolio.symbols)}只")
        parts.append(f"🎯 研究分散度: {portfolio.diversification_score:.1%}")

        return "\n".join(parts)

    def to_markdown(self, report: DailyReport) -> str:
        lines = []
        lines.append("# AI量化选股研究日报")
        lines.append(f"**日期**: {report.date}")
        lines.append(f"**阈值版本**: {report.version}")
        lines.append("")
        lines.append("**免责声明**: 本报告仅供研究复核，不构成交易指令或投资建议。")
        lines.append("")

        lines.append("## 📊 市场概览")
        lines.append(
            f"- 市场状态: {report.market_regime} (置信度: {report.regime_confidence:.1%})"
        )
        lines.append(
            f"- 熔断状态: {'🚨 ' if '触发' in report.breaker_status else '✅ '}{report.breaker_status}"
        )
        lines.append("")

        lines.append("## 🎯 策略表现")
        lines.append("| 策略 | 胜率 | 平均收益 | 最大回撤 | Sharpe | 权重 |")
        lines.append("|------|------|----------|----------|--------|------|")
        for s in report.strategies:
            lines.append(
                f"| {s.strategy_name} | {s.win_rate:.1%} | {s.avg_return:.2%} | {s.max_drawdown:.1%} | {s.sharpe_ratio:.2f} | {s.weight:.2f} |"
            )
        lines.append("")

        lines.append("## 📦 研究覆盖")
        lines.append(f"- 跟踪标的数: {len(report.portfolio.symbols)}只")
        lines.append(f"- 研究分散度: {report.portfolio.diversification_score:.1%}")
        lines.append("")

        lines.append("### 板块覆盖")
        for sector, weight in sorted(
            report.portfolio.sector_allocation_dict.items(), key=lambda x: -x[1]
        ):
            lines.append(f"- {sector}: {weight:.1%}")
        lines.append("")

        lines.append("### 跟踪标的明细")
        lines.append("| 标的 | 研究权重 |")
        lines.append("|------|----------|")
        for symbol in sorted(report.portfolio.symbols):
            lines.append(
                f"| {symbol} | {report.portfolio.weights.get(symbol, 0):.2%} |"
            )
        lines.append("")

        lines.append("## 📝 摘要")
        lines.append(report.summary)

        return "\n".join(lines)

    def save(self, report: DailyReport, filepath: str) -> None:
        content = self.to_markdown(report)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
