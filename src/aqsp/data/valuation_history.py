"""估值历史（东财 RPT_VALUEANALYSIS_DET）—— 日频 PE/PB/PS/市值 截面。

为什么需要它（2026-11-13 定）：
- 生产 ``config/thresholds.yaml`` 里 ``value`` 子分 ``enabled: false``、权重 0.0
  ⇒ **估值维度整格空着**。research 侧至今唯一站得住的真因子「行业内 PB 分位」
  要进生产，必须补齐两条数据：本模块的**日频估值** + ``industry_pit`` 的
  **申万行业 PIT**（两者皆为「信号日之前可知」的硬前提）。
- ``pit_cache/valuation_history.csv`` 此前是**外部导入**产物（仓库里没有任何
  写入方）⇒ 不可复现、不可续采、来源无从校验。本模块把采集管线正式化。

口径纪律（红线）：
- 估值按**交易日 TRADE_DATE** 落库，代表「当日收盘后可知」的截面。消费方必须
  **滞后 ≥1 个交易日**（``snapshot_asof(lag_trading_days=1)``），否则当日收盘
  数据会泄漏进当日盘中决策。
- 只增不覆盖：同日重复采集靠「缓存里已有该日期就跳过」保证不产生重复行。

取数接口（2026-11-13 实测）：
- host ``datacenter-web.eastmoney.com``，``reportName=RPT_VALUEANALYSIS_DET``；
  ``filter=(TRADE_DATE>='Y')(TRADE_DATE<='Z')`` + ``sortColumns=TRADE_DATE``
  ⇒ 可按区间批量取；单日全市场 5,216 行（pageSize=1000 ⇒ 6 页，约 1.5s/页）。
- ⚠️ **按 SECURITY_CODE 过滤无效**（实测返回「返回数据为空」）⇒ 只能按日期取。
- ⚠️ 区间整段取时**不能一次性堆进内存**（3 年 ≈ 380 万行 ⇒ 数 GB，2G 生产机
  会 OOM）⇒ 本模块按页流式落盘，内存恒定在单页量级。
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import date
from typing import Iterable, Iterator, Optional

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.runtime import runtime_data_root

EM_VALUATION_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_VALUATION_REPORT = "RPT_VALUEANALYSIS_DET"

#: 东财该报表的完整列（实测 columns=ALL 的返回顺序，落盘列序即此序）。
VALUATION_COLUMNS: tuple[str, ...] = (
    "SECURITY_CODE",
    "SECUCODE",
    "SECURITY_NAME_ABBR",
    "ORG_CODE",
    "TRADE_MARKET",
    "BOARD_CODE",
    "BOARD_NAME",
    "ORIG_BOARD_CODE",
    "TOTAL_MARKET_CAP",
    "NOTLIMITED_MARKETCAP_A",
    "CLOSE_PRICE",
    "CHANGE_RATE",
    "TOTAL_SHARES",
    "FREE_SHARES_A",
    "PE_TTM",
    "PE_LAR",
    "PB_MRQ",
    "PCF_OCF_LAR",
    "PCF_OCF_TTM",
    "PS_TTM",
    "PEG_CAR",
    "TRADE_DATE",
)

_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
}

_PAGE_SIZE = 1000
_DEFAULT_TIMEOUT = 60
_logger = logging.getLogger("aqsp.data.valuation_history")


def _norm_day(value: object) -> str:
    """规整成 YYYY-MM-DD；空/非法一律返回空串。"""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("/", "-")
    if " " in text:
        text = text.split(" ", 1)[0]
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def _parse_rows(payload: object) -> list[dict]:
    """东财 datacenter 响应 → 规范行列表。

    ``SECURITY_CODE`` 一律补成 6 位字符串（东财有时给 "600000" 有时给 600000；
    丢前导零会让 "000001" 变成 1，消费方**静默**匹配失败）。
    """
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if isinstance(result, dict):
        data = result.get("data")
    elif isinstance(result, list):
        data = result
    else:
        data = payload.get("data")
    if not isinstance(data, list):
        return []
    rows: list[dict] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        row: dict[str, object] = {}
        for col in VALUATION_COLUMNS:
            row[col] = raw.get(col)
        code = str(row.get("SECURITY_CODE") or "").strip()
        if not code:
            continue
        row["SECURITY_CODE"] = code.zfill(6)
        trade_date = _norm_day(row.get("TRADE_DATE"))
        if not trade_date:
            continue
        row["TRADE_DATE"] = trade_date
        rows.append(row)
    return rows


def _fetch_page(params: dict[str, str], timeout: int) -> tuple[list[dict], int]:
    """取一页；返回 (行, 总页数)。失败抛 DataError。"""
    try:
        import requests
    except ImportError as e:  # pragma: no cover - 依赖缺失属环境态
        raise DataError(f"valuation_history: 缺少依赖 requests（{e}）") from e
    try:
        response = requests.get(
            EM_VALUATION_URL, params=params, headers=_EM_HEADERS, timeout=timeout
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        raise DataError(f"valuation_history: 东财估值抓取失败（{e}）") from e
    rows = _parse_rows(payload)
    pages = 1
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        try:
            pages = max(1, int(result.get("pages") or 1))
        except (TypeError, ValueError):
            pages = 1
    return rows, pages


def _base_params(start: str, end: str, page_size: int) -> dict[str, str]:
    return {
        "reportName": EM_VALUATION_REPORT,
        "columns": "ALL",
        "pageSize": str(page_size),
        "pageNumber": "1",
        "sortColumns": "TRADE_DATE",
        "sortTypes": "1",
        "filter": f"(TRADE_DATE>='{start}')(TRADE_DATE<='{end}')",
        "source": "WEB",
        "client": "WEB",
    }


def iter_date_batches(
    start_date: str,
    end_date: str,
    *,
    page_size: int = _PAGE_SIZE,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Iterator[tuple[str, list[dict]]]:
    """按交易日分批产出 (TRADE_DATE, 行)。

    走**区间**查询（东财只回真实交易日 ⇒ 非交易日自然为空），按页流式取，
    内存恒定在单页量级（3 年 ≈ 380 万行，若一次性堆内存会 OOM 掉生产机）。
    """
    start = _norm_day(start_date)
    end = _norm_day(end_date)
    if not start or not end or start > end:
        raise DataError(f"valuation_history: 非法日期区间 {start_date!r}~{end_date!r}")
    params = _base_params(start, end, page_size)
    page = 1
    total_pages = 1
    current_date = ""
    buffer: list[dict] = []
    while page <= total_pages:
        params["pageNumber"] = str(page)
        rows, pages = _fetch_page(params, timeout)
        total_pages = pages
        for row in rows:
            trade_date = str(row["TRADE_DATE"])
            if current_date and trade_date != current_date:
                yield current_date, buffer
                buffer = []
            current_date = trade_date
            buffer.append(row)
        if not rows:
            break
        page += 1
    if current_date and buffer:
        yield current_date, buffer


def fetch_date_rows(
    trade_date: str,
    *,
    page_size: int = _PAGE_SIZE,
    timeout: int = _DEFAULT_TIMEOUT,
) -> list[dict]:
    """取单个交易日的全市场估值截面（稀疏回填用；一次 6 页左右）。"""
    day = _norm_day(trade_date)
    if not day:
        raise DataError(f"valuation_history: 非法日期 {trade_date!r}")
    params = _base_params(day, day, page_size)
    rows: list[dict] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        params["pageNumber"] = str(page)
        batch, pages = _fetch_page(params, timeout)
        total_pages = pages
        rows.extend(batch)
        if not batch:
            break
        page += 1
    return rows


class ValuationHistorySource:
    """日频估值截面源：取数 + 本地缓存 + 时点查询。

    缓存落 ``<runtime_data_root>/pit_cache/valuation_history.csv``（与既有
    同名文件同 schema ⇒ 消费方与 research 侧产物零改动对接）。
    """

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._df: Optional[pd.DataFrame] = None

    # ── 路径 ────────────────────────────────────────────────────────────
    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        root = runtime_data_root()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "valuation_history.csv")

    # ── 读 ──────────────────────────────────────────────────────────────
    def _read_cache(self) -> pd.DataFrame:
        path = self._default_cache_path()
        if not os.path.exists(path):
            return pd.DataFrame(columns=list(VALUATION_COLUMNS))
        df = pd.read_csv(path, dtype={"SECURITY_CODE": str})
        if "SECURITY_CODE" in df.columns:
            df["SECURITY_CODE"] = (
                df["SECURITY_CODE"].astype(str).str.strip().str.zfill(6)
            )
        if "TRADE_DATE" in df.columns:
            df["TRADE_DATE"] = df["TRADE_DATE"].astype(str).str.slice(0, 10)
        return df

    def load_cached(self) -> bool:
        """仅从本地缓存加载（**绝不联网**）；命中返回 True。"""
        if not os.path.exists(self._default_cache_path()):
            return False
        try:
            self._df = self._read_cache()
            return True
        except Exception:
            self._df = None
            return False

    def load(self) -> pd.DataFrame:
        """读缓存为 DataFrame（不联网）。"""
        if self._df is None:
            self._df = self._read_cache()
        return self._df

    def cached_dates(self) -> set[str]:
        """缓存里已有的交易日集合（增量采集的去重依据）。"""
        path = self._default_cache_path()
        if not os.path.exists(path):
            return set()
        try:
            df = pd.read_csv(path, usecols=["TRADE_DATE"])
        except Exception:
            return set()
        return {d for d in (df["TRADE_DATE"].astype(str).str.slice(0, 10)) if d}

    # ── 写 ──────────────────────────────────────────────────────────────
    def _append_rows(self, rows: list[dict]) -> int:
        """把行**追加**进缓存（流式写，内存恒定）。返回写入行数。"""
        if not rows:
            return 0
        path = self._default_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        exists = os.path.exists(path) and os.path.getsize(path) > 0
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(VALUATION_COLUMNS))
            if not exists:
                writer.writeheader()
            for row in rows:
                writer.writerow({col: row.get(col) for col in VALUATION_COLUMNS})
        self._df = None
        return len(rows)

    def sync_dates(self, dates: Iterable[str], *, timeout: int = _DEFAULT_TIMEOUT) -> int:
        """采集指定交易日的截面，**跳过缓存里已有的日期**。返回新增行数。"""
        known = self.cached_dates()
        added = 0
        for raw_day in dates:
            day = _norm_day(raw_day)
            if not day or day in known:
                continue
            rows = fetch_date_rows(day, timeout=timeout)
            if not rows:
                continue
            added += self._append_rows(rows)
            known.add(day)
        return added

    def sync_range(
        self,
        start_date: str,
        end_date: str,
        *,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> int:
        """采集整个日期区间（流式分页，内存恒定）。返回新增行数。"""
        known = self.cached_dates()
        added = 0
        for day, rows in iter_date_batches(start_date, end_date, timeout=timeout):
            if day in known:
                continue
            added += self._append_rows(rows)
            known.add(day)
        return added

    # ── 时点查询 ────────────────────────────────────────────────────────
    def snapshot_asof(
        self,
        as_of: str,
        *,
        lag_trading_days: int = 1,
    ) -> pd.DataFrame:
        """取「不晚于 as_of、且已滞后 lag 个交易日」的最近一个估值截面。

        ``lag_trading_days=1``（默认）杜绝「当日收盘数据泄漏进当日盘中决策」。
        滞后按**缓存自身的交易日序列**计（每个 TRADE_DATE 即一个已采集的交易日），
        不依赖外部交易日历 ⇒ 纯离线、可复现。
        """
        day = _norm_day(as_of)
        if not day:
            raise DataError(f"valuation_history: 非法日期 {as_of!r}")
        df = self.load()
        if df.empty or "TRADE_DATE" not in df.columns:
            return pd.DataFrame(columns=list(VALUATION_COLUMNS))
        available = sorted({d for d in df["TRADE_DATE"].tolist() if d <= day})
        if not available:
            return pd.DataFrame(columns=list(VALUATION_COLUMNS))
        idx = len(available) - 1 - max(0, lag_trading_days)
        cutoff = available[max(idx, 0)]
        return df[df["TRADE_DATE"] == cutoff].reset_index(drop=True)
