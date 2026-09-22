"""分红送转（东财）—— 事件日历数据源。

移植自 akshare `stock_fhps_em`（东财数据中心-年报季报-分红送配）。
按 AQSP 改造：纯解析 + lazy 网络 + 单条容错。

字段来源：2026-09-22 生产机实测。
reportName=RPT_SHAREBONUS_DET（datacenter-web 通用接口）。
filter 需带报告期等值：`(REPORT_DATE='YYYY-MM-DD')`。
字段名：SECURITY_CODE / SECURITY_NAME_ABBR / REPORT_DATE / PLAN_NOTICE_DATE /
EX_DIVIDEND_DATE（可能为未来日期）/ BONUS_IT_RATIO（每 10 股送转合计股数）/
PRETAX_BONUS_RMB（每 10 股税前派息，元）/ ASSIGN_PROGRESS（方案进度）。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai

# 东财分红送转（2026-09-22 生产机实测：RPT_SHAREBONUS_DET，
# 需 filter=(REPORT_DATE='YYYY-MM-DD')；单报告期约 3600 条）
EM_DIVIDEND_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_DIVIDEND_REPORT = "RPT_SHAREBONUS_DET"
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}


@dataclass(frozen=True)
class DividendPlanItem:
    """单条分红送转方案（股票-报告期粒度）。"""

    symbol: str
    name: str
    report_date: str  # 报告期 YYYY-MM-DD
    plan_notice_date: str  # 预案公告日 YYYY-MM-DD
    ex_dividend_date: str  # 除权除息日 YYYY-MM-DD（可能是未来日期）
    bonus_ratio: float  # 每 10 股送转合计股数
    cash_per_10: float  # 每 10 股税前派息（元）
    progress: str  # 方案进度：预案/股东大会通过/实施分配…


def _to_float(v: object) -> float:
    try:
        if v in (None, ""):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _norm_date(v: object) -> str:
    """东财日期形如 "2026-07-29 00:00:00" → 截断为 YYYY-MM-DD。"""
    if v in (None, ""):
        return ""
    s = str(v).strip()
    return s[:10]


def _default_report_period(today: date) -> str:
    """最近一个「已过法定披露截止」的报告期，避免默认取到尚未披露的季度。"""
    if today.month >= 11:
        return f"{today.year}-09-30"
    if today.month >= 9:
        return f"{today.year}-06-30"
    if today.month >= 5:
        return f"{today.year}-03-31"
    return f"{today.year - 1}-12-31"


def _parse_items(payload: object) -> list[DividendPlanItem]:
    """东财 datacenter 响应：{"result": {"data": [...]}}。脏行（无代码）跳过。"""
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if isinstance(result, dict):
        data = result.get("data")
    elif isinstance(result, list):
        data = result
    else:
        data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    out: list[DividendPlanItem] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            symbol = str(raw.get("SECURITY_CODE") or raw.get("symbol") or "").strip()
            if not symbol:
                continue
            out.append(
                DividendPlanItem(
                    symbol=symbol,
                    name=str(
                        raw.get("SECURITY_NAME_ABBR") or raw.get("name") or ""
                    ).strip(),
                    report_date=_norm_date(
                        raw.get("REPORT_DATE") or raw.get("report_date")
                    ),
                    plan_notice_date=_norm_date(
                        raw.get("PLAN_NOTICE_DATE") or raw.get("plan_notice_date")
                    ),
                    ex_dividend_date=_norm_date(
                        raw.get("EX_DIVIDEND_DATE") or raw.get("ex_dividend_date")
                    ),
                    bonus_ratio=_to_float(
                        raw.get("BONUS_IT_RATIO") or raw.get("bonus_ratio")
                    ),
                    cash_per_10=_to_float(
                        raw.get("PRETAX_BONUS_RMB") or raw.get("cash_per_10")
                    ),
                    progress=str(
                        raw.get("ASSIGN_PROGRESS") or raw.get("progress") or ""
                    ).strip(),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


class DividendPlanSource:
    """分红送转源：取数 + 本地缓存。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[DividendPlanItem] = []

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目 runtime data root 约定；未配置时落系统临时目录，避免污染源码树
        root = os.environ.get("AQSP_RUNTIME_DATA_ROOT") or tempfile.gettempdir()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "dividend_plan.csv")

    def from_items(self, items: list[DividendPlanItem]) -> "DividendPlanSource":
        self._items = list(items)
        return self

    def _fetch(self, report_date: str = "") -> list[DividendPlanItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"dividend_plan: 缺少依赖 requests（{e}）") from e
        period = report_date.strip() or _default_report_period(today_shanghai())
        params: dict[str, str] = {
            "reportName": EM_DIVIDEND_REPORT,
            "columns": "ALL",
            "pageSize": "500",
            "pageNumber": "1",
            "sortColumns": "PLAN_NOTICE_DATE",
            "sortTypes": "-1",
            "quoteColumns": "",
            "source": "WEB",
            "client": "WEB",
            "filter": f"(REPORT_DATE='{period}')",
        }
        try:
            r = requests.get(
                EM_DIVIDEND_URL, params=params, headers=_EM_HEADERS, timeout=60
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            raise DataError(f"dividend_plan: 东财分红送转抓取失败（{e}）") from e
        return _parse_items(payload)

    def load(
        self, force: bool = False, report_date: str = ""
    ) -> list[DividendPlanItem]:
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                # dtype 固定 symbol 为 str：否则 "000001" 会被推断成 1，丢前导零
                df = pd.read_csv(path, dtype={"symbol": str})
                self._items = [DividendPlanItem(**row) for row in df.to_dict("records")]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch(report_date=report_date)
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(
        self, autoload: bool = False, report_date: str = ""
    ) -> list[DividendPlanItem]:
        if not self._items and autoload:
            self.load(report_date=report_date)
        return list(self._items)
