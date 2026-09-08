"""东财概念板块（slist）—— 概念分类 + 资金流向数据源。

`a-stock-data` v3.8 复核记录注明「东财 `slist` 概念板块替代失效百度 PAE」；
AQSP `eastmoney_source` 是否实装 slist 待核，本模块以独立 fetcher 形式补齐，
保持与 `cls_news` / `longhubang` / `lockup` 一致的 fail-soft 模式。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError

EM_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"


@dataclass(frozen=True)
class ConceptBoardItem:
    """单个概念板块。"""

    board_code: str
    board_name: str
    constituent_count: int
    change_pct: float  # 当日涨跌幅 %
    main_net_inflow: float  # 主力净流入（万元）


def _to_float(v: object) -> float:
    try:
        if v in (None, ""):
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _to_int(v: object) -> int:
    try:
        if v in (None, ""):
            return 0
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _parse_items(payload: object) -> list[ConceptBoardItem]:
    """东财 clist 响应：{"data": {"diff": [...]}}。单条漂移跳过。"""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        diff = data.get("diff")
    elif isinstance(data, list):
        diff = data
    else:
        diff = payload.get("diff") if isinstance(payload, dict) else None
        if diff is None:
            diff = payload if isinstance(payload, list) else []
    if not isinstance(diff, list):
        return []
    out: list[ConceptBoardItem] = []
    for raw in diff:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(
                ConceptBoardItem(
                    board_code=str(raw.get("f12") or raw.get("code") or "").strip(),
                    board_name=str(raw.get("f14") or raw.get("name") or "").strip(),
                    constituent_count=_to_int(raw.get("f20") or raw.get("count")),
                    change_pct=_to_float(raw.get("f3") or raw.get("change_pct")),
                    main_net_inflow=_to_float(
                        raw.get("f184") or raw.get("main_net_inflow")
                    ),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


class ConceptBoardSource:
    """概念板块源：取数 + 本地缓存。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._items: list[ConceptBoardItem] = []

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目 runtime data root 约定；未配置时落系统临时目录，避免污染源码树
        root = os.environ.get("AQSP_RUNTIME_DATA_ROOT") or tempfile.gettempdir()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "concept_board.csv")

    def from_items(self, items: list[ConceptBoardItem]) -> "ConceptBoardSource":
        self._items = list(items)
        return self

    def _fetch(self) -> list[ConceptBoardItem]:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise DataError(f"concept_board: 缺少依赖 requests（{e}）") from e
        # m:90+t:2 = 概念板块；fs 按东财 clist 约定
        params = {
            "pn": "1",
            "pz": "200",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:90+t:2",
            "fields": "f1,f2,f3,f4,f12,f14,f20,f184",
        }
        try:
            r = requests.get(EM_CLIST_URL, params=params, timeout=60)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            raise DataError(f"concept_board: 东财概念板块抓取失败（{e}）") from e
        return _parse_items(payload)

    def load(self, force: bool = False) -> list[ConceptBoardItem]:
        path = self._default_cache_path()
        if not force and not self._items and os.path.exists(path):
            try:
                df = pd.read_csv(path)
                self._items = [ConceptBoardItem(**row) for row in df.to_dict("records")]
                return self._items
            except Exception:
                self._items = []
        self._items = self._fetch()
        try:
            pd.DataFrame([i.__dict__ for i in self._items]).to_csv(path, index=False)
        except Exception:
            pass
        return self._items

    def items(self, autoload: bool = False) -> list[ConceptBoardItem]:
        if not self._items and autoload:
            self.load()
        return list(self._items)
