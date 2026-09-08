"""限售解禁（东财）—— 风险日历数据源。

移植自 `simonlin1212/TradingAgents-astock` v0.5.17（东财 datacenter 取解禁计划）。
按 AQSP 改造：纯解析 + lazy 网络 + 单条容错。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError

# 东财解禁（2026-09-08 生产机实测：RPT_LIFT_STAGE，FREE_DATE 倒序；
# RPT_LIFTING_DATA 不存在。FREE_SHARES 为东财原值未换算）
EM_LOCKUP_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_LOCKUP_REPORT = "RPT_LIFT_STAGE"
_EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}


@dataclass(frozen=True)
class LockupItem:
    """单条解禁计划。"""

    symbol: str
    name: str
    plan_date: str  # 解禁日 YYYY-MM-DD
    lockup_shares: float  # 解禁股数（万股）
    ratio: float  # 占总股本比例
    lockup_type: str  # 首发/定增/股权激励等


def _to_float(v: object) -> float:
    try:
        if v in (None, ""):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_items(payload: object) -> list[LockupItem]:
    """东财 datacenter 响应：{"result": {"data": [...]}}。单条漂移跳过。"""
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
    out: list[LockupItem] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(
                LockupItem(
                    symbol=str(
                        raw.get("SECURITY_CODE") or raw.get("code") or ""
                    ).strip(),
                    name=str(
                        raw.get("SECURITY_NAME_ABBR") or raw.get("name") or ""
                    ).strip(),
                    plan_date=str(
                        raw.get("FREE_DATE") or raw.get("plan_date") or ""
                    ).strip(),
                    lockup_shares=_to_float(
                        raw.get("FREE_SHARES") or raw.get("shares")
                    ),
                    ratio=_to_float(raw.get("FREE_RATIO") or raw.get("ratio")),
                    lockup_type=str(
                        raw.get("FREE_SHARES_TYPE") or raw.get("type") or ""
                    ).strip(),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


class LockupSource:
    """限售解禁源：取数 + 本地缓存。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[LockupItem] = []

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目 runtime data root 约定；未配置时落系统临时目录，避免污染源码树
        root = os.environ.get("AQSP_RUNTIME_DATA_ROOT") or tempfile.gettempdir()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "lockup.csv")

    def from_items(self, items: list[LockupItem]) -> "LockupSource":
        self._items = list(items)
        return self

    def _fetch(self, from_date: str = "") -> list[LockupItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"lockup: 缺少依赖 requests（{e}）") from e
        params: dict[str, str] = {
            "reportName": EM_LOCKUP_REPORT,
            "columns": "ALL",
            "pageSize": "500",
            "pageNumber": "1",
            "sortColumns": "FREE_DATE",
            "sortTypes": "-1",
        }
        if from_date.strip():
            params["filter"] = f"(FREE_DATE>='{from_date.strip()}')"
        try:
            r = requests.get(
                EM_LOCKUP_URL, params=params, headers=_EM_HEADERS, timeout=60
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            raise DataError(f"lockup: 东财解禁抓取失败（{e}）") from e
        return _parse_items(payload)

    def load(self, force: bool = False, from_date: str = "") -> list[LockupItem]:
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                df = pd.read_csv(path)
                self._items = [LockupItem(**row) for row in df.to_dict("records")]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch(from_date=from_date)
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(self, autoload: bool = False, from_date: str = "") -> list[LockupItem]:
        if not self._items and autoload:
            self.load(from_date=from_date)
        return list(self._items)
