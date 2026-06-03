from __future__ import annotations

import numpy as np
import pandas as pd


def enrich_indicators(raw: pd.DataFrame) -> pd.DataFrame:
    df = normalize_ohlcv(raw).copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    amount = df["amount"]

    for window in (5, 10, 20, 60):
        df[f"ma{window}"] = close.rolling(window).mean()
        df[f"vol_ma{window}"] = volume.rolling(window).mean()

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
    df["amount_ma20"] = amount.rolling(20).mean()
    df["range_pos"] = (close - low) / (high - low).replace(0, np.nan)
    df["upper_shadow_pct"] = (high - np.maximum(close, df["open"])) / close * 100
    df["amplitude_pct"] = (high - low) / close * 100
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
