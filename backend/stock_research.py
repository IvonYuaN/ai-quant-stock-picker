"""Evidence-cited, tenant-scoped reports for current single-stock research."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date
import json
import logging
import math
from pathlib import Path
import re
import threading
from typing import Any, Literal
from uuid import uuid4

import astock
import aqsp_bridge
import portfolio as pf
import tenant
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock, atomic_write_text
from pydantic import BaseModel, ConfigDict, Field

_CODE_RE = re.compile(r"^\d{6}$")
_LOCK = threading.RLock()
_MAX_REPORTS = 500
_DIMENSIONS = ("technical", "fundamental", "financial", "news")
_TRADE_DIRECTIVE_PATTERNS = (
    re.compile(r"买入|买进|卖出|卖掉|加仓|减仓|建仓|开仓|清仓|止盈|止损|回补|做\s*t|买点|卖点", re.IGNORECASE),
    re.compile(r"目标价|目标价格|价格目标|目标位|target\s+price|stop\s+loss|take\s+profit", re.IGNORECASE),
    re.compile(r"仓位(?:建议|比例|配置|调整)|position\s+size", re.IGNORECASE),
)
logger = logging.getLogger(__name__)


class StockResearchError(ValueError):
    """The requested report lacks a current, verifiable research basis."""


class StockResearchModelError(RuntimeError):
    """A bounded model request or evidence-schema validation failed."""


class StockResearchContentError(StockResearchModelError):
    """The model text crosses the report's non-trading content boundary."""


class StockResearchFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=350)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class StockResearchDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "partial", "unavailable"]
    findings: list[StockResearchFinding] = Field(max_length=5)
    missing: list[str] = Field(max_length=5)


class StockResearchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=700)
    summary_evidence_ids: list[str] = Field(min_length=1, max_length=12)
    technical: StockResearchDimension
    fundamental: StockResearchDimension
    financial: StockResearchDimension
    news: StockResearchDimension
    missing_evidence: list[str] = Field(max_length=12)


SYSTEM = """你为用户提供当前个股研究摘要，只能依据输入 evidence，不得推断出未提供的事实。
逐项分析技术、基本面、财务、消息四个维度；数据不可用就标 unavailable 或 partial，并说明具体缺口，不得用一个维度代替另一个维度。
每条 finding 和总摘要必须引用 allowed_evidence_ids 中的来源。财务证据是最新报告期摘要，不代表历史研究时点可知；只按报告期陈述，不用于 PIT 回测结论。
技术价位若标记 pit_as_of 才能跨日比较；raw_partial 只可描述单日 raw 参考。展示 K 线若价基未确认，不可当作技术计算证据。
用户的 personal_context 是研究问题和个人假设，不是客观证据；检验其假设并指出反方/未验证处，不得让它改变 AQSP 确定性评分。
不输出买入、卖出、止损、加仓、减仓、仓位或价格目标，不预测收益，不下单。输出符合 output_schema 的 JSON，不加 Markdown 围栏，不输出思考过程。"""


def _file() -> Path:
    tenant_id = tenant.current_tenant.get()
    root = Path(pf.CACHE_DIR) if tenant_id == "local" else Path(pf.CACHE_DIR) / "users" / tenant_id
    return root / "stock_research_reports.json"


def _load() -> dict[str, Any]:
    path = _file()
    if not path.exists():
        return {"version": 1, "items": []}
    payload = json.loads(path.read_text("utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("items"), list)
        or any(not isinstance(item, dict) for item in payload["items"])
    ):
        raise ValueError("个股研究报告存储格式无效，已停止写入以保护原记录")
    return payload


def _write(payload: dict[str, Any]) -> None:
    atomic_write_text(_file(), json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _clean(value: Any, *, limit: int = 500) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        return {str(key)[:100]: _clean(item, limit=limit) for key, item in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [_clean(item, limit=limit) for item in value[:30]]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            return str(value)[:limit]
    return str(value)[:limit]


def _personal_context(value: dict[str, str] | None) -> dict[str, str]:
    limits = {"personal_intent": 500, "time_horizon": 120, "personal_hypothesis": 500, "counterargument": 500}
    if not isinstance(value, dict):
        raise StockResearchError("请先记录本人的问题和观察期限")
    result: dict[str, str] = {}
    for key, limit in limits.items():
        raw = value.get(key, "")
        if not isinstance(raw, str) or len(raw) > limit:
            raise StockResearchError("个人研究上下文长度或格式无效")
        result[key] = raw.strip()
    if not result["personal_intent"] or not result["time_horizon"]:
        raise StockResearchError("请先记录本人的研究问题和观察期限")
    return result


def _safe_source(function, unavailable: str) -> tuple[Any, str]:
    try:
        return function(), ""
    except Exception as exc:  # noqa: BLE001 -- public-data sources degrade independently
        logger.warning("stock_research_source_unavailable", source=unavailable, error_type=type(exc).__name__)
        return None, unavailable


def _evidence_entry(
    domain: str,
    title: str,
    value: Any,
    source: str,
    as_of: str | None,
    price_basis: str | None = None,
) -> dict[str, Any]:
    entry = {"domain": domain, "title": title, "value": _clean(value), "source": source, "as_of": as_of or ""}
    if price_basis:
        entry["price_basis"] = price_basis
    return entry


async def _collect_evidence(code: str) -> tuple[str, dict[str, Any], dict[str, list[str]]]:
    from decision_context import position_quote_issue
    from stock_levels import load_stock_levels

    fetched_at = now_shanghai().isoformat()
    quote_result, info_result, financial_result, news_result, levels_result = await asyncio.gather(
        asyncio.to_thread(_safe_source, lambda: astock.tencent_quote([code]).get(code), "实时行情源不可用"),
        asyncio.to_thread(_safe_source, lambda: astock.individual_info(code), "公司资料源不可用"),
        asyncio.to_thread(_safe_source, lambda: astock.financials(code), "最新报告期财务源不可用"),
        asyncio.to_thread(_safe_source, lambda: astock.stock_news(code, limit=12), "个股新闻源不可用"),
        asyncio.to_thread(_safe_source, lambda: load_stock_levels(code), "raw 日线或 PIT 复权因子不可用"),
    )
    quote, quote_error = quote_result
    info, info_error = info_result
    financials, financial_error = financial_result
    news_rows, news_error = news_result
    levels, levels_error = levels_result

    evidence: dict[str, Any] = {}
    missing: dict[str, list[str]] = {key: [] for key in _DIMENSIONS}
    name = code
    if isinstance(quote, dict):
        name = str(quote.get("name") or code)
        quote_as_of = str(quote.get("as_of") or astock.parse_gtimg_time(quote.get("quote_time")) or "")
        quote_issue = position_quote_issue(quote, checked_at=now_shanghai())
        if quote.get("price") is not None and quote_as_of and not quote_issue:
            evidence["technical.quote"] = _evidence_entry(
                "technical", "实时行情", quote, str(quote.get("source") or "腾讯行情"), quote_as_of,
            )
        else:
            missing["technical"].append(quote_issue or "实时行情缺少有效价格或时点")
    else:
        missing["technical"].append(quote_error)

    if isinstance(levels, dict):
        for item in levels.get("levels", []):
            if item.get("status") == "available" and item.get("levels"):
                evidence[f"technical.levels.{item['type']}"] = _evidence_entry(
                    "technical", str(item.get("type")),
                    {"formula": item.get("formula"), "levels": item.get("levels")},
                    str(item.get("source") or levels.get("source") or "未知日线源"),
                    str(item.get("as_of") or levels.get("as_of") or ""),
                    str(item.get("price_basis") or levels.get("price_basis") or "unknown"),
                )
            elif item.get("status") == "unavailable":
                missing["technical"].append(str(item.get("reason") or f"{item.get('type')} 不可用"))
        if levels.get("warning") and levels.get("price_basis") != "pit_as_of":
            missing["technical"].append(str(levels["warning"]))
        if not any(key.startswith("technical.levels.") for key in evidence):
            missing["technical"].append(levels_error if levels is None else "没有可引用的结构价位")
    else:
        missing["technical"].append(levels_error)

    if isinstance(info, dict) and info:
        evidence["fundamental.profile"] = _evidence_entry(
            "fundamental", "公司资料", info, "东财/巨潮公司资料", fetched_at,
        )
    else:
        missing["fundamental"].append(info_error if info is None else "公司资料源未返回内容")

    if isinstance(financials, dict) and any(value not in (None, "") for value in financials.values()):
        evidence["financial.latest"] = _evidence_entry(
            "financial", "最新报告期财务摘要", financials, "同花顺财务摘要", str(financials.get("period") or "报告期未标注"),
        )
    else:
        missing["financial"].append(financial_error if financials is None else "没有可展示的最新报告期财务指标")

    usable_news: list[dict[str, Any]] = []
    if isinstance(news_rows, list):
        for index, row in enumerate(news_rows[:12]):
            if not isinstance(row, dict):
                continue
            title = next((row.get(key) for key in ("新闻标题", "标题", "title") if row.get(key)), "")
            if not title:
                continue
            published = next((row.get(key) for key in ("发布时间", "published_at", "时间") if row.get(key)), "")
            value = _clean({
                "title": title,
                "published_at": published,
                "source": row.get("文章来源") or row.get("source") or "东财个股新闻",
                "summary": row.get("新闻内容") or row.get("摘要") or row.get("summary") or "",
            })
            usable_news.append(value)
            evidence[f"news.item.{index + 1}"] = _evidence_entry(
                "news", "个股新闻", value, "东财个股新闻", str(published or "发布时间未标注"),
            )
    if not usable_news:
        missing["news"].append(news_error if news_rows is None else "个股新闻源未返回可引用新闻")

    if not evidence:
        raise StockResearchError("当前没有可核验的个股研究证据，暂不能生成报告")
    return name, evidence, missing


def _parse_result(raw: str, evidence: dict[str, Any]) -> StockResearchSummary:
    text = raw.strip()
    fence = chr(96) * 3
    if text.startswith(fence) and text.endswith(fence):
        text = text.split("\n", 1)[1].rsplit(fence, 1)[0]
    result = StockResearchSummary.model_validate_json(text)
    displayed_text = [result.summary, *result.missing_evidence]
    for domain in _DIMENSIONS:
        dimension = getattr(result, domain)
        displayed_text.extend(finding.text for finding in dimension.findings)
        displayed_text.extend(dimension.missing)
    if any(pattern.search(value) for value in displayed_text for pattern in _TRADE_DIRECTIVE_PATTERNS):
        raise StockResearchContentError("模型报告包含交易指令或目标价内容，已拒绝整份报告")
    for reference in result.summary_evidence_ids:
        if reference not in evidence:
            raise StockResearchModelError("模型引用了不存在的研究证据")
    for domain in _DIMENSIONS:
        dimension = getattr(result, domain)
        domain_ids = {key for key, item in evidence.items() if item.get("domain") == domain}
        if domain_ids and dimension.status == "unavailable":
            raise StockResearchModelError(f"模型把有证据的{domain}维度标成不可用")
        for finding in dimension.findings:
            if any(reference not in domain_ids for reference in finding.evidence_ids):
                raise StockResearchModelError(f"模型的{domain}结论引用了错误或不存在的证据")
        if not dimension.findings and dimension.status != "unavailable":
            raise StockResearchModelError(f"模型未提供{domain}结论却没有标为不可用")
    return result


def _store_draft(report: dict[str, Any]) -> None:
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load()
        payload["items"].append(report)
        saved = [item for item in payload["items"] if item.get("saved") is True]
        drafts = [item for item in payload["items"] if item.get("saved") is not True]
        remaining = max(0, _MAX_REPORTS - len(saved))
        payload["items"] = saved + (drafts[-remaining:] if remaining else [])
        _write(payload)


async def run_stock_research(
    code: str,
    personal_context: dict[str, str] | None,
    config: dict[str, str] | None = None,
    server_model: str | None = None,
) -> dict[str, Any]:
    """Generate a bounded draft; only an explicit save enters review history."""
    if not _CODE_RE.fullmatch(code):
        raise StockResearchError("股票代码必须是 6 位数字")
    context = _personal_context(personal_context)
    with pf._LOCK:
        holdings = pf._load().get("holdings", [])
    if any(row.get("code") == code for row in holdings):
        raise StockResearchError("该标的属于当前持仓，请使用持仓工作区的专属复核")
    try:
        aqsp_bridge.candidate_payload(code)
    except aqsp_bridge.AQSPCandidateNotFound:
        pass
    except aqsp_bridge.AQSPBridgeError:
        # A missing/stale candidate snapshot must not be represented as a candidate match.
        pass
    else:
        raise StockResearchError("该标的属于当前候选，请使用候选专属复核")

    name, evidence, missing = await _collect_evidence(code)
    try:
        from position_discussion import call_model, discussion_limits, resolve_endpoint

        try:
            endpoint = resolve_endpoint(config)
        except (ValueError, RuntimeError, OSError) as exc:
            raise StockResearchError(str(exc)) from exc
        if server_model:
            if config:
                raise StockResearchError("本次自定义接口与服务器型号选择不能同时使用")
            from position_model_catalog import read_catalog

            catalog = await read_catalog(endpoint, Path(pf.CACHE_DIR))
            if catalog.get("directory_complete", catalog.get("listed") is not None) and server_model not in catalog["models"]:
                raise StockResearchError("所选型号未在当前提供商目录中，请刷新后重选；未自动替换")
            endpoint = type(endpoint)(endpoint.url, endpoint.key, server_model, endpoint.anthropic)
        limits = discussion_limits()
        payload = {
            "symbol": code,
            "name": name,
            "generated_at": now_shanghai().isoformat(),
            "evidence": evidence,
            "source_gaps": missing,
            "personal_context": context,
            "allowed_evidence_ids": sorted(evidence),
            "output_schema": StockResearchSummary.model_json_schema(),
        }
        raw = await call_model(endpoint, payload, limits.synthesis_timeout, limits, system_prompt=SYSTEM)
        summary = _parse_result(raw, evidence)
    except StockResearchError:
        raise
    except StockResearchContentError:
        raise
    except Exception as exc:  # noqa: BLE001 -- no partial report is persisted as complete
        raise StockResearchModelError("四维模型分析未完成或未通过证据校验，报告未保存") from exc

    dimensions: dict[str, Any] = {}
    for domain in _DIMENSIONS:
        parsed = getattr(summary, domain).model_dump()
        parsed["missing"] = list(dict.fromkeys([*missing[domain], *parsed["missing"]]))[:8]
        dimensions[domain] = parsed
    report = {
        "report_id": "stock-research:" + uuid4().hex,
        "status": "complete",
        "generated_at": now_shanghai().isoformat(),
        "symbol": code,
        "name": name,
        "model": endpoint.model,
        "summary": summary.summary,
        "summary_evidence_ids": summary.summary_evidence_ids,
        "dimensions": dimensions,
        "evidence": evidence,
        "missing_evidence": list(dict.fromkeys([
            *summary.missing_evidence,
            *(item for domain in _DIMENSIONS for item in missing[domain]),
        ]))[:12],
        "personal_context": context,
        "saved": False,
        "saved_at": None,
        "revision": 1,
        "notice": "当前研究时点的辅助分析，不改变确定性评分、持仓计划或成交记录，也不构成买卖指令。",
    }
    await asyncio.to_thread(_store_draft, report)
    return deepcopy(report)


def list_saved_reports(symbol: str | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        items = deepcopy(_load()["items"])
    reports = [item for item in items if item.get("saved") is True and (symbol is None or item.get("symbol") == symbol)]
    reports.sort(key=lambda item: item.get("saved_at") or item.get("generated_at") or "", reverse=True)
    return reports[:100]


def get_saved_report(report_id: str) -> dict[str, Any]:
    """Read one saved report from the current tenant without exposing draft IDs."""
    with _LOCK:
        report = next(
            (
                item
                for item in _load()["items"]
                if item.get("report_id") == report_id and item.get("saved") is True
            ),
            None,
        )
    if report is None:
        raise StockResearchError("已保存报告不存在或不属于当前租户")
    return deepcopy(report)


def save_report(report_id: str) -> dict[str, Any]:
    path = _file()
    with _LOCK, advisory_lock(path):
        payload = _load()
        report = next((item for item in payload["items"] if item.get("report_id") == report_id), None)
        if report is None:
            raise StockResearchError("报告草稿不存在或已过期，请重新分析")
        if report.get("status") != "complete":
            raise StockResearchError("只有完成且证据通过校验的报告才能保存")
        if not report.get("saved"):
            report["saved"] = True
            report["saved_at"] = now_shanghai().isoformat()
            report["revision"] = int(report.get("revision") or 0) + 1
            _write(payload)
        return deepcopy(report)


__all__ = [
    "StockResearchError",
    "StockResearchModelError",
    "get_saved_report",
    "list_saved_reports",
    "run_stock_research",
    "save_report",
]
