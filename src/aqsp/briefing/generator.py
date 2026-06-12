from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from aqsp.core.time import now_shanghai
from aqsp.core.types import PickResult
from aqsp.portfolio.manager import (
    PortfolioDecision,
    PortfolioDecisionSummary,
    summarize_portfolio_decisions,
)
from aqsp.presentation import (
    describe_source_health,
    describe_source_layers,
    format_source_route,
    format_symbol_name,
    format_watch_review_line,
    normalize_research_tone,
    review_priority_label,
)
from aqsp.research.summary import ResearchSummary, research_findings_display
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

_RUNTIME_STAGE_LABELS = {
    "gated_runtime": "满足条件后启用",
    "report_only": "仅写进研究记录",
    "runtime": "已接入主流程",
}

_PIPELINE_LABELS = {
    "data_source": "数据源",
    "strategy": "策略",
    "signal": "信号",
    "presentation": "展示",
}


def _candidate_status_label(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_status", "") or "")


def _candidate_blocker_label(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_blocker", "") or "")


def _candidate_next_step_label(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_next_step", "") or "")


def _candidate_review_window_label(pick: PickResult) -> str:
    return str(pick.metrics.get("candidate_review_window", "") or "")


def _review_priority_label(value: str) -> str:
    return review_priority_label(value)


def _candidate_review_priority_label(pick: PickResult) -> str:
    value = str(pick.metrics.get("candidate_review_priority", "") or "")
    return _review_priority_label(value)


def _format_pick_with_status(
    pick: PickResult,
    *,
    include_score: bool = False,
) -> str:
    display = format_symbol_name(pick.symbol, pick.name)
    status = _candidate_status_label(pick)
    if status:
        display = f"{display}({status})"
    if include_score:
        display = f"{display}({pick.score:.1f}分)"
    return display


def _safe_markdown_text(value: object) -> str:
    return escape(normalize_research_tone(str(value).strip()), quote=False)


def _section_text(lines: list[str]) -> str:
    return normalize_research_tone("\n".join(lines))


def _dedupe_watchlist_against_focus(
    watchlist: tuple[str, ...],
    top_focus: tuple[str, ...],
) -> tuple[str, ...]:
    focus_symbols = {
        str(item).split(" ", 1)[0].strip() for item in top_focus if str(item).strip()
    }
    filtered: list[str] = []
    seen: set[str] = set()
    for item in watchlist:
        text = str(item).strip()
        symbol = text.split(" ", 1)[0].strip()
        if not text or symbol in focus_symbols or text in seen:
            continue
        seen.add(text)
        filtered.append(text)
    return tuple(filtered)


def _pick_symbol_from_display(display: str) -> str:
    return str(display).split(" ", 1)[0].strip()


@dataclass(frozen=True)
class BriefingSection:
    title: str
    content: str


@dataclass(frozen=True)
class Briefing:
    date: str
    sections: list[BriefingSection]
    picks: list[PickResult] = field(default_factory=list)
    debate_results: list[DebateResult] = field(default_factory=list)
    portfolio_summary: PortfolioDecisionSummary | None = None

    def to_markdown(self) -> str:
        lines = [f"# 每日研究复盘-{_safe_markdown_text(self.date)}", ""]
        lines.append("**免责声明**: 本报告仅供研究参考，不构成交易指令或投资建议。")
        lines.append("")

        for section in self.sections:
            lines.append(f"## {_safe_markdown_text(section.title)}")
            lines.append("")
            lines.append(_safe_markdown_text(section.content))
            lines.append("")

        # 添加辩论结果
        if self.debate_results:
            lines.append("## 不同看法")
            lines.append("")
            lines.append("不同看法结论：")
            lines.append("")
            for result in self.debate_results[:3]:
                lines.append(_safe_markdown_text(format_debate_result(result)))
                lines.append("---")
                lines.append("")

        return "\n".join(lines)

    def _get_section(self, *titles: str) -> str:
        for section in self.sections:
            if section.title in titles:
                return section.content
        return ""

    def _extract_actionable_picks(self) -> list[str]:
        next_day = self._get_section("明日重点")
        if not next_day or any(
            marker in next_day for marker in ("无可执行", "暂无重点跟踪", "今日无候选")
        ):
            return []
        return re.findall(r"\*\*(\d{6}\s+\S+)\*\*", next_day)

    def _extract_candidate_count(self) -> int:
        if self.picks:
            return len(self.picks)
        evidence = self._get_section("候选来龙去脉", "候选证据链")
        if not evidence:
            return 0
        return len(re.findall(r"###\s+\d{6}", evidence))

    def _extract_top_scores(self) -> list[tuple[str, str]]:
        if self.picks:
            return [
                (
                    format_symbol_name(p.symbol, p.name),
                    f"{p.score:.1f}",
                )
                for p in self.picks[:3]
            ]
        evidence = self._get_section("候选来龙去脉", "候选证据链")
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
                "再看顺序",
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
        return normalize_research_tone("\n".join(lines))

    def _format_summary_block(self, title: str, items: list[str]) -> list[str]:
        if not items:
            return [f"### {title}", "- 无", ""]
        return [f"### {title}", *(_safe_markdown_text(item) for item in items), ""]

    @staticmethod
    def _strip_leading_markers(text: str) -> str:
        return text.lstrip("📉📊📈🤖⚠️ ").strip()

    def _build_core_items(self) -> list[str]:
        items: list[str] = []
        if self.portfolio_summary:
            watchlist = _dedupe_watchlist_against_focus(
                self.portfolio_summary.watchlist,
                self.portfolio_summary.top_focus,
            )
            if (
                self.portfolio_summary.watchlist
                and not self.portfolio_summary.top_focus
            ):
                watch_names = "、".join(
                    _format_pick_with_status(pick, include_score=True)
                    for pick in self.picks[:3]
                )
                items.append(f"- 继续观察名单: {watch_names}")
            items.append(f"- 今日结论: {self.portfolio_summary.headline}")
            if self.portfolio_summary.regime_label:
                items.append(f"- 当前市况: {self.portfolio_summary.regime_label}")
            if self.portfolio_summary.strategy_mix_name:
                items.append(f"- 现在偏向: {self.portfolio_summary.strategy_mix_name}")
            if self.portfolio_summary.top_focus:
                items.append(
                    "- 今日重点名单: " + "、".join(self.portfolio_summary.top_focus)
                )
            if watchlist:
                items.append("- 继续观察名单: " + "、".join(watchlist))
            if self.portfolio_summary.action_hotspots:
                items.append(
                    "- 需要重点确认: "
                    + "；".join(self.portfolio_summary.action_hotspots[:2])
                )
            if self.portfolio_summary.allocations:
                top_alloc = "、".join(
                    f"{item.symbol} {item.name} {item.weight:.0%}"
                    for item in self.portfolio_summary.allocations[:3]
                )
                items.append(f"- 比例参考: {top_alloc}")
                first = self.portfolio_summary.allocations[0]
                rationale = "；".join(first.rationale[:2])
                if rationale:
                    items.append(
                        f"- 首个纸面理由: {first.symbol} {first.name} | {rationale}"
                    )
            if self.portfolio_summary.cash_reserve > 0:
                items.append(f"- 现金留存: {self.portfolio_summary.cash_reserve:.0%}")
        if self.debate_results:
            items.append(
                f"- 不同看法: 已分析 {len(self.debate_results[:3])} 只重点候选"
            )
        else:
            items.append("- 不同看法: 今日无重点候选或仍在观察阶段")
        return items

    def _build_data_items(
        self,
        top_scores: list[tuple[str, str]],
        source_points: list[str],
        regime_points: list[str],
    ) -> list[str]:
        items: list[str] = []
        if top_scores:
            if self.picks:
                names = "、".join(
                    _format_pick_with_status(pick, include_score=True)
                    for pick in self.picks[:3]
                )
            else:
                names = "、".join(f"{s[0]}({s[1]}分)" for s in top_scores[:3])
            items.append(f"- 筛出的股票: {names}")
        if source_points:
            items.append(f"- 数据来源: {self._strip_leading_markers(source_points[0])}")
        if regime_points:
            items.append(f"- 市场走势: {self._strip_leading_markers(regime_points[0])}")
        return items

    def _build_action_items(
        self,
        actionable: list[str],
        top_scores: list[tuple[str, str]],
        debate_points: list[str],
    ) -> list[str]:
        items: list[str] = []
        tradable_picks = [
            pick for pick in self.picks if is_tradable_rating(pick.rating)
        ]
        if tradable_picks:
            names = "、".join(
                _format_pick_with_status(pick, include_score=True)
                for pick in tradable_picks[:3]
            )
            items.append(f"- 重点跟踪对象: {names}")
        elif self.picks:
            names = "、".join(
                _format_pick_with_status(pick, include_score=True)
                for pick in self.picks[:3]
            )
            items.append(f"- 重点观察: {names}")
        elif top_scores:
            names = "、".join(f"{s[0]}({s[1]}分)" for s in top_scores[:3])
            items.append(f"- 重点观察: {names}")
        elif self.portfolio_summary and self.portfolio_summary.watchlist:
            watchlist = _dedupe_watchlist_against_focus(
                self.portfolio_summary.watchlist,
                self.portfolio_summary.top_focus,
            )
            items.append("- 继续观察名单: " + "、".join(watchlist[:3]))
        if self.portfolio_summary and self.portfolio_summary.allocations:
            top_alloc = self.portfolio_summary.allocations[0]
            line = f"- 比例参考: {top_alloc.symbol} {top_alloc.name} {top_alloc.weight:.0%}"
            rationale = "；".join(top_alloc.rationale[:2])
            if rationale:
                line += f" | {rationale}"
            items.append(line)
        if self.portfolio_summary and self.portfolio_summary.strategy_focus:
            items.append(
                "- 用这个方法: " + "、".join(self.portfolio_summary.strategy_focus[:3])
            )
        if debate_points:
            items.append(
                f"- 多个观点说: {self._strip_leading_markers(debate_points[0])}"
            )
        if self.portfolio_summary and self.portfolio_summary.allocation_note:
            items.append(f"- 跟踪约束: {self.portfolio_summary.allocation_note}")
        if self.portfolio_summary and self.portfolio_summary.execution_blockers:
            items.append(
                "- 现在卡在哪: "
                + "；".join(self.portfolio_summary.execution_blockers[:2])
            )
        if self.picks:
            lead_pick = self.picks[0]
            lead_display = format_symbol_name(lead_pick.symbol, lead_pick.name)
            next_step = _candidate_next_step_label(lead_pick)
            if next_step:
                items.append(f"- 解锁后先看: {lead_display} | {next_step}")
            review_meta = " / ".join(
                part
                for part in (
                    _candidate_review_priority_label(lead_pick),
                    _candidate_review_window_label(lead_pick),
                )
                if part
            )
            if review_meta:
                items.append(f"- 再看时间: {lead_display} | {review_meta}")
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
        regime_points = self._extract_regime_points()
        if regime_points:
            items.append(f"- 市场提示: {self._strip_leading_markers(regime_points[0])}")
        if len(debate_points) > 1:
            items.append(
                f"- 有人不同意: {self._strip_leading_markers(debate_points[1])}"
            )
        if source_points:
            items.append(f"- 数据提示: {self._strip_leading_markers(source_points[0])}")
        return items

    def _extract_risk_points(self) -> list[str]:
        points: list[str] = []
        regime = self._get_section("市场态势")
        if regime and "组合保护中" in regime:
            reason_match = re.search(r"组合保护中\*\*[:：]?\s*(.+)", regime)
            reason = reason_match.group(1).strip() if reason_match else "组合保护生效中"
            points.append(f"⚠️ 组合保护已触发: {reason}，暂停新增纸面再看")
        evidence = self._get_section("候选来龙去脉", "候选证据链")
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
                    f"倾向下调；附件参考分{result.adjusted_score:.1f}，不覆盖runtime打分"
                )
            elif result.recommended_adjustment == "raise":
                points.append(
                    f"🤖 辩论共识: {result.name}({result.symbol}) "
                    f"倾向上调；附件参考分{result.adjusted_score:.1f}，不覆盖runtime打分"
                )
            elif result.disagreement_score > 0.5:
                points.append(
                    f"🤖 辩论分歧较大: {result.name}({result.symbol}) "
                    f"多空分歧{result.disagreement_score:.0%}"
                )
        return points

    def _extract_source_health_points(self) -> list[str]:
        source = self._get_section("数据源状态")
        if not source:
            return []
        if "降低信任度" in source:
            route_match = re.search(r"(?:路径|数据来源)[:：]\s*\*\*(.+?)\*\*", source)
            route = route_match.group(1) if route_match else "未知"
            return [f"📉 数据源降级: {route}，结果请降低信任度"]
        return []

    def _extract_regime_points(self) -> list[str]:
        regime = self._get_section("市场态势")
        if not regime:
            return []
        match = re.search(r"\*\*(.+?)\*\*", regime)
        if match:
            desc = match.group(1)
        else:
            desc = regime.strip()
        if "熊" in desc or "下跌" in desc:
            return [f"📉 市场态势: {desc}，控制纸面暴露"]
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
            match = re.search(r"市场(?:态势|环境)[:：]\s*\*\*(.+?)\*\*", regime)
            if match:
                regime_desc = match.group(1).split("：")[0].split(":")[0].strip()

        parts: list[str] = []
        if regime_desc:
            parts.append(f"市场现在{regime_desc}")
        if candidate_count > 0:
            parts.append(f"筛出{candidate_count}只候选")
        tradable_count = len(
            [pick for pick in self.picks if is_tradable_rating(pick.rating)]
        )
        effective_actionable_count = actionable_count or tradable_count
        if effective_actionable_count > 0:
            parts.append(f"其中{effective_actionable_count}只适合优先再看")
        elif candidate_count > 0:
            parts.append("有继续观察名单，当前暂无重点跟踪对象")
        if risk_count > 0:
            parts.append(f"要特别注意{risk_count}条风险")
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
        portfolio_summary: PortfolioDecisionSummary | None = None,
    ) -> Briefing:
        """Build a research briefing from ranked picks and runtime context.

        Args:
            picks: Candidate picks produced by the screening pipeline.
            frames: Mapping of symbol to the validated OHLCV frame used for analysis.
            regime: Current market regime label used in the regime summary section.
            validation: Optional validation context reserved for upstream pipeline hooks.
            circuit_breaker_status: Optional account-level risk status for the regime block.
            source_status: Optional data-source health and routing metadata.
            research_summary: Optional supplemental research findings to embed.
            portfolio_summary: Optional precomputed portfolio summary. When omitted,
                the generator derives one from the ranked picks.

        Returns:
            A ``Briefing`` containing ordered sections, optional debate results, and
            the portfolio summary used to render markdown and notifications.
        """
        date_str = now_shanghai().strftime("%Y-%m-%d %H:%M")
        ordered_picks = sorted(picks, key=lambda item: item.score, reverse=True)
        summary = portfolio_summary or self._build_portfolio_summary(
            ordered_picks,
            regime=regime,
        )
        debate_results: list[DebateResult] = []
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
        sections = [
            self._build_main_chain_section(ordered_picks, summary, debate_results),
            self._build_regime_section(regime, circuit_breaker_status),
            self._build_source_section(source_status),
            self._build_research_section(research_summary),
            self._build_evidence_section(ordered_picks),
            self._build_theme_section(ordered_picks),
            self._build_next_day_section(ordered_picks, frames),
        ]

        return Briefing(
            date=date_str,
            sections=sections,
            picks=ordered_picks,
            debate_results=debate_results,
            portfolio_summary=summary,
        )

    def _build_main_chain_section(
        self,
        picks: list[PickResult],
        portfolio_summary: PortfolioDecisionSummary | None,
        debate_results: list[DebateResult],
    ) -> BriefingSection:
        if not picks or portfolio_summary is None:
            return BriefingSection(
                title="主链总览",
                content=normalize_research_tone("今日无候选标的，继续观察。"),
            )

        lines = [f"- 今日结论: {portfolio_summary.headline}"]
        signal_date = picks[0].date if picks and picks[0].date else ""
        display_watchlist = _dedupe_watchlist_against_focus(
            portfolio_summary.watchlist,
            portfolio_summary.top_focus,
        )
        if signal_date:
            lines.append(f"- 信号日期: {signal_date}")
        if portfolio_summary.top_focus:
            lines.append(
                "- 今日重点名单: " + "、".join(portfolio_summary.top_focus[:3])
            )
        if display_watchlist:
            lines.append("- 继续观察名单: " + "、".join(display_watchlist[:3]))
        if portfolio_summary.watch_reviews:
            lines.append("- 观察名单下一步:")
            for item in portfolio_summary.watch_reviews[:2]:
                lines.append(
                    "  - "
                    + format_watch_review_line(
                        format_symbol_name(item.symbol, item.name),
                        priority=item.priority,
                        review_window=item.review_window,
                        next_step=item.next_step,
                    )
                )
        if portfolio_summary.action_hotspots:
            lines.append(
                "- 需要重点确认: " + "；".join(portfolio_summary.action_hotspots[:3])
            )
        if portfolio_summary.execution_blockers:
            lines.append("- 现在卡在哪:")
            for item in portfolio_summary.execution_blockers[:3]:
                lines.append(f"  - {item}")
        if portfolio_summary.regime_label:
            lines.append(f"- 当前市况: {portfolio_summary.regime_label}")
        if portfolio_summary.strategy_mix_name:
            lines.append(
                f"- 现在偏向: {portfolio_summary.strategy_mix_name} | {portfolio_summary.strategy_mix_description}"
            )
        if portfolio_summary.strategy_focus:
            lines.append(
                "- 更偏好这些方向: " + "、".join(portfolio_summary.strategy_focus[:4])
            )
        if portfolio_summary.strategy_weights:
            lines.append(
                "- 方向占比参考: "
                + "、".join(
                    f"{strategy_id} {weight:.0%}"
                    for strategy_id, weight in portfolio_summary.strategy_weights[:4]
                )
            )
        if portfolio_summary.allocations:
            lines.append("- 比例参考:")
            for item in portfolio_summary.allocations[:3]:
                display = format_symbol_name(item.symbol, item.name)
                rationale = "；".join(item.rationale[:3])
                line = f"  - {display}: {item.weight:.0%}"
                if rationale:
                    line += f" | {rationale}"
                lines.append(line)
            if portfolio_summary.cash_reserve > 0:
                lines.append(f"  - 现金留存: {portfolio_summary.cash_reserve:.0%}")
        if portfolio_summary.allocation_note:
            lines.append(f"- 跟踪约束: {portfolio_summary.allocation_note}")
        if debate_results:
            lines.append("- 不同看法:")
            for result in debate_results[:2]:
                display = format_symbol_name(result.symbol, result.name)
                consensus = (
                    result.final_consensus or result.adjustment_reason or "暂无总结"
                )
                lines.append(
                    "  - "
                    f"{display}: {result.recommended_adjustment.upper()} / "
                    f"分歧 {result.disagreement_score:.0%} / {consensus}"
                )

        focus_symbols = {
            _pick_symbol_from_display(display)
            for display in portfolio_summary.top_focus
        }
        lead_pick = next(
            (pick for pick in picks if pick.symbol in focus_symbols),
            picks[0],
        )
        lead_display = format_symbol_name(lead_pick.symbol, lead_pick.name)
        lead_status = _candidate_status_label(lead_pick)
        lead_line = f"- 先看这个: {lead_display} | {rating_label(lead_pick.rating)}"
        if lead_status:
            lead_line += f" | {lead_status}"
        lead_line += f" | 评分 {lead_pick.score:.1f}"
        lines.append(lead_line)
        if not portfolio_summary.top_focus:
            lines.append("- 当前暂无重点跟踪对象，先观察。")
        return BriefingSection(title="主链总览", content=_section_text(lines))

    def _build_portfolio_summary(
        self,
        picks: list[PickResult],
        *,
        regime: str = "",
    ) -> PortfolioDecisionSummary | None:
        if not picks:
            return None
        decisions = [
            PortfolioDecision(
                symbol=pick.symbol,
                action=str(pick.metrics.get("portfolio_action", "keep") or "keep"),
                score_delta=0.0,
                reasons=("保持原排序",),
            )
            for pick in picks
        ]
        return summarize_portfolio_decisions(
            picks,
            decisions,
            regime=regime,
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

        # 用大白话描述市场
        regime_names = {
            "stable_bull": "上升期（稳定上涨）- 多数股票涨，少亏",
            "volatile_bull": "上升但剧烈（乱涨乱跌但总体涨）- 容易坐过山车",
            "stable_bear": "下降期（稳定下跌）- 多数股票跌，多亏",
            "volatile_bear": "下降且剧烈（乱跌乱涨但总体跌）- 很危险",
            "stable_sideways": "盘整期（不涨不跌）- 无聊但安全",
            "volatile_sideways": "盘整但剧烈（震荡）- 容易被套",
        }

        desc = regime_names.get(regime, regime or "未知")
        lines.append("### 现在市场是什么样？")
        lines.append("")
        lines.append(f"**{desc}**")
        lines.append("")

        if "下降" in desc or "bear" in regime:
            lines.append("> 💡 提示：下降期要特别小心，宁可观望，别急着买")
        elif "上升" in desc and "稳定" in desc:
            lines.append("> 💡 提示：好时候！可以适当多做一些")
        elif "盘整" in desc:
            lines.append("> 💡 提示：震荡市容易被套，要控制风险")
        elif "剧烈" in desc:
            lines.append("> 💡 提示：波动大，一定要设止损，别赌")

        return BriefingSection(title="市场态势", content=_section_text(lines))

    def _build_source_section(
        self,
        source_status: dict[str, str | bool] | None,
    ) -> BriefingSection:
        if not source_status:
            return BriefingSection(
                title="数据源状态",
                content=normalize_research_tone("暂无最近一次运行的数据源状态记录。"),
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
            route = format_source_route(requested, actual)
        lines = [
            f"- 数据来源: **{route}**",
            f"- 数据完整度: {describe_source_layers(freshness, coverage)}",
            f"- 数据状态: **{describe_source_health(label, message)}**",
            f"- 是否启用备用源: {'是' if fallback_used else '否'}",
        ]
        if label in {"fallback", "degraded", "cold_start"}:
            lines.append("- 提示: 本次结果请降低信任度，优先人工复核。")
        return BriefingSection(title="数据源状态", content=_section_text(lines))

    def _build_research_section(
        self,
        research_summary: ResearchSummary | None,
    ) -> BriefingSection:
        if research_summary is None:
            return BriefingSection(
                title="研究吸收",
                content=normalize_research_tone(
                    "研究进展本次未更新；这份日报只基于当前主链结果。"
                ),
            )
        lines = [
            f"- 研究发现落盘: **{research_findings_display(research_summary)}**",
            f"- 已纳入观察但不直接打分: **{len(research_summary.absorbed_families)}**",
            f"- 已部分实现策略: **{research_summary.implemented_family_count}**",
            f"- 仅写进研究记录: **{research_summary.report_only_family_count}**",
            f"- 需满足条件后启用: **{research_summary.gated_family_count}**",
        ]
        top_pipelines = list(research_summary.pipeline_summaries[:3])
        if top_pipelines:
            for item in top_pipelines:
                pipeline_label = _PIPELINE_LABELS.get(item.pipeline, item.pipeline)
                lines.append(
                    f"- 研究来源 {pipeline_label}: 高优先级 {item.p1} / 共 {item.total} / 先参考 {item.top_repo or '-'}"
                )
        if research_summary.absorbed_families:
            names = "、".join(
                f"{item.name}（{_RUNTIME_STAGE_LABELS.get(item.runtime_stage, item.runtime_stage)}）"
                for item in research_summary.absorbed_families[:4]
            )
            lines.append(f"- 已吸收主题: {names}")
        if research_summary.next_actions:
            next_item = research_summary.next_actions[0]
            lines.append(
                f"- 下一接入重点: {next_item.kind}/{next_item.item_id} [{next_item.priority}] - {next_item.blocker or '还缺前置条件'}"
            )
        prereq_item = next(
            (item for item in research_summary.prereq_items if item.status != "ready"),
            None,
        )
        if prereq_item is not None:
            missing_env = "、".join(prereq_item.missing_env_vars) or "回归样本"
            lines.append(
                f"- 当前前置缺口: {prereq_item.kind}/{prereq_item.item_id} - {prereq_item.status} ({missing_env})"
            )
        lines.append("- 原则: 研究内容只做候选和解释，不直接改写系统评分。")
        return BriefingSection(title="研究吸收", content=_section_text(lines))

    def _build_evidence_section(self, picks: list[PickResult]) -> BriefingSection:
        lines: list[str] = []
        if not picks:
            lines.append("今天无候选标的。")
            return BriefingSection(title="候选来龙去脉", content=_section_text(lines))

        for pick in picks:
            display = format_symbol_name(pick.symbol, pick.name)
            pm_action = str(pick.metrics.get("portfolio_action", "") or "")
            pm_text = portfolio_action_label(pm_action) if pm_action else "未决定"
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
            headline = f"### {display} (风险等级: {rating_label(pick.rating)} / 分数: {pick.score}"
            if candidate_status:
                headline += f" / 状态: {candidate_status}"
            headline += f" / 意见: {pm_text})"
            lines.append(headline)
            if pick.strategies:
                lines.append(f"- 用了这些方法: {', '.join(pick.strategies)}")
            for reason in pick.reasons:
                lines.append(f"- {reason}")
            if blocker:
                lines.append(f"- 现在卡在哪: {blocker}")
            if next_step:
                lines.append(f"- 接下来先看: {next_step}")
            if review_meta:
                lines.append(f"- 再看优先级/时机: {review_meta}")
            if pick.risks:
                lines.append(f"风险提示: {'；'.join(pick.risks)}")
            lines.append("")
        return BriefingSection(title="候选来龙去脉", content=_section_text(lines))

    def _build_theme_section(self, picks: list[PickResult]) -> BriefingSection:
        lines: list[str] = []
        if not picks:
            lines.append("无题材热度数据。")
            return BriefingSection(title="题材热度", content=_section_text(lines))
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
        return BriefingSection(title="题材热度", content=_section_text(lines))

    def _build_next_day_section(
        self,
        picks: list[PickResult],
        frames: dict[str, pd.DataFrame],
    ) -> BriefingSection:
        lines: list[str] = []
        tradable_picks = [p for p in picks if is_tradable_rating(p.rating)]
        if not tradable_picks:
            if picks:
                lead_pick = picks[0]
                names = "、".join(_format_pick_with_status(p) for p in picks[:3])
                blocker = _candidate_blocker_label(lead_pick)
                next_step = _candidate_next_step_label(lead_pick)
                review_meta = " / ".join(
                    part
                    for part in (
                        _candidate_review_priority_label(lead_pick),
                        _candidate_review_window_label(lead_pick),
                    )
                    if part
                )
                lines.append("### 当前暂无重点跟踪对象")
                lines.append("")
                lines.append(f"继续观察名单: {names}")
                lines.append("")
                if blocker:
                    lines.append(f"现在卡在哪: {blocker}")
                if next_step:
                    lines.append(f"接下来先看: {next_step}")
                if review_meta:
                    lines.append(f"再看时间: {review_meta}")
                lines.append("")
                lines.append("待阻塞条件解除后再考虑转入重点跟踪名单。")
            else:
                lines.append("### 明天有什么要做吗？")
                lines.append("")
                lines.append(
                    "**没有。** 今天没有合适的股票，继续等待。不要为了交易而交易，这很关键！"
                )
            return BriefingSection(title="明日重点", content=_section_text(lines))

        # 新手友好的行动清单
        lines.append("### 明天开盘怎么做？（新手按这个来）")
        lines.append("")
        lines.append("#### ⏰ 开盘前（9:20-9:25）")
        lines.append("1. 打开行情软件或看板")
        lines.append("2. 把下面的股票代码加入纸面观察")
        lines.append("")

        for idx, pick in enumerate(tradable_picks[:3], start=1):
            entry = pick.ideal_buy
            stop = pick.stop_loss
            tp = pick.take_profit
            display = _format_pick_with_status(pick)
            position_text = str(pick.position or "").strip() or "10%-15%"
            lines.append(f"##### {idx}. {display}")
            lines.append("")
            lines.append(
                f"- **为什么要关注**: {pick.reasons[0] if pick.reasons else '符合选股标准'}"
            )
            lines.append(f"- **记录时价格**: {entry}元（只用于回看当时价格位置）")
            lines.append(f"- **最多亏到**: {stop}元（跌破这里说明原来的判断变弱）")
            lines.append(f"- **先看目标**: {tp}元（接近这里就回看是否已经走完）")
            lines.append(
                f"- **比例参考**: 这只股票占纸面组合的 {position_text}（不要集中到单一标的）"
            )
            lines.append("")

        lines.append("#### 9:30 开盘后（核心步骤）")
        for idx, pick in enumerate(tradable_picks[:3], start=1):
            entry = pick.ideal_buy
            display = _format_pick_with_status(pick)
            lines.append(f"**{idx}. {display}**:")
            lines.append("   - 看现在价格是多少")
            lines.append(
                f"   - 如果在 {entry - 0.5:.2f} ~ {entry + 0.5:.2f}元之间，价格位置接近纸面参考"
            )
            lines.append(f"   - 如果比 {entry + 0.5:.2f}元还高，先降低追踪优先级")
            lines.append("")

        lines.append("#### 📱 下午2:45 ~ 3:00（收盘前检查）")
        lines.append("1. 看看纸面观察标的的涨跌情况")
        lines.append("2. 如果跌破最多亏到的位置，标记为风险升高，次日优先再看")
        lines.append("3. 如果接近先看目标，回看是否已经走完原来的判断")
        lines.append("")

        lines.append("> ⚠️ **新手必知的5个规则**:")
        lines.append(
            "> 1. **最多亏到最重要** - 跌破这条线说明原来的判断变弱，要先降风险"
        )
        lines.append("> 2. **别集中** - 单一股票不要占纸面组合太高")
        lines.append("> 3. **别逆势** - 纸面观察也要顺着市况验证，弱市里先降低优先级")
        lines.append("> 4. **留现金** - 纸面组合也要保留缓冲，避免把单日信号看得过满")
        lines.append("> 5. **看不懂就观察** - 如果理由不清楚，就只记录，不推进")
        lines.append("")

        if len(tradable_picks) > 3:
            lines.append("#### 其他可以观察的股票（备选）")
            for pick in tradable_picks[3:5]:
                display = _format_pick_with_status(pick)
                lines.append(f"- {display}: 可以观察，暂时不作为优先选择")
            lines.append("")

        return BriefingSection(title="明日重点", content=_section_text(lines))

    def render_template(
        self,
        briefing: Briefing,
        picks: list[PickResult],
        circuit_breaker_status: object | None = None,
    ) -> str:
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(default_for_string=True, default=True),
        )
        template = env.get_template("default.md.j2")
        cb_triggered = circuit_breaker_status is not None and getattr(
            circuit_breaker_status, "triggered", False
        )
        cb_reason = (
            _safe_markdown_text(getattr(circuit_breaker_status, "reason", ""))
            if cb_triggered
            else ""
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
                "name": _safe_markdown_text(p.name),
                "score": p.score,
                "rating": _safe_markdown_text(p.rating),
                "strategies": [_safe_markdown_text(item) for item in p.strategies],
                "reasons": [_safe_markdown_text(item) for item in p.reasons],
            }
            for p in picks
        ]
        return normalize_research_tone(
            template.render(
                date=_safe_markdown_text(briefing.date),
                circuit_breaker_triggered=cb_triggered,
                circuit_breaker_reason=cb_reason,
                main_chain_section=_safe_markdown_text(main_chain_section),
                regime_section=_safe_markdown_text(regime_section),
                picks=pick_dicts,
                theme_section=_safe_markdown_text(theme_section),
                next_day_section=_safe_markdown_text(next_day_section),
            )
        )
