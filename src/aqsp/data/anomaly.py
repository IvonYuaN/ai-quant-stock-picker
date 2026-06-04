from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class AnomalyAlert:
    symbol: str
    anomaly_type: str
    severity: str
    detail: str
    value: float
    threshold: float


def detect_anomalies(frames: dict[str, pd.DataFrame]) -> list[AnomalyAlert]:
    alerts: list[AnomalyAlert] = []
    for symbol, df in frames.items():
        if df.empty or len(df) < 5:
            continue
        alerts.extend(_check_limit_moves(symbol, df))
        alerts.extend(_check_volume_spike(symbol, df))
        alerts.extend(_check_price_gap(symbol, df))
    return alerts


def _is_chinext_or_star(symbol: str) -> bool:
    return symbol.startswith("300") or symbol.startswith("688")


def _check_limit_moves(symbol: str, df: pd.DataFrame) -> list[AnomalyAlert]:
    if "close" not in df.columns:
        return []
    alerts: list[AnomalyAlert] = []
    pct = df["close"].pct_change().dropna()
    if pct.empty:
        return []
    threshold = 0.195 if _is_chinext_or_star(symbol) else 0.095
    board_label = "创业板/科创板" if _is_chinext_or_star(symbol) else "主板"
    for idx, val in pct.items():
        if abs(val) >= threshold:
            alerts.append(
                AnomalyAlert(
                    symbol=symbol,
                    anomaly_type="limit_move",
                    severity="info",
                    detail=f"{board_label}涨跌停: {val:+.2%}",
                    value=round(float(val), 4),
                    threshold=threshold,
                )
            )
    return alerts


def _check_volume_spike(symbol: str, df: pd.DataFrame) -> list[AnomalyAlert]:
    if "volume" not in df.columns or len(df) < 20:
        return []
    alerts: list[AnomalyAlert] = []
    vol = df["volume"].astype(float)
    ma20 = vol.rolling(window=20, min_periods=20).mean()
    ratio = vol / ma20
    for idx in range(len(df)):
        r = ratio.iloc[idx]
        if pd.isna(r):
            continue
        if r > 5.0:
            alerts.append(
                AnomalyAlert(
                    symbol=symbol,
                    anomaly_type="volume_spike",
                    severity="warning",
                    detail=f"成交量异常放大: {r:.1f}x 20日均量",
                    value=round(float(r), 2),
                    threshold=5.0,
                )
            )
        elif r > 3.0:
            alerts.append(
                AnomalyAlert(
                    symbol=symbol,
                    anomaly_type="volume_spike",
                    severity="info",
                    detail=f"成交量放大: {r:.1f}x 20日均量",
                    value=round(float(r), 2),
                    threshold=3.0,
                )
            )
    return alerts


def _check_price_gap(symbol: str, df: pd.DataFrame) -> list[AnomalyAlert]:
    required = {"open", "close"}
    if not required.issubset(df.columns) or len(df) < 2:
        return []
    alerts: list[AnomalyAlert] = []
    prev_close = df["close"].shift(1)
    gap = (df["open"] - prev_close) / prev_close
    gap = gap.dropna()
    for idx, val in gap.items():
        if abs(val) > 0.05:
            alerts.append(
                AnomalyAlert(
                    symbol=symbol,
                    anomaly_type="price_gap",
                    severity="info",
                    detail=f"开盘跳空: {val:+.2%}",
                    value=round(float(val), 4),
                    threshold=0.05,
                )
            )
    return alerts


def format_anomaly_alerts(alerts: list[AnomalyAlert]) -> str:
    if not alerts:
        return ""
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    sorted_alerts = sorted(
        alerts, key=lambda a: (severity_order.get(a.severity, 9), a.symbol)
    )
    lines = ["## 数据异常检测", ""]
    lines.append("| 标的 | 类型 | 严重度 | 详情 | 实际值 | 阈值 |")
    lines.append("|------|------|--------|------|--------|------|")
    type_labels = {
        "limit_move": "涨跌停",
        "volume_spike": "成交量",
        "price_gap": "跳空缺口",
    }
    severity_labels = {
        "critical": "🔴 严重",
        "warning": "🟡 警告",
        "info": "🔵 提示",
    }
    for alert in sorted_alerts:
        lines.append(
            f"| {alert.symbol} "
            f"| {type_labels.get(alert.anomaly_type, alert.anomaly_type)} "
            f"| {severity_labels.get(alert.severity, alert.severity)} "
            f"| {alert.detail} "
            f"| {alert.value} "
            f"| {alert.threshold} |"
        )
    return "\n".join(lines)
