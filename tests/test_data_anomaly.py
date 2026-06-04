from __future__ import annotations

import pandas as pd

from aqsp.data.anomaly import detect_anomalies


def test_limit_move_is_trading_state_info_not_critical_data_error() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-06-01", periods=5).strftime("%Y-%m-%d"),
            "open": [10.0, 10.1, 10.2, 10.3, 10.4],
            "close": [10.0, 10.99, 10.8, 10.7, 10.6],
            "volume": [100, 100, 100, 100, 100],
        }
    )

    alerts = detect_anomalies({"601899": frame})
    limit_alerts = [a for a in alerts if a.anomaly_type == "limit_move"]

    assert limit_alerts
    assert {a.severity for a in limit_alerts} == {"info"}
