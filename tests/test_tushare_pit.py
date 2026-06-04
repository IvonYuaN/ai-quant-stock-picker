from __future__ import annotations

from datetime import date

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.data.tushare_pit import TusharePitClient


def test_fetch_disclosure_dates_suppresses_noisy_broken_pipe_output(
    capsys,
) -> None:
    client = object.__new__(TusharePitClient)

    class DummyPro:
        def disclosure_date(self, **kwargs):
            print("[Errno 32] Broken pipe")
            print("接收数据异常，请稍后再试。")
            raise OSError(32, "Broken pipe")

    client._pro = DummyPro()

    try:
        client.fetch_disclosure_dates(
            ["600519"],
            date(2024, 1, 1),
            date(2024, 12, 31),
        )
    except DataError as exc:
        assert "tushare 披露日获取失败" in str(exc)
        assert "Broken pipe" in str(exc)
    else:
        raise AssertionError("expected DataError")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_fetch_index_weights_suppresses_noisy_transport_output(capsys) -> None:
    client = object.__new__(TusharePitClient)

    class DummyPro:
        def index_weight(self, **kwargs):
            print("接收数据异常，请稍后再试。")
            raise ConnectionError("transport reset")

    client._pro = DummyPro()

    try:
        client.fetch_index_weights(
            "000300.SH",
            date(2024, 1, 1),
            date(2024, 12, 31),
        )
    except DataError as exc:
        assert "tushare 指数成分获取失败" in str(exc)
        assert "接收数据异常，请稍后再试。" in str(exc)
    else:
        raise AssertionError("expected DataError")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_fetch_trade_calendar_uses_safe_call_when_successful() -> None:
    client = object.__new__(TusharePitClient)

    class DummyPro:
        def trade_cal(self, **kwargs):
            return pd.DataFrame(
                [
                    {
                        "cal_date": "20240102",
                        "is_open": 1,
                        "pretrade_date": "20231229",
                    }
                ]
            )

    client._pro = DummyPro()

    df = client.fetch_trade_calendar(
        date(2024, 1, 1),
        date(2024, 1, 31),
    )

    assert list(df.columns) == ["exchange", "cal_date", "is_open", "pretrade_date"]
    assert df.iloc[0]["cal_date"] == "2024-01-02"
