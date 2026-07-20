import pandas as pd

from aqsp.backtest.variant_account import VariantOrder, simulate_variant


def _frame(*, suspended=None, limit_up=None, limit_down=None):
    values = {
        "date": ["2026-07-01", "2026-07-02", "2026-07-03"],
        "open": [10.0, 11.0, 12.0],
        "high": [10.2, 11.2, 12.2],
        "low": [9.8, 10.8, 11.8],
        "close": [10.0, 11.0, 12.0],
    }
    if suspended is not None:
        values["suspended"] = suspended
    if limit_up is not None:
        values["limit_up"] = limit_up
    if limit_down is not None:
        values["limit_down"] = limit_down
    return pd.DataFrame(values)


def test_simulate_variant_rejects_same_day_sell_under_t_plus_one():
    result = simulate_variant(
        "t1",
        {"AAA": _frame()},
        [
            VariantOrder("2026-07-01", "AAA", "buy"),
            VariantOrder("2026-07-01", "AAA", "sell"),
        ],
    )
    assert result.initial_cash == 100_000.0
    assert [fill.status for fill in result.fills] == ["filled", "rejected"]
    assert result.fills[1].reason == "t_plus_one"
    assert result.positions["AAA"] > 0


def test_simulate_variant_keeps_accounts_independent():
    data = {"AAA": _frame()}
    first = simulate_variant("first", data, [VariantOrder("2026-07-01", "AAA", "buy")])
    second = simulate_variant("second", data, [])
    assert first.initial_cash == second.initial_cash == 100_000.0
    assert first.final_equity != second.final_equity
    assert second.cash == 100_000.0


def test_simulate_variant_rejects_suspended_and_price_limit_orders():
    result = simulate_variant(
        "hard-gates",
        {
            "AAA": _frame(
                suspended=[False, True, False],
                limit_up=[10.0, 11.0, 99.0],
                limit_down=[1.0, 1.0, 1.0],
            )
        },
        [
            VariantOrder("2026-07-01", "AAA", "buy"),
            VariantOrder("2026-07-02", "AAA", "buy"),
            VariantOrder("2026-07-03", "AAA", "sell"),
        ],
    )
    assert [fill.reason for fill in result.fills] == ["limit_up", "suspended", "t_plus_one"]
    assert result.rejected_orders == 3
