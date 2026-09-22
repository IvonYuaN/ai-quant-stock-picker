"""停复牌（东财）—— 事件日历数据源。

移植自 akshare `stock_tfp_em`（东财数据中心-特色数据-停复牌信息）。
按 AQSP 改造：纯解析 + lazy 网络 + 单条容错。

字段来源：2026-09-22 生产机实测。
reportName=RPT_CUSTOM_SUSPEND_DATA_INTERFACE（datacenter-web 通用接口）。
该报表必须带 `filter`，且 `DATETIME` 是「查询日」虚拟列，仅支持等值：
`(MARKET="全部")(DATETIME='YYYY-MM-DD')`，返回该日处于停牌/复牌流程的记录。
字段名：SECURITY_CODE / SECURITY_NAME_ABBR / SUSPEND_START_DATE /
PREDICT_RESUME_DATE / SUSPEND_EXPIRE（停牌期限文本，如「连续停牌」）/
SUSPEND_REASON。
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

# 东财停复牌（2026-09-22 生产机实测：RPT_CUSTOM_SUSPEND_DATA_INTERFACE，
# 需 filter=(MARKET="全部")(DATETIME='YYYY-MM-DD')；单日约 20 条）
EM_SUSPEND_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_SUSPEND_REPORT = "RPT_CUSTOM_SUSPEND_DATA_INTERFACE"
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}


@dataclass(frozen=True)
class SuspendResumeItem:
    """单条停复牌记录（股票-查询日粒度）。"""

    symbol: str
    name: str
    suspend_date: str  # 停牌日 YYYY-MM-DD
    resume_date: str  # 预计复牌日 YYYY-MM-DD（东财未给则为空串）
    suspend_days: float  # 预计停牌自然日数（复牌日 - 停牌日）
    suspend_type: str  # 停牌期限类型（连续停牌/盘中停牌/停牌1天等）
    reason: str  # 停牌原因


def _to_float(v: object) -> float:
    try:
        if v in (None, ""):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _norm_date(v: object) -> str:
    """东财日期形如 "2026-09-22 00:00:00" → 截断为 YYYY-MM-DD。"""
    if v in (None, ""):
        return ""
    s = str(v).strip()
    return s[:10]


def _days_between(start: str, end: str) -> float:
    """两个 YYYY-MM-DD 的自然日差；任一不可解析或为负则 0。"""
    try:
        delta = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except (TypeError, ValueError):
        return 0.0
    return float(delta) if delta > 0 else 0.0


def _parse_items(payload: object) -> list[SuspendResumeItem]:
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
    out: list[SuspendResumeItem] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            symbol = str(raw.get("SECURITY_CODE") or raw.get("symbol") or "").strip()
            if not symbol:
                continue
            suspend_date = _norm_date(
                raw.get("SUSPEND_START_DATE") or raw.get("suspend_date")
            )
            resume_date = _norm_date(
                raw.get("PREDICT_RESUME_DATE") or raw.get("resume_date")
            )
            out.append(
                SuspendResumeItem(
                    symbol=symbol,
                    name=str(
                        raw.get("SECURITY_NAME_ABBR") or raw.get("name") or ""
                    ).strip(),
                    suspend_date=suspend_date,
                    resume_date=resume_date,
                    suspend_days=_days_between(suspend_date, resume_date),
                    suspend_type=str(
                        raw.get("SUSPEND_EXPIRE") or raw.get("suspend_type") or ""
                    ).strip(),
                    reason=str(
                        raw.get("SUSPEND_REASON") or raw.get("reason") or ""
                    ).strip(),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


class SuspendResumeSource:
    """停复牌源：取数 + 本地缓存。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[SuspendResumeItem] = []

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目 runtime data root 约定；未配置时落系统临时目录，避免污染源码树
        root = os.environ.get("AQSP_RUNTIME_DATA_ROOT") or tempfile.gettempdir()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "suspend_resume.csv")

    def from_items(self, items: list[SuspendResumeItem]) -> "SuspendResumeSource":
        self._items = list(items)
        return self

    def _fetch(self, query_date: str = "") -> list[SuspendResumeItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"suspend_resume: 缺少依赖 requests（{e}）") from e
        # DATETIME 仅支持「查询日」等值过滤，缺省用上海时区当日
        day = query_date.strip() or today_shanghai().isoformat()
        params: dict[str, str] = {
            "reportName": EM_SUSPEND_REPORT,
            "columns": "ALL",
            "pageSize": "500",
            "pageNumber": "1",
            "sortColumns": "SUSPEND_START_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
            "filter": f"(MARKET=\"全部\")(DATETIME='{day}')",
        }
        try:
            r = requests.get(
                EM_SUSPEND_URL, params=params, headers=_EM_HEADERS, timeout=60
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            raise DataError(f"suspend_resume: 东财停复牌抓取失败（{e}）") from e
        return _parse_items(payload)

    def load(
        self, force: bool = False, query_date: str = ""
    ) -> list[SuspendResumeItem]:
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                # dtype 固定 symbol 为 str：否则 "002860" 会被推断成 286，丢前导零
                df = pd.read_csv(path, dtype={"symbol": str})
                self._items = [
                    SuspendResumeItem(**row) for row in df.to_dict("records")
                ]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch(query_date=query_date)
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(
        self, autoload: bool = False, query_date: str = ""
    ) -> list[SuspendResumeItem]:
        if not self._items and autoload:
            self.load(query_date=query_date)
        return list(self._items)
