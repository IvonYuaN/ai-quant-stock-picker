from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


# Indicator periods follow the locked TickFlow daily feature family and are part
# of the expression contract used by custom strategy scans.
MA_WINDOWS = (5, 10, 20, 60)
KDJ_LOOKBACK = 9
KDJ_SMOOTHING = 3
BOLLINGER_WINDOW = 20
BOLLINGER_DEVIATIONS = 2.0
VOLATILITY_WINDOW = 20
ANNUAL_TRADING_SESSIONS = 252

DAILY_RAW_FEATURE_FIELDS = frozenset(
    {"open", "high", "low", "close", "prev_close", "volume", "amount"}
)
DAILY_INDICATOR_FIELDS = frozenset(
    {
        *(f"ma{window}" for window in MA_WINDOWS),
        *(f"vol_ma{window}" for window in MA_WINDOWS),
        "ema12", "ema26", "macd_dif", "macd_dea", "macd_hist", "rsi12",
        "atr14", "ret_1", "ret_5", "ret_10", "ret_20", "high_20", "low_20",
        "bias20", "volume_ratio", "amount_ma20", "range_pos",
        "upper_shadow_pct", "amplitude_pct", "bb_middle20", "bb_upper20",
        "bb_lower20", "annualized_volatility20", "kdj_k", "kdj_d", "kdj_j",
    }
)
DAILY_EXPRESSION_FIELDS = DAILY_RAW_FEATURE_FIELDS | DAILY_INDICATOR_FIELDS
DAILY_PIT_PRICE_FIELDS = frozenset(
    DAILY_RAW_FEATURE_FIELDS & {"open", "high", "low", "close", "prev_close"}
) | frozenset(
    {
        *(f"ma{window}" for window in MA_WINDOWS),
        "ema12", "ema26", "macd_dif", "macd_dea", "macd_hist", "rsi12",
        "atr14", "ret_1", "ret_5", "ret_10", "ret_20", "high_20", "low_20",
        "bias20", "range_pos", "upper_shadow_pct", "amplitude_pct",
        "bb_middle20", "bb_upper20", "bb_lower20", "annualized_volatility20",
        "kdj_k", "kdj_d", "kdj_j",
    }
)
DAILY_FEATURE_LOOKBACK_BARS = {
    **{f"ma{window}": window for window in MA_WINDOWS},
    **{f"vol_ma{window}": window for window in MA_WINDOWS},
    "ema12": 12,
    "ema26": 26,
    "macd_dif": 26,
    "macd_dea": 35,
    "macd_hist": 35,
    "rsi12": 12,
    "atr14": 14,
    "ret_1": 2,
    "ret_5": 6,
    "ret_10": 11,
    "ret_20": 21,
    "high_20": 20,
    "low_20": 20,
    "bias20": 20,
    "volume_ratio": 5,
    "amount_ma20": 20,
    "range_pos": 1,
    "upper_shadow_pct": 1,
    "amplitude_pct": 1,
    "bb_middle20": BOLLINGER_WINDOW,
    "bb_upper20": BOLLINGER_WINDOW,
    "bb_lower20": BOLLINGER_WINDOW,
    "annualized_volatility20": VOLATILITY_WINDOW + 1,
    "kdj_k": KDJ_LOOKBACK,
    "kdj_d": KDJ_LOOKBACK + KDJ_SMOOTHING,
    "kdj_j": KDJ_LOOKBACK + KDJ_SMOOTHING,
}


def enrich_indicators(raw: pd.DataFrame) -> pd.DataFrame:
    df = normalize_ohlcv(raw).copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    amount = df["amount"]

    for window in MA_WINDOWS:
        df[f"ma{window}"] = close.rolling(window).mean()
        df[f"vol_ma{window}"] = volume.rolling(window).mean()

    df["prev_close"] = close.shift(1)
    df["ema12"] = close.ewm(span=12, adjust=False).mean()
    df["ema26"] = close.ewm(span=26, adjust=False).mean()
    df["macd_dif"] = df["ema12"] - df["ema26"]
    df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = (df["macd_dif"] - df["macd_dea"]) * 2
    df["rsi12"] = rsi(close, 12)
    df["atr14"] = atr(high, low, close, 14)
    df["ret_1"] = close.pct_change()
    df["ret_5"] = close / close.shift(5) - 1
    df["ret_10"] = close / close.shift(10) - 1
    df["ret_20"] = close / close.shift(20) - 1
    df["high_20"] = high.rolling(20).max()
    df["low_20"] = low.rolling(20).min()
    df["bias20"] = (close / df["ma20"] - 1) * 100
    df["volume_ratio"] = volume / df["vol_ma5"]
    if {"intraday_elapsed_minutes", "intraday_session_minutes"}.issubset(df.columns):
        elapsed = pd.to_numeric(df["intraday_elapsed_minutes"], errors="coerce")
        session = pd.to_numeric(df["intraday_session_minutes"], errors="coerce")
        historical_volume = volume.shift(1).rolling(5, min_periods=5).mean()
        intraday_mask = elapsed.notna() & session.notna() & (elapsed > 0)
        adjusted_ratio = (volume / historical_volume * session / elapsed).where(
            historical_volume > 0
        )
        df.loc[intraday_mask, "volume_ratio"] = adjusted_ratio[intraday_mask]
    df["amount_ma20"] = amount.rolling(20).mean()
    df["range_pos"] = (close - low) / (high - low).replace(0, np.nan)
    df["upper_shadow_pct"] = (high - np.maximum(close, df["open"])) / close * 100
    df["amplitude_pct"] = (high - low) / close * 100
    middle = close.rolling(BOLLINGER_WINDOW).mean()
    deviation = close.rolling(BOLLINGER_WINDOW).std(ddof=0)
    df["bb_middle20"] = middle
    df["bb_upper20"] = middle + BOLLINGER_DEVIATIONS * deviation
    df["bb_lower20"] = middle - BOLLINGER_DEVIATIONS * deviation
    df["annualized_volatility20"] = (
        df["ret_1"].rolling(VOLATILITY_WINDOW).std(ddof=0)
        * np.sqrt(ANNUAL_TRADING_SESSIONS)
    )
    lowest = low.rolling(KDJ_LOOKBACK).min()
    highest = high.rolling(KDJ_LOOKBACK).max()
    spread = (highest - lowest).replace(0, np.nan)
    rsv = (close - lowest) / spread * 100
    smoothing_alpha = 1 / KDJ_SMOOTHING
    df["kdj_k"] = rsv.ewm(alpha=smoothing_alpha, adjust=False).mean()
    df["kdj_d"] = df["kdj_k"].ewm(alpha=smoothing_alpha, adjust=False).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]
    return df


def normalize_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
        "代码": "symbol",
        "名称": "name",
    }
    df = raw.rename(
        columns={k: v for k, v in aliases.items() if k in raw.columns}
    ).copy()
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")
    if "amount" not in df.columns:
        df["amount"] = df["close"] * df["volume"] * 100
    if "symbol" not in df.columns:
        df["symbol"] = ""
    if "name" not in df.columns:
        df["name"] = ""
    # date 用 coerce：脏日期（非法格式）变 NaT，先丢弃 NaT 行再格式化，
    # 避免单个脏日期让整个 normalize_ohlcv 崩溃（进而整只标的被静默跳过）。
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).sort_values(
        "date"
    )
    return df.reset_index(drop=True)


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    value = 100 - 100 / (1 + rs)
    return value.mask((avg_loss == 0) & (avg_gain > 0), 100).mask(
        (avg_gain == 0) & (avg_loss > 0), 0
    )


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


@dataclass(frozen=True)
class MonitorIndicator:
    """A daily feature that a human-authored observation rule may watch.

    Periods and lookbacks come from the locked daily feature family above; this
    catalogue only describes the observation contract (label, unit, threshold
    domain). Price-based values are expressed on the as-of PIT-adjusted basis,
    not the raw exchange price of an older bar.
    """

    key: str
    label: str
    unit: str
    min_threshold: float | None
    max_threshold: float | None
    price_based: bool

    @property
    def min_bars(self) -> int:
        """Bars needed before the last value is fully warmed up."""
        return DAILY_FEATURE_LOOKBACK_BARS[self.key] + 1

    def validate_threshold(self, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{self.label} 阈值必须是数字")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{self.label} 阈值必须是有限数")
        if self.min_threshold is not None and number < self.min_threshold:
            raise ValueError(f"{self.label} 阈值不得低于 {self.min_threshold:g}")
        if self.max_threshold is not None and number > self.max_threshold:
            raise ValueError(f"{self.label} 阈值不得高于 {self.max_threshold:g}")
        return number

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "min_threshold": self.min_threshold,
            "max_threshold": self.max_threshold,
            "min_bars": self.min_bars,
            "price_based": self.price_based,
        }


MONITOR_INDICATORS: tuple[MonitorIndicator, ...] = (
    MonitorIndicator("rsi12", "RSI(12)", "", 0.0, 100.0, False),
    MonitorIndicator("macd_hist", "MACD 柱", "元", None, None, True),
    MonitorIndicator("atr14", "ATR(14)", "元", 0.0, None, True),
    MonitorIndicator("volume_ratio", "量比(5日均量)", "倍", 0.0, None, False),
    MonitorIndicator("kdj_j", "KDJ J", "", None, None, False),
)
MONITOR_INDICATOR_BY_KEY: dict[str, MonitorIndicator] = {
    item.key: item for item in MONITOR_INDICATORS
}
if len(MONITOR_INDICATOR_BY_KEY) != len(MONITOR_INDICATORS) or any(
    item.key not in DAILY_INDICATOR_FIELDS
    or item.key not in DAILY_FEATURE_LOOKBACK_BARS
    for item in MONITOR_INDICATORS
):
    raise RuntimeError("监控指标目录与日线特征字段不一致")


def monitor_indicator_spec(key: object) -> MonitorIndicator:
    """Resolve one supported observation metric, failing closed on anything else."""
    if not isinstance(key, str) or key not in MONITOR_INDICATOR_BY_KEY:
        raise ValueError("不支持的监控指标，请从指标目录中选择")
    return MONITOR_INDICATOR_BY_KEY[key]


def monitor_indicator_catalog() -> list[dict[str, object]]:
    """Serializable catalogue consumed by the /monitor rule editor."""
    return [item.as_dict() for item in MONITOR_INDICATORS]
