from __future__ import annotations

from dataclasses import dataclass

from aqsp.portfolio.correlation import CorrelationResult
from aqsp.portfolio.optimizer import PortfolioAllocation
from aqsp.portfolio.sector_check import ConcentrationResult


@dataclass(frozen=True)
class PortfolioRiskSummary:
    max_weight: float
    hhi: float
    effective_positions: float
    cash_reserve: float
    avg_correlation: float
    lines: tuple[str, ...]


def summarize_portfolio_risk(
    allocations: tuple[PortfolioAllocation, ...],
    *,
    cash_reserve: float,
    concentration: ConcentrationResult | None = None,
    correlation_result: CorrelationResult | None = None,
) -> PortfolioRiskSummary:
    weights = tuple(max(0.0, float(item.weight)) for item in allocations)
    hhi = sum(weight * weight for weight in weights)
    effective_positions = 0.0 if hhi <= 0 else 1.0 / hhi
    max_weight = max(weights) if weights else 0.0
    avg_correlation = (
        float(correlation_result.avg_correlation)
        if correlation_result is not None
        else 0.0
    )
    lines = _risk_lines(
        max_weight=max_weight,
        hhi=hhi,
        effective_positions=effective_positions,
        cash_reserve=cash_reserve,
        avg_correlation=avg_correlation,
        concentration=concentration,
        correlation_result=correlation_result,
    )
    return PortfolioRiskSummary(
        max_weight=round(max_weight, 4),
        hhi=round(hhi, 4),
        effective_positions=round(effective_positions, 2),
        cash_reserve=round(float(cash_reserve), 4),
        avg_correlation=round(avg_correlation, 4),
        lines=lines,
    )


def _risk_lines(
    *,
    max_weight: float,
    hhi: float,
    effective_positions: float,
    cash_reserve: float,
    avg_correlation: float,
    concentration: ConcentrationResult | None,
    correlation_result: CorrelationResult | None,
) -> tuple[str, ...]:
    lines = [
        f"组合集中度 HHI {hhi:.3f}，有效持仓 {effective_positions:.1f}",
        f"最大单票 {max_weight:.1%}，现金留存 {cash_reserve:.1%}",
    ]
    if avg_correlation > 0:
        lines.append(f"候选平均相关 {avg_correlation:.2f}")
    if concentration is not None and concentration.is_concentrated:
        lines.append("板块集中度偏高，维持压缩暴露")
    if correlation_result is not None and correlation_result.high_corr_pairs:
        lines.append("存在高相关候选对，避免重复下注同一因子")
    return tuple(lines)
