"""Briefing 渲染器 - 从结构化数据生成各种格式输出。

宪法要求：渲染器只读取结构化数据，不反向解析 markdown。
"""

from __future__ import annotations

from datetime import date

from aqsp.briefing.conclusion import build_debate_conclusion_view
from aqsp.briefing.debate import debate_active_role_summary
from aqsp.briefing.schema import BriefingData, CommitteeConclusion
from aqsp.core.time import now_shanghai
from aqsp.presentation import (
    describe_source_health,
    describe_source_layers,
    display_section_title,
    format_review_meta,
    format_symbol_name,
    format_watch_review_line,
    normalize_research_tone,
    review_priority_label,
)
from aqsp.ratings import rating_label, portfolio_action_label
from aqsp.research.summary import research_findings_display


def _candidate_status_label(pick) -> str:
    return str(pick.metrics.get("candidate_status", "") or "")


def _candidate_blocker_label(pick) -> str:
    return str(pick.metrics.get("candidate_blocker", "") or "")


def _candidate_next_step_label(pick) -> str:
    return str(pick.metrics.get("candidate_next_step", "") or "")


def _candidate_review_window_label(pick) -> str:
    return str(pick.metrics.get("candidate_review_window", "") or "")


def _candidate_review_priority_label(pick) -> str:
    value = str(pick.metrics.get("candidate_review_priority", "") or "")
    return _review_priority_label(value)


def _review_priority_label(value: str) -> str:
    return review_priority_label(value)


def _format_pick_with_status(pick, *, include_score: bool = False) -> str:
    display = format_symbol_name(pick.symbol, pick.name)
    status = _candidate_status_label(pick)
    if status:
        display = f"{display}({status})"
    if include_score:
        display = f"{display}({pick.score:.1f}分)"
    return display


def _date_only(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text.replace("T", " ").split(" ", 1)[0]
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return ""


def _is_today(value: object) -> bool:
    return _date_only(value) == now_shanghai().date().isoformat()


def _join_or_default(values: tuple[str, ...], default: str) -> str:
    return "；".join(values[:3]) if values else default


class MarkdownRenderer:
    """将 BriefingData 渲染为 markdown 格式。"""

    def render(self, data: BriefingData) -> str:
        """生成完整的 markdown 日报。"""
        lines = [f"# 每日研究复盘-{data.date}", ""]

        lines.extend(self._render_committee_conclusions(data))
        lines.extend(self._render_main_chain(data))
        lines.extend(self._render_regime(data))
        lines.extend(self._render_source(data))
        lines.extend(self._render_research(data))
        lines.extend(self._render_decision_context(data))
        lines.extend(self._render_evidence(data))
        lines.extend(self._render_theme(data))
        lines.extend(self._render_next_day(data))
        lines.extend(self._render_artifacts(data))
        lines.extend(self._render_debate_process(data))

        lines.append("> 仅供研究，不构成交易指令或投资建议。")
        return normalize_research_tone("\n".join(lines))

    def _render_committee_conclusions(self, data: BriefingData) -> list[str]:
        if not data.debate_results:
            return []

        conclusions = tuple(
            CommitteeConclusion.from_debate_result(result)
            for result in data.debate_results[:3]
        )
        all_current = all(
            _is_today(item.signal_date or data.date) for item in conclusions
        )
        heading_suffix = "今日 advisory-only" if all_current else "历史归档，非今日建议"
        lines = [f"## 多 Agent 结论（{heading_suffix}）", ""]
        lines.append("重点候选的委员会结论如下：")
        lines.append(
            "委员会只提供 advisory-only 研究参考，不改写确定性评分，也不构成交易指令。"
        )
        lines.append("")

        for conclusion in conclusions:
            signal_date = _date_only(conclusion.signal_date) or _date_only(data.date)
            signal_date = signal_date or "未记录"
            date_note = "今日信号" if _is_today(signal_date) else "历史信号，非今日建议"
            confidence = (
                f"{conclusion.confidence:.0%}"
                if conclusion.confidence is not None
                else "未记录"
            )
            lines.extend(
                [
                    f"### 多 Agent 结论 - {conclusion.symbol} {conclusion.name}",
                    f"- 信号日期: {signal_date}（{date_note}）",
                    "- 数据状态: "
                    + (
                        "可用"
                        if conclusion.data_status == "available"
                        else f"空数据，{conclusion.data_note or '不形成证据结论'}"
                    ),
                    f"- 结论: **{conclusion.headline}**",
                    f"- 委员会置信度: **{confidence}**",
                    "- 投票: "
                    f"看多 {conclusion.bullish_votes} / "
                    f"看空 {conclusion.bearish_votes} / "
                    f"中性 {conclusion.neutral_votes}",
                    "- 支持理由: "
                    + _join_or_default(conclusion.support_points, "未记录"),
                    "- 事件证据: "
                    + _join_or_default(conclusion.event_evidence, "未记录"),
                    "- 跨市证据: "
                    + _join_or_default(conclusion.cross_market_evidence, "未记录"),
                    "- 传导链: "
                    + _join_or_default(conclusion.transmission_points, "未记录"),
                    "- 反对理由: "
                    + _join_or_default(conclusion.opposition_points, "未记录"),
                    "- 风险: " + _join_or_default(conclusion.risk_points, "未记录"),
                    "- 失效条件: "
                    + _join_or_default(
                        conclusion.failure_conditions,
                        "未记录，需补充实时验证",
                    ),
                    "- 待确认: "
                    + _join_or_default(
                        conclusion.pending_confirmations,
                        "未记录，需补充实时验证",
                    ),
                    "- 评分边界: 确定性评分保持不变；委员会结果仅作 advisory-only 附件",
                ]
            )
            if not conclusion.advisory_only:
                lines.append("- 边界异常: advisory-only 标记缺失，禁止据此形成行动判断")
            lines.append("")

        return lines

    def _render_debate_process(self, data: BriefingData) -> list[str]:
        if not data.debate_results:
            return []

        lines = ["## 结构化讨论过程", ""]
        lines.append(
            "以下仅记录角色、轮次、投票与证据分层；不展示原始 AI 思考话术，过程不能覆盖确定性评分。"
        )
        lines.append("")
        for result in data.debate_results[:3]:
            conclusion = CommitteeConclusion.from_debate_result(result)
            view = build_debate_conclusion_view(result)
            roles = "、".join(conclusion.active_roles) or "未记录"
            lines.append(f"### {conclusion.symbol} {conclusion.name}")
            lines.append(f"- 讨论轮次: {conclusion.round_count}")
            lines.append(f"- 参与角色: {roles}")
            if result.rounds:
                lines.append("- 轮次摘要（结构化字段）:")
                for round_data in result.rounds[:3]:
                    summary = str(round_data.summary or "").strip() or "未提供"
                    lines.append(f"  - 第{round_data.round_num}轮: {summary}")
            lines.append(
                "- 最终投票: "
                f"看多 {conclusion.bullish_votes} / "
                f"看空 {conclusion.bearish_votes} / "
                f"中性 {conclusion.neutral_votes}"
            )
            if view.quality_audit is not None:
                status = "通过" if view.quality_audit.passed else "未通过"
                lines.append(f"- 过程审计: {status}")
            if conclusion.llm_advisory_count:
                lines.append(
                    f"- LLM advisory: {conclusion.llm_advisory_count} 个角色有增强内容，已留在审计字段，未作为结论"
                )
            lines.append("- 过程边界: advisory-only；不改写确定性评分")
            lines.append("")
        return lines

    def _render_main_chain(self, data: BriefingData) -> list[str]:
        """渲染主链总览。"""
        lines = [f"## {display_section_title('主链总览')}", ""]

        if not data.picks or data.portfolio_summary is None:
            lines.append("今日无主链候选，保持观察。")
            lines.append("")
            return lines

        ps = data.portfolio_summary
        lines.append(f"- PM主裁决: {ps.headline}")

        signal_date = data.picks[0].date if data.picks else ""
        if signal_date:
            lines.append(f"- 信号日期: {signal_date}")

        if ps.top_focus:
            lines.append("- 主看名单: " + "、".join(ps.top_focus[:3]))

        if ps.watchlist:
            lines.append("- 观察名单: " + "、".join(ps.watchlist[:3]))
        if ps.portfolio_risk_lines:
            lines.append("- 组合风险: " + "；".join(ps.portfolio_risk_lines[:2]))
        if ps.watch_reviews:
            lines.append("- 观察名单:")
            for item in ps.watch_reviews[:2]:
                lines.append(
                    "  - "
                    + format_watch_review_line(
                        format_symbol_name(item.symbol, item.name),
                        priority=item.priority,
                        review_window=item.review_window,
                        next_step=item.next_step,
                    )
                )
        active_role_summary = self._debate_role_summary(data)
        if active_role_summary:
            lines.append(f"- 讨论视角: {active_role_summary}")
        role_selection_plan = self._debate_role_plan_summary(data)
        if role_selection_plan:
            lines.append(f"- 角色分工: {role_selection_plan}")

        lead = data.picks[0]
        lead_display = format_symbol_name(lead.symbol, lead.name)
        lead_status = _candidate_status_label(lead)
        lead_line = f"- 当前主看: {lead_display} | {rating_label(lead.rating)}"
        if lead_status:
            lead_line += f" | {lead_status}"
        lead_line += f" | 评分 {lead.score:.1f}"
        lines.append(lead_line)

        if not ps.top_focus:
            lines.append("- 今日复核: 仅观察，不放大纸面仓位。")

        lines.append("")
        return lines

    def _render_decision_context(self, data: BriefingData) -> list[str]:
        lines = ["## 候选上下文", ""]

        if not data.decision_context_cards:
            lines.append("暂无候选上下文卡。")
            lines.append("")
            return lines

        for card in data.decision_context_cards[:5]:
            display = format_symbol_name(card.symbol, card.name)
            lines.append(f"### {display}")
            for label, value in (
                ("量价", card.price_signal),
                ("消息", card.news_judgement),
                ("跨市", card.cross_market),
                ("讨论", card.debate),
                ("风险", card.risk),
                ("下一步", card.next_step),
            ):
                if value:
                    lines.append(f"- {label}: {value}")
            if card.artifact_ids:
                lines.append("- 证据: " + "、".join(card.artifact_ids[:3]))
            lines.append("")

        return lines

    def _render_regime(self, data: BriefingData) -> list[str]:
        """渲染市场态势。"""
        lines = [f"## {display_section_title('市场态势')}", ""]

        if data.regime_info.circuit_breaker_triggered:
            reason = data.regime_info.circuit_breaker_reason
            lines.append(f"> ⚠️ **组合保护中**: {reason}")
            lines.append("")

        lines.append(f"当前市场态势: **{data.regime_info.description}**")
        lines.append("")
        return lines

    def _render_artifacts(self, data: BriefingData) -> list[str]:
        if not data.artifacts:
            return []

        lines = ["## 产物追溯", ""]
        for artifact in data.artifacts[:6]:
            source_text = "、".join(artifact.sources[:3]) or "-"
            version_text = "、".join(
                f"{key}={value}"
                for key, value in sorted(artifact.upstream_versions.items())[:3]
            )
            if not version_text:
                version_text = "-"
            hash_text = artifact.input_hash or "-"
            lines.append(
                f"- {artifact.artifact_id} | {artifact.artifact_type} | {artifact.generated_at} | 来源 {source_text} | hash {hash_text} | 版本 {version_text}"
            )
        lines.append("")
        return lines

    def _render_source(self, data: BriefingData) -> list[str]:
        """渲染数据源状态。"""
        lines = [f"## {display_section_title('数据源状态')}", ""]

        if data.source_status is None:
            lines.append("暂无最近一次运行的数据源状态记录。")
            lines.append("")
            return lines

        s = data.source_status
        lines.append(f"- 数据来源: **{s.route}**")
        lines.append(
            f"- 数据完整度: {describe_source_layers(s.freshness_tier, s.coverage_tier)}"
        )
        lines.append(
            f"- 数据状态: **{describe_source_health(s.health_label, s.health_message)}**"
        )
        lines.append(f"- 是否启用备用源: {'是' if s.fallback_used else '否'}")

        if s.is_degraded:
            lines.append("- 复核: 本次结果需人工复核。")

        lines.append("")
        return lines

    def _render_research(self, data: BriefingData) -> list[str]:
        """渲染研究吸收。"""
        lines = [f"## {display_section_title('研究吸收')}", ""]

        if data.research_summary is None:
            lines.append("研究进展本次未更新；这份日报只基于当前主链结果。")
            lines.append("")
            return lines

        rs = data.research_summary
        lines.append(f"- 研究发现落盘: **{research_findings_display(rs)}**")
        lines.append(f"- 已纳入观察但不直接打分: **{len(rs.absorbed_families)}**")
        lines.append(f"- 已部分实现策略: **{rs.implemented_family_count}**")
        lines.append(f"- 仅写进研究记录: **{rs.report_only_family_count}**")
        lines.append(f"- 需满足条件后启用: **{rs.gated_family_count}**")
        if rs.repo_intake_total:
            lines.append(
                "- 开源扫描池: "
                f"共 {rs.repo_intake_total} 项 / "
                f"底座候选 {rs.repo_substrate_candidate_count} / "
                f"执行红线 {rs.repo_reject_boundary_count} / "
                f"仅记录 {rs.repo_report_only_count}"
            )
        if rs.repo_lane_summaries:
            lanes = "、".join(
                f"{item.lane} {item.count}" for item in rs.repo_lane_summaries[:4]
            )
            lines.append(f"- 扫描分类: {lanes}")
        if rs.repo_backlog:
            item = rs.repo_backlog[0]
            lines.append(
                f"- 开源接入队列: {item.repo} [{item.priority}/{item.lane}] -> {item.landing}"
            )

        top_pipelines = list(rs.pipeline_summaries[:3])
        if top_pipelines:
            for item in top_pipelines:
                lines.append(
                    f"- 研究来源 {item.pipeline}: 高优先级 {item.p1} / 共 {item.total} / 先参考 {item.top_repo or '-'}"
                )

        if rs.absorbed_families:
            names = "、".join(
                f"{item.name}({item.runtime_stage})"
                for item in rs.absorbed_families[:4]
            )
            lines.append(f"- 已吸收主题: {names}")

        if rs.next_actions:
            next_item = rs.next_actions[0]
            lines.append(
                f"- 门控候选主题: {next_item.kind}/{next_item.item_id} [{next_item.priority}] - {next_item.blocker or '还缺前置条件'}"
            )

        prereq_item = next(
            (item for item in rs.prereq_items if item.status != "ready"),
            None,
        )
        if prereq_item is not None:
            missing_env = "、".join(prereq_item.missing_env_vars) or "回归样本"
            lines.append(
                f"- 当前前置缺口: {prereq_item.kind}/{prereq_item.item_id} - {prereq_item.status} ({missing_env})"
            )

        lines.append("- 原则: 研究内容只做候选和解释，不直接改写系统评分。")
        lines.append("")
        return lines

    def _render_evidence(self, data: BriefingData) -> list[str]:
        """渲染候选来龙去脉。"""
        lines = [f"## {display_section_title('候选来龙去脉')}", ""]

        if not data.picks:
            lines.append("今日无候选标的。")
            lines.append("")
            return lines

        for pick in data.picks:
            display = format_symbol_name(pick.symbol, pick.name)
            pm_action = str(pick.metrics.get("portfolio_action", "") or "")
            pm_text = portfolio_action_label(pm_action) if pm_action else "未裁决"
            candidate_status = _candidate_status_label(pick)
            blocker = _candidate_blocker_label(pick)
            next_step = _candidate_next_step_label(pick)
            review_meta = format_review_meta(
                _candidate_review_priority_label(pick),
                _candidate_review_window_label(pick),
            )
            headline = (
                f"### {display} (评分: {pick.score} / {rating_label(pick.rating)}"
            )
            if candidate_status:
                headline += f" / 状态: {candidate_status}"
            headline += f" / PM: {pm_text})"
            lines.append(headline)

            if pick.strategies:
                lines.append(f"- 命中策略: {', '.join(pick.strategies)}")

            for reason in pick.reasons:
                lines.append(f"- {reason}")
            if blocker:
                lines.append(f"- 阻塞: {blocker}")
            if next_step:
                lines.append(f"- 下一步: {next_step}")
            if review_meta:
                lines.append(f"- 复核窗口: {review_meta}")

            if pick.risks:
                lines.append(f"风险提示: {'；'.join(pick.risks)}")

            lines.append("")

        return lines

    def _render_theme(self, data: BriefingData) -> list[str]:
        """渲染题材热度。"""
        lines = ["## 题材热度", ""]

        if not data.theme_heats:
            if data.picks:
                lines.append("今日候选未归类到已知题材。")
            else:
                lines.append("无题材热度数据。")
            lines.append("")
            return lines

        for heat in data.theme_heats:
            lines.append(f"- **{heat.label}**: {heat.count} 条线索")

        lines.append("")
        return lines

    def _render_next_day(self, data: BriefingData) -> list[str]:
        """渲染明日重点。"""
        is_current = _is_today(data.date)
        section_title = "明日重点" if is_current else "历史后续观察（非今日建议）"
        lines = [f"## {section_title}", ""]
        if not is_current:
            lines.append("以下只记录该历史信号日的后续观察，不代表今日建议。")
            lines.append("")

        tradable = data.tradable_picks
        if not tradable:
            if data.picks:
                lead = data.picks[0]
                names = "、".join(_format_pick_with_status(p) for p in data.picks[:3])
                blocker = _candidate_blocker_label(lead)
                next_step = _candidate_next_step_label(lead)
                review_meta = format_review_meta(
                    _candidate_review_priority_label(lead),
                    _candidate_review_window_label(lead),
                )
                line = (
                    f"{'今日' if is_current else '该信号日'}无纸面复核对象；观察名单: {names}。"
                    "待阻塞解除后再考虑转入纸面复核名单。"
                )
                if blocker:
                    line += f" 阻塞: {blocker}。"
                if next_step:
                    line += f" 下一步: {next_step}。"
                if review_meta:
                    line += f" 复核窗口: {review_meta}。"
                lines.append(line)
            else:
                lines.append("无纸面复核重点标的；今日无候选，继续等待下一轮信号。")

            lines.append("")
            return lines

        for pick in tradable[:5]:
            display = _format_pick_with_status(pick)
            lines.append(
                f"- **{display}**: "
                f"记录时价格 {pick.ideal_buy} / 最多亏到 {pick.stop_loss} / 先看目标 {pick.take_profit} / 纸面仓位上限 {pick.position}"
            )

        lines.append("")
        lines.append("> 注: 事件型催化尚未纳入主链门控，需人工补充复核。")
        lines.append("")
        return lines

    def generate_smart_summary(self, data: BriefingData) -> str:
        """生成智能摘要（邮件/通知用）。

        直接从结构化数据读取，不再反向解析 markdown。
        """
        one_liner = self._build_one_liner(data)
        lines = [f"**{one_liner}**", ""]

        lines.extend(
            self._format_summary_block("核心结论", self._build_core_items(data))
        )
        lines.extend(
            self._format_summary_block("数据透视", self._build_data_items(data))
        )
        lines.extend(
            self._format_summary_block("作战计划", self._build_action_items(data))
        )
        lines.extend(
            self._format_summary_block("风险提示", self._build_risk_items(data))
        )

        lines.append("")
        return "\n".join(lines)

    def _format_summary_block(self, title: str, items: list[str]) -> list[str]:
        if not items:
            return [f"### {title}", "- 无", ""]
        return [f"### {title}", *items, ""]

    @staticmethod
    def _strip_leading_markers(text: str) -> str:
        return text.lstrip("📉📊📈🤖⚠️ ").strip()

    def _build_core_items(self, data: BriefingData) -> list[str]:
        items: list[str] = []
        if data.portfolio_summary:
            ps = data.portfolio_summary
            items.append(f"- PM主裁决: {ps.headline}")
            if ps.top_focus:
                items.append("- 主链候选: " + "、".join(ps.top_focus))
            if ps.watchlist:
                items.append("- 观察名单: " + "、".join(ps.watchlist))
        if data.debate_results:
            items.append(f"- 委员会覆盖: 已分析 {len(data.debate_results)} 只重点候选")
        else:
            items.append("- 委员会覆盖: 今日无重点标的或处于冷却期")
        active_role_summary = self._debate_role_summary(data)
        if active_role_summary:
            items.append(f"- 讨论视角: {active_role_summary}")
        role_selection_summary = self._debate_role_selection_summary(data)
        if role_selection_summary:
            items.append(f"- 选角理由: {role_selection_summary}")
        role_selection_plan = self._debate_role_plan_summary(data)
        if role_selection_plan:
            items.append(f"- 角色分工: {role_selection_plan}")

        return items

    def _build_data_items(self, data: BriefingData) -> list[str]:
        items: list[str] = []

        if data.picks:
            top = data.top_picks
            names = "、".join(
                _format_pick_with_status(p, include_score=True) for p in top
            )
            items.append(f"- 候选标的: {names}")
        if data.decision_context_cards:
            lead = data.decision_context_cards[0]
            context_bits = tuple(
                bit
                for bit in (
                    lead.news_judgement,
                    lead.cross_market,
                    lead.risk,
                )
                if bit
            )
            if context_bits:
                items.append(
                    f"- 候选上下文: {format_symbol_name(lead.symbol, lead.name)} | "
                    + "；".join(context_bits[:3])
                )

        source_summary = data.source_health_summary
        if source_summary:
            items.append(f"- 数据源: {self._strip_leading_markers(source_summary)}")

        regime_summary = data.regime_summary
        items.append(f"- 市场态势: {self._strip_leading_markers(regime_summary)}")

        return items

    def _build_action_items(self, data: BriefingData) -> list[str]:
        items: list[str] = []

        tradable = data.tradable_picks
        if tradable:
            names = "、".join(
                format_symbol_name(p.symbol, p.name) for p in tradable[:3]
            )
            items.append(f"- 纸面复核对象: {names}")
        elif data.picks:
            top = data.top_picks
            names = "、".join(
                _format_pick_with_status(p, include_score=True) for p in top
            )
            items.append(f"- 观察名单: {names}")
        elif data.portfolio_summary and data.portfolio_summary.watchlist:
            items.append(
                "- 观察名单: " + "、".join(data.portfolio_summary.watchlist[:3])
            )

        debate_points = data.debate_points
        if debate_points:
            items.append(f"- 辩论结论: {self._strip_leading_markers(debate_points[0])}")

        if data.picks:
            first = data.picks[0]
            items.append(
                f"- 首先关注: {_format_pick_with_status(first, include_score=True)}"
            )
            next_step = _candidate_next_step_label(first)
            if next_step:
                items.append(f"- 解锁关注: {first.symbol} {first.name} | {next_step}")
            review_meta = format_review_meta(
                _candidate_review_priority_label(first),
                _candidate_review_window_label(first),
            )
            if review_meta:
                items.append(f"- 复核窗口: {first.symbol} {first.name} | {review_meta}")
        elif data.portfolio_summary:
            fallback = (
                data.portfolio_summary.top_focus or data.portfolio_summary.watchlist
            )
            if fallback:
                items.append(f"- 首先关注: {fallback[0]}")

        return items

    def _build_risk_items(self, data: BriefingData) -> list[str]:
        items: list[str] = []

        risk_points = data.risk_points
        for point in risk_points[:2]:
            items.append(point)

        debate_points = data.debate_points
        if len(debate_points) > 1:
            items.append(f"- 辩论分歧: {self._strip_leading_markers(debate_points[1])}")

        source_summary = data.source_health_summary
        if source_summary:
            items.append(f"- 数据源提示: {self._strip_leading_markers(source_summary)}")
        if data.artifacts:
            items.append(f"- 证据追溯: 已记录 {len(data.artifacts)} 个产物元数据")

        return items

    def _build_one_liner(self, data: BriefingData) -> str:
        regime_desc = data.regime_info.description.split("：")[0].split(":")[0].strip()

        parts: list[str] = []
        if regime_desc:
            parts.append(regime_desc)

        if data.candidate_count > 0:
            parts.append(f"筛出{data.candidate_count}只候选")

        if data.actionable_count > 0:
            parts.append(f"{data.actionable_count}只纸面复核")
        elif data.candidate_count > 0:
            parts.append("有观察名单，今日无纸面复核对象")

        risk_count = len(data.risk_points)
        if risk_count > 0:
            parts.append(f"{risk_count}条风险提示")

        if not parts:
            return "今日无候选标的，保持观望"

        return "，".join(parts) + "。"

    @staticmethod
    def _debate_role_summary(data: BriefingData) -> str:
        if not data.debate_results:
            return ""
        if len(data.debate_results) > 1:
            return "；".join(
                f"{result.symbol} {result.name}: "
                f"{debate_active_role_summary(result, language='zh-CN', max_labels=3) or '无完整角色记录'}"
                for result in data.debate_results[:3]
            )
        return debate_active_role_summary(
            data.debate_results[0],
            language="zh-CN",
            max_labels=5,
        )

    @staticmethod
    def _debate_role_selection_summary(data: BriefingData) -> str:
        if not data.debate_results:
            return ""
        if len(data.debate_results) > 1:
            return "各候选按自身证据分别选角，详见候选讨论"
        return str(data.debate_results[0].role_selection_summary or "").strip()

    @staticmethod
    def _debate_role_plan_summary(data: BriefingData) -> str:
        if not data.debate_results:
            return ""
        if len(data.debate_results) > 1:
            return "各候选分别记录角色分工，不用首个候选代表全部候选"
        return str(data.debate_results[0].role_selection_plan or "").strip()
