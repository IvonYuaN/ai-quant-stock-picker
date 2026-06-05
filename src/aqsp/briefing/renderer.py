"""Briefing 渲染器 - 从结构化数据生成各种格式输出。

宪法要求：渲染器只读取结构化数据，不反向解析 markdown。
"""

from __future__ import annotations

from aqsp.briefing.schema import BriefingData
from aqsp.briefing.debate import format_debate_result
from aqsp.presentation import format_symbol_name
from aqsp.ratings import rating_label, portfolio_action_label


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
    labels = {"high": "高优先级", "medium": "中优先级", "low": "低优先级"}
    return labels.get(value, value)


def _format_pick_with_status(pick, *, include_score: bool = False) -> str:
    display = format_symbol_name(pick.symbol, pick.name)
    status = _candidate_status_label(pick)
    if status:
        display = f"{display}({status})"
    if include_score:
        display = f"{display}({pick.score:.1f}分)"
    return display


class MarkdownRenderer:
    """将 BriefingData 渲染为 markdown 格式。"""

    def render(self, data: BriefingData) -> str:
        """生成完整的 markdown 日报。"""
        lines = [f"# AI 量化选股日报 - {data.date}", ""]

        lines.extend(self._render_main_chain(data))
        lines.extend(self._render_regime(data))
        lines.extend(self._render_source(data))
        lines.extend(self._render_research(data))
        lines.extend(self._render_evidence(data))
        lines.extend(self._render_theme(data))
        lines.extend(self._render_next_day(data))

        # 添加辩论结果
        if data.debate_results:
            lines.append("## 多Agent辩论")
            lines.append("")
            lines.append("对重点候选标的进行了多Agent辩论分析：")
            lines.append("")
            for result in data.debate_results[:3]:
                lines.append(format_debate_result(result))
                lines.append("---")
                lines.append("")

        lines.append("> 仅供研究，不构成投资建议。")
        return "\n".join(lines)

    def _render_main_chain(self, data: BriefingData) -> list[str]:
        """渲染主链总览。"""
        lines = ["## 主链总览", ""]

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
            lines.append("- 可执行主链: " + "、".join(ps.top_focus[:3]))

        if ps.watchlist:
            lines.append("- 候选观察池: " + "、".join(ps.watchlist[:3]))
        if ps.watch_reviews:
            lines.append("- 观察复核:")
            for item in ps.watch_reviews[:2]:
                meta = " / ".join(
                    part
                    for part in (
                        _review_priority_label(item.priority),
                        item.review_window,
                    )
                    if part
                )
                line = f"  - {format_symbol_name(item.symbol, item.name)}"
                if meta:
                    line += f" | {meta}"
                if item.next_step:
                    line += f" | {item.next_step}"
                lines.append(line)

        lead = data.picks[0]
        lead_display = format_symbol_name(lead.symbol, lead.name)
        lead_status = _candidate_status_label(lead)
        lead_line = f"- 首位候选: {lead_display} | {rating_label(lead.rating)}"
        if lead_status:
            lead_line += f" | {lead_status}"
        lead_line += f" | 评分 {lead.score:.1f}"
        lines.append(lead_line)

        if not ps.top_focus:
            lines.append("- 今日动作: 仅观察，不做放大仓位动作。")

        lines.append("")
        return lines

    def _render_regime(self, data: BriefingData) -> list[str]:
        """渲染市场态势。"""
        lines = ["## 市场态势", ""]

        if data.regime_info.circuit_breaker_triggered:
            reason = data.regime_info.circuit_breaker_reason
            lines.append(f"> ⚠️ **组合保护中**: {reason}")
            lines.append("")

        lines.append(f"当前市场态势: **{data.regime_info.description}**")
        lines.append("")
        return lines

    def _render_source(self, data: BriefingData) -> list[str]:
        """渲染数据源状态。"""
        lines = ["## 数据源状态", ""]

        if data.source_status is None:
            lines.append("暂无最近一次运行的数据源状态记录。")
            lines.append("")
            return lines

        s = data.source_status
        lines.append(f"- 路径: **{s.route}**")
        lines.append(f"- 层级: fresh={s.freshness_tier} / cover={s.coverage_tier}")
        lines.append(f"- 健康: **{s.health_label}**")
        lines.append(f"- fallback: {'yes' if s.fallback_used else 'no'}")
        lines.append(f"- 说明: {s.health_message}")

        if s.is_degraded:
            lines.append("- 提示: 本次结果请降低信任度，优先人工复核。")

        lines.append("")
        return lines

    def _render_research(self, data: BriefingData) -> list[str]:
        """渲染研究吸收。"""
        lines = ["## 研究吸收", ""]

        if data.research_summary is None:
            lines.append("研究吸收未更新；本次日报仅基于当前运行主链。")
            lines.append("")
            return lines

        rs = data.research_summary
        lines.append(f"- 研究候选总数: **{rs.total_findings}**")
        lines.append(f"- 已吸收但未直接入分策略族: **{len(rs.absorbed_families)}**")
        lines.append(f"- 已部分实现策略族: **{rs.implemented_family_count}**")
        lines.append(f"- report-only 研究族: **{rs.report_only_family_count}**")
        lines.append(f"- 运行门控研究族: **{rs.gated_family_count}**")

        top_pipelines = list(rs.pipeline_summaries[:3])
        if top_pipelines:
            for item in top_pipelines:
                lines.append(
                    f"- 研究管线 {item.pipeline}: P1={item.p1} / total={item.total} / top={item.top_repo or '-'}"
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
                f"- 下一接入重点: {next_item.kind}/{next_item.item_id} [{next_item.priority}] - {next_item.blocker or '待补 gate'}"
            )

        prereq_item = next(
            (item for item in rs.prereq_items if item.status != "ready"),
            None,
        )
        if prereq_item is not None:
            missing_env = "、".join(prereq_item.missing_env_vars) or "fixture"
            lines.append(
                f"- 当前前置缺口: {prereq_item.kind}/{prereq_item.item_id} - {prereq_item.status} ({missing_env})"
            )

        lines.append("- 原则: 研究内容只做候选和解释，不直接覆盖 runtime 打分。")
        lines.append("")
        return lines

    def _render_evidence(self, data: BriefingData) -> list[str]:
        """渲染候选证据链。"""
        lines = ["## 候选证据链", ""]

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
            review_meta = " / ".join(
                part
                for part in (
                    _candidate_review_priority_label(pick),
                    _candidate_review_window_label(pick),
                )
                if part
            )
            headline = f"### {display} (评分: {pick.score} / {rating_label(pick.rating)}"
            if candidate_status:
                headline += f" / 状态: {candidate_status}"
            headline += f" / PM: {pm_text})"
            lines.append(headline)

            if pick.strategies:
                lines.append(f"- 命中策略: {', '.join(pick.strategies)}")

            for reason in pick.reasons:
                lines.append(f"- {reason}")
            if blocker:
                lines.append(f"- 当前阻塞: {blocker}")
            if next_step:
                lines.append(f"- 下一步关注: {next_step}")
            if review_meta:
                lines.append(f"- 复核优先级/时机: {review_meta}")

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
        lines = ["## 明日重点", ""]

        tradable = data.tradable_picks
        if not tradable:
            if data.picks:
                lead = data.picks[0]
                names = "、".join(_format_pick_with_status(p) for p in data.picks[:3])
                blocker = _candidate_blocker_label(lead)
                next_step = _candidate_next_step_label(lead)
                review_meta = " / ".join(
                    part
                    for part in (
                        _candidate_review_priority_label(lead),
                        _candidate_review_window_label(lead),
                    )
                    if part
                )
                line = (
                    f"当前暂无可执行重点标的；候选观察池: {names}。"
                    "先观察最强票，待阻塞条件解除后再考虑转入执行名单。"
                )
                if blocker:
                    line += f" 当前阻塞: {blocker}。"
                if next_step:
                    line += f" 下一步关注: {next_step}。"
                if review_meta:
                    line += f" 复核节奏: {review_meta}。"
                lines.append(line)
            else:
                lines.append("无可执行重点标的；今日无候选，继续等待下一轮信号。")

            lines.append("")
            return lines

        for pick in tradable[:5]:
            display = _format_pick_with_status(pick)
            lines.append(
                f"- **{display}**: "
                f"参考买点 {pick.ideal_buy} / 止损 {pick.stop_loss} / 止盈 {pick.take_profit} / 仓位 {pick.position}"
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

        lines.extend(self._format_summary_block("核心结论", self._build_core_items(data)))
        lines.extend(self._format_summary_block("数据透视", self._build_data_items(data)))
        lines.extend(self._format_summary_block("作战计划", self._build_action_items(data)))
        lines.extend(self._format_summary_block("风险提示", self._build_risk_items(data)))

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
                items.append("- 候选观察池: " + "、".join(ps.watchlist))

        if data.debate_results:
            items.append(f"- 多Agent辩论: 已分析 {len(data.debate_results[:3])} 只重点候选")
        else:
            items.append("- 多Agent辩论: 今日无重点标的或处于冷却期")

        return items

    def _build_data_items(self, data: BriefingData) -> list[str]:
        items: list[str] = []

        if data.picks:
            top = data.top_picks
            names = "、".join(
                _format_pick_with_status(p, include_score=True) for p in top
            )
            items.append(f"- 候选标的: {names}")

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
            names = "、".join(format_symbol_name(p.symbol, p.name) for p in tradable[:3])
            items.append(f"- 可执行标的: {names}")
        elif data.picks:
            top = data.top_picks
            names = "、".join(
                _format_pick_with_status(p, include_score=True) for p in top
            )
            items.append(f"- 候选观察池: {names}")
        elif data.portfolio_summary and data.portfolio_summary.watchlist:
            items.append("- 候选观察池: " + "、".join(data.portfolio_summary.watchlist[:3]))

        debate_points = data.debate_points
        if debate_points:
            items.append(f"- 辩论结论: {self._strip_leading_markers(debate_points[0])}")

        if data.picks:
            first = data.picks[0]
            items.append(
                f"- 首选观察: {_format_pick_with_status(first, include_score=True)}"
            )
            next_step = _candidate_next_step_label(first)
            if next_step:
                items.append(f"- 解锁关注: {first.symbol} {first.name} | {next_step}")
            review_meta = " / ".join(
                part
                for part in (
                    _candidate_review_priority_label(first),
                    _candidate_review_window_label(first),
                )
                if part
            )
            if review_meta:
                items.append(f"- 复核节奏: {first.symbol} {first.name} | {review_meta}")
        elif data.portfolio_summary:
            fallback = data.portfolio_summary.top_focus or data.portfolio_summary.watchlist
            if fallback:
                items.append(f"- 首选观察: {fallback[0]}")

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

        return items

    def _build_one_liner(self, data: BriefingData) -> str:
        regime_desc = data.regime_info.description.split("：")[0].split(":")[0].strip()

        parts: list[str] = []
        if regime_desc:
            parts.append(regime_desc)

        if data.candidate_count > 0:
            parts.append(f"筛出{data.candidate_count}只候选")

        if data.actionable_count > 0:
            parts.append(f"{data.actionable_count}只可执行")
        elif data.candidate_count > 0:
            parts.append("有候选观察池，当前暂无可执行标的")

        risk_count = len(data.risk_points)
        if risk_count > 0:
            parts.append(f"{risk_count}条风险提示")

        if not parts:
            return "今日无候选标的，保持观望"

        return "，".join(parts) + "。"
