from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from aqsp.briefing.agent_roles import AgentRole
from aqsp.briefing.debate import debate_active_role_summary
from aqsp.briefing.debate_tracker import DebateQualityAudit, audit_debate_quality


@dataclass(frozen=True)
class DebateConclusionView:
    headline: str = ""
    active_roles_line: str = ""
    role_selection_line: str = ""
    role_plan_line: str = ""
    risk_gate_line: str = ""
    validation_line: str = ""
    invalidation_line: str = ""
    trigger_line: str = ""
    historical_context_line: str = ""
    reliability_line: str = ""
    support_line: str = ""
    opposition_line: str = ""
    watch_line: str = ""
    runtime_status_line: str = ""
    historical_evaluation_only: bool = True
    candidate_mapped: bool = False
    quality_audit: DebateQualityAudit | None = None


@dataclass(frozen=True)
class DebateEvidenceProvenance:
    """Evidence buckets kept separate so rules are not presented as news."""

    real_messages: tuple[str, ...] = ()
    cross_market_evidence: tuple[str, ...] = ()
    rule_transmissions: tuple[str, ...] = ()
    pending_confirmations: tuple[str, ...] = ()


def debate_evidence_provenance(result: Any) -> DebateEvidenceProvenance:
    """Read explicit provenance and provide a safe fallback for old results."""
    lines = _market_context_lines(result)

    def values(name: str) -> tuple[str, ...]:
        return tuple(
            text
            for item in (getattr(result, name, ()) or ())
            if (text := str(item).strip()) and not _is_non_evidence_text(text)
        )

    messages = values("real_message_evidence")
    if not messages:
        messages = tuple(
            message
            for line in lines
            if (message := _message_evidence_from_context_line(line))
        )

    cross_market = values("cross_market_evidence")
    if not cross_market:
        cross_market = tuple(
            line[len("跨市证据:") :].strip()
            for line in lines
            if line.startswith("跨市证据:")
            and line[len("跨市证据:") :].strip()
            and not _is_non_evidence_text(line[len("跨市证据:") :].strip())
        )

    rules = values("rule_transmission_evidence")
    if not rules:
        rules = tuple(
            line
            for line in lines
            if line.startswith(("传导推演[", "候选传导:", "传导链:"))
            and not _is_non_evidence_text(line)
        )

    pending = values("pending_confirmations")
    if not pending:
        pending = tuple(
            line for line in lines if line.startswith(("确认信号:", "失效条件:"))
        )
    return DebateEvidenceProvenance(
        real_messages=tuple(dict.fromkeys(messages)),
        cross_market_evidence=tuple(dict.fromkeys(cross_market)),
        rule_transmissions=tuple(dict.fromkeys(rules)),
        pending_confirmations=tuple(dict.fromkeys(pending)),
    )


_PROVENANCE_GAP_MARKERS = (
    "输入未提供",
    "无可用",
    "无新增",
    "尚未形成",
    "等待更多确认",
    "等待新证据",
    "未筛出",
    "抓取失败",
    "不能确认",
    "无法确认",
    "暂不确认",
    "不据此形成判断",
)


def _is_non_evidence_text(value: object) -> bool:
    text = str(value or "").strip()
    return not text or any(marker in text for marker in _PROVENANCE_GAP_MARKERS)


def _message_evidence_from_context_line(value: object) -> str:
    text = str(value or "").strip()
    for prefix in ("候选消息:", "个股催化:", "全局雷达:", "消息结果:"):
        if text.startswith(prefix):
            message = text[len(prefix) :].strip()
            return "" if _is_non_evidence_text(message) else message
    return ""


def _market_context_lines(result: Any) -> tuple[str, ...]:
    return tuple(
        str(line).strip()
        for line in (getattr(result, "market_context_lines", ()) or ())
        if str(line).strip()
    )


def _context_line_value(
    result: Any,
    *,
    attr_names: tuple[str, ...] = (),
    prefixes: tuple[str, ...] = (),
) -> str:
    for attr_name in attr_names:
        value = str(getattr(result, attr_name, "") or "").strip()
        if value:
            return value
    if not prefixes:
        return ""
    for raw in _market_context_lines(result):
        for prefix in prefixes:
            if raw.startswith(prefix):
                return raw[len(prefix) :].strip()
    return ""


def _selected_roles(result: Any) -> tuple[AgentRole | str, ...]:
    configured_roles = tuple(
        str(role).strip()
        for role in (getattr(result, "expected_roles", ()) or ())
        if str(role).strip()
    )
    if configured_roles:
        return configured_roles
    roles: list[AgentRole | str] = []
    for role in getattr(result, "final_vote", {}) or {}:
        if role not in roles:
            roles.append(role)
    if roles:
        return tuple(roles)
    for round_data in getattr(result, "rounds", ()) or ():
        for opinion in getattr(round_data, "opinions", ()) or ():
            role = getattr(opinion, "role", None)
            if role is not None and role not in roles:
                roles.append(role)
    return tuple(roles)


def _opinion_points(
    result: Any,
    *,
    stances: tuple[str, ...],
    risk: bool = False,
) -> tuple[str, ...]:
    rounds = tuple(getattr(result, "rounds", ()) or ())
    if not rounds:
        return ()
    opinions = tuple(getattr(rounds[-1], "opinions", ()) or ())
    points: list[str] = []
    for opinion in opinions:
        if str(getattr(opinion, "stance", "") or "") not in stances:
            continue
        fields = (
            ("risk_factors", "arguments")
            if risk
            else (
                "opportunity_factors",
                "arguments",
            )
        )
        for field_name in fields:
            for raw in getattr(opinion, field_name, ()) or ():
                text = str(raw).strip()
                if text and text not in points:
                    points.append(text)
                if len(points) >= 2:
                    return tuple(points)
    return tuple(points)


_LEGACY_EVIDENCE_GAP_MARKERS = (
    "输入未提供",
    "未提供可核验",
    "尚未形成明确",
    "等待更多确认",
    "等待新证据",
)


def _legacy_conclusion_is_compatible(
    result: Any,
    audit: DebateQualityAudit,
) -> bool:
    """Allow old conclusion-only results without weakening the new audit."""
    # Old reports may omit rounds, but they must not bypass the live-data gate.
    if (
        not audit.candidate_mapped
        or getattr(result, "rounds", ())
        or str(getattr(result, "data_status", "available") or "available")
        != "available"
    ):
        return False
    # Once a producer writes quality metadata, a consumer must not downgrade
    # an explicit failed/incomplete audit into a legacy conclusion.
    if isinstance(result, Mapping) and any(
        field_name in result
        for field_name in (
            "process_recorded",
            "conclusion_recorded",
            "debate_quality_issues",
            "evidence_sufficient",
        )
    ):
        return False
    if any(
        hasattr(result, field_name)
        for field_name in (
            "process_recorded",
            "conclusion_recorded",
            "debate_quality_issues",
            "evidence_sufficient",
        )
    ):
        return False
    if int(getattr(result, "debate_rounds_requested", 0) or 0) > 0:
        return False
    if any(
        getattr(result, field_name, ())
        for field_name in (
            "real_message_evidence",
            "cross_market_evidence",
            "rule_transmission_evidence",
            "pending_confirmations",
            "cross_market_evidence_stack_summary",
        )
    ):
        return False
    if not audit.conclusion_recorded:
        return False

    legacy_text = " ".join(
        str(item).strip()
        for field_name in ("support_points", "opposition_points", "risk_warnings")
        for item in (getattr(result, field_name, ()) or ())
        if str(item).strip()
    )
    return not (
        not audit.evidence_sufficient
        and any(marker in legacy_text for marker in _LEGACY_EVIDENCE_GAP_MARKERS)
    )


def _quality_block_headline(audit: DebateQualityAudit) -> str:
    if "empty_market_data" in audit.issues:
        return "结论已阻断：行情数据为空"
    if "no_substantive_evidence" in audit.issues:
        return "结论已阻断：缺少可核验证据"
    if "non_interactive_round" in audit.issues:
        return "结论已阻断：多轮讨论未形成有效交锋"
    if "advisory_boundary_violation" in audit.issues:
        return "结论已阻断：越过 advisory-only 边界"
    return "结论已阻断：讨论链路不完整"


def build_debate_conclusion_view(
    result: Any,
    *,
    candidate: Any | None = None,
    language: str = "zh-CN",
    max_role_labels: int = 5,
) -> DebateConclusionView:
    selected_roles = _selected_roles(result)
    if selected_roles:
        quality_audit = audit_debate_quality(
            result,
            candidate=candidate,
            expected_roles=selected_roles,
        )
    else:
        quality_audit = audit_debate_quality(result, candidate=candidate)
    if (
        selected_roles
        and len(selected_roles) < 2
        and "insufficient_roles" not in quality_audit.issues
    ):
        quality_audit = replace(
            quality_audit,
            process_recorded=False,
            issues=(*quality_audit.issues, "insufficient_roles"),
        )
    if _legacy_conclusion_is_compatible(result, quality_audit):
        quality_audit = None
    if quality_audit is not None and not quality_audit.candidate_mapped:
        return DebateConclusionView(
            headline="结论已阻断：无法映射候选",
            candidate_mapped=False,
            quality_audit=quality_audit,
        )
    if quality_audit is not None and not quality_audit.passed:
        return DebateConclusionView(
            headline=_quality_block_headline(quality_audit),
            candidate_mapped=True,
            historical_context_line=str(
                getattr(result, "historical_context_note", "") or ""
            ).strip(),
            quality_audit=quality_audit,
        )

    headline = (
        str(getattr(result, "research_verdict", "") or "").strip()
        or str(getattr(result, "final_consensus", "") or "").strip()
        or str(getattr(result, "adjustment_reason", "") or "").strip()
        or "暂无共识摘要"
    )
    active_role_summary = debate_active_role_summary(
        result,
        language=language,
        max_labels=max_role_labels,
    )
    role_selection_summary = str(
        getattr(result, "role_selection_summary", "") or ""
    ).strip()
    role_selection_plan = str(getattr(result, "role_selection_plan", "") or "").strip()
    primary_risk_gate = str(getattr(result, "primary_risk_gate", "") or "").strip()
    validation_signal = _context_line_value(
        result,
        attr_names=("cross_market_validation_summary",),
        prefixes=("确认信号:", "确认条件:"),
    )
    invalidation_signal = _context_line_value(
        result,
        attr_names=("cross_market_invalidation_summary",),
        prefixes=("失效条件:", "失效信号:"),
    )
    next_trigger = str(getattr(result, "next_trigger", "") or "").strip()
    historical_context_note = str(
        getattr(result, "historical_context_note", "") or ""
    ).strip()
    role_reliability_lines = tuple(
        str(item).strip()
        for item in (getattr(result, "role_reliability_lines", ()) or ())
        if str(item).strip()
    )
    support_points = tuple(
        str(item).strip()
        for item in (getattr(result, "support_points", ()) or ())
        if str(item).strip()
    )
    if not support_points:
        support_points = _opinion_points(result, stances=("bullish",))
    opposition_points = tuple(
        str(item).strip()
        for item in (getattr(result, "opposition_points", ()) or ())
        if str(item).strip()
    )
    if not opposition_points:
        opposition_points = _opinion_points(
            result,
            stances=("bearish", "neutral"),
            risk=True,
        )
    watch_items = tuple(
        str(item).strip()
        for item in (getattr(result, "watch_items", ()) or ())
        if str(item).strip()
    )
    runtime_blocked = bool(getattr(result, "realtime_blocked", False))
    runtime_status = str(getattr(result, "runtime_status", "") or "").strip()
    runtime_blocker = str(getattr(result, "runtime_blocker", "") or "").strip()
    if not primary_risk_gate and opposition_points:
        primary_risk_gate = opposition_points[0]
    if not next_trigger and (
        bool(getattr(result, "rounds", ()) or ()) or runtime_blocked
    ):
        next_trigger = (
            watch_items[0]
            if watch_items
            else "等待实时量价、消息与跨市场上下文确认后再复核。"
        )
    return DebateConclusionView(
        headline=headline,
        active_roles_line=(
            f"讨论视角: {active_role_summary}" if active_role_summary else ""
        ),
        role_selection_line=(
            f"选角理由: {role_selection_summary}" if role_selection_summary else ""
        ),
        role_plan_line=(
            f"角色分工: {role_selection_plan}" if role_selection_plan else ""
        ),
        risk_gate_line=(f"核心卡点: {primary_risk_gate}" if primary_risk_gate else ""),
        validation_line=(f"确认信号: {validation_signal}" if validation_signal else ""),
        invalidation_line=(
            f"失效信号: {invalidation_signal}" if invalidation_signal else ""
        ),
        trigger_line=(f"下一触发: {next_trigger}" if next_trigger else ""),
        historical_context_line=(
            historical_context_note
            if historical_context_note.startswith("历史校验:")
            else (
                f"历史校验: {historical_context_note}"
                if historical_context_note
                else ""
            )
        ),
        reliability_line=(
            f"角色可信度: {'；'.join(role_reliability_lines[:2])}"
            if role_reliability_lines
            else ""
        ),
        support_line=(
            f"支持观点: {'；'.join(support_points[:2])}" if support_points else ""
        ),
        opposition_line=(
            f"反对观点: {'；'.join(opposition_points[:2])}" if opposition_points else ""
        ),
        watch_line=(f"待确认: {'；'.join(watch_items[:2])}" if watch_items else ""),
        runtime_status_line=(
            f"运行状态: {'阻塞' if runtime_blocked else runtime_status}"
            + (f"｜{runtime_blocker}" if runtime_blocker else "")
            if runtime_blocked or runtime_status == "observation"
            else ""
        ),
        historical_evaluation_only=True,
        candidate_mapped=True,
        quality_audit=quality_audit,
    )


def debate_summary_segments(
    result: Any,
    *,
    candidate: Any | None = None,
    language: str = "zh-CN",
    max_role_labels: int = 4,
) -> tuple[str, ...]:
    view = build_debate_conclusion_view(
        result,
        candidate=candidate,
        language=language,
        max_role_labels=max_role_labels,
    )
    if not view.candidate_mapped:
        return (view.headline,)
    risk_warnings = tuple(
        str(item).strip()
        for item in (getattr(result, "risk_warnings", ()) or ())
        if str(item).strip()
    )
    opportunity_highlights = tuple(
        str(item).strip()
        for item in (getattr(result, "opportunity_highlights", ()) or ())
        if str(item).strip()
    )
    parts = [view.headline]
    if view.runtime_status_line:
        parts.append(view.runtime_status_line)
    if view.active_roles_line:
        parts.append(view.active_roles_line.replace("讨论视角: ", "视角: "))
    if view.role_selection_line:
        parts.append(view.role_selection_line.replace("选角理由: ", "选角: "))
    if view.role_plan_line:
        parts.append(view.role_plan_line.replace("角色分工: ", "分工: "))
    if view.risk_gate_line:
        parts.append(view.risk_gate_line.replace("核心卡点: ", "卡点: "))
    elif risk_warnings:
        parts.append(f"风险: {risk_warnings[0]}")
    elif opportunity_highlights:
        parts.append(f"机会: {opportunity_highlights[0]}")
    if view.validation_line:
        parts.append(view.validation_line.replace("确认信号: ", "确认: "))
    if view.invalidation_line:
        parts.append(view.invalidation_line.replace("失效信号: ", "失效: "))
    if view.trigger_line:
        parts.append(view.trigger_line.replace("下一触发: ", "触发: "))
    elif view.watch_line:
        parts.append(view.watch_line.replace("待确认: ", "待确认: "))
    return tuple(part for part in parts if part)


def debate_consensus_point(
    result: Any,
    *,
    candidate: Any | None = None,
    language: str = "zh-CN",
    max_role_labels: int = 4,
) -> str:
    view = build_debate_conclusion_view(
        result,
        candidate=candidate,
        language=language,
        max_role_labels=max_role_labels,
    )
    if not view.candidate_mapped:
        return ""
    symbol = str(getattr(result, "symbol", "") or "").strip()
    name = str(getattr(result, "name", "") or "").strip()
    display = f"{name}({symbol})" if name or symbol else "未知标的"
    if view.quality_audit is not None and not view.quality_audit.passed:
        return f"委员会阻塞: {display} {view.headline}"
    recommendation = str(getattr(result, "recommended_adjustment", "") or "").strip()
    disagreement_score = float(getattr(result, "disagreement_score", 0.0) or 0.0)
    adjusted_score = float(getattr(result, "adjusted_score", 0.0) or 0.0)

    if recommendation == "raise":
        headline = view.headline if view.headline != "暂无共识摘要" else "倾向上调"
        point = (
            f"委员会结论: {display} {headline}；"
            f"附件参考分{adjusted_score:.1f}，不改写系统评分"
        )
    elif recommendation == "lower":
        headline = view.headline if view.headline != "暂无共识摘要" else "倾向下调"
        point = (
            f"委员会结论: {display} {headline}；"
            f"附件参考分{adjusted_score:.1f}，不改写系统评分"
        )
    elif disagreement_score > 0.5:
        headline = (
            view.headline
            if view.headline != "暂无共识摘要"
            else f"多空分歧{disagreement_score:.0%}"
        )
        point = f"委员会分歧: {display} {headline}"
    elif bool(getattr(result, "realtime_blocked", False)):
        point = f"委员会阻塞观察: {display} {view.headline}"
    else:
        return ""

    suffixes: list[str] = []
    if view.support_line:
        suffixes.append(view.support_line.replace("支持观点: ", "支持 "))
    elif view.active_roles_line:
        suffixes.append(view.active_roles_line.replace("讨论视角: ", "视角 "))
    if view.opposition_line:
        suffixes.append(view.opposition_line.replace("反对观点: ", "反对 "))
    elif view.risk_gate_line:
        suffixes.append(view.risk_gate_line.replace("核心卡点: ", "卡点 "))
    if view.watch_line:
        suffixes.append(view.watch_line.replace("待确认: ", "待确认 "))
    elif view.trigger_line:
        suffixes.append(view.trigger_line.replace("下一触发: ", "触发 "))
    if not view.support_line and not view.opposition_line:
        if view.validation_line:
            suffixes.append(view.validation_line.replace("确认信号: ", "确认 "))
        if view.invalidation_line:
            suffixes.append(view.invalidation_line.replace("失效信号: ", "失效 "))
    if suffixes:
        point += "｜" + "｜".join(suffixes)
    return point


def cross_market_priority_digest(
    result: Any | None = None,
    *,
    overview: str = "",
    focus_display: str = "",
) -> str:
    overview_text = str(overview or "").strip()
    focus_text = str(focus_display or "").strip()
    transmission_text = ""
    if result is not None:
        for raw in _market_context_lines(result):
            if raw.startswith("传导推演["):
                transmission_text = raw.split(": ", 1)[1] if ": " in raw else raw
                transmission_parts = [
                    part.rstrip("。")
                    for part in transmission_text.split("；")
                    if part.strip()
                ]
                transmission_text = "；".join(transmission_parts[:2]).strip()
                break
            if raw.startswith("跨市传导:"):
                transmission_text = raw[len("跨市传导:") :].strip()
                break
    if not overview_text:
        overview_text = transmission_text
    view = build_debate_conclusion_view(result) if result is not None else None
    if (
        view is not None
        and view.quality_audit is not None
        and not view.quality_audit.passed
    ):
        return ""
    parts: list[str] = []
    if overview_text:
        parts.append(overview_text)
    if focus_text:
        parts.append(f"先看 {focus_text}")
    if view is not None and view.validation_line:
        parts.append(view.validation_line.replace("确认信号: ", "确认 "))
    if view is not None and view.invalidation_line:
        parts.append(view.invalidation_line.replace("失效信号: ", "失效 "))
    if len(parts) == 1 and focus_text and parts[0] == f"先看 {focus_text}":
        return ""
    if len(parts) <= 1 and parts[:1] == [overview_text]:
        return ""
    return " | ".join(part for part in parts if part)


def debate_focus_parts(
    result: Any,
    *,
    focus_display: str = "",
    language: str = "zh-CN",
    max_role_labels: int = 3,
) -> tuple[str, ...]:
    view = build_debate_conclusion_view(
        result,
        language=language,
        max_role_labels=max_role_labels,
    )
    if view.quality_audit is not None and not view.quality_audit.passed:
        return (view.headline,)
    cross_market_line = cross_market_priority_digest(
        result,
        overview="",
        focus_display=focus_display,
    )
    parts: list[str] = []
    if cross_market_line:
        parts.append(cross_market_line)
    else:
        parts.append(
            str(getattr(result, "final_consensus", "") or "").strip()
            or str(getattr(result, "adjustment_reason", "") or "").strip()
            or view.headline
            or "看分歧地图"
        )
    if view.support_line:
        parts.append(view.support_line.replace("支持观点: ", "支持 "))
    elif view.active_roles_line and not view.role_selection_line:
        parts.append(view.active_roles_line.replace("讨论视角: ", "视角 "))
    if view.opposition_line:
        parts.append(view.opposition_line.replace("反对观点: ", "反对 "))
    elif view.risk_gate_line:
        parts.append(view.risk_gate_line.replace("核心卡点: ", "卡点 "))
    if view.watch_line:
        parts.append(view.watch_line.replace("待确认: ", "待确认 "))
    elif view.trigger_line:
        parts.append(view.trigger_line.replace("下一触发: ", "触发 "))
    if view.role_selection_line:
        parts.append(view.role_selection_line.replace("选角理由: ", "选角 "))
    if view.role_plan_line:
        parts.append(view.role_plan_line.replace("角色分工: ", "分工 "))
    return tuple(part for part in parts if part)
