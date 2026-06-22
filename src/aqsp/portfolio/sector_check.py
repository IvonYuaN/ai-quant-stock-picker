"""候选股板块集中度检查"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class SectorConcentration:
    sector: str
    count: int
    total: int
    ratio: float
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class ConcentrationResult:
    total_candidates: int
    sector_count: int
    max_concentration: float
    warnings: tuple[str, ...]
    sectors: tuple[SectorConcentration, ...]

    @property
    def is_concentrated(self) -> bool:
        return bool(self.warnings)


# 常见A股板块映射（静态缓存，避免每次查询）
_SECTOR_CACHE: dict[str, str] = {
    "600519": "白酒",
    "000858": "白酒",
    "000568": "白酒",
    "600809": "白酒",
    "002304": "白酒",
    "603369": "白酒",
    "600779": "白酒",
    "000799": "白酒",
    "600600": "白酒",
    "603198": "白酒",
    "600036": "银行",
    "601398": "银行",
    "601288": "银行",
    "601939": "银行",
    "601166": "银行",
    "600000": "银行",
    "600016": "银行",
    "601818": "银行",
    "601328": "银行",
    "000001": "银行",
    "002142": "银行",
    "600919": "银行",
    "601988": "银行",
    "600015": "银行",
    "601229": "银行",
    "601318": "保险",
    "601628": "保险",
    "601601": "保险",
    "601336": "保险",
    "600030": "证券",
    "601211": "证券",
    "600837": "证券",
    "601688": "证券",
    "000776": "证券",
    "002736": "证券",
    "000333": "家电",
    "000651": "家电",
    "600690": "家电",
    "002032": "家电",
    "002508": "家电",
    "603868": "家电",
    "600276": "医药",
    "000538": "医药",
    "300760": "医药",
    "600196": "医药",
    "002007": "医药",
    "300122": "医药",
    "000963": "医药",
    "600085": "医药",
    "300015": "医药",
    "601012": "新能源",
    "300750": "新能源",
    "002594": "新能源",
    "600438": "新能源",
    "601865": "新能源",
    "300274": "新能源",
    "002129": "新能源",
    "000725": "电子",
    "002371": "电子",
    "603986": "电子",
    "002475": "电子",
    "300408": "电子",
    "002241": "电子",
    "600183": "电子",
    "000002": "房地产",
    "600048": "房地产",
    "001979": "房地产",
    "600383": "房地产",
    "000069": "房地产",
    "600340": "房地产",
    "600585": "建材",
    "000401": "建材",
    "600176": "建材",
    "002271": "建材",
    "601088": "煤炭",
    "600188": "煤炭",
    "601225": "煤炭",
    "601898": "煤炭",
    "600019": "钢铁",
    "600010": "钢铁",
    "000709": "钢铁",
    "000898": "钢铁",
    "601857": "石油",
    "600028": "石油",
    "600346": "石油",
    "601808": "石油",
    "600900": "电力",
    "600886": "电力",
    "601985": "电力",
    "000027": "电力",
    "601669": "建筑",
    "601186": "建筑",
    "600170": "建筑",
    "002051": "建筑",
    "002714": "农牧",
    "000876": "农牧",
    "002311": "农牧",
    "600132": "食品",
    "603288": "食品",
    "002557": "食品",
    "600597": "食品",
}


def _clean_sector_label(value: str) -> str:
    label = str(value or "").strip()
    if not label or label.lower() == "nan":
        return ""
    return label


def get_sector(
    symbol: str,
    *,
    sector_hint: str = "",
    industry_hint: str = "",
) -> str:
    """获取股票板块，优先使用运行时行业信息。"""
    sector = _clean_sector_label(sector_hint)
    if sector:
        return sector
    industry = _clean_sector_label(industry_hint)
    if industry:
        return industry
    return _SECTOR_CACHE.get(symbol, "其他")


def check_sector_concentration(
    symbols: list[str],
    max_concentration: float = 0.4,
    sector_map: dict[str, str] | None = None,
    industry_map: dict[str, str] | None = None,
) -> ConcentrationResult:
    """
    检查候选股板块集中度

    Args:
        symbols: 候选股代码列表
        max_concentration: 最大允许集中度（默认40%）

    Returns:
        ConcentrationResult 包含集中度分析和警告
    """
    if not symbols:
        return ConcentrationResult(
            total_candidates=0,
            sector_count=0,
            max_concentration=0.0,
            warnings=(),
            sectors=(),
        )

    sector_symbols: dict[str, list[str]] = {}
    sector_map = sector_map or {}
    industry_map = industry_map or {}
    for sym in symbols:
        sector = get_sector(
            sym,
            sector_hint=sector_map.get(sym, ""),
            industry_hint=industry_map.get(sym, ""),
        )
        sector_symbols.setdefault(sector, []).append(sym)

    total = len(symbols)
    sector_counts = Counter({s: len(syms) for s, syms in sector_symbols.items()})

    # 排除"其他"板块（行业数据缺失的兜底标签）再算集中度。
    # "其他"意味着"未知行业"，不能当作"同一行业"触发集中度告警，
    # 否则行业数据缺失时全部票被误归"其他"→ 100%集中 → 无差别全降级（误杀）。
    known_sector_counts = {s: c for s, c in sector_counts.items() if s != "其他"}

    if known_sector_counts:
        max_count = max(known_sector_counts.values())
        max_ratio = max_count / total
        max_sector = max(known_sector_counts, key=known_sector_counts.get)
    else:
        # 所有票都是"其他"（无行业数据），集中度不可判，不告警
        max_count = 0
        max_ratio = 0.0
        max_sector = "其他"

    warnings = []
    if max_ratio > max_concentration:
        warnings.append(
            f"⚠️ 板块集中度过高：{max_sector}占比{max_ratio:.0%}（{max_count}/{total}只）"
        )

    sectors = tuple(
        SectorConcentration(
            sector=s,
            count=sector_counts[s],
            total=total,
            ratio=sector_counts[s] / total,
            symbols=tuple(sector_symbols[s]),
        )
        for s in sorted(sector_counts, key=sector_counts.get, reverse=True)
    )

    return ConcentrationResult(
        total_candidates=total,
        sector_count=len(sector_counts),
        max_concentration=max_ratio,
        warnings=tuple(warnings),
        sectors=sectors,
    )


def format_concentration(result: ConcentrationResult) -> str:
    """格式化集中度检查结果"""
    lines = []
    lines.append(
        f"📊 板块分布（{result.sector_count}个板块，{result.total_candidates}只股票）"
    )

    for s in result.sectors:
        bar = "█" * int(s.ratio * 20)
        lines.append(f"   {s.sector:8s} {bar} {s.ratio:.0%} ({s.count}只)")

    if result.warnings:
        lines.append("")
        for w in result.warnings:
            lines.append(f"   {w}")

    return "\n".join(lines)
