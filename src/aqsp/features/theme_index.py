"""题材指数（日 / 周 / 月 K）—— 强制区分「数据源口径」与「等权参考口径」。

问题
----
``data/concept_board.py`` 只给**当日一个截面**（506 个概念板块的涨跌幅 / 主力净流入），
没有历史，因此回答不了「这个题材是刚启动，还是已经连涨三周」。要做这件事就得把
每日截面攒成序列，再聚合成日 / 周 / 月 K。

但这里有一个**极易被误读**的坑：同名的「题材指数」在两种口径下数值可以差一倍以上。

- **数据源口径**（:data:`KIND_VENDOR`）：直接复利链数据源给的板块日涨跌幅。
  东财的 ``f3`` 是它自己的**市值加权**板块指数收益，成分权重、调样规则、基期
  全都不公开，且**不可交易**（没有对应 ETF / 指数基金）。它回答的是
  「这个板块的市值加权平均涨了多少」，不是「等权买一篮子成分赚了多少」。
- **等权参考口径**（:data:`KIND_REFERENCE`）：由成分股日收益**等权**合成，
  口径完全可解释、可复现，能当作「一篮子等权持有」的粗略参考（仍不含交易成本、
  不含权重漂移处理）。

⇒ 因此本模块的每个输出都**强制带 ``kind`` 字段**，并且两种口径各由独立函数产出，
不存在「一个函数既能算这个又能算那个」的模糊地带。展示层若丢掉 ``kind``，
就会把市值加权链当成等权指数——这正是参考实现（``jundizhou/easy-stock``
``internal/themeindex``，PolyForm Noncommercial 1.0.0）刻意区分两者的原因。
本模块的规则与实现均为 AQSP 自研，未复制其代码。

今日可用性
----------
- ``KIND_VENDOR``：**现在就能用**。数据来自 ``pit_cache/theme_history.csv``
  （``scripts/data/fetch_concept_board.py`` 每日盘后追加），历史从首次积累日开始，
  **不可回补**。
- ``KIND_REFERENCE``：**今天算不出来**。AQSP 目前没有「概念板块 → 成分股」的成员源
  （东财 ``push2 slist/get`` 全出口被切，见 #333）。本函数已实现并测试，等成员源
  落地（开盘啦「逐股炒作题材」那条线）即可直接接上。

两个刻意的设计约束
------------------
1. **缺数据不插平盘**：某日没有涨跌幅（数据源失败 / 停更）就整日跳过并记入
   :attr:`ThemeSeries.skipped_dates`，绝不按 0% 补齐——插 0 会把「当天没数据」
   伪装成「当天板块走平」，直接污染周 / 月 K 的形态。
2. **``close_high`` / ``close_low`` 不是真实最高 / 最低**：数据源只给日收盘涨跌幅，
   没有盘中价。这两个字段是**收盘序列**（含周期起始开盘价）的极值，字段名刻意不叫
   ``high`` / ``low``，避免被当成真实 K 线的影线。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Literal, Mapping, Sequence

# ---------------------------------------------------------------------------
# 口径常量（展示层必须原样透出，不允许改写成「题材指数」四个字了事）
# ---------------------------------------------------------------------------

KIND_VENDOR: str = "vendor"
KIND_REFERENCE: str = "reference"
IndexKind = Literal["vendor", "reference"]

_KIND_LABELS: dict[str, str] = {
    KIND_VENDOR: "数据源口径（板块日涨跌幅复利链，市值加权、不可交易）",
    KIND_REFERENCE: "等权参考口径（成分股日收益等权合成，不含成本）",
}

FREQ_DAY: str = "day"
FREQ_WEEK: str = "week"
FREQ_MONTH: str = "month"
Freq = Literal["day", "week", "month"]

FREQS: tuple[str, ...] = (FREQ_DAY, FREQ_WEEK, FREQ_MONTH)

#: 序列基点。用 1000 而不是 1，是为了让「指数点位」在 UI 上与常见指数同量级，
#: 且百分比变化与基点无关（纯缩放不影响涨跌幅）。
BASE_LEVEL: float = 1000.0

#: 等权参考指数的**数据完整性门**：当日有行情的成分占比低于此值 ⇒ 该日不出 bar。
#: 这是数据完整性门，不是策略阈值（不进 ``config/thresholds.yaml``，也不参与
#: walkforward 冻结）。
MIN_REFERENCE_COVERAGE: float = 0.5

#: 涨跌幅合法性下界（开区间）：单日跌 100% 以上在板块层面只能是脏数据。
#: 命中即视为无效值跳过，避免把指数点位链成 0 或负数。
MIN_VALID_CHANGE_PCT: float = -100.0


def describe_kind(kind: str) -> str:
    """返回口径的中文说明（展示层直接透出，别自己另编措辞）。"""
    return _KIND_LABELS.get(str(kind), f"未知口径（{kind}）")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyPoint:
    """日频序列上的一个点（口径无关）。

    ``change_pct`` 为 ``None`` 表示该日**无有效观测**（数据源缺数 / 脏值），
    调用方必须跳过而不是补 0。
    """

    trade_date: str
    change_pct: float | None = None
    member_count: int = 0
    total_members: int = 0


@dataclass(frozen=True)
class IndexBar:
    """一根 K 线。

    ``close_high`` / ``close_low`` 是**收盘序列**的极值（含周期起始的开盘价），
    **不是**盘中最高 / 最低——见模块 docstring 的约束 2。
    """

    period: str
    start: str
    end: str
    open: float
    close: float
    close_high: float
    close_low: float
    change_pct: float
    points: int
    partial: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "period": self.period,
            "start": self.start,
            "end": self.end,
            "open": self.open,
            "close": self.close,
            "close_high": self.close_high,
            "close_low": self.close_low,
            "change_pct": self.change_pct,
            "points": self.points,
            "partial": self.partial,
        }


@dataclass(frozen=True)
class ThemeSeries:
    """一条题材指数序列（某一板块、某一频率、某一口径）。"""

    board_name: str
    kind: str
    freq: str
    base_level: float
    bars: tuple[IndexBar, ...]
    daily: tuple[DailyPoint, ...]
    skipped_dates: tuple[str, ...]
    reference_coverage: float | None = None

    @property
    def level(self) -> float | None:
        """最新点位；无 bar ⇒ ``None``（不返回基点冒充「平盘」）。"""
        return self.bars[-1].close if self.bars else None

    @property
    def total_change_pct(self) -> float | None:
        """相对基点的累计涨跌幅 %；无 bar ⇒ ``None``。"""
        if not self.bars or self.base_level <= 0:
            return None
        return (self.bars[-1].close / self.base_level - 1.0) * 100.0

    @property
    def coverage(self) -> float:
        """有观测日 / (有观测日 + 被跳过日)。空序列 ⇒ 0.0。"""
        kept = len(self.daily)
        total = kept + len(self.skipped_dates)
        return kept / total if total else 0.0

    @property
    def degraded(self) -> bool:
        """是否发生过缺数据跳过（缺数据 ≠ 平盘，展示层应给出提示）。"""
        return bool(self.skipped_dates)

    def as_dict(self) -> dict[str, object]:
        return {
            "board_name": self.board_name,
            "kind": self.kind,
            "kind_label": describe_kind(self.kind),
            "freq": self.freq,
            "base_level": self.base_level,
            "level": self.level,
            "total_change_pct": self.total_change_pct,
            "coverage": self.coverage,
            "degraded": self.degraded,
            "reference_coverage": self.reference_coverage,
            "skipped_dates": list(self.skipped_dates),
            "bars": [bar.as_dict() for bar in self.bars],
        }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _parse_day(value: object) -> date | None:
    """解析 ``YYYY-MM-DD`` / ``YYYYMMDD`` / ``date``；失败 ⇒ ``None``（不抛）。

    ``datetime`` 先降级成 ``date``，否则 ``isoformat()`` 会带上时间戳，
    让 ``trade_date`` 与其它点不同源（同为 8 位日期字符串）。
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _valid_pct(value: object) -> float | None:
    """把任意输入收成合法涨跌幅；``None`` / 非数 / ≤ -100 ⇒ ``None``。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        pct = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if pct != pct or pct in (float("inf"), float("-inf")):  # NaN / inf
        return None
    if pct <= MIN_VALID_CHANGE_PCT:
        return None
    return pct


def _normalize_pairs(
    pairs: Iterable[tuple[object, object]],
) -> tuple[list[DailyPoint], list[str]]:
    """把 ``(日期, 涨跌幅)`` 序列整理成按日升序、同日去重（取最后一条）的日点。

    返回 ``(有效点, 被跳过日期)``。被跳过 = 日期无法解析 **或** 涨跌幅无效。
    """
    latest: dict[date, float] = {}
    skipped: list[str] = []
    for raw_date, raw_pct in pairs:
        day = _parse_day(raw_date)
        if day is None:
            skipped.append(str(raw_date))
            continue
        pct = _valid_pct(raw_pct)
        if pct is None:
            # 同日先有值后被覆盖成脏值 ⇒ 视为当日无有效观测
            latest.pop(day, None)
            skipped.append(day.isoformat())
            continue
        latest[day] = pct
    ordered = sorted(latest)
    points = [DailyPoint(trade_date=day.isoformat(), change_pct=latest[day]) for day in ordered]
    # 跳过日按日期去重后排序，便于稳定断言与展示
    unique_skipped = sorted({item for item in skipped})
    return points, unique_skipped


def _chain(points: Sequence[DailyPoint], *, base_level: float) -> tuple[float, ...]:
    """复利链：``level_t = level_{t-1} * (1 + pct/100)``。"""
    levels: list[float] = []
    level = base_level
    for point in points:
        pct = point.change_pct or 0.0
        level *= 1.0 + pct / 100.0
        levels.append(level)
    return tuple(levels)


def _period_key(day: date, freq: str) -> str:
    if freq == FREQ_WEEK:
        iso = day.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if freq == FREQ_MONTH:
        return f"{day.year}-{day.month:02d}"
    return day.isoformat()


def _build_bars(
    points: Sequence[DailyPoint],
    levels: Sequence[float],
    *,
    freq: str,
    base_level: float,
) -> tuple[IndexBar, ...]:
    """把日点位序列聚合成 ``freq`` 频率的 K 线。

    ``partial``：只有**最后一根**周 / 月 K 会被标记为未完成——因为「这个周期走完没有」
    需要交易日历才能判定，而本模块是零 I/O 纯函数。宁可把已走完的最后一根也标成
    ``partial``（保守），也不误报「已收」。
    """
    if freq not in FREQS:
        raise ValueError(f"theme_index: 未知频率 {freq!r}，可用 {list(FREQS)}")
    if not points:
        return ()

    if freq == FREQ_DAY:
        bars: list[IndexBar] = []
        prev = base_level
        for point, level in zip(points, levels):
            bars.append(
                IndexBar(
                    period=point.trade_date,
                    start=point.trade_date,
                    end=point.trade_date,
                    open=prev,
                    close=level,
                    close_high=max(prev, level),
                    close_low=min(prev, level),
                    change_pct=point.change_pct or 0.0,
                    points=1,
                    partial=False,
                )
            )
            prev = level
        return tuple(bars)

    # 周 / 月：按自然周期分组，组内顺序即交易日顺序（points 已升序）
    groups: list[list[int]] = []
    keys: list[str] = []
    for idx, point in enumerate(points):
        day = _parse_day(point.trade_date)
        if day is None:  # pragma: no cover - _normalize_pairs 已保证可解析
            continue
        key = _period_key(day, freq)
        if keys and keys[-1] == key:
            groups[-1].append(idx)
        else:
            keys.append(key)
            groups.append([idx])

    bars = []
    prev = base_level
    last_group = len(groups) - 1
    for pos, group in enumerate(groups):
        closes = [levels[idx] for idx in group]
        open_level = prev
        close_level = closes[-1]
        change_pct = (close_level / open_level - 1.0) * 100.0 if open_level > 0 else 0.0
        bars.append(
            IndexBar(
                period=keys[pos],
                start=points[group[0]].trade_date,
                end=points[group[-1]].trade_date,
                open=open_level,
                close=close_level,
                close_high=max([open_level, *closes]),
                close_low=min([open_level, *closes]),
                change_pct=change_pct,
                points=len(group),
                partial=pos == last_group,
            )
        )
        prev = close_level
    return tuple(bars)


def _assemble(
    *,
    board_name: str,
    kind: str,
    freq: str,
    base_level: float,
    points: Sequence[DailyPoint],
    skipped: Sequence[str],
    reference_coverage: float | None = None,
) -> ThemeSeries:
    levels = _chain(points, base_level=base_level)
    return ThemeSeries(
        board_name=str(board_name),
        kind=kind,
        freq=freq,
        base_level=base_level,
        bars=_build_bars(points, levels, freq=freq, base_level=base_level),
        daily=tuple(points),
        skipped_dates=tuple(skipped),
        reference_coverage=reference_coverage,
    )


# ---------------------------------------------------------------------------
# 口径一：数据源（东财板块日涨跌幅复利链）
# ---------------------------------------------------------------------------


def build_vendor_series(
    pairs: Iterable[tuple[object, object]],
    *,
    board_name: str,
    freq: str = FREQ_DAY,
    base_level: float = BASE_LEVEL,
) -> ThemeSeries:
    """由「板块日涨跌幅」序列构建数据源口径的题材指数。

    :param pairs: ``(trade_date, change_pct)`` 序列，顺序任意（内部按日排序、
        同日取最后一条）。``change_pct`` 为 ``None`` / 脏值 ⇒ 该日跳过。
    :param board_name: 板块名（原样透出，不做归一化）。
    :param freq: ``day`` / ``week`` / ``month``。
    :param base_level: 基点，默认 :data:`BASE_LEVEL`。

    ⚠️ 输出 ``kind`` 恒为 :data:`KIND_VENDOR`。这是**市值加权、不可交易**的口径，
    不是等权篮子收益。
    """
    points, skipped = _normalize_pairs(pairs)
    return _assemble(
        board_name=board_name,
        kind=KIND_VENDOR,
        freq=freq,
        base_level=base_level,
        points=points,
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# 口径二：等权参考（成分股日收益等权合成）
# ---------------------------------------------------------------------------


def build_reference_series(
    returns_by_symbol: Mapping[str, Iterable[tuple[object, object]]],
    *,
    board_name: str,
    freq: str = FREQ_DAY,
    base_level: float = BASE_LEVEL,
    total_members: int | None = None,
    min_coverage: float = MIN_REFERENCE_COVERAGE,
) -> ThemeSeries:
    """由成分股日收益构建**等权参考**题材指数。

    :param returns_by_symbol: ``{symbol: [(trade_date, change_pct), ...]}``。
    :param board_name: 题材名。
    :param total_members: 该题材的**应有**成分数。缺省 = ``len(returns_by_symbol)``。
        传入真实成分数可以暴露「成分股大面积取不到数」的情况——这是本函数
        拒绝输出失真指数的主要手段。
    :param min_coverage: 当日有值成分占比低于此值 ⇒ 该日不出 bar（计入
        ``skipped_dates``），默认 :data:`MIN_REFERENCE_COVERAGE`。

    口径：当日对**有值成分**等权平均（不做权重漂移修正、不含交易成本）。
    有效成分为 0 的日期一律跳过，**绝不**输出「0% 平盘」的假 bar。

    ⚠️ 输出 ``kind`` 恒为 :data:`KIND_REFERENCE`，``reference_coverage`` 是
    保留日的平均成分覆盖率。
    """
    by_date: dict[date, list[float]] = {}
    symbol_count = 0
    for pairs in returns_by_symbol.values():
        symbol_count += 1
        for raw_date, raw_pct in pairs:
            day = _parse_day(raw_date)
            if day is None:
                continue
            pct = _valid_pct(raw_pct)
            if pct is None:
                continue
            by_date.setdefault(day, []).append(pct)

    denominator = int(total_members) if total_members is not None else symbol_count
    points: list[DailyPoint] = []
    skipped: list[str] = []
    coverages: list[float] = []
    for day in sorted(by_date):
        values = by_date[day]
        coverage = len(values) / denominator if denominator > 0 else 0.0
        if denominator <= 0 or coverage < min_coverage:
            skipped.append(day.isoformat())
            continue
        coverages.append(coverage)
        points.append(
            DailyPoint(
                trade_date=day.isoformat(),
                change_pct=sum(values) / len(values),
                member_count=len(values),
                total_members=denominator,
            )
        )

    mean_coverage = sum(coverages) / len(coverages) if coverages else None
    return _assemble(
        board_name=board_name,
        kind=KIND_REFERENCE,
        freq=freq,
        base_level=base_level,
        points=points,
        skipped=skipped,
        reference_coverage=mean_coverage,
    )


__all__ = [
    "BASE_LEVEL",
    "FREQS",
    "FREQ_DAY",
    "FREQ_MONTH",
    "FREQ_WEEK",
    "KIND_REFERENCE",
    "KIND_VENDOR",
    "MIN_REFERENCE_COVERAGE",
    "MIN_VALID_CHANGE_PCT",
    "DailyPoint",
    "Freq",
    "IndexBar",
    "IndexKind",
    "ThemeSeries",
    "build_reference_series",
    "build_vendor_series",
    "describe_kind",
]
