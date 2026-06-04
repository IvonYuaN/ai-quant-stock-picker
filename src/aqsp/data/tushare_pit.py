from __future__ import annotations

import contextlib
import io
import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from aqsp.core.errors import DataError


@dataclass(frozen=True)
class TusharePitConfig:
    token: str


class TusharePitClient:
    def __init__(self, config: TusharePitConfig | None = None) -> None:
        cfg = config or load_tushare_pit_config()
        try:
            import tushare as ts

            self._pro = ts.pro_api(cfg.token)
        except ImportError as exc:
            raise RuntimeError(
                "tushare is not installed; run: pip install -e '.[data]'"
            ) from exc
        self._token = cfg.token

    def _safe_pro_call(self, method_name: str, **kwargs: Any) -> pd.DataFrame:
        method = getattr(self._pro, method_name)
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(
                stderr_buffer
            ):
                return method(**kwargs)
        except Exception as exc:
            captured = " ".join(
                part.strip()
                for part in (stdout_buffer.getvalue(), stderr_buffer.getvalue())
                if part.strip()
            )
            if captured:
                raise DataError(f"tushare 接口异常: {captured}") from exc
            raise

    def fetch_trade_calendar(
        self,
        start: date,
        end: date,
        exchange: str = "SSE",
    ) -> pd.DataFrame:
        try:
            df = self._safe_pro_call(
                "trade_cal",
                exchange=exchange,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as exc:
            raise DataError(f"tushare 交易日历获取失败: {exc}") from exc
        if df is None or df.empty:
            raise DataError("tushare 交易日历为空")
        return self._normalize_trade_calendar(df, exchange)

    def fetch_index_weights(
        self,
        index_code: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        try:
            df = self._safe_pro_call(
                "index_weight",
                index_code=index_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as exc:
            raise DataError(f"tushare 指数成分获取失败: {index_code} - {exc}") from exc
        if df is None or df.empty:
            raise DataError(f"tushare 指数成分为空: {index_code}")
        return self._normalize_index_weights(df, index_code)

    def fetch_disclosure_dates(
        self,
        symbols: list[str],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        rows: list[pd.DataFrame] = []
        for symbol in symbols:
            ts_code = symbol_to_ts_code(symbol)
            try:
                df = self._safe_pro_call(
                    "disclosure_date",
                    ts_code=ts_code,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
            except Exception as exc:
                raise DataError(f"tushare 披露日获取失败: {symbol} - {exc}") from exc
            if df is None or df.empty:
                continue
            rows.append(self._normalize_disclosure_dates(df, symbol, ts_code))
        if not rows:
            raise DataError("tushare 披露日为空")
        return pd.concat(rows, ignore_index=True)

    def _normalize_trade_calendar(
        self,
        df: pd.DataFrame,
        exchange: str,
    ) -> pd.DataFrame:
        normalized = df.copy()
        required = {"cal_date", "is_open", "pretrade_date"}
        missing = required - set(normalized.columns)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise DataError(f"tushare 交易日历缺列: {missing_text}")
        normalized["exchange"] = exchange
        normalized["cal_date"] = pd.to_datetime(
            normalized["cal_date"], format="%Y%m%d", errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        normalized["pretrade_date"] = pd.to_datetime(
            normalized["pretrade_date"], format="%Y%m%d", errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        normalized["is_open"] = (
            pd.to_numeric(normalized["is_open"], errors="coerce").fillna(0).astype(int)
        )
        return normalized[["exchange", "cal_date", "is_open", "pretrade_date"]]

    def _normalize_index_weights(
        self,
        df: pd.DataFrame,
        index_code: str,
    ) -> pd.DataFrame:
        normalized = df.copy()
        required = {"con_code", "trade_date", "weight"}
        missing = required - set(normalized.columns)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise DataError(f"tushare 指数成分缺列: {missing_text}")
        normalized["index_code"] = index_code
        normalized["trade_date"] = pd.to_datetime(
            normalized["trade_date"], format="%Y%m%d", errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        normalized["weight"] = pd.to_numeric(normalized["weight"], errors="coerce")
        normalized["symbol"] = normalized["con_code"].astype(str).map(ts_code_to_symbol)
        return (
            normalized[["index_code", "trade_date", "con_code", "symbol", "weight"]]
            .sort_values(["trade_date", "con_code"])
            .reset_index(drop=True)
        )

    def _normalize_disclosure_dates(
        self,
        df: pd.DataFrame,
        symbol: str,
        ts_code: str,
    ) -> pd.DataFrame:
        normalized = df.copy()
        required = {"end_date", "ann_date"}
        missing = required - set(normalized.columns)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise DataError(f"tushare 披露日缺列: {missing_text}")
        normalized["ts_code"] = normalized.get("ts_code", ts_code)
        normalized["symbol"] = symbol
        for column in ("end_date", "ann_date", "actual_date", "modify_date"):
            if column in normalized.columns:
                normalized[column] = pd.to_datetime(
                    normalized[column], format="%Y%m%d", errors="coerce"
                ).dt.strftime("%Y-%m-%d")
        keep = [
            column
            for column in (
                "ts_code",
                "symbol",
                "end_date",
                "ann_date",
                "actual_date",
                "modify_date",
            )
            if column in normalized.columns
        ]
        return (
            normalized[keep]
            .sort_values(["end_date", "ann_date"])
            .reset_index(drop=True)
        )


def load_tushare_pit_config() -> TusharePitConfig:
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        raise ValueError("TUSHARE_TOKEN is required for tushare PIT data")
    return TusharePitConfig(token=token)


def symbol_to_ts_code(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        suffix = "SH"
    elif symbol.startswith(("8", "4")):
        suffix = "BJ"
    else:
        suffix = "SZ"
    return f"{symbol}.{suffix}"


def ts_code_to_symbol(ts_code: Any) -> str:
    text = str(ts_code).strip()
    if "." not in text:
        return text
    return text.split(".", maxsplit=1)[0]


def is_open_day_in_calendar(calendar_df: pd.DataFrame, target: date) -> bool:
    normalized = _calendar_dates(calendar_df)
    matched = normalized[normalized["cal_date"] == target.isoformat()]
    if matched.empty:
        return False
    return bool(int(matched.iloc[-1]["is_open"]) == 1)


def previous_trade_date_from_calendar(
    calendar_df: pd.DataFrame,
    target: date,
) -> date:
    normalized = _calendar_dates(calendar_df)
    open_days = normalized[
        (normalized["is_open"] == 1) & (normalized["cal_date"] < target.isoformat())
    ]
    if open_days.empty:
        raise DataError(f"calendar 中找不到 {target.isoformat()} 之前的交易日")
    return date.fromisoformat(str(open_days.iloc[-1]["cal_date"]))


def next_trade_date_from_calendar(
    calendar_df: pd.DataFrame,
    target: date,
) -> date:
    normalized = _calendar_dates(calendar_df)
    open_days = normalized[
        (normalized["is_open"] == 1) & (normalized["cal_date"] > target.isoformat())
    ]
    if open_days.empty:
        raise DataError(f"calendar 中找不到 {target.isoformat()} 之后的交易日")
    return date.fromisoformat(str(open_days.iloc[0]["cal_date"]))


def overlay_disclosure_dates(
    financial_df: pd.DataFrame,
    disclosure_df: pd.DataFrame,
) -> pd.DataFrame:
    if financial_df.empty or disclosure_df.empty:
        return financial_df.copy()
    normalized_fin = financial_df.copy()
    normalized_disclosure = disclosure_df.copy()
    for column in ("end_date", "ann_date", "actual_date", "modify_date"):
        if column in normalized_disclosure.columns:
            normalized_disclosure[column] = pd.to_datetime(
                normalized_disclosure[column], errors="coerce"
            ).dt.strftime("%Y-%m-%d")
    if "statDate" in normalized_fin.columns:
        normalized_fin["statDate"] = pd.to_datetime(
            normalized_fin["statDate"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
    merge_key = "statDate" if "statDate" in normalized_fin.columns else "end_date"
    merged = normalized_fin.merge(
        normalized_disclosure[
            [
                column
                for column in ("symbol", "end_date", "ann_date", "actual_date")
                if column in normalized_disclosure.columns
            ]
        ],
        how="left",
        left_on=["symbol", merge_key]
        if "symbol" in normalized_fin.columns
        else [merge_key],
        right_on=["symbol", "end_date"]
        if "symbol" in normalized_disclosure.columns
        and "symbol" in normalized_fin.columns
        else ["end_date"],
        suffixes=("", "_tushare"),
    )
    if "actual_date" in merged.columns:
        merged["pubDate"] = (
            merged["actual_date"]
            .fillna(merged.get("ann_date"))
            .fillna(merged.get("pubDate"))
        )
    elif "ann_date" in merged.columns:
        merged["pubDate"] = merged["ann_date"].fillna(merged.get("pubDate"))
    return merged


def _calendar_dates(calendar_df: pd.DataFrame) -> pd.DataFrame:
    normalized = calendar_df.copy()
    normalized["cal_date"] = pd.to_datetime(
        normalized["cal_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    normalized["is_open"] = (
        pd.to_numeric(normalized["is_open"], errors="coerce").fillna(0).astype(int)
    )
    return normalized.sort_values("cal_date").reset_index(drop=True)
