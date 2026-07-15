from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict

import pandas as pd
import numpy as np

try:
    import baostock as bs
except ModuleNotFoundError:
    bs = None

from aqsp.core.errors import DataError
from aqsp.data.cache import DataCache
from aqsp.data.pit_policy import PitTimestampPolicy, validate_point_in_time_frame
from aqsp.data.source_health import record_source_auth
from aqsp.data.tushare_pit import TusharePitClient, overlay_disclosure_dates

_REQUEST_DELAY = 0.05
_BS_LOGGED_IN = False


def _ensure_baostock_login() -> bool:
    global _BS_LOGGED_IN
    if bs is None:
        record_source_auth(
            "baostock",
            "missing_package",
            "baostock is not installed; run: pip install -e '.[data]'",
        )
        return False
    if _BS_LOGGED_IN:
        record_source_auth("baostock", "ok", "baostock 登录成功，复用已有会话。")
        return True
    try:
        result = bs.login()
    except Exception as exc:
        record_source_auth("baostock", "login_failed", f"baostock 登录异常: {exc}")
        return False
    if getattr(result, "error_code", "0") != "0":
        message = str(getattr(result, "error_msg", "") or "").strip()
        record_source_auth(
            "baostock",
            "login_failed",
            f"baostock 登录失败: {message or 'unknown error'}",
        )
        return False
    _BS_LOGGED_IN = True
    record_source_auth("baostock", "ok", "baostock 登录成功。")
    return True


@dataclass(frozen=True)
class PitEnrichmentResult:
    frames: Dict[str, pd.DataFrame]
    financial_symbol_count: int
    disclosure_symbol_count: int
    source_statuses: tuple["PitSourceStatus", ...] = ()


@dataclass(frozen=True)
class PitSourceStatus:
    source_id: str
    status: str
    message: str


def fetch_pit_financials(
    symbols: list[str],
    start_year: int,
    end_year: int,
    cache: DataCache | None = None,
) -> Dict[str, pd.DataFrame]:
    data, _ = _fetch_pit_financials_with_status(
        symbols,
        start_year,
        end_year,
        cache=cache,
    )
    return data


def _fetch_pit_financials_with_status(
    symbols: list[str],
    start_year: int,
    end_year: int,
    cache: DataCache | None = None,
) -> tuple[Dict[str, pd.DataFrame], PitSourceStatus]:
    if not _ensure_baostock_login():
        return (
            {},
            PitSourceStatus(
                source_id="baostock",
                status="missing_package" if bs is None else "login_failed",
                message=(
                    "baostock is not installed; run: pip install -e '.[data]'"
                    if bs is None
                    else "baostock 未登录成功，财务补充已跳过。"
                ),
            ),
        )
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        if cache:
            cached = cache.get_financial(symbol)
            if cached is not None and not cached.empty:
                out[symbol] = cached
                continue

        rows = _fetch_all_quarters(symbol, start_year, end_year)
        if rows:
            df = pd.DataFrame(rows)
            df["symbol"] = symbol
            df = _clean_financial_df(df)
            if cache:
                cache.set_financial(symbol, df, source="baostock")
            out[symbol] = df
    return (
        out,
        PitSourceStatus(
            source_id="baostock",
            status="ok",
            message=f"baostock 财务数据可用，覆盖 {len(out)} 只标的。",
        ),
    )


def merge_pit_financials(
    ohlcv_data: Dict[str, pd.DataFrame],
    financial_data: Dict[str, pd.DataFrame],
    disclosure_data: dict[str, pd.DataFrame] | None = None,
) -> Dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for symbol, ohlcv in ohlcv_data.items():
        if symbol not in financial_data or financial_data[symbol].empty:
            ohlcv = ohlcv.copy()
            for col in ["roe", "roa", "operating_margin", "pe", "pb"]:
                ohlcv[col] = np.nan
            out[symbol] = ohlcv
            continue

        fin = financial_data[symbol].copy()
        if (
            disclosure_data
            and symbol in disclosure_data
            and not disclosure_data[symbol].empty
        ):
            fin = overlay_disclosure_dates(fin, disclosure_data[symbol])
        fin = validate_point_in_time_frame(
            fin,
            PitTimestampPolicy(
                artifact_type=f"{symbol} financials",
                timestamp_columns=("pubDate",),
            ),
        )
        ohlcv = ohlcv.copy()
        ohlcv["date"] = pd.to_datetime(ohlcv["date"])
        fin["pubDate"] = pd.to_datetime(fin["pubDate"])

        fin_sorted = fin.sort_values("pubDate").drop_duplicates(
            subset=["pubDate"], keep="last"
        )

        merged = pd.merge_asof(
            ohlcv.sort_values("date"),
            fin_sorted[
                ["pubDate", "roeAvg", "gpMargin", "epsTTM", "totalShare"]
            ].rename(columns={"pubDate": "date"}),
            on="date",
            direction="backward",
        )

        merged["roe"] = pd.to_numeric(merged.get("roeAvg"), errors="coerce")
        merged["operating_margin"] = pd.to_numeric(
            merged.get("gpMargin"), errors="coerce"
        )
        eps = pd.to_numeric(merged.get("epsTTM"), errors="coerce")
        close = pd.to_numeric(merged["close"], errors="coerce")

        merged["pe"] = np.where(
            (eps > 0) & np.isfinite(eps),
            close / eps,
            np.nan,
        )
        bvps = np.where(
            (merged["roe"] > 0) & np.isfinite(eps),
            eps / merged["roe"],
            np.nan,
        )
        merged["pb"] = np.where(
            (bvps > 0) & np.isfinite(bvps),
            close / bvps,
            np.nan,
        )
        merged["roa"] = np.nan
        merged["debt_ratio"] = np.nan
        merged["dividend_yield"] = np.nan

        merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")
        out[symbol] = merged

    return out


def load_optional_disclosure_data(
    symbols: list[str],
    start: date,
    end: date,
    client: TusharePitClient | None = None,
) -> dict[str, pd.DataFrame]:
    data, _ = _load_optional_disclosure_data_with_status(symbols, start, end, client)
    return data


def _load_optional_disclosure_data_with_status(
    symbols: list[str],
    start: date,
    end: date,
    client: TusharePitClient | None = None,
) -> tuple[dict[str, pd.DataFrame], PitSourceStatus]:
    try:
        pit_client = client or TusharePitClient()
    except RuntimeError as exc:
        record_source_auth("tushare", "missing_package", str(exc))
        return (
            {},
            PitSourceStatus(
                source_id="tushare",
                status="missing_package",
                message=str(exc),
            ),
        )
    except ValueError as exc:
        record_source_auth("tushare", "missing_env", str(exc))
        return (
            {},
            PitSourceStatus(
                source_id="tushare",
                status="missing_env",
                message=str(exc),
            ),
        )
    try:
        disclosure_df = pit_client.fetch_disclosure_dates(symbols, start, end)
    except DataError as exc:
        record_source_auth("tushare", "request_failed", str(exc))
        return (
            {},
            PitSourceStatus(
                source_id="tushare",
                status="request_failed",
                message=str(exc),
            ),
        )
    if disclosure_df.empty or "symbol" not in disclosure_df.columns:
        record_source_auth("tushare", "empty", "Tushare 披露日为空。")
        return (
            {},
            PitSourceStatus(
                source_id="tushare",
                status="empty",
                message="Tushare 披露日为空。",
            ),
        )
    grouped = {
        str(symbol): part.reset_index(drop=True)
        for symbol, part in disclosure_df.groupby("symbol")
    }
    record_source_auth(
        "tushare",
        "ok",
        f"Tushare 披露日可用，覆盖 {len(grouped)} 只标的。",
    )
    return (
        grouped,
        PitSourceStatus(
            source_id="tushare",
            status="ok",
            message=f"Tushare 披露日可用，覆盖 {len(grouped)} 只标的。",
        ),
    )


def enrich_ohlcv_with_pit_financials(
    ohlcv_data: Dict[str, pd.DataFrame],
    symbols: list[str],
    start: date,
    end: date,
    cache: DataCache | None = None,
) -> PitEnrichmentResult:
    financial_data, financial_status = _fetch_pit_financials_with_status(
        symbols,
        start.year,
        end.year,
        cache=cache,
    )
    disclosure_data, disclosure_status = _load_optional_disclosure_data_with_status(
        symbols,
        start,
        end,
    )
    merged = merge_pit_financials(
        ohlcv_data,
        financial_data,
        disclosure_data=disclosure_data,
    )
    return PitEnrichmentResult(
        frames=merged,
        financial_symbol_count=len(financial_data),
        disclosure_symbol_count=len(disclosure_data),
        source_statuses=(financial_status, disclosure_status),
    )


def _fetch_all_quarters(symbol: str, start_year: int, end_year: int) -> list[dict]:
    bs_code = f"sh.{symbol}" if symbol.startswith("6") else f"sz.{symbol}"
    rows = []
    for year in range(start_year, end_year + 1):
        for quarter in range(1, 5):
            try:
                rs = bs.query_profit_data(code=bs_code, year=year, quarter=quarter)
                while (rs.error_code == "0") and rs.next():
                    row = rs.get_row_data()
                    if row:
                        rows.append(dict(zip(rs.fields, row)))
            except Exception:
                continue
    return rows


def _clean_financial_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["pubDate"] = pd.to_datetime(df["pubDate"], errors="coerce")
    df["statDate"] = pd.to_datetime(df["statDate"], errors="coerce")
    df = df.dropna(subset=["pubDate"])
    for col in ["roeAvg", "npMargin", "gpMargin", "epsTTM", "totalShare"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("pubDate")
    return df
