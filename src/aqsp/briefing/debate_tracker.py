"""辩论表现追踪模块 - 追踪Agent预测准确率"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from aqsp.briefing.agent_roles import (
    AgentRole,
    DEFAULT_RUNTIME_AGENT_ROLE_ORDER,
    agent_role_label,
    agent_role_focus,
)
from aqsp.briefing.debate import AgentPerformanceMetrics
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import append_jsonl

_CROSS_MARKET_CONTEXT_STRONG_FACTOR = 1.15
_CROSS_MARKET_CONTEXT_SUPPORTIVE_FACTOR = 1.05
_CROSS_MARKET_CONTEXT_UNKNOWN_FACTOR = 0.85
_CROSS_MARKET_CONTEXT_CONFLICTED_FACTOR = 0.90
_CROSS_MARKET_CONTEXT_WEAK_FACTOR = 0.75
_CROSS_MARKET_CONTEXT_MIN_BUCKET_SAMPLES = 3
_CROSS_MARKET_CONTEXT_MIN_TOTAL_SAMPLES = 5
_VALID_STANCES = frozenset(("bullish", "bearish", "neutral"))

# 学习层只在足够的、可归属的历史上生效，避免盘中重复运行造成权重抖动。
_MIN_AGENT_SAMPLES = 5
_MIN_INDEPENDENT_SIGNAL_DAYS = 3
_LEARNING_COOLDOWN_DAYS = 3


@dataclass(frozen=True)
class DebateQualityAudit:
    """多 Agent 讨论的可审计性结果，不参与评分。"""

    candidate_mapped: bool
    round_count: int
    expected_round_counts: tuple[int, ...]
    expected_role_count: int
    recorded_role_count: int
    process_recorded: bool
    conclusion_recorded: bool
    next_trigger_recorded: bool
    historical_evaluation_only: bool
    empty_rounds: tuple[int, ...]
    missing_roles_by_round: tuple[tuple[int, tuple[str, ...]], ...]
    duplicate_roles_by_round: tuple[tuple[int, tuple[str, ...]], ...]
    non_interactive_rounds: tuple[int, ...]
    support_recorded: bool
    opposition_recorded: bool
    risk_recorded: bool
    cross_market_recorded: bool
    issues: tuple[str, ...]
    evidence_sufficient: bool = False
    advisory_boundary_ok: bool = True
    data_status: str = "available"

    @property
    def passed(self) -> bool:
        return not self.issues


def audit_debate_quality(
    result: Any,
    *,
    candidate: Any | None = None,
    expected_roles: Iterable[AgentRole | str] = DEFAULT_RUNTIME_AGENT_ROLE_ORDER,
    expected_round_counts: tuple[int, ...] = (2, 3),
) -> DebateQualityAudit:
    """审计讨论过程、结论和触发条件；历史表现只标记为评估用途。"""
    expected_role_values = tuple(
        role.value if isinstance(role, AgentRole) else str(role).strip()
        for role in expected_roles
        if (role.value if isinstance(role, AgentRole) else str(role).strip())
    )
    expected_role_set = set(expected_role_values)
    rounds = tuple(_field(result, "rounds", ()) or ())
    empty_rounds: list[int] = []
    missing_roles: list[tuple[int, tuple[str, ...]]] = []
    duplicate_roles: list[tuple[int, tuple[str, ...]]] = []
    non_interactive_rounds: list[int] = []
    recorded_roles: set[str] = set()
    previous_by_role: dict[str, Any] = {}
    round_numbers: list[int] = []

    for round_data in rounds:
        round_num = int(_field(round_data, "round_num", 0) or 0)
        round_numbers.append(round_num)
        opinions = tuple(_field(round_data, "opinions", ()) or ())
        if not opinions:
            empty_rounds.append(round_num)
            continue

        role_values = [_role_value(_field(opinion, "role", "")) for opinion in opinions]
        clean_roles = [role for role in role_values if role]
        recorded_roles.update(clean_roles)
        seen: set[str] = set()
        duplicated: list[str] = []
        for role in clean_roles:
            if role in seen:
                duplicated.append(role)
            else:
                seen.add(role)
        if duplicated:
            duplicate_roles.append((round_num, tuple(dict.fromkeys(duplicated))))
        missing = tuple(sorted(expected_role_set - set(clean_roles)))
        if missing:
            missing_roles.append((round_num, missing))

        if round_num > 1:
            interactive = False
            previous_roles = set(previous_by_role)
            for opinion in opinions:
                role = _role_value(_field(opinion, "role", ""))
                if not role:
                    continue
                counterarguments = tuple(
                    str(item).strip()
                    for item in (_field(opinion, "counterarguments", ()) or ())
                    if str(item).strip() and not _is_placeholder_text(item)
                )
                peer_reviewed_roles = tuple(
                    _role_value(item)
                    for item in (_field(opinion, "peer_reviewed_roles", ()) or ())
                    if _role_value(item)
                )
                counterargument_roles = tuple(
                    _role_value(item)
                    for item in (_field(opinion, "counterargument_roles", ()) or ())
                    if _role_value(item)
                )
                referenced_roles = set(peer_reviewed_roles) | set(counterargument_roles)
                if counterarguments and referenced_roles & previous_roles:
                    interactive = True
                    break
            if not interactive:
                non_interactive_rounds.append(round_num)
        previous_by_role = {
            _role_value(_field(opinion, "role", "")): opinion
            for opinion in opinions
            if _role_value(_field(opinion, "role", ""))
        }

    result_symbol = _clean_text(_field(result, "symbol", ""))
    candidate_symbol = _candidate_symbol(candidate)
    candidate_mapped = bool(result_symbol) and (
        not candidate_symbol or result_symbol == candidate_symbol
    )
    result_date = _normal_date(
        _field(result, "related_signal_date", _field(result, "signal_date", ""))
    )
    candidate_date = _normal_date(
        _field(candidate, "date", _field(candidate, "signal_date", ""))
    )
    if candidate_mapped and result_date and candidate_date:
        candidate_mapped = result_date == candidate_date
    result_fingerprint = _result_fingerprint(result)
    candidate_fingerprint = _candidate_fingerprint(candidate)
    if candidate_mapped and result_fingerprint and candidate_fingerprint:
        candidate_mapped = result_fingerprint == candidate_fingerprint
    round_count = len(rounds)
    expected_sequence = tuple(range(1, round_count + 1))
    valid_round_sequence = tuple(round_numbers) == expected_sequence
    process_recorded = (
        round_count in expected_round_counts
        and len(recorded_roles) >= 2
        and not empty_rounds
        and not missing_roles
        and not duplicate_roles
        and not non_interactive_rounds
        and valid_round_sequence
        and all(_round_has_valid_opinions(round_data) for round_data in rounds)
    )
    conclusion_recorded = bool(
        _clean_text(_field(result, "final_consensus", _field(result, "conclusion", "")))
        and _valid_final_vote(_field(result, "final_vote", {}))
    )
    next_trigger_recorded = bool(_clean_text(_field(result, "next_trigger", "")))
    evidence_sufficient = _has_substantive_evidence(result, rounds)
    data_status = _clean_text(_field(result, "data_status", "available")) or "available"
    if data_status != "available":
        process_recorded = False
        evidence_sufficient = False
    advisory_boundary_ok = _advisory_boundary_is_intact(result)
    support_recorded = bool(
        _substantive_values(_field(result, "support_points", ()))
        or _stance_viewpoint_recorded(rounds, "bullish")
    )
    opposition_recorded = bool(
        _substantive_values(_field(result, "opposition_points", ()))
        or _stance_viewpoint_recorded(rounds, "bearish")
    )
    risk_recorded = bool(
        _substantive_values(_field(result, "risk_warnings", ()))
        or _has_substantive_text(_field(result, "primary_risk_gate", ""))
        or any(
            _substantive_values(_field(opinion, "risk_factors", ()))
            for round_data in rounds
            for opinion in (_field(round_data, "opinions", ()) or ())
        )
    )
    cross_market_required = "cross_market" in expected_role_set
    cross_market_recorded = not cross_market_required or (
        "cross_market" in recorded_roles
        and any(
            _role_value(_field(opinion, "role", "")) == "cross_market"
            and bool(
                # An explicit "no verified cross-market evidence" judgment is
                # still a valid role output. It must remain neutral, but should
                # not be mistaken for the role being absent from the debate.
                _meaningful_values(_field(opinion, "arguments", ()))
                or _meaningful_values(_field(opinion, "risk_factors", ()))
            )
            for round_data in rounds
            for opinion in (_field(round_data, "opinions", ()) or ())
        )
    )

    issues: list[str] = []
    if not candidate_mapped:
        issues.append("candidate_unmapped")
    if len(recorded_roles) < 2:
        issues.append("insufficient_roles")
    if round_count == 0:
        issues.append("empty_discussion")
    elif round_count not in expected_round_counts:
        issues.append("unexpected_round_count")
    if not valid_round_sequence:
        issues.append("invalid_round_sequence")
    if empty_rounds:
        issues.append("empty_round")
    if missing_roles:
        issues.append("missing_role")
    if duplicate_roles:
        issues.append("duplicate_role")
    if non_interactive_rounds:
        issues.append("non_interactive_round")
    if not all(_round_has_valid_opinions(round_data) for round_data in rounds):
        issues.append("invalid_opinion")
    if not conclusion_recorded:
        issues.append("missing_conclusion")
    if not _valid_final_vote(_field(result, "final_vote", {})):
        issues.append("invalid_final_vote")
    if not next_trigger_recorded:
        issues.append("missing_next_trigger")
    if not support_recorded:
        issues.append("missing_support_viewpoint")
    if not opposition_recorded:
        issues.append("missing_opposition_viewpoint")
    if not risk_recorded:
        issues.append("missing_risk_viewpoint")
    if not cross_market_recorded:
        issues.append("missing_cross_market_viewpoint")
    if not evidence_sufficient:
        issues.append("no_substantive_evidence")
    if data_status != "available":
        issues.append("empty_market_data")
    if not advisory_boundary_ok:
        issues.append("advisory_boundary_violation")

    return DebateQualityAudit(
        candidate_mapped=candidate_mapped,
        round_count=round_count,
        expected_round_counts=expected_round_counts,
        expected_role_count=len(expected_role_set),
        recorded_role_count=len(recorded_roles),
        process_recorded=process_recorded,
        conclusion_recorded=conclusion_recorded,
        next_trigger_recorded=next_trigger_recorded,
        historical_evaluation_only=True,
        empty_rounds=tuple(empty_rounds),
        missing_roles_by_round=tuple(missing_roles),
        duplicate_roles_by_round=tuple(duplicate_roles),
        non_interactive_rounds=tuple(non_interactive_rounds),
        support_recorded=support_recorded,
        opposition_recorded=opposition_recorded,
        risk_recorded=risk_recorded,
        cross_market_recorded=cross_market_recorded,
        issues=tuple(dict.fromkeys(issues)),
        evidence_sufficient=evidence_sufficient,
        advisory_boundary_ok=advisory_boundary_ok,
        data_status=data_status,
    )


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _role_value(value: Any) -> str:
    return _clean_text(getattr(value, "value", value))


_NON_EVIDENCE_MARKERS = (
    "输入未提供",
    "无可用",
    "无可用新闻记录",
    "尚未形成",
    "等待更多确认",
    "等待新证据",
    "不能确认",
    "无法确认",
    "暂不确认",
    "不据此形成判断",
    "未提供额外风控证据",
    "未发现明显风险因素",
    "未出现需要反驳",
)


def _is_placeholder_text(value: Any) -> bool:
    text = _clean_text(value)
    return not text or any(marker in text for marker in _NON_EVIDENCE_MARKERS)


def _has_substantive_text(value: Any) -> bool:
    return bool(_clean_text(value)) and not _is_placeholder_text(value)


def _substantive_values(values: Any) -> bool:
    if isinstance(values, str):
        values = (values,)
    return any(_has_substantive_text(item) for item in (values or ()))


def _has_substantive_evidence(result: Any, rounds: tuple[Any, ...]) -> bool:
    for field_name in (
        "real_message_evidence",
        "cross_market_evidence",
        "rule_transmission_evidence",
        "support_points",
        "opposition_points",
        "risk_warnings",
    ):
        if _substantive_values(_field(result, field_name, ())):
            return True
    return any(
        _substantive_values(_field(opinion, field_name, ()))
        for round_data in rounds
        for opinion in (_field(round_data, "opinions", ()) or ())
        for field_name in ("arguments", "risk_factors", "opportunity_factors")
    )


def _advisory_boundary_is_intact(result: Any) -> bool:
    if _field(result, "advisory_only", True) is not True:
        return False
    if _field(result, "deterministic_score_unchanged", True) is not True:
        return False
    original = _field(result, "original_score", None)
    deterministic = _field(result, "deterministic_score", None)
    if original is None or deterministic in (None, "", 0.0) and original != 0.0:
        return True
    try:
        return float(original) == float(deterministic)
    except (TypeError, ValueError):
        return False


def _evaluation_record_key(data: dict[str, Any]) -> str:
    """Build a dedupe key only for fully attributable post-debate outcomes."""
    task_id = _clean_text(data.get("task_id"))
    identity = tuple(
        _clean_text(data.get(field))
        for field in (
            "debate_id",
            "signal_date",
            "candidate_fingerprint",
            "role",
            "agent_id",
        )
    )
    return "|".join((task_id, *identity)) if all(identity) else ""


def _candidate_symbol(candidate: Any | None) -> str:
    if candidate is None:
        return ""
    if isinstance(candidate, str):
        return candidate.strip()
    if isinstance(candidate, dict):
        return _clean_text(candidate.get("symbol"))
    return _clean_text(getattr(candidate, "symbol", ""))


def _normal_date(value: Any) -> str:
    return _clean_text(value)[:10]


def _parse_record_datetime(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=now_shanghai().tzinfo)
    return parsed


def _candidate_fingerprint(candidate: Any | None) -> str:
    if candidate is None:
        return ""
    if isinstance(candidate, Mapping):
        metrics = candidate.get("metrics")
        metrics = metrics if isinstance(metrics, Mapping) else {}
        return _clean_text(
            candidate.get("candidate_fingerprint")
            or candidate.get("debate_candidate_fingerprint")
            or metrics.get("candidate_fingerprint")
            or metrics.get("debate_candidate_fingerprint")
        )
    metrics = getattr(candidate, "metrics", {})
    metrics = metrics if isinstance(metrics, Mapping) else {}
    return _clean_text(
        getattr(candidate, "candidate_fingerprint", "")
        or getattr(candidate, "debate_candidate_fingerprint", "")
        or metrics.get("candidate_fingerprint")
        or metrics.get("debate_candidate_fingerprint")
    )


def _result_fingerprint(result: Any) -> str:
    return _clean_text(
        _field(result, "candidate_fingerprint", "")
        or _field(result, "debate_candidate_fingerprint", "")
    )


def _round_has_valid_opinions(round_data: Any) -> bool:
    opinions = tuple(_field(round_data, "opinions", ()) or ())
    if not opinions:
        return False
    for opinion in opinions:
        role = _role_value(_field(opinion, "role", ""))
        stance = _clean_text(_field(opinion, "stance", ""))
        agent_id = _clean_text(_field(opinion, "agent_id", ""))
        text_fields = (
            _field(opinion, "arguments", ()),
            _field(opinion, "counterarguments", ()),
            _field(opinion, "risk_factors", ()),
            _field(opinion, "opportunity_factors", ()),
        )
        has_text = any(
            _clean_text(item) for group in text_fields for item in (group or ())
        )
        if not role or not agent_id or stance not in _VALID_STANCES or not has_text:
            return False
    return True


def _meaningful_values(values: Any) -> bool:
    return any(_clean_text(item) for item in (values or ()))


def _stance_viewpoint_recorded(rounds: tuple[Any, ...], stance: str) -> bool:
    return any(
        _clean_text(_field(opinion, "stance", "")) == stance
        and (
            _substantive_values(_field(opinion, "arguments", ()))
            or _substantive_values(_field(opinion, "opportunity_factors", ()))
        )
        for round_data in rounds
        for opinion in (_field(round_data, "opinions", ()) or ())
    )


def _valid_final_vote(vote: Any) -> bool:
    if not isinstance(vote, dict) or not vote:
        return False
    for role, stance in vote.items():
        role_value = _role_value(role)
        if not _clean_text(role_value) or _clean_text(stance) not in _VALID_STANCES:
            return False
    return True


def _opinion_changed(previous: Any | None, current: Any) -> bool:
    if previous is None:
        return False
    fields = (
        "stance",
        "confidence",
        "arguments",
        "risk_factors",
        "opportunity_factors",
    )
    return any(
        _field(previous, field, None) != _field(current, field, None)
        for field in fields
    )


@dataclass(frozen=True)
class DebateContextPerformanceSummary:
    bucket: str
    label: str
    sample_count: int
    correct_count: int
    bullish_count: int
    bearish_count: int
    neutral_count: int
    avg_support_event_count: float
    avg_conflict_event_count: float

    @property
    def accuracy(self) -> float:
        if self.sample_count <= 0:
            return 0.0
        return self.correct_count / self.sample_count


@dataclass(frozen=True)
class CrossMarketContextHistorySummary:
    current_bucket: str
    current_label: str
    current_sample_count: int
    current_accuracy: float
    total_sample_count: int
    governance_note: str
    bucket_summaries: tuple[DebateContextPerformanceSummary, ...]


@dataclass(frozen=True)
class AgentReliabilitySummary:
    role: AgentRole
    role_label: str
    agent_id: str
    sample_count: int
    correct_count: int
    accuracy: float
    adjustment_weight: float
    bias_toward: str

    @property
    def summary_line(self) -> str:
        if self.sample_count <= 0:
            return f"{self.role_label}: 近21天暂无样本｜当前权重 {self.adjustment_weight:.2f}"
        line = (
            f"{self.role_label}: 近21天 {self.correct_count}/{self.sample_count} "
            f"({self.accuracy:.0%})｜当前权重 {self.adjustment_weight:.2f}"
        )
        if self.bias_toward != "neutral":
            line += f"｜偏向{_bias_label(self.bias_toward)}"
        return line


@dataclass(frozen=True)
class AgentResponsibility:
    """本轮讨论的责任人快照；只描述职责，不参与评分。"""

    role: AgentRole
    role_label: str
    agent_id: str
    responsibility: str

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role.value,
            "role_label": self.role_label,
            "agent_id": self.agent_id,
            "responsibility": self.responsibility,
        }


class DebatePerformanceTracker:
    """追踪辩论中各Agent的预测表现"""

    PERFORMANCE_WINDOW_DAYS = 21  # 3周窗口
    MIN_AGENT_SAMPLES = _MIN_AGENT_SAMPLES
    MIN_INDEPENDENT_SIGNAL_DAYS = _MIN_INDEPENDENT_SIGNAL_DAYS
    LEARNING_COOLDOWN_DAYS = _LEARNING_COOLDOWN_DAYS

    def __init__(
        self,
        storage_path: str = "data/debate_performance.jsonl",
        *,
        task_id: str | None = None,
    ):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.task_id = _clean_text(task_id) or _clean_text(
            os.getenv("AQSP_RUN_TASK_ID", "")
        )
        self._performance_cache: dict[str, AgentPerformanceMetrics] = {}
        self._record_keys: set[str] = set()
        self._agent_signal_days: dict[str, set[str]] = {}
        self._agent_latest_record_at: dict[str, datetime] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        """从文件加载历史表现数据"""
        if not self.storage_path.exists():
            return

        cutoff_date = now_shanghai() - timedelta(days=self.PERFORMANCE_WINDOW_DAYS)

        for line in self.storage_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if not self._record_is_usable(data) or not self._record_matches_task(
                    data, self.task_id
                ):
                    continue
                record_date = data.get("created_at", "")
                if record_date:
                    try:
                        record_dt = datetime.fromisoformat(
                            record_date.replace("Z", "+00:00")
                        )
                        if record_dt < cutoff_date:
                            continue
                    except (ValueError, TypeError):
                        pass

                role_str = data.get("role", "")
                try:
                    role = AgentRole(role_str)
                except ValueError:
                    continue

                agent_id = data.get("agent_id", "")
                record_key = _evaluation_record_key(data)
                if record_key and record_key in self._record_keys:
                    continue
                if record_key:
                    self._record_keys.add(record_key)
                key = self._agent_cache_key(role, agent_id)

                if key not in self._performance_cache:
                    self._performance_cache[key] = AgentPerformanceMetrics(
                        agent_id=agent_id,
                        role=role,
                        total_predictions=0,
                        correct_predictions=0,
                        avg_confidence=0.5,
                        bias_toward="neutral",
                    )

                metrics = self._performance_cache[key]
                metrics.total_predictions += 1
                if data.get("was_correct", False):
                    metrics.correct_predictions += 1
                self._track_record_provenance(key, data)

            except (json.JSONDecodeError, KeyError):
                continue

    def get_agent_metrics(
        self, role: AgentRole, agent_id: str
    ) -> AgentPerformanceMetrics:
        """获取指定Agent的性能指标"""
        key = self._agent_cache_key(role, agent_id)

        if key not in self._performance_cache:
            self._performance_cache[key] = AgentPerformanceMetrics(
                agent_id=agent_id,
                role=role,
            )

        return self._performance_cache[key]

    def record_prediction(
        self,
        role: AgentRole,
        agent_id: str,
        predicted_stance: str,
        was_correct: bool,
        context: dict[str, Any] | None = None,
        *,
        task_id: str = "",
        debate_id: str = "",
        signal_date: str = "",
        candidate_fingerprint: str = "",
    ) -> None:
        """记录一次预测结果，可按 task_id 过滤上下文历史。"""
        if not self._prediction_is_valid(agent_id, predicted_stance):
            return
        effective_task_id = _clean_text(task_id) or self.task_id
        if effective_task_id and self.task_id and effective_task_id != self.task_id:
            return
        record_key = _evaluation_record_key(
            {
                "task_id": effective_task_id,
                "role": role.value,
                "agent_id": agent_id,
                "debate_id": debate_id,
                "signal_date": signal_date,
                "candidate_fingerprint": candidate_fingerprint,
            }
        )
        if record_key and record_key in self._record_keys:
            return
        in_tracker_scope = (
            effective_task_id == self.task_id if self.task_id else not effective_task_id
        )
        if in_tracker_scope:
            metrics = self.get_agent_metrics(role, agent_id)
            metrics.total_predictions += 1
            if was_correct:
                metrics.correct_predictions += 1

        if in_tracker_scope and metrics.total_predictions > 5:
            if metrics.correct_predictions / metrics.total_predictions > 0.6:
                metrics.bias_toward = "bullish"
            elif metrics.correct_predictions / metrics.total_predictions < 0.4:
                metrics.bias_toward = "bearish"
            else:
                metrics.bias_toward = "neutral"

        self._persist_record(
            role,
            agent_id,
            predicted_stance,
            was_correct,
            context=context,
            task_id=effective_task_id,
            debate_id=debate_id,
            signal_date=signal_date,
            candidate_fingerprint=candidate_fingerprint,
        )
        if record_key:
            self._record_keys.add(record_key)
        if in_tracker_scope:
            self._track_record_provenance(
                self._agent_cache_key(role, agent_id),
                {
                    "signal_date": signal_date,
                    "created_at": now_shanghai().isoformat(timespec="seconds"),
                },
            )

    def _persist_record(
        self,
        role: AgentRole,
        agent_id: str,
        predicted_stance: str,
        was_correct: bool,
        context: dict[str, Any] | None = None,
        task_id: str = "",
        debate_id: str = "",
        signal_date: str = "",
        candidate_fingerprint: str = "",
    ) -> None:
        """持久化单条记录"""
        record = {
            "agent_id": agent_id,
            "role": role.value,
            "predicted_stance": predicted_stance,
            "was_correct": was_correct,
            "created_at": now_shanghai().isoformat(timespec="seconds"),
        }
        clean_task_id = _clean_text(task_id) or self.task_id
        if clean_task_id:
            record["task_id"] = clean_task_id
        if _clean_text(debate_id):
            record["debate_id"] = _clean_text(debate_id)
        if _clean_text(signal_date):
            record["signal_date"] = _clean_text(signal_date)
        if _clean_text(candidate_fingerprint):
            record["candidate_fingerprint"] = _clean_text(candidate_fingerprint)
        if context:
            record["context"] = context

        append_jsonl(self.storage_path, record)

    def calculate_adjustment_weight(
        self,
        role: AgentRole,
        agent_id: str,
        regime: str = "unknown",
        debate_context: dict[str, Any] | None = None,
    ) -> float:
        """
        计算Agent的调整权重（含时间衰减和市场状态自适应）

        逻辑：
        - 准确率 >= 70%: 权重 0.15~0.25
        - 准确率 50%~70%: 权重 0.05~0.15
        - 准确率 < 50%: 权重 -0.1~0.05 (反向影响)
        - 时间衰减：3周窗口，越新权重越大
        - 市场状态自适应：牛市时多头加权，熊市时空头加权
        """
        metrics = self.get_agent_metrics(role, agent_id)
        if not self._learning_is_unlocked(role, agent_id):
            return 0.0
        accuracy = metrics.accuracy

        if accuracy >= 0.7:
            base_weight = 0.15 + (accuracy - 0.7) * 0.5
        elif accuracy >= 0.5:
            base_weight = 0.05 + (accuracy - 0.5) * 0.5
        elif accuracy >= 0.3:
            base_weight = -0.1 + (accuracy - 0.3) * 0.25
        else:
            base_weight = -0.1

        # 时间衰减：按天数加权，越新的数据权重越高
        decay_factor = self._calculate_time_decay(role, agent_id)
        weight = base_weight * decay_factor

        # 市场状态自适应
        regime_factor = self._get_regime_factor(role, regime)
        weight *= regime_factor
        weight *= self._get_context_factor(role, debate_context)

        return max(-0.15, min(0.30, weight))

    def _calculate_time_decay(self, role: AgentRole, agent_id: str) -> float:
        """计算时间衰减因子：新数据权重高，旧数据权重低"""
        key = self._agent_cache_key(role, agent_id)
        if key not in self._performance_cache:
            return 0.8

        # 简单实现：按总预测次数衰减，最近的数据衰减少
        metrics = self._performance_cache[key]
        if metrics.total_predictions <= 0:
            return 0.8

        # 最近的数据权重1.0，越老衰减越大
        # 最低衰减到0.5
        return min(
            1.0,
            0.5 + 0.5 * (metrics.total_predictions / (metrics.total_predictions + 10)),
        )

    def _track_record_provenance(self, key: str, record: dict[str, Any]) -> None:
        signal_date = _normal_date(record.get("signal_date", ""))
        created_at = _parse_record_datetime(record.get("created_at", ""))
        if not signal_date and created_at is not None:
            signal_date = created_at.date().isoformat()
        if signal_date:
            self._agent_signal_days.setdefault(key, set()).add(signal_date)
        if created_at is not None:
            previous = self._agent_latest_record_at.get(key)
            if previous is None or created_at > previous:
                self._agent_latest_record_at[key] = created_at

    def _learning_is_unlocked(self, role: AgentRole, agent_id: str) -> bool:
        """只有满足样本、独立信号日且离最近记录足够久才允许自适应。"""
        key = self._agent_cache_key(role, agent_id)
        metrics = self.get_agent_metrics(role, agent_id)
        signal_days = len(self._agent_signal_days.get(key, set()))
        # 兼容仅用于离线单元测试/旧缓存的手工聚合指标；生产记录都会带来源信息。
        has_provenance = (
            key in self._agent_signal_days or key in self._agent_latest_record_at
        )
        if has_provenance and (
            metrics.total_predictions < self.MIN_AGENT_SAMPLES
            or signal_days < self.MIN_INDEPENDENT_SIGNAL_DAYS
        ):
            return False
        latest = self._agent_latest_record_at.get(key)
        if latest is not None:
            elapsed = now_shanghai() - latest
            if elapsed < timedelta(days=self.LEARNING_COOLDOWN_DAYS):
                return False
        return metrics.total_predictions >= self.MIN_AGENT_SAMPLES

    def _get_regime_factor(self, role: AgentRole, regime: str) -> float:
        """市场状态自适应：不同市场状态下调整不同Agent的权重"""
        regime_lower = regime.lower()

        # 牛市：多头Agent加权，空头减权
        if "bull" in regime_lower or "up" in regime_lower:
            if role == AgentRole.BULL:
                return 1.2
            elif role == AgentRole.BEAR:
                return 0.8
            elif role == AgentRole.NORTHBOUND:
                return 1.1

        # 熊市：空头Agent加权，多头减权
        elif "bear" in regime_lower or "down" in regime_lower:
            if role == AgentRole.BEAR:
                return 1.2
            elif role == AgentRole.BULL:
                return 0.8
            elif role == AgentRole.RISK_CONTROL:
                return 1.1

        # 震荡市：风险控制和板块轮动加权
        elif "shock" in regime_lower or "震荡" in regime_lower:
            if role == AgentRole.RISK_CONTROL:
                return 1.2
            elif role == AgentRole.SECTOR_LEADER:
                return 1.1

        return 1.0

    def calculate_debate_adjustment(
        self,
        votes: dict[AgentRole, str],
        agent_weights: dict[AgentRole, float],
    ) -> tuple[float, float, str]:
        """
        计算辩论对评分的调整

        返回: (adjustment_weight, disagreement_score, recommended_adjustment)

        adjustment_weight: 综合调整权重
        disagreement_score: 分歧程度 0~1
        recommended_adjustment: "raise", "lower", "keep"
        """
        if not votes:
            return 0.0, 0.0, "keep"

        vote_values = list(votes.values())
        bullish_count = vote_values.count("bullish")
        bearish_count = vote_values.count("bearish")
        neutral_count = vote_values.count("neutral")
        total = len(vote_values)

        max_vote = max(bullish_count, bearish_count, neutral_count)
        expected_random = 1 / 3
        observed_max = max_vote / total
        disagreement_score = 1 - (observed_max - expected_random) / (
            1 - expected_random
        )
        disagreement_score = max(0.0, min(1.0, disagreement_score))

        weighted_sum = 0.0
        for role, stance in votes.items():
            weight = agent_weights.get(role, 0.1)
            if stance == "bullish":
                weighted_sum += weight
            elif stance == "bearish":
                weighted_sum -= weight

        max_possible = sum(agent_weights.values()) if agent_weights else 0.1
        if max_possible > 0:
            normalized = weighted_sum / max_possible
        else:
            normalized = 0.0

        adjustment_weight = normalized * 0.3

        if normalized > 0.2:
            recommended = "raise"
        elif normalized < -0.2:
            recommended = "lower"
        else:
            recommended = "keep"

        return adjustment_weight, disagreement_score, recommended

    def get_all_weights(
        self,
        agent_ids: dict[AgentRole, str],
        regime: str = "unknown",
        debate_context: dict[str, Any] | None = None,
    ) -> dict[AgentRole, float]:
        """获取所有Agent的调整权重"""
        return {
            role: self.calculate_adjustment_weight(
                role,
                agent_id,
                regime,
                debate_context=debate_context,
            )
            for role, agent_id in agent_ids.items()
        }

    def _get_context_factor(
        self,
        role: AgentRole,
        debate_context: dict[str, Any] | None,
    ) -> float:
        if role != AgentRole.CROSS_MARKET or not debate_context:
            return 1.0

        support_count = int(
            debate_context.get("cross_market_support_event_count", 0) or 0
        )
        conflict_count = int(
            debate_context.get("cross_market_conflict_event_count", 0) or 0
        )

        if support_count <= 0 and conflict_count <= 0:
            return _CROSS_MARKET_CONTEXT_UNKNOWN_FACTOR
        if support_count >= 2 and conflict_count == 0:
            return _CROSS_MARKET_CONTEXT_STRONG_FACTOR
        if support_count > conflict_count and support_count >= 2:
            return _CROSS_MARKET_CONTEXT_SUPPORTIVE_FACTOR
        if conflict_count > support_count:
            return _CROSS_MARKET_CONTEXT_WEAK_FACTOR
        if conflict_count > 0:
            return _CROSS_MARKET_CONTEXT_CONFLICTED_FACTOR
        return 1.0

    def get_context_breakdown(
        self,
        role: AgentRole,
        *,
        task_id: str | None = None,
    ) -> tuple[DebateContextPerformanceSummary, ...]:
        """按上下文场景汇总历史表现。"""
        records = self._load_recent_records(role, task_id=task_id)
        if not records:
            return ()

        stats: dict[str, dict[str, float | int]] = {}
        for record in records:
            bucket = self._context_bucket_from_context(record.get("context"))
            bucket_stats = stats.setdefault(
                bucket,
                {
                    "sample_count": 0,
                    "correct_count": 0,
                    "bullish_count": 0,
                    "bearish_count": 0,
                    "neutral_count": 0,
                    "support_sum": 0,
                    "conflict_sum": 0,
                },
            )
            bucket_stats["sample_count"] += 1
            if record.get("was_correct", False):
                bucket_stats["correct_count"] += 1
            stance = str(record.get("predicted_stance", "") or "").strip()
            if stance == "bullish":
                bucket_stats["bullish_count"] += 1
            elif stance == "bearish":
                bucket_stats["bearish_count"] += 1
            else:
                bucket_stats["neutral_count"] += 1
            context = record.get("context")
            support_count, conflict_count = self._context_counts(context)
            bucket_stats["support_sum"] += support_count
            bucket_stats["conflict_sum"] += conflict_count

        ordered_buckets = (
            "strong_supportive",
            "supportive",
            "conflicted",
            "conflicts_dominate",
            "unknown",
        )
        summaries: list[DebateContextPerformanceSummary] = []
        for bucket in ordered_buckets:
            bucket_stats = stats.get(bucket)
            if not bucket_stats:
                continue
            sample_count = int(bucket_stats["sample_count"])
            summaries.append(
                DebateContextPerformanceSummary(
                    bucket=bucket,
                    label=self._context_bucket_label(bucket),
                    sample_count=sample_count,
                    correct_count=int(bucket_stats["correct_count"]),
                    bullish_count=int(bucket_stats["bullish_count"]),
                    bearish_count=int(bucket_stats["bearish_count"]),
                    neutral_count=int(bucket_stats["neutral_count"]),
                    avg_support_event_count=(
                        float(bucket_stats["support_sum"]) / sample_count
                    ),
                    avg_conflict_event_count=(
                        float(bucket_stats["conflict_sum"]) / sample_count
                    ),
                )
            )
        return tuple(summaries)

    def get_cross_market_context_history(
        self,
        debate_context: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
    ) -> CrossMarketContextHistorySummary:
        """返回跨市角色在不同证据场景下的历史表现摘要。"""
        summaries = self.get_context_breakdown(
            AgentRole.CROSS_MARKET,
            task_id=task_id,
        )
        current_bucket = self._context_bucket_from_context(debate_context)
        current_label = self._context_bucket_label(current_bucket)
        current_summary = next(
            (item for item in summaries if item.bucket == current_bucket),
            None,
        )
        total_sample_count = sum(item.sample_count for item in summaries)
        governance_note = self._build_cross_market_governance_note(
            summaries,
            current_bucket=current_bucket,
            total_sample_count=total_sample_count,
        )
        return CrossMarketContextHistorySummary(
            current_bucket=current_bucket,
            current_label=current_label,
            current_sample_count=0
            if current_summary is None
            else current_summary.sample_count,
            current_accuracy=0.0
            if current_summary is None
            else current_summary.accuracy,
            total_sample_count=total_sample_count,
            governance_note=governance_note,
            bucket_summaries=summaries,
        )

    def get_agent_reliability_summary(
        self,
        role: AgentRole,
        agent_id: str,
        *,
        regime: str = "unknown",
        debate_context: dict[str, Any] | None = None,
    ) -> AgentReliabilitySummary:
        metrics = self.get_agent_metrics(role, agent_id)
        return AgentReliabilitySummary(
            role=role,
            role_label=self._get_role_name(role),
            agent_id=metrics.agent_id,
            sample_count=metrics.total_predictions,
            correct_count=metrics.correct_predictions,
            accuracy=metrics.accuracy,
            adjustment_weight=self.calculate_adjustment_weight(
                role,
                agent_id,
                regime=regime,
                debate_context=debate_context,
            ),
            bias_toward=metrics.bias_toward,
        )

    def get_all_reliability_summaries(
        self,
        agent_ids: dict[AgentRole, str],
        *,
        regime: str = "unknown",
        debate_context: dict[str, Any] | None = None,
    ) -> tuple[AgentReliabilitySummary, ...]:
        return tuple(
            self.get_agent_reliability_summary(
                role,
                agent_id,
                regime=regime,
                debate_context=debate_context,
            )
            for role, agent_id in agent_ids.items()
        )

    def get_agent_responsibilities(
        self,
        agent_ids: dict[AgentRole, str],
        *,
        language: str = "zh-CN",
    ) -> tuple[AgentResponsibility, ...]:
        """返回本轮实际启用的角色和责任范围，不改变任何评分。"""
        return tuple(
            AgentResponsibility(
                role=role,
                role_label=agent_role_label(role, language),
                agent_id=str(agent_id or "").strip(),
                responsibility=agent_role_focus(role, language),
            )
            for role, agent_id in agent_ids.items()
            if str(agent_id or "").strip()
        )

    def get_leaderboard(self) -> list[dict[str, Any]]:
        """获取Agent表现排行榜"""
        metrics_list = list(self._performance_cache.values())

        return [
            {
                "role": m.role.value,
                "role_name": self._get_role_name(m.role),
                "accuracy": m.accuracy,
                "total_predictions": m.total_predictions,
                "weight": self.calculate_adjustment_weight(m.role, m.agent_id),
            }
            for m in metrics_list
            if m.total_predictions > 0
        ]

    def _get_role_name(self, role: AgentRole) -> str:
        """获取角色中文名"""
        return agent_role_label(role, language="zh-CN")

    @staticmethod
    def _agent_cache_key(role: AgentRole, agent_id: str) -> str:
        clean_agent_id = str(agent_id or "").strip()
        if clean_agent_id.startswith(f"{role.value}_"):
            return clean_agent_id
        if clean_agent_id:
            return f"{role.value}_{clean_agent_id}"
        return role.value

    def _load_recent_records(
        self,
        role: AgentRole | None = None,
        *,
        task_id: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if not self.storage_path.exists():
            return ()
        cutoff_date = now_shanghai() - timedelta(days=self.PERFORMANCE_WINDOW_DAYS)
        effective_task_id = self.task_id if task_id is None else _clean_text(task_id)
        records: list[dict[str, Any]] = []
        for line in self.storage_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not self._record_is_usable(data) or not self._record_matches_task(
                data, effective_task_id
            ):
                continue
            if not self._record_is_recent(data, cutoff_date):
                continue
            if (
                role is not None
                and str(data.get("role", "") or "").strip() != role.value
            ):
                continue
            records.append(data)
        return tuple(records)

    @staticmethod
    def _prediction_is_valid(agent_id: str, predicted_stance: str) -> bool:
        return (
            bool(_clean_text(agent_id))
            and _clean_text(predicted_stance) in _VALID_STANCES
        )

    @staticmethod
    def _record_is_usable(record: dict[str, Any]) -> bool:
        role = _clean_text(record.get("role"))
        agent_id = _clean_text(record.get("agent_id"))
        stance = _clean_text(record.get("predicted_stance"))
        return bool(role and agent_id and stance in _VALID_STANCES)

    @staticmethod
    def _record_matches_task(record: dict[str, Any], task_id: str) -> bool:
        record_task_id = _clean_text(record.get("task_id"))
        if task_id:
            return record_task_id == task_id
        # 未指定任务时只读明确没有任务归属的 legacy 记录，禁止跨任务串读。
        return not record_task_id

    @staticmethod
    def _record_is_recent(
        record: dict[str, Any],
        cutoff_date: datetime,
    ) -> bool:
        record_date = str(record.get("created_at", "") or "").strip()
        if not record_date:
            return True
        try:
            record_dt = datetime.fromisoformat(record_date.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return True
        return record_dt >= cutoff_date

    @staticmethod
    def _context_counts(context: Any) -> tuple[int, int]:
        payload = context if isinstance(context, dict) else {}
        return (
            int(payload.get("cross_market_support_event_count", 0) or 0),
            int(payload.get("cross_market_conflict_event_count", 0) or 0),
        )

    @classmethod
    def _context_bucket_from_context(cls, context: Any) -> str:
        support_count, conflict_count = cls._context_counts(context)
        if support_count <= 0 and conflict_count <= 0:
            return "unknown"
        if support_count >= 2 and conflict_count == 0:
            return "strong_supportive"
        if conflict_count > support_count:
            return "conflicts_dominate"
        if conflict_count > 0:
            return "conflicted"
        return "supportive"

    @staticmethod
    def _context_bucket_label(bucket: str) -> str:
        return {
            "strong_supportive": "强证据",
            "supportive": "同向支持",
            "conflicted": "支持但有分歧",
            "conflicts_dominate": "冲突主导",
            "unknown": "证据未知",
        }.get(bucket, bucket or "未知场景")

    def _build_cross_market_governance_note(
        self,
        summaries: tuple[DebateContextPerformanceSummary, ...],
        *,
        current_bucket: str,
        total_sample_count: int,
    ) -> str:
        if total_sample_count < _CROSS_MARKET_CONTEXT_MIN_TOTAL_SAMPLES:
            return (
                f"历史校验: 跨市角色近{self.PERFORMANCE_WINDOW_DAYS}天样本不足"
                f"({total_sample_count}条)，暂按当期证据质量处理。"
            )

        summary_by_bucket = {item.bucket: item for item in summaries}
        current_summary = summary_by_bucket.get(current_bucket)
        parts: list[str] = []
        if (
            current_summary is not None
            and current_summary.sample_count >= _CROSS_MARKET_CONTEXT_MIN_BUCKET_SAMPLES
        ):
            parts.append(
                f"{current_summary.label} {current_summary.correct_count}/"
                f"{current_summary.sample_count} ({current_summary.accuracy:.0%})"
            )
        else:
            current_count = (
                0 if current_summary is None else current_summary.sample_count
            )
            parts.append(
                f"{self._context_bucket_label(current_bucket)}样本不足({current_count}条)"
            )

        strong_summary = summary_by_bucket.get("strong_supportive")
        weak_summary = summary_by_bucket.get("conflicts_dominate")
        if (
            strong_summary is not None
            and strong_summary.sample_count >= _CROSS_MARKET_CONTEXT_MIN_BUCKET_SAMPLES
            and strong_summary.bucket != current_bucket
        ):
            parts.append(
                f"强证据 {strong_summary.correct_count}/{strong_summary.sample_count}"
            )
        if (
            weak_summary is not None
            and weak_summary.sample_count >= _CROSS_MARKET_CONTEXT_MIN_BUCKET_SAMPLES
            and weak_summary.bucket != current_bucket
        ):
            parts.append(
                f"冲突主导 {weak_summary.correct_count}/{weak_summary.sample_count}"
            )
        return "历史校验: " + "；".join(parts)


def _bias_label(value: str) -> str:
    return {
        "bullish": "看多",
        "bearish": "看空",
        "neutral": "中性",
    }.get(value, value or "中性")
