"""业绩预告（东财）—— 事件日历数据源。

移植自 akshare `stock_yjyg_em`（东财数据中心-年报季报-业绩预告）。
按 AQSP 改造：纯解析 + lazy 网络 + 单条容错。

字段来源：2026-09-22 生产机实测。
reportName=RPT_PUBLIC_OP_NEWPREDICT，宿主为 securities 接口：
`https://datacenter.eastmoney.com/securities/api/data/v1/get`
（与 lockup/longhubang 的 datacenter-web 不是同一 host，akshare 亦如此）。
filter 必须带报告期等值：`(REPORT_DATE='YYYY-MM-DD')`。
可选 `notice_from` 追加 `(NOTICE_DATE>='YYYY-MM-DD')`，把结果集收窄到「最近发布」
的小窗口（事件日历只需要这一片，也顺带规避 pageSize 截断）。
字段名：SECURITY_CODE / SECURITY_NAME_ABBR / NOTICE_DATE / REPORT_DATE /
PREDICT_TYPE（预告类型，如「扭亏」「预增」）/ PREDICT_AMT_LOWER /
PREDICT_AMT_UPPER（预计净利润上下限，单位「元」）/ ADD_AMP_LOWER /
ADD_AMP_UPPER（业绩变动幅度上下限，单位 %）/ CHANGE_REASON_EXPLAIN（变动原因）/
IS_LATEST（是否该股该报告期的最新一条，东财原值为 "T"/"F"）。

截断守卫：单页取 `pageSize=500`，页满时置 `source.truncated=True` 并 warning
（该报表单报告期实测约 5000 条，不加 `notice_from` 时**很可能触发**，
请务必用 `notice_from` 收窄窗口）。绝不静默少拿数据。
"""

from __future__ import annotations

import logging
import os
from aqsp.core.runtime import runtime_data_root
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.time import today_shanghai

# 东财业绩预告（2026-09-22 生产机实测：RPT_PUBLIC_OP_NEWPREDICT，
# 必须带 filter=(REPORT_DATE='YYYY-MM-DD')；单报告期约 5000 条）
EM_EF_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
EM_EF_REPORT = "RPT_PUBLIC_OP_NEWPREDICT"
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
_PAGE_SIZE = 500
_logger = logging.getLogger("aqsp.data.earnings_forecast")


@dataclass(frozen=True)
class EarningsForecastItem:
    """单条业绩预告（股票-报告期-公告粒度）。"""

    symbol: str
    name: str
    notice_date: str  # 公告日 YYYY-MM-DD
    report_date: str  # 报告期 YYYY-MM-DD
    forecast_type: str  # 预告类型：预增/预减/扭亏/首亏/续盈…
    forecast_amt_lower: float  # 预计净利润下限（万元）
    forecast_amt_upper: float  # 预计净利润上限（万元）
    change_pct_lower: float  # 业绩变动幅度下限（%）
    change_pct_upper: float  # 业绩变动幅度上限（%）
    reason: str  # 业绩变动原因
    is_latest: bool  # 是否该股该报告期最新修订（东财 IS_LATEST=="T"）


def _to_float(v: object) -> float:
    try:
        if v in (None, ""):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _norm_date(v: object) -> str:
    """东财日期形如 "2026-08-19 00:00:00" → 截断为 YYYY-MM-DD。"""
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


def _is_truncated(parsed_count: int, page_size: int) -> bool:
    """页满即可能被 pageSize 截断 —— 用于避免「静默少拿数据」。"""
    return parsed_count >= page_size


def _parse_items(payload: object) -> list[EarningsForecastItem]:
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
    out: list[EarningsForecastItem] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            symbol = str(raw.get("SECURITY_CODE") or raw.get("symbol") or "").strip()
            if not symbol:
                continue
            out.append(
                EarningsForecastItem(
                    symbol=symbol,
                    name=str(
                        raw.get("SECURITY_NAME_ABBR") or raw.get("name") or ""
                    ).strip(),
                    notice_date=_norm_date(
                        raw.get("NOTICE_DATE") or raw.get("notice_date")
                    ),
                    report_date=_norm_date(
                        raw.get("REPORT_DATE") or raw.get("report_date")
                    ),
                    forecast_type=str(
                        raw.get("PREDICT_TYPE") or raw.get("forecast_type") or ""
                    ).strip(),
                    # 东财原始单位为「元」，统一换算为「万元」
                    forecast_amt_lower=_to_float(
                        raw.get("PREDICT_AMT_LOWER") or raw.get("forecast_amt_lower")
                    )
                    / 1e4,
                    forecast_amt_upper=_to_float(
                        raw.get("PREDICT_AMT_UPPER") or raw.get("forecast_amt_upper")
                    )
                    / 1e4,
                    change_pct_lower=_to_float(
                        raw.get("ADD_AMP_LOWER") or raw.get("change_pct_lower")
                    ),
                    change_pct_upper=_to_float(
                        raw.get("ADD_AMP_UPPER") or raw.get("change_pct_upper")
                    ),
                    reason=str(
                        raw.get("CHANGE_REASON_EXPLAIN") or raw.get("reason") or ""
                    ).strip(),
                    # 东财原值为 "T"/"F"；缺失/异常一律视为非最新
                    is_latest=str(raw.get("IS_LATEST") or "").strip().upper() == "T",
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


class EarningsForecastSource:
    """业绩预告源：取数 + 本地缓存。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[EarningsForecastItem] = []
        # 上一次 _fetch 的结果是否疑似被 pageSize 截断（真截断时已 warning）
        self.truncated = False

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目 runtime data root 约定；未配置时落系统临时目录，避免污染源码树
        root = runtime_data_root()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "earnings_forecast.csv")

    def from_items(self, items: list[EarningsForecastItem]) -> "EarningsForecastSource":
        self._items = list(items)
        return self

    def _fetch(
        self, report_date: str = "", notice_from: str = ""
    ) -> list[EarningsForecastItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"earnings_forecast: 缺少依赖 requests（{e}）") from e
        period = report_date.strip() or _default_report_period(today_shanghai())
        # 东财 filter 为 (A)(B) 拼接形式；notice_from 用来把窗口收窄到「最近发布」
        filters = [f"(REPORT_DATE='{period}')"]
        notice = notice_from.strip()
        if notice:
            filters.append(f"(NOTICE_DATE>='{notice}')")
        params: dict[str, str] = {
            "reportName": EM_EF_REPORT,
            "columns": "ALL",
            "pageSize": str(_PAGE_SIZE),
            "pageNumber": "1",
            "sortColumns": "NOTICE_DATE,SECURITY_CODE",
            "sortTypes": "-1,-1",
            "filter": "".join(filters),
        }
        try:
            r = requests.get(EM_EF_URL, params=params, headers=_EM_HEADERS, timeout=60)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            raise DataError(f"earnings_forecast: 东财业绩预告抓取失败（{e}）") from e
        items = _parse_items(payload)
        self.truncated = _is_truncated(len(items), _PAGE_SIZE)
        if self.truncated:
            _logger.warning(
                "earnings_forecast: 结果可能被 pageSize=%d 截断（实得 %d 条），"
                "请收窄 filter（如传 notice_from）或加分页",
                _PAGE_SIZE,
                len(items),
            )
        return items

    def load(
        self, force: bool = False, report_date: str = "", notice_from: str = ""
    ) -> list[EarningsForecastItem]:
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                # dtype 固定 symbol 为 str：否则 "000001" 会被推断成 1，丢前导零
                df = pd.read_csv(path, dtype={"symbol": str})
                self._items = [
                    EarningsForecastItem(**row) for row in df.to_dict("records")
                ]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch(report_date=report_date, notice_from=notice_from)
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(
        self, autoload: bool = False, report_date: str = "", notice_from: str = ""
    ) -> list[EarningsForecastItem]:
        if not self._items and autoload:
            self.load(report_date=report_date, notice_from=notice_from)
        return list(self._items)
