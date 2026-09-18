"""数据密度探测：把「窗口有数据」从**日期边界**升级为**每个交易日都有足够标的**。

为什么需要
----------
2026-09 的生产事故：gate 配了 5 年窗口（2021-09-12 起），而原始库真实数据只从
2023-09-05 起才密集。旧校验只看 `MIN(trade_date)` / `MAX(trade_date)` ——
库里存在几百行 2021~2022 的零散残留，于是 5 年窗口"看起来有覆盖"，
`covered_symbols` 也报得很高。结果 gate 跑到第 1/36 期，训练窗口落在空缺区，
整批取数为空，直接抛 DataError 退出。**连挂 6 天没人发现。**

教训：`MIN/MAX` 只能证明"边界上碰巧有数据"，不能证明"中段是连续的"。
窗口是否可用，必须按**逐交易日的标的数**来判断。

本模块只做纯计算，不碰数据库；取数由调用方负责。
"""
from __future__ import annotations

from dataclasses import dataclass

# 判定某交易日"够密集"的默认参照：
# 取近端中位数的这个比例与绝对下限的较大者。用中位数而不是最大值，
# 是为了不被个别异常日（如全市场停牌/半日市）把阈值抬高。
DEFAULT_REFERENCE_RATIO = 0.5
# 密集窗口内允许的稀疏日比例：95% 的交易日达标即可，容忍零星缺日。
DEFAULT_COVERAGE_RATIO = 0.95
# 计算"近端常态水平"时回看的交易日数。
RECENT_WINDOW_DAYS = 60
# 前置空档容差：请求起点到第一个有数据的交易日之间，自然日差超过这个值
# 就认为窗口超出数据覆盖（春节长假最长 ~15 天，不会误伤）。
LEADING_GAP_TOLERANCE_DAYS = 20


@dataclass(frozen=True)
class SparseSpan:
    """一段连续的不达标交易日。"""

    start: str
    end: str
    trade_days: int
    min_symbols: int


@dataclass(frozen=True)
class DensityReport:
    start: str
    end: str
    trade_days: int
    threshold: int
    peak_symbols: int
    median_symbols: int
    # 最早的密集起点；None 表示窗口内没有任何一段可用的密集区间
    dense_start: str | None
    sparse_spans: tuple[SparseSpan, ...]

    @property
    def usable(self) -> bool:
        return self.dense_start is not None

    @property
    def has_leading_gap(self) -> bool:
        """窗口起点到密集起点之间是否有缺口 —— 有则说明请求窗口超出数据覆盖。"""
        if not self.dense_start:
            return False
        return canonical_day(self.dense_start) != canonical_day(self.start)

    def describe(self) -> str:
        if not self.usable:
            return (
                f"窗口 {iso_day(self.start)}~{iso_day(self.end)} 内没有任何一段密集区间"
                f"（阈值 {self.threshold} 只/日，共 {self.trade_days} 个交易日）"
            )
        if self.has_leading_gap:
            dense_key = canonical_day(self.dense_start or "")
            gap_days = sum(
                span.trade_days
                for span in self.sparse_spans
                if canonical_day(span.end) < dense_key
            )
            return (
                f"窗口起点 {iso_day(self.start)} 早于数据覆盖："
                f"{iso_day(self.start)}~{iso_day(self.dense_start or '')} 之间"
                f"有 {gap_days} 个交易日不足 {self.threshold} 只，"
                f"实际可用起点为 {iso_day(self.dense_start or '')}"
            )
        return (
            f"窗口 {iso_day(self.start)}~{iso_day(self.end)} 数据密集"
            f"（阈值 {self.threshold} 只/日）"
        )


def resolve_threshold(
    counts: list[int],
    *,
    reference_ratio: float = DEFAULT_REFERENCE_RATIO,
    recent_days: int = RECENT_WINDOW_DAYS,
    absolute_floor: int = 1,
) -> int:
    """判定"够密集"的标的数下限 = 近端中位数 × 比例。

    **刻意不做绝对大小判断**：那是 `covered_symbols < min_symbols` 闸门的职责。
    本函数只回答相对问题 ——「这段是不是明显比其余部分空」。
    若在这里也设绝对门槛，等于把同一条策略判两遍：
    小样本库会被密度检查误杀（实现过程中真实踩到），而大样本库的绝对不足
    本该由标的数闸门给出更准确的提示。
    """
    if not counts:
        return absolute_floor
    tail = counts[-recent_days:] if len(counts) > recent_days else counts
    ordered = sorted(tail)
    mid = len(ordered) // 2
    median = (
        ordered[mid]
        if len(ordered) % 2 == 1
        else (ordered[mid - 1] + ordered[mid]) / 2
    )
    return max(absolute_floor, int(median * reference_ratio))


def find_dense_start(
    counts: list[int],
    *,
    threshold: int,
    coverage_ratio: float = DEFAULT_COVERAGE_RATIO,
) -> int | None:
    """最早的密集起点索引：从该日起，后续至少 `coverage_ratio` 的交易日达标。

    **起点当日必须自身达标** —— 容忍度是为了放过密集区内部的零星缺日，
    不是为了把边界外的空日圈进来。否则 gate 的第一期训练窗口仍可能落在空段上
    （2026-09 事故正是死在第一期）。

    用后缀统计一次遍历完成（O(n)）—— 1215 个交易日逐段试算会明显更慢，
    而这个函数每次 gate 启动都要跑。
    """
    total = len(counts)
    if total == 0:
        return None
    suffix_bad = [0] * (total + 1)
    for index in range(total - 1, -1, -1):
        suffix_bad[index] = suffix_bad[index + 1] + (1 if counts[index] < threshold else 0)
    for index in range(total):
        if counts[index] < threshold:
            continue
        span = total - index
        good = span - suffix_bad[index]
        if good >= coverage_ratio * span:
            return index
    return None


def find_sparse_spans(
    days: list[str],
    counts: list[int],
    *,
    threshold: int,
) -> list[SparseSpan]:
    """把连续不达标的交易日合并成区间，便于报告"缺了哪一段"。"""
    spans: list[SparseSpan] = []
    run_start: int | None = None
    for index, count in enumerate(counts):
        if count < threshold:
            if run_start is None:
                run_start = index
        elif run_start is not None:
            spans.append(_make_span(days, counts, run_start, index - 1, threshold))
            run_start = None
    if run_start is not None:
        spans.append(_make_span(days, counts, run_start, len(counts) - 1, threshold))
    return spans


def _make_span(
    days: list[str], counts: list[int], start: int, end: int, threshold: int
) -> SparseSpan:
    return SparseSpan(
        start=days[start],
        end=days[end],
        trade_days=end - start + 1,
        min_symbols=min(counts[start : end + 1]),
    )


def build_density_report(
    days: list[str],
    counts: list[int],
    *,
    start: str,
    end: str,
    absolute_floor: int = 1,
    coverage_ratio: float = DEFAULT_COVERAGE_RATIO,
    reference_ratio: float = DEFAULT_REFERENCE_RATIO,
) -> DensityReport:
    if len(days) != len(counts):
        raise ValueError(
            f"days 与 counts 长度不一致：{len(days)} vs {len(counts)}"
        )
    threshold = resolve_threshold(
        counts, reference_ratio=reference_ratio, absolute_floor=absolute_floor
    )
    index = find_dense_start(counts, threshold=threshold, coverage_ratio=coverage_ratio)
    ordered = sorted(counts)
    median = (
        ordered[len(ordered) // 2]
        if ordered and len(ordered) % 2 == 1
        else (
            (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
            if ordered
            else 0
        )
    )
    return DensityReport(
        start=start,
        end=end,
        trade_days=len(days),
        threshold=threshold,
        peak_symbols=max(counts) if counts else 0,
        median_symbols=int(median),
        dense_start=days[index] if index is not None else None,
        sparse_spans=tuple(find_sparse_spans(days, counts, threshold=threshold)),
    )


def canonical_day(value: str) -> str:
    """把交易日归一成紧凑数字形式，用于**比较**。

    必须归一：库里取出来的是 `20210913`，而请求窗口写的是 `2021-09-13`。
    直接比字符串会得出 `"20210913" > "2021-09-13"`（因为 `0` > `-`），
    于是本不该收敛的窗口也被"收敛"了 —— 这正是原事故的同款错误（混格式比较）。
    """
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def iso_day(value: str) -> str:
    """紧凑交易日 → ISO，用于**展示**。给人看的一律 ISO，避免一条提示里混两种格式。"""
    digits = canonical_day(value)
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return str(value or "")


def resolve_effective_window(
    report: DensityReport,
    *,
    requested_start: str,
    first_data_day: str,
    tolerance_days: int = LEADING_GAP_TOLERANCE_DAYS,
) -> tuple[str, bool]:
    """决定实际生效的窗口起点，返回 (生效起点, 是否被收敛)。

    收敛只在两种情形发生：
      a) **超出覆盖** —— 请求起点到第一个有数据的交易日之间空档过长
         （超过 `tolerance_days`，说明不是假期而是真的没数据）；
      b) **内部缺口** —— 密集起点晚于第一个有数据的交易日（中段有空段）。

    用容差而不是交易日历，是因为项目的基础日历不认元旦等节假日，
    拿它当基准会把「2024-01-01」这种正常起点误判成缺口。

    ⚠️ 未收敛时必须原样返回 `requested_start`。曾经返回过"比较基准"
    （首个有数据的交易日），结果未收敛也把窗口起点从 2024-01-01 改成了 01-02。
    """
    if not report.dense_start:
        return requested_start, False

    dense_key = canonical_day(report.dense_start)
    first_key = canonical_day(first_data_day)
    internal_gap = bool(first_key) and dense_key > first_key
    beyond_coverage = _calendar_gap_days(requested_start, first_data_day) > tolerance_days
    if not (internal_gap or beyond_coverage):
        return requested_start, False
    return report.dense_start, True


def _calendar_gap_days(left: str, right: str) -> int:
    """自然日差（右 - 左）。无法解析时返回 0（保守：不触发收敛）。"""
    left_key = canonical_day(left)
    right_key = canonical_day(right)
    if len(left_key) != 8 or len(right_key) != 8:
        return 0
    from datetime import date as _date

    try:
        return (_date.fromisoformat(iso_day(right_key)) - _date.fromisoformat(iso_day(left_key))).days
    except ValueError:
        return 0
