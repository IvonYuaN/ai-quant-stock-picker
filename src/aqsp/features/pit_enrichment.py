"""Pipeline integration adapters for point-in-time data sources.

Wires :mod:`aqsp.data.industry_pit` (SW industry PIT) and
:mod:`aqsp.data.macro_pit` (social financing / PMI PIT) into the screening and
market-context pipeline.

Design rules (architecture §5 / §9 no-look-ahead red line):
- Every ``as_of`` query is ``<= signal_date`` — never peek at future
  reclassifications or yet-unpublished macro prints.
- All adapters are fail-soft: when a PIT source is unavailable (network blocked,
  cache missing) they fall back to the existing label / an empty dict, so the
  pipeline behaviour is *unchanged* when data is absent. The default singletons
  are preloaded **cache-only** (never network — see ``_ensure_cache_preloaded``);
  production preloads the caches via ``scripts/data/fetch_pit_data.py`` to
  activate them.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from aqsp.data.industry_pit import IndustryLabel, IndustryPitSource
from aqsp.data.macro_pit import MacroPitSource

_logger = logging.getLogger("aqsp.features.pit_enrichment")

_DEFAULT_INDUSTRY_SOURCE = IndustryPitSource()
_DEFAULT_MACRO_SOURCE = MacroPitSource()
_cache_preload_attempted = False


def _ensure_cache_preloaded() -> None:
    """消费者接线（#335）：进程内一次性把 pit_cache 预热进默认单例。

    此前单例从不 ``load()``、查询又一律 ``autoload=False`` ⇒ ``industry_pit``
    / ``macro_pit`` 运行时永远空转（数据从未进入管线）。预热走
    ``load_cached()``——**只读本地缓存、绝不联网**；缓存缺失/损坏时静默
    保持未加载，沿用优雅回退（行为与「无缓存」时完全一致）。
    """
    global _cache_preload_attempted
    if _cache_preload_attempted:
        return
    _cache_preload_attempted = True
    try:
        if not _DEFAULT_INDUSTRY_SOURCE.load_cached():
            _logger.info("industry_pit 缓存缺失，行业 PIT 回退现有标签")
    except Exception:  # noqa: BLE001 - best-effort enrichment
        _logger.warning("industry_pit 缓存预热失败，回退现有标签", exc_info=True)
    try:
        if not _DEFAULT_MACRO_SOURCE.load_cached():
            _logger.info("macro_pit 缓存缺失，宏观 PIT 保持空上下文")
    except Exception:  # noqa: BLE001 - best-effort enrichment
        _logger.warning("macro_pit 缓存预热失败，保持空上下文", exc_info=True)


def pit_sector_industry_maps(
    picks: Sequence[Any],
    as_of: str,
    industry_source: Optional[IndustryPitSource] = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build ``(sector_map, industry_map)`` using point-in-time SW industry.

    Uses the PIT L2 industry code when available — eliminating the look-ahead
    bias of using a data source's *current* industry for historical signal
    dates — otherwise falls back to the pick's existing ``metrics`` label.

    Args:
        picks: iterable of pick-like objects exposing ``.symbol`` and
            ``.metrics`` (a mapping with optional ``sector`` / ``industry``).
        as_of: signal/query date, ISO ``YYYY-MM-DD``. Must be ``<=`` today.
        industry_source: optional preloaded :class:`IndustryPitSource`; defaults
            to a module-level singleton (unloaded -> graceful fallback).

    Returns:
        Two dicts keyed by symbol. Symbols with neither a PIT label nor an
        existing label are omitted (matching the prior non-empty-only contract).
    """
    if industry_source is None:
        _ensure_cache_preloaded()
    source = industry_source or _DEFAULT_INDUSTRY_SOURCE
    sector_map: dict[str, str] = {}
    industry_map: dict[str, str] = {}
    for pick in picks:
        symbol = str(getattr(pick, "symbol", "") or "")
        if not symbol:
            continue
        label = _safe_industry_as_of(source, symbol, as_of)
        if label is not None:
            code = label.l2_code or label.industry_code
            sector_map[symbol] = code
            industry_map[symbol] = code
            continue
        metrics = getattr(pick, "metrics", None) or {}
        existing_sector = str(metrics.get("sector", "") or "")
        existing_industry = str(metrics.get("industry", "") or "")
        if existing_sector.strip():
            sector_map[symbol] = existing_sector
        if existing_industry.strip():
            industry_map[symbol] = existing_industry
    return sector_map, industry_map


def _safe_industry_as_of(
    source: IndustryPitSource, symbol: str, as_of: str
) -> Optional[IndustryLabel]:
    try:
        return source.industry_as_of(symbol, as_of, autoload=False)
    except Exception:  # noqa: BLE001 - best-effort enrichment, degrade to fallback
        return None


def macro_pit_context(
    as_of: str,
    macro_source: Optional[MacroPitSource] = None,
) -> dict[str, float]:
    """Return point-in-time macro context (social financing / PMI) as-of ``as_of``.

    Returns an empty dict when the macro source is unavailable or has no print
    for that month. Never raises — best-effort enrichment for ``market_context``.

    Args:
        as_of: query date, ISO ``YYYY-MM-DD`` (resolved to its month).
        macro_source: optional preloaded :class:`MacroPitSource`; defaults to a
            module-level singleton (unloaded -> empty dict, no network).
    """
    if macro_source is None:
        _ensure_cache_preloaded()
    source = macro_source or _DEFAULT_MACRO_SOURCE
    try:
        sf = source.social_financing(as_of, autoload=False)
        pmi_m = source.pmi(as_of, "pmi_manufacturing", autoload=False)
        pmi_nm = source.pmi(as_of, "pmi_non_manufacturing", autoload=False)
        pmi_c = source.pmi(as_of, "pmi_composite", autoload=False)
    except Exception:  # noqa: BLE001 - best-effort enrichment
        return {}
    ctx: dict[str, float] = {}
    if sf is not None:
        ctx["macro_social_financing_increment"] = float(sf)
    if pmi_m is not None:
        ctx["macro_pmi_manufacturing"] = float(pmi_m)
    if pmi_nm is not None:
        ctx["macro_pmi_non_manufacturing"] = float(pmi_nm)
    if pmi_c is not None:
        ctx["macro_pmi_composite"] = float(pmi_c)
    return ctx
