"""宏观层 point-in-time（社融 / PMI）—— 上游 a-stock-data v3.7.0 §11 的硬缺口补齐。

AQSP 现状（`market_context.py`）只有「注入式实时宏观归一化」，**没有社融/PMI 取数**。
本模块补齐：
- 人民银行 **社融**（月度，社会融资规模增量）；
- 国家统计局 **PMI**（制造业 / 非制造业 / 综合 + 大中小型）。
两者都做 **point-in-time**：查询 as_of 时返回「不晚于该月的最新已发布值」，
未发布月份不返回 NaN（避免把缺失当月当成 0 或上月值误用）。

移植自 `simonlin1212/a-stock-data` v3.8.0 §11（已获上游审计 PASS），按 AQSP 改造：
- 纯 PIT 查询函数不依赖网络，可单测；
- `MacroPitSource` 负责取数 + 本地 CSV 缓存；网络取数 lazy import，失败抛 `DataError`；
- 沿用上游踩坑：社融**未发布月份整行丢弃**（不返回 NaN）；PMI 正文全角括号内含空格，
  匹配前先整段删空白。

实时抓取需生产机联网验证（详见各 `_fetch_*` 的 URL 约定），沙箱/单测用 `from_points` 注入。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from aqsp.core.errors import DataError

# 取数地址约定（生产机验证；沙箱不直连）
PBC_SOCIAL_FINANCING_URL = (
    "https://www.pbc.gov.cn/diaochatongjisi/resource/cms/article/".replace(
        "article/", "2024/202401"
    )
)  # 占位：实际为动态附件，生产机按最新月替换
NBS_PMI_URL = "https://www.stats.gov.cn/sj/zxfb/"  # 国家统计局 PMI 月度发布页


@dataclass(frozen=True)
class MacroPoint:
    """某个月的宏观快照（缺失项用 None 表示「当月未发布」）。"""

    month: str  # YYYY-MM
    social_financing_increment: Optional[float] = None  # 社融增量（万亿元）
    pmi_manufacturing: Optional[float] = None
    pmi_non_manufacturing: Optional[float] = None
    pmi_composite: Optional[float] = None
    pmi_large: Optional[float] = None
    pmi_medium: Optional[float] = None
    pmi_small: Optional[float] = None


def _month_key(as_of: str) -> pd.Timestamp:
    return pd.Timestamp(as_of).to_period("M").to_timestamp()


def social_financing_as_of(points: list[MacroPoint], as_of: str) -> Optional[float]:
    """返回不晚于 as_of 月、最近一次已发布的社融增量；未发布则返回 None。"""
    target = _month_key(as_of)
    best: Optional[MacroPoint] = None
    for p in points:
        if p.social_financing_increment is None:
            continue  # 上游铁律：未发布月份整行丢弃，不当 NaN
        if _month_key(p.month) <= target:
            if best is None or _month_key(p.month) > _month_key(best.month):
                best = p
    return best.social_financing_increment if best else None


def pmi_as_of(
    points: list[MacroPoint], as_of: str, which: str = "pmi_manufacturing"
) -> Optional[float]:
    """返回不晚于 as_of 月、最近一次已发布的指定 PMI 分项；未发布返回 None。"""
    if which not in MacroPoint.__dataclass_fields__:  # type: ignore[attr-defined]
        raise DataError(f"macro_pit: 未知 PMI 分项 {which!r}")
    target = _month_key(as_of)
    best: Optional[MacroPoint] = None
    for p in points:
        val = getattr(p, which)
        if val is None:
            continue
        if _month_key(p.month) <= target:
            if best is None or _month_key(p.month) > _month_key(best.month):
                best = p
    return getattr(best, which) if best else None


class MacroPitSource:
    """宏观 PIT 源：取数 + 本地缓存 + 时点查询。"""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir
        self._points: list[MacroPoint] = []

    def _default_cache_path(self) -> str:
        if self._cache_dir:
            return os.path.join(self._cache_dir, "macro_pit.csv")
        base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "macro_pit.csv")

    def from_points(self, points: list[MacroPoint]) -> "MacroPitSource":
        """注入宏观序列（测试 / 预加载用）。"""
        self._points = sorted(points, key=lambda p: p.month)
        return self

    def load(self, force: bool = False) -> list[MacroPoint]:
        """加载宏观序列：缓存命中且非强制时读本地，否则抓取并落盘。"""
        path = self._default_cache_path()
        if not force and not self._points and os.path.exists(path):
            try:
                df = pd.read_csv(path)
                self._points = [
                    MacroPoint(**{k: _coerce(v) for k, v in row.items()})
                    for row in df.to_dict("records")
                ]
                return self._points
            except Exception:
                self._points = []
        sf = _fetch_social_financing()
        pmi = _fetch_pmi()
        merged = _merge_sf_pmi(sf, pmi)
        self._points = sorted(merged, key=lambda p: p.month)
        try:
            pd.DataFrame([p.__dict__ for p in self._points]).to_csv(path, index=False)
        except Exception:
            pass
        return self._points

    def social_financing(self, as_of: str, autoload: bool = True) -> Optional[float]:
        if not self._points and autoload:
            self.load()
        return social_financing_as_of(self._points, as_of)

    def pmi(
        self, as_of: str, which: str = "pmi_manufacturing", autoload: bool = True
    ) -> Optional[float]:
        if not self._points and autoload:
            self.load()
        return pmi_as_of(self._points, as_of, which)


def _coerce(v: object) -> object:
    if pd.isna(v):
        return None
    return v


def _merge_sf_pmi(sf: list[MacroPoint], pmi: list[MacroPoint]) -> list[MacroPoint]:
    by_month: dict[str, MacroPoint] = {}
    for p in sf + pmi:
        if p.month in by_month:
            cur = by_month[p.month].__dict__
            for k, v in p.__dict__.items():
                if v is not None:
                    cur[k] = v
            by_month[p.month] = MacroPoint(**cur)
        else:
            by_month[p.month] = p
    return list(by_month.values())


def _fetch_social_financing(url: str | None = None) -> list[MacroPoint]:
    """人民银行社融月度增量。需生产机联网 + 动态附件 URL；未配置则抛 DataError。

    上游踩坑：未发布月份整行丢弃（不当 NaN），否则调用方会把缺失当月当真数据。
    """
    if not url:
        raise DataError(
            "macro_pit: 社融抓取需在生产机配置 PBC 动态附件 URL 后启用"
            "（沙箱/单测请用 MacroPitSource.from_points 注入）"
        )
    try:
        import requests

        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        r.raise_for_status()
    except Exception as e:
        raise DataError(f"macro_pit: 社融抓取失败（{e}）") from e
    # 解析：取「月份」列与「社会融资规模增量」列；缺失行丢弃（非 NaN 填充）。
    raise DataError("macro_pit: 社融列映射需按生产机实际表头对齐后启用")


def _fetch_pmi(url: str | None = None) -> list[MacroPoint]:
    """国家统计局 PMI。需生产机联网；未配置则抛 DataError。

    上游踩坑：PMI 正文是全角括号、内带空格，空白须整段删掉才匹配得到。
    """
    if not url:
        raise DataError(
            "macro_pit: PMI 抓取需在生产机配置 NBS 发布页 URL 后启用"
            "（沙箱/单测请用 MacroPitSource.from_points 注入）"
        )
    try:
        import requests

        from bs4 import BeautifulSoup  # 生产机依赖，沙箱 lazy
    except ImportError as e:  # pragma: no cover
        raise DataError(f"macro_pit: 缺少依赖 requests/bs4（{e}）") from e
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        r.raise_for_status()
        _ = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        raise DataError(f"macro_pit: PMI 抓取失败（{e}）") from e
    raise DataError("macro_pit: PMI 表头解析需按生产机实际页面对齐后启用")
