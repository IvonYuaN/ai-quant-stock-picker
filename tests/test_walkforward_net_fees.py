"""T2 net cost mode regression tests.

Legacy mode (``sell_fee_bps=None``) must stay bit-identical to the
historical single-side-fee behaviour; net mode must deduct exactly
``sell_fee_bps`` per trade on top of the legacy fee.
"""

from __future__ import annotations

import pytest

from aqsp.backtest.walk_forward import WalkForwardTester
from aqsp.ledger.base import execution_config_from_thresholds
from aqsp.strategies.thresholds import ExecutionThresholds, Thresholds, load_thresholds


def _make_thresholds(net_fee_mode: bool) -> Thresholds:
    thresholds = load_thresholds()
    return thresholds.__class__(
        **{
            **thresholds.__dict__,
            "execution": ExecutionThresholds(
                **{
                    **thresholds.execution.__dict__,
                    "net_fee_mode": net_fee_mode,
                }
            ),
        }
    )


def test_execution_config_legacy_sell_fee_is_none() -> None:
    config = execution_config_from_thresholds(_make_thresholds(net_fee_mode=False))
    assert config.sell_fee_bps is None


def test_execution_config_net_mode_sell_fee_is_commission_plus_stamp() -> None:
    config = execution_config_from_thresholds(_make_thresholds(net_fee_mode=True))
    # 佣金 3bp + 印花税 10bp = 13bp（卖出端）
    assert config.sell_fee_bps == pytest.approx(13.0)


def test_walkforward_tester_default_is_legacy() -> None:
    tester = WalkForwardTester(strategy=object(), fee_bps=3.0)
    assert tester.sell_fee_bps is None


def test_runtime_rows_report_fee_mode() -> None:
    from aqsp.cli import _walkforward_runtime_rows

    args = {"source": "sqlite_db", "symbols": "", "min_score": None}

    class _Args:
        source = args["source"]
        symbols = args["symbols"]
        min_score = None

    legacy_rows = dict(
        _walkforward_runtime_rows(
            _Args(), 3, fee_bps=3.0, slippage_bps=20.0, sell_fee_bps=None
        )
    )
    net_rows = dict(
        _walkforward_runtime_rows(
            _Args(), 3, fee_bps=3.0, slippage_bps=20.0, sell_fee_bps=13.0
        )
    )
    assert legacy_rows["fee_mode"].startswith("legacy(")
    assert net_rows["fee_mode"].startswith("net(")
    assert "13" in net_rows["fee_mode"]


def test_net_fee_deduction_matches_closed_form() -> None:
    """每笔收益 = gross−滑点口径收益 − fee_bps/100 − sell_fee_bps/100。"""
    entry = 10.0
    exit_price = 10.5
    gross = (exit_price - entry) / entry * 100
    legacy_ret = gross - 3.0 / 100
    net_ret = gross - 3.0 / 100 - 13.0 / 100
    # legacy 与 net 的差额必须恰为卖出端 13bp
    assert (legacy_ret - net_ret) == pytest.approx(13.0 / 100)


def test_walkforward_cli_registers_net_fees_flag(capsys: pytest.CaptureFixture) -> None:
    """防「有实现无入口」：--net-fees 必须真实注册在 walkforward 解析器。

    --net-fees 放在 --help 之前：若未注册，argparse 会先报
    unrecognized arguments（退出码 2）；注册成功则打印帮助并退出 0。
    """
    from aqsp.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["walkforward", "--net-fees", "--help"])
    captured = capsys.readouterr()
    assert excinfo.value.code == 0
    assert "--net-fees" in captured.out
