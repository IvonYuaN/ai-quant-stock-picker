from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader

from aqsp.core.time import now_shanghai
from aqsp.core.types import PickResult
from aqsp.portfolio.manager import PortfolioDecisionSummary
from aqsp.presentation import format_symbol_name
from aqsp.research.summary import ResearchSummary
from aqsp.ratings import rating_label
from aqsp.config import load_debate_runtime_config
from aqsp.briefing.debate import (
    AShareDebateCoordinator,
    DebateResult,
    format_debate_result,
    parse_agent_roles,
)
from aqsp.ratings import is_tradable_rating, portfolio_action_label

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_THEMES = {
    "volume": ["量", "成交", "换手", "放量"],
    "momentum": ["动量", "趋势", "突破", "均线", "金叉"],
    "value": ["低估", "PE", "PB", "股息", "估值"],
    "quality": ["盈利", "ROE", "毛利率", "利润"],
    "technical": ["MACD", "KDJ", "RSI", "技术"],
}

_REGIME_DESCRIPTIONS = {
    "stable_bull": "稳定上涨：低波动 + 正趋势",
    "volatile_bull": "波动上涨：高波动 + 正趋势",
    "stable_bear": "稳定下跌：低波动 + 负趋势",
    "volatile_bear": "波动下跌：高波动 + 负趋势",
    "stable_sideways": "稳定盘整：低波动 + 无趋势",
    "volatile_sideways": "波动盘整：高波动 + 无趋势",
}


@dataclass(frozen=True)
class BriefingSection:
    title: str
    content: str


@dataclass(frozen=True)
class Briefing:
    date: str
    sections: list[BriefingSection]
    debate_results: list[DebateResult] = field(default_factory=list)
    portfolio_summary: PortfolioDecisionSummary | None = None

    def to_markdown(self) -> str:
        lines = [f"# AI 量化选股日报 - {self.date}", ""]
        for section in self.sections:
            lines.append(f"## {section.title}")
            lines.append("")
            lines.append(section.content)
            lines.append("")

        # 添加辩论结果
        if self.debate_results:
            lines.append("## 多Agent辩论")
            lines.append("")
            lines.append("对重点候选标的进行了多Agent辩论分析：")
            lines.append("")
            for result in self.debate_results[:3]:
                lines.append(format_debate_result(result))
                lines.append("---")
                lines.append("")

        lines.append("> 仅供研究，不构成投资建议。")
        return "\n".join(lines)

    def _get_section(self, title: str) -> str:
        for section in self.sections:
            if section.title == title:
                return section.content
        return ""

    def _extract_actionable_picks(self) -> list[str]:
        next_day = self._get_section("明日重点")
        if not next_day or "无可执行" in next_day:
            return []
        return re.findall(r"\*\*(\d{6}\s+\S+)\*\*", next_day)

    def _extract_candidate_count(self) -> int:
        evidence = self._get_section("候选证据链")
        if not evidence:
            return 0
        return len(re.findall(r"###\s+\d{6}", evidence))

    def _extract_top_scores(self) -> list[str]:
        evidence = self._get_section("候选证据链")
        if not evidence:
            return []
        return re.findall(r"###\s+(\d{6}\s+\S+)\s+\(评分[:：]\s*([\d.]+)\)", evidence)

    def generate_smart_summary(self) -> str:
        risk_points = self._extract_risk_points()
        debate_points = self._extract_debate_points()
        top_scores = self._extract_top_scores()
        actionable = self._extract_actionable_picks()
        source_points = self._extract_source_health_points()
        regime_points = self._extract_regime_points()

        one_liner = self._build_one_liner(
            candidate_count=self._extract_candidate_count(),
            actionable_count=len(actionable),
            risk_count=len(risk_points),
        )

        lines = [f"**{one_liner}**", ""]

        lines.extend(self._format_summary_block("核心结论", self._build_core_items()))
        lines.extend(
            self._format_summary_block(
                "数据透视",
                self._build_data_items(top_scores, source_points, regime_points),
            )
        )
        lines.extend(
            self._format_summary_block(
                "作战计划",
                self._build_action_items(actionable, top_scores, debate_points),
            )
        )
        lines.extend(
            self._format_summary_block(
                "风险提示",
                self._build_risk_items(risk_points, debate_points, source_points),
            )
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

    def _build_core_items(self) -> list[str]:
        items: list[str] = []
        if self.portfolio_summary:
            items.append(f"- PM主裁决: {self.portfolio_summary.headline}")
            if self.portfolio_summary.top_focus:
                items.append(
                    "- 主链候选: " + "、".join(self.portfolio_summary.top_focus)
                )
            if self.portfolio_summary.watchlist:
                items.append(
                    "- 候选观察池: " + "、".join(self.portfolio_summary.watchlist)
                )
        if self.debate_results:
            items.append(
                f"- 多Agent辩论: 已分析 {len(self.debate_results[:3])} 只重点候选"
            )
        else:
            items.append("- 多Agent辩论: 今日无重点标的或处于冷却期")
        return items

    def _build_data_items(
        self,
        top_scores: list[str],
        source_points: list[str],
        regime_points: list[str],
    ) -> list[str]:
        items: list[str] = []
        if top_scores:
            names = "、".join(f"{s[0]}({s[1]}分)" for s in top_scores[:3])
            items.append(f"- 候选标的: {names}")
        if source_points:
            items.append(f"- 数据源: {self._strip_leading_markers(source_points[0])}")
        if regime_points:
            items.append(f"- 市场态势: {self._strip_leading_markers(regime_points[0])}")
        return items

    def _build_action_items(
        self,
        actionable: list[str],
        top_scores: list[str],
        debate_points: list[str],
    ) -> list[str]:
        items: list[str] = []
        if actionable:
            names = "、".join(actionable[:3])
            items.append(f"- 可执行标的: {names}")
        elif top_scores:
            names = "、".join(f"{s[0]}({s[1]}分)" for s in top_scores[:3])
            items.append(f"- 候选观察池: {names}")
        elif self.portfolio_summary and self.portfolio_summary.watchlist:
            items.append(
                "- 候选观察池: " + "、".join(self.portfolio_summary.watchlist[:3])
            )
        if debate_points:
            items.append(f"- 辩论结论: {self._strip_leading_markers(debate_points[0])}")
        if top_scores:
            items.append(f"- 首选观察: {top_scores[0][0]}({top_scores[0][1]}分)")
        elif self.portfolio_summary:
            fallback = (
                self.portfolio_summary.top_focus or self.portfolio_summary.watchlist
            )
            if fallback:
                items.append(f"- 首选观察: {fallback[0]}")
        return items

    def _build_risk_items(
        self,
        risk_points: list[str],
        debate_points: list[str],
        source_points: list[str],
    ) -> list[str]:
        items: list[str] = []
        if risk_points:
            for point in risk_points[:2]:
                items.append(point)
        if len(debate_points) > 1:
            items.append(f"- 辩论分歧: {self._strip_leading_markers(debate_points[1])}")
        if source_points:
            items.append(
                f"- 数据源提示: {self._strip_leading_markers(source_points[0])}"
            )
        return items

    def _extract_risk_points(self) -> list[str]:
        points: list[str] = []
        regime = self._get_section("市场态势")
        if regime and "组合保护中" in regime:
            reason_match = re.search(r"组合保护中\*\*[:：]?\s*(.+)", regime)
            reason = reason_match.group(1).strip() if reason_match else "组合保护生效中"
            points.append(f"⚠️ 组合保护已触发: {reason}，建议暂停新开仓")
        evidence = self._get_section("候选证据链")
        if evidence:
            risk_matches = re.findall(
                r"(?:^|\n)-?\s*风险(?:提示)?[:：]\s*(.+)", evidence
            )
            for risk in risk_matches[:2]:
                clean = risk.strip().rstrip("；").strip()
                if clean:
                    points.append(f"⚠️ 风险提示: {clean}")
        return points

    def _extract_debate_points(self) -> list[str]:
        if not self.debate_results:
            return []
        points: list[str] = []
        for result in self.debate_results[:2]:
            if result.recommended_adjustment == "lower":
                points.append(
                    f"🤖 辩论共识: {result.name}({result.symbol}) "
                    f"建议下调评分至{result.adjusted_score:.1f}"
                )
            elif result.recommended_adjustment == "raise":
                points.append(
                    f"🤖 辩论共识: {result.name}({result.symbol}) "
                    f"建议上调评分至{result.adjusted_score:.1f}"
                )
            elif result.disagreement_score > 0.5:
                points.append(
                    f"🤖 辩论分歧较大: {result.name}({result.symbol}) "
                    f"多空分歧度{result.disagreement_score:.0%}"
                )
        return points

    def _extract_source_health_points(self) -> list[str]:
        source = self._get_section("数据源状态")
        if not source:
            return []
        if "降低信任度" in source:
            route_match = re.search(r"路径[:：]\s*\*\*(.+?)\*\*", source)
            route = route_match.group(1) if route_match else "未知"
            return [f"📉 数据源降级: {route}，结果请降低信任度"]
        return []

    def _extract_regime_points(self) -> list[str]:
        regime = self._get_section("市场态势")
        if not regime:
            return []
        match = re.search(r"市场态势[:：]\s*\*\*(.+?)\*\*", regime)
        if not match:
            return []
        desc = match.group(1)
        if "熊" in desc or "下跌" in desc:
            return [f"📉 市场态势: {desc}，注意控制仓位"]
        if "盘整" in desc:
            return [f"📊 市场态势: {desc}，关注突破方向"]
        return [f"📈 市场态势: {desc}"]

    def _build_one_liner(
        self,
        candidate_count: int,
        actionable_count: int,
        risk_count: int,
    ) -> str:
        regime = self._get_section("市场态势")
        regime_desc = ""
        if regime:
            match = re.search(r"市场态势[:：]\s*\*\*(.+?)\*\*", regime)
            if match:
                regime_desc = match.group(1).split("：")[0].split(":")[0].strip()

        parts: list[str] = []
        if regime_desc:
            parts.append(regime_desc)
        if candidate_count > 0:
            parts.append(f"筛出{candidate_count}只候选")
        if actionable_count > 0:
            parts.append(f"{actionable_count}只可执行")
        elif candidate_count > 0:
            parts.append("有候选观察池，暂无可执行标的")
        if risk_count > 0:
            parts.append(f"{risk_count}条风险提示")
        if not parts:
            return "今日无候选标的，保持观望"
        return "，".join(parts) + "。"


class BriefingGenerator:
    def __init__(self, enable_debate: bool = False):
        debate_runtime = load_debate_runtime_config()
        self.enable_debate = enable_debate or debate_runtime.enabled
        self.debate_coordinator = AShareDebateCoordinator(
            enable_llm=debate_runtime.enable_llm,
            max_rounds=debate_runtime.max_rounds,
            language=debate_runtime.language,
            roles=parse_agent_roles(debate_runtime.roles),
            role_runtime=debate_runtime.role_runtime,
        )

    def generate(
        self,
        picks: list[PickResult],
        frames: dict[str, pd.DataFrame],
        regime: str = "",
        validation: object | None = None,
        circuit_breaker_status: object | None = None,
        source_status: dict[str, str | bool] | None = None,
        research_summary: ResearchSummary | None = None,
    ) -> Briefing:
        date_str = now_shanghai().strftime("%Y-%m-%d %H:%M")
        ordered_picks = sorted(picks, key=lambda item: item.score, reverse=True)
        portfolio_summary = self._build_portfolio_summary(ordered_picks)
        sections = [
            self._build_main_chain_section(ordered_picks, portfolio_summary),
            self._build_regime_section(regime, circuit_breaker_status),
            self._build_source_section(source_status),
            self._build_research_section(research_summary),
            self._build_evidence_section(ordered_picks),
            self._build_theme_section(ordered_picks),
            self._build_next_day_section(ordered_picks, frames),
        ]

        debate_results = []
        if self.enable_debate and ordered_picks:
            # 对评分最高的前3只股票进行辩论
            for pick in ordered_picks[:3]:
                df = frames.get(pick.symbol, pd.DataFrame())
                if not df.empty:
                    try:
                        result = self.debate_coordinator.run_debate(pick, df)
                        debate_results.append(result)
                    except Exception as e:
                        import logging

                        logger = logging.getLogger(__name__)
                        logger.warning(f"辩论失败 {pick.symbol}: {e}")

        return Briefing(
            date=date_str,
            sections=sections,
            debate_results=debate_results,
            portfolio_summary=portfolio_summary,
        )

    def _build_main_chain_section(
        self,
        picks: list[PickResult],
        portfolio_summary: PortfolioDecisionSummary | None,
    ) -> BriefingSection:
        if not picks or portfolio_summary is None:
            return BriefingSection(
                title="主链总览",
                content="今日无主链候选，保持观察。",
            )

        lines = [f"- PM主裁决: {portfolio_summary.headline}"]
        signal_date = picks[0].date if picks and picks[0].date else ""
        if signal_date:
            lines.append(f"- 信号日期: {signal_date}")
        if portfolio_summary.top_focus:
            lines.append("- 可执行主链: " + "、".join(portfolio_summary.top_focus[:3]))
        if portfolio_summary.watchlist:
            lines.append("- 候选观察池: " + "、".join(portfolio_summary.watchlist[:3]))

        lead_pick = picks[0]
        lead_display = format_symbol_name(lead_pick.symbol, lead_pick.name)
        lines.append(
            f"- 首位候选: {lead_display} | {rating_label(lead_pick.rating)} | 评分 {lead_pick.score:.1f}"
        )
        if not portfolio_summary.top_focus:
            lines.append("- 今日动作: 仅观察，不做放大仓位动作。")
        return BriefingSection(title="主链总览", content="\n".join(lines))

    def _build_portfolio_summary(
        self, picks: list[PickResult]
    ) -> PortfolioDecisionSummary | None:
        if not picks:
            return None
        promote = sum(
            1
            for pick in picks
            if str(pick.metrics.get("portfolio_action", "")) == "promote"
        )
        downgrade = sum(
            1
            for pick in picks
            if str(pick.metrics.get("portfolio_action", "")) == "downgrade"
        )
        keep = sum(
            1
            for pick in picks
            if str(pick.metrics.get("portfolio_action", "")) == "keep"
        )

        def _display_name(pick: PickResult) -> str:
            return format_symbol_name(pick.symbol, pick.name)

        focus = [
            _display_name(pick)
            for pick in picks
            if rating_label(pick.rating) in {"重点关注", "观察候选"}
        ][:3]
        watchlist = [
            _display_name(pick)
            for pick in picks
            if rating_label(pick.rating) in {"候选观察池", "仅观察"}
        ][:3]
        return PortfolioDecisionSummary(
            promote_count=promote,
            downgrade_count=downgrade,
            keep_count=keep,
            top_focus=tuple(focus),
            watchlist=tuple(watchlist),
        )

    def _build_regime_section(
        self,
        regime: str,
        circuit_breaker_status: object | None,
    ) -> BriefingSection:
        lines: list[str] = []
        if circuit_breaker_status is not None and getattr(
            circuit_breaker_status, "triggered", False
        ):
            reason = getattr(circuit_breaker_status, "reason", "")
            lines.append(f"> ⚠️ **组合保护中**: {reason}")
            lines.append("")
        desc = _REGIME_DESCRIPTIONS.get(regime, regime or "未知")
        lines.append(f"当前市场态势: **{desc}**")
        return BriefingSection(title="市场态势", content="\n".join(lines))

    def _build_source_section(
        self,
        source_status: dict[str, str | bool] | None,
    ) -> BriefingSection:
        if not source_status:
            return BriefingSection(
                title="数据源状态",
                content="暂无最近一次运行的数据源状态记录。",
            )
        requested = str(source_status.get("requested_source", "") or "")
        actual = str(source_status.get("actual_source", "") or "")
        freshness = str(source_status.get("freshness_tier", "") or "unknown")
        coverage = str(source_status.get("coverage_tier", "") or "unknown")
        label = str(source_status.get("health_label", "") or "unknown")
        message = str(source_status.get("health_message", "") or "暂无说明")
        fallback_used = bool(source_status.get("fallback_used", False))
        route = actual or requested or "unknown"
        if requested and actual and requested != actual:
            route = f"{requested} -> {actual}"
        lines = [
            f"- 路径: **{route}**",
            f"- 层级: fresh={freshness} / cover={coverage}",
            f"- 健康: **{label}**",
            f"- fallback: {'yes' if fallback_used else 'no'}",
            f"- 说明: {message}",
        ]
        if label in {"fallback", "degraded", "cold_start"}:
            lines.append("- 提示: 本次结果请降低信任度，优先人工复核。")
        return BriefingSection(title="数据源状态", content="\n".join(lines))

    def _build_research_section(
        self,
        research_summary: ResearchSummary | None,
    ) -> BriefingSection:
        if research_summary is None:
            return BriefingSection(
                title="研究吸收",
                content="研究吸收未更新；本次日报仅基于当前运行主链。",
            )
        lines = [
            f"- 研究候选总数: **{research_summary.total_findings}**",
            f"- 已吸收但未直接入分策略族: **{len(research_summary.absorbed_families)}**",
            f"- 已部分实现策略族: **{research_summary.implemented_family_count}**",
            f"- report-only 研究族: **{research_summary.report_only_family_count}**",
            f"- 运行门控研究族: **{research_summary.gated_family_count}**",
        ]
        top_pipelines = list(research_summary.pipeline_summaries[:3])
        if top_pipelines:
            for item in top_pipelines:
                lines.append(
                    f"- 研究管线 {item.pipeline}: P1={item.p1} / total={item.total} / top={item.top_repo or '-'}"
                )
        if research_summary.absorbed_families:
            names = "、".join(
                f"{item.name}({item.runtime_stage})"
                for item in research_summary.absorbed_families[:4]
            )
            lines.append(f"- 已吸收主题: {names}")
        if research_summary.next_actions:
            next_item = research_summary.next_actions[0]
            lines.append(
                f"- 下一接入重点: {next_item.kind}/{next_item.item_id} [{next_item.priority}] - {next_item.blocker or '待补 gate'}"
            )
        prereq_item = next(
            (item for item in research_summary.prereq_items if item.status != "ready"),
            None,
        )
        if prereq_item is not None:
            missing_env = "、".join(prereq_item.missing_env_vars) or "fixture"
            lines.append(
                f"- 当前前置缺口: {prereq_item.kind}/{prereq_item.item_id} - {prereq_item.status} ({missing_env})"
            )
        lines.append("- 原则: 研究内容只做候选和解释，不直接覆盖 runtime 打分。")
        return BriefingSection(title="研究吸收", content="\n".join(lines))

    def _build_evidence_section(self, picks: list[PickResult]) -> BriefingSection:
        lines: list[str] = []
        if not picks:
            lines.append("今日无候选标的。")
            return BriefingSection(title="候选证据链", content="\n".join(lines))
        for pick in picks:
            display = format_symbol_name(pick.symbol, pick.name)
            pm_action = str(pick.metrics.get("portfolio_action", "") or "")
            pm_text = portfolio_action_label(pm_action) if pm_action else "未裁决"
            lines.append(
                f"### {display} (评分: {pick.score} / {rating_label(pick.rating)} / PM: {pm_text})"
            )
            if pick.strategies:
                lines.append(f"- 命中策略: {', '.join(pick.strategies)}")
            for reason in pick.reasons:
                lines.append(f"- {reason}")
            if pick.risks:
                lines.append(f"风险提示: {'；'.join(pick.risks)}")
            lines.append("")
        return BriefingSection(title="候选证据链", content="\n".join(lines))

    def _build_theme_section(self, picks: list[PickResult]) -> BriefingSection:
        lines: list[str] = []
        if not picks:
            lines.append("无题材热度数据。")
            return BriefingSection(title="题材热度", content="\n".join(lines))
        theme_counts: Counter[str] = Counter()
        for pick in picks:
            for reason in pick.reasons:
                for theme, keywords in _THEMES.items():
                    if any(kw in reason for kw in keywords):
                        theme_counts[theme] += 1
        if not theme_counts:
            lines.append("今日候选未归类到已知题材。")
        else:
            for theme, count in theme_counts.most_common():
                label = {
                    "volume": "量价",
                    "momentum": "动量/趋势",
                    "value": "估值",
                    "quality": "质量",
                    "technical": "技术指标",
                }.get(theme, theme)
                lines.append(f"- **{label}**: {count} 条线索")
        return BriefingSection(title="题材热度", content="\n".join(lines))

    def _build_next_day_section(
        self,
        picks: list[PickResult],
        frames: dict[str, pd.DataFrame],
    ) -> BriefingSection:
        lines: list[str] = []
        tradable_picks = [p for p in picks if is_tradable_rating(p.rating)]
        if not tradable_picks:
            if picks:
                names = "、".join(
                    format_symbol_name(p.symbol, p.name) for p in picks[:3]
                )
                lines.append(
                    f"暂无可执行重点标的；候选观察池: {names}。今日先观察，不做放大仓位动作。"
                )
            else:
                lines.append("无可执行重点标的；今日无候选，继续等待下一轮信号。")
            return BriefingSection(title="明日重点", content="\n".join(lines))
        for pick in tradable_picks[:5]:
            entry = pick.ideal_buy
            stop = pick.stop_loss
            tp = pick.take_profit
            display = format_symbol_name(pick.symbol, pick.name)
            lines.append(
                f"- **{display}**: "
                f"参考买点 {entry} / 止损 {stop} / 止盈 {tp} / 仓位 {pick.position}"
            )
        lines.append("")
        lines.append("> 注: 事件型催化尚未纳入主链门控，需人工补充复核。")
        return BriefingSection(title="明日重点", content="\n".join(lines))

    def render_template(
        self,
        briefing: Briefing,
        picks: list[PickResult],
        circuit_breaker_status: object | None = None,
    ) -> str:
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))
        template = env.get_template("default.md.j2")
        cb_triggered = circuit_breaker_status is not None and getattr(
            circuit_breaker_status, "triggered", False
        )
        cb_reason = (
            getattr(circuit_breaker_status, "reason", "") if cb_triggered else ""
        )
        regime_section = ""
        main_chain_section = ""
        theme_section = ""
        next_day_section = ""
        for section in briefing.sections:
            if section.title == "主链总览":
                main_chain_section = section.content
            elif section.title == "市场态势":
                regime_section = section.content
            elif section.title == "题材热度":
                theme_section = section.content
            elif section.title == "明日重点":
                next_day_section = section.content
        pick_dicts = [
            {
                "symbol": p.symbol,
                "name": p.name,
                "score": p.score,
                "rating": p.rating,
                "strategies": list(p.strategies),
                "reasons": list(p.reasons),
            }
            for p in picks
        ]
        return template.render(
            date=briefing.date,
            circuit_breaker_triggered=cb_triggered,
            circuit_breaker_reason=cb_reason,
            main_chain_section=main_chain_section,
            regime_section=regime_section,
            picks=pick_dicts,
            theme_section=theme_section,
            next_day_section=next_day_section,
        )
