from __future__ import annotations

from aqsp.core.types import PickResult
from aqsp.portfolio.correlation import CorrelationResult
from aqsp.portfolio.manager import apply_portfolio_manager
from aqsp.portfolio.sector_check import ConcentrationResult, SectorConcentration


def _pick(
    symbol: str, score: float, *, recommended_adjustment: str = "keep"
) -> PickResult:
    return PickResult(
        symbol=symbol,
        name=symbol,
        date="2026-06-02",
        close=10.0,
        score=score,
        rating="buy_candidate",
        entry_type="close",
        ideal_buy=10.0,
        stop_loss=9.5,
        take_profit=11.0,
        position="10%-30%",
        recommended_adjustment=recommended_adjustment,
        adjusted_score=score - 3 if recommended_adjustment == "lower" else score + 3,
    )


def test_apply_portfolio_manager_downgrades_when_debate_and_correlation_stack() -> None:
    picks = [
        _pick("600036", 42, recommended_adjustment="lower"),
        _pick("000001", 38),
    ]
    concentration = ConcentrationResult(
        total_candidates=2,
        sector_count=1,
        max_concentration=1.0,
        warnings=("too concentrated",),
        sectors=(
            SectorConcentration(
                sector="银行",
                count=2,
                total=2,
                ratio=1.0,
                symbols=("600036", "000001"),
            ),
        ),
    )
    correlation = CorrelationResult(
        matrix={
            "600036": {"600036": 1.0, "000001": 0.82},
            "000001": {"600036": 0.82, "000001": 1.0},
        },
        high_corr_pairs=[("000001", "600036", 0.82)],
        avg_correlation=0.82,
    )

    bundle = apply_portfolio_manager(
        picks,
        concentration=concentration,
        correlation_result=correlation,
    )

    by_symbol = {item.symbol: item for item in bundle.picks}
    decisions = {item.symbol: item for item in bundle.decisions}

    assert by_symbol["600036"].rating == "avoid"
    assert decisions["600036"].action == "downgrade"
    assert any("多Agent辩论偏谨慎" in reason for reason in decisions["600036"].reasons)


def test_apply_portfolio_manager_promotes_when_debate_supports_buy_candidate() -> None:
    picks = [_pick("300750", 66, recommended_adjustment="raise")]

    bundle = apply_portfolio_manager(picks)

    assert bundle.picks[0].rating == "strong_buy_candidate"
    assert bundle.decisions[0].action == "promote"


def test_apply_portfolio_manager_keeps_action_when_no_incremental_override() -> None:
    picks = [_pick("300750", 18, recommended_adjustment="keep")]

    bundle = apply_portfolio_manager(picks)

    assert bundle.picks[0].score == 18
    assert bundle.picks[0].rating == "buy_candidate"
    assert bundle.decisions[0].action == "keep"
    assert bundle.decisions[0].reasons == ("保持原排序",)
