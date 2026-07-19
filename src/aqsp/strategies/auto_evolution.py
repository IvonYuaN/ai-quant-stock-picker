from __future__ import annotations

import json
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

from aqsp.core.time import now_shanghai
from aqsp.regime.hmm_detector import HMMRegimeDetector
from aqsp.regime.strategy_mixer import canonical_regime_from_hmm
from aqsp.strategies.thresholds import load_thresholds
from aqsp.utils.jsonl_io import append_jsonl
from aqsp.walkforward_gate import build_walkforward_gate_evidence


_LOGGER = logging.getLogger(__name__)


PROPOSAL_STATUS = "proposal_only"
BLOCKED_PROPOSAL_STATUS = "blocked_proposal"
PROPOSAL_VALIDATION_REQUIREMENTS = (
    "重新运行 Purged + Embargoed Walk-Forward，使用不复权价格和 point-in-time 数据",
    "DSR > 1.0",
    "0 < PBO < 0.5，且 PBO 来自多变体 CSCV 证据",
    "通过独立 held-out 验证和人工审核后，才可单独提交 thresholds.yaml",
)


@dataclass(frozen=True)
class ParameterEvolution:
    param_name: str
    current_value: float
    optimal_value: float
    confidence: float
    last_updated: str
    performance_history: List[Dict[str, Any]]


@dataclass(frozen=True)
class EvolutionConfig:
    enabled: bool = True
    check_interval_days: int = 7
    min_samples: int = 30
    max_evolution_per_cycle: int = 3
    confidence_threshold: float = 0.7
    performance_threshold: float = 0.05
    param_spaces: Dict[str, Dict[str, List[float]]] = field(default_factory=dict)
    regime_adaptation: Dict[str, Any] = field(default_factory=dict)
    rollback: Dict[str, Any] = field(default_factory=dict)
    monitoring: Dict[str, Any] = field(default_factory=dict)
    optimization: Dict[str, Any] = field(default_factory=dict)
    walk_forward: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketRegimeAnalysis:
    regime: str
    confidence: float
    volatility: float
    trend_strength: float
    momentum: float
    timestamp: datetime


@dataclass(frozen=True)
class EvolutionResult:
    strategy_name: str
    old_params: Dict[str, float]
    new_params: Dict[str, float]
    # Kept for API compatibility; this is a research-score delta, not forward return.
    performance_improvement: float
    confidence: float
    timestamp: datetime
    reason: str
    sample_count: int = 0
    cooldown_days: int = 0
    eligible_after: str | None = None
    validation_requirements: tuple[str, ...] = PROPOSAL_VALIDATION_REQUIREMENTS
    gate_evidence: Dict[str, Any] = field(default_factory=dict)
    thresholds_version: str | None = None
    status: str = PROPOSAL_STATUS
    applied: bool = False
    runtime_writeback: bool = False
    forward_performance_validated: bool = False


class AutoEvolution:
    def __init__(
        self,
        config_path: str = "config/evolution_config.yaml",
        thresholds_path: str = "config/thresholds.yaml",
        data_dir: str = "data/evolution",
        walkforward_gate_path: str = "data/walkforward_gate.json",
    ) -> None:
        self.config_path = Path(config_path)
        self.thresholds_path = Path(thresholds_path)
        self.data_dir = Path(data_dir)
        self.walkforward_gate_path = Path(walkforward_gate_path)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.config = self._load_config()
        self.thresholds = load_thresholds(str(self.thresholds_path))
        self.evolution_history: List[EvolutionResult] = []
        self._last_evolution_time: Optional[datetime] = self._load_last_evolution_time()

    def _load_config(self) -> EvolutionConfig:
        if not self.config_path.exists():
            return EvolutionConfig()

        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        evolution_data = data.get("evolution", {})
        return EvolutionConfig(
            enabled=evolution_data.get("enabled", True),
            check_interval_days=evolution_data.get("check_interval_days", 7),
            min_samples=evolution_data.get("min_samples", 30),
            max_evolution_per_cycle=evolution_data.get("max_evolution_per_cycle", 3),
            confidence_threshold=evolution_data.get("confidence_threshold", 0.7),
            performance_threshold=evolution_data.get("performance_threshold", 0.05),
            param_spaces=evolution_data.get("param_spaces", {}),
            regime_adaptation=evolution_data.get("regime_adaptation", {}),
            rollback=evolution_data.get("rollback", {}),
            monitoring=evolution_data.get("monitoring", {}),
            optimization=evolution_data.get("optimization", {}),
            walk_forward=evolution_data.get("walk_forward", {}),
        )

    def analyze_market_regime(
        self,
        index_data: pd.DataFrame,
        lookback_days: int = 60,
    ) -> MarketRegimeAnalysis:
        if index_data is None or index_data.empty:
            return MarketRegimeAnalysis(
                regime="unknown",
                confidence=0.0,
                volatility=0.0,
                trend_strength=0.0,
                momentum=0.0,
                timestamp=now_shanghai(),
            )

        df = index_data.sort_values("date").tail(lookback_days)
        if len(df) < 20:
            return MarketRegimeAnalysis(
                regime="unknown",
                confidence=0.0,
                volatility=0.0,
                trend_strength=0.0,
                momentum=0.0,
                timestamp=now_shanghai(),
            )

        prices = df["close"].values
        returns = np.diff(prices) / prices[:-1]

        volatility = float(np.std(returns) * np.sqrt(252))
        momentum = float((prices[-1] - prices[0]) / prices[0])

        df_copy = df.copy()
        df_copy["ma20"] = df_copy["close"].rolling(20).mean()
        df_copy["ma60"] = df_copy["close"].rolling(60).mean()
        ma20_last = df_copy["ma20"].iloc[-1]
        ma60_last = df_copy["ma60"].iloc[-1]
        trend_strength = 0.0
        if pd.notna(ma20_last) and pd.notna(ma60_last) and ma60_last != 0:
            trend_strength = float((ma20_last - ma60_last) / ma60_last)

        hmm_result = HMMRegimeDetector(
            lookback_days=lookback_days,
            min_data_points=max(20, self.thresholds.regime.min_sample_size),
        ).detect_regime(df)
        regime = canonical_regime_from_hmm(
            str(hmm_result.regime),
            annualized_volatility=volatility,
            volatility_high=float(self.thresholds.regime.volatility_high),
        )
        confidence = float(hmm_result.confidence)

        return MarketRegimeAnalysis(
            regime=regime,
            confidence=confidence,
            volatility=volatility,
            trend_strength=trend_strength,
            momentum=momentum,
            timestamp=now_shanghai(),
        )

    def _classify_regime(
        self,
        volatility: float,
        trend_strength: float,
        momentum: float,
    ) -> str:
        if volatility > 0.3:
            if momentum > 0.1:
                return "volatile_bull"
            elif momentum < -0.1:
                return "volatile_bear"
            else:
                return "volatile_sideways"
        else:
            if trend_strength > 0.02:
                return "stable_bull"
            elif trend_strength < -0.02:
                return "stable_bear"
            else:
                return "stable_sideways"

    def _calculate_confidence(
        self,
        volatility: float,
        trend_strength: float,
        momentum: float,
    ) -> float:
        conf = 0.5

        if volatility > 0.2:
            conf += 0.2
        if abs(trend_strength) > 0.01:
            conf += 0.15
        if abs(momentum) > 0.05:
            conf += 0.15

        return min(conf, 1.0)

    def get_optimal_params(
        self,
        regime: str,
        strategy_name: str,
    ) -> Dict[str, float]:
        regime_params = self.config.regime_adaptation.get("regime_params", {})
        regime_config = regime_params.get(regime, {})

        base_params = self._get_base_params(strategy_name)

        adapted_params = {}
        for param_name, base_value in base_params.items():
            boost_key = f"{param_name}_boost"
            adjust_key = f"{param_name}_adjust"

            if boost_key in regime_config:
                adapted_params[param_name] = base_value * regime_config[boost_key]
            elif adjust_key in regime_config:
                adapted_params[param_name] = base_value + regime_config[adjust_key]
            else:
                adapted_params[param_name] = base_value

        return adapted_params

    def _get_base_params(self, strategy_name: str) -> Dict[str, float]:
        param_spaces = self.config.param_spaces.get(strategy_name, {})
        base_params: Dict[str, float] = {}
        section_name = self._threshold_section(strategy_name)
        section = getattr(self.thresholds, section_name, None)

        for param_name, bounds in param_spaces.items():
            if len(bounds) == 2:
                configured = getattr(section, param_name, None)
                if isinstance(configured, (int, float)) and not isinstance(
                    configured, bool
                ):
                    base_params[param_name] = float(configured)
                else:
                    # Legacy parameter spaces may contain fields that are not
                    # in the frozen threshold dataclass. Keep their midpoint
                    # fallback, but never let them mutate runtime thresholds.
                    base_params[param_name] = (bounds[0] + bounds[1]) / 2

        return base_params

    def update_params_from_performance(
        self,
        performance: Dict[str, float],
        *,
        sample_count: int | None = None,
    ) -> bool:
        if not self.config.enabled:
            return False

        if not self._has_minimum_samples(sample_count):
            return False

        return self._cooldown_days_remaining() == 0

    def should_evolve(
        self,
        current_performance: Dict[str, float],
        threshold: float | None = None,
        sample_count: int | None = None,
    ) -> bool:
        if not self.config.enabled:
            return False

        observed_samples = sample_count
        if observed_samples is None:
            raw_sample_count = current_performance.get("sample_count")
            if isinstance(raw_sample_count, int) and not isinstance(
                raw_sample_count, bool
            ):
                observed_samples = raw_sample_count
        if observed_samples is None or observed_samples < self.config.min_samples:
            return False
        if self._cooldown_days_remaining() > 0:
            return False

        recent_performance = self._get_recent_performance()
        if recent_performance is None:
            return True

        improvement = current_performance.get(
            "sharpe_ratio", 0
        ) - recent_performance.get("sharpe_ratio", 0)
        effective_threshold = (
            self.config.performance_threshold if threshold is None else threshold
        )
        return improvement < -effective_threshold

    def _get_recent_performance(self) -> Optional[Dict[str, float]]:
        history_file = self.data_dir / "performance_history.jsonl"
        if not history_file.exists():
            return None

        with open(history_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if not lines:
            return None

        latest = json.loads(lines[-1])
        return latest.get("performance", {})

    def evolve_parameters(
        self,
        strategy_name: str,
        data: Dict[str, pd.DataFrame],
        *,
        sample_count: int | None = None,
        walkforward_payload: Mapping[str, object] | None = None,
    ) -> EvolutionResult | None:
        if not self.config.enabled:
            return None

        effective_sample_count = self._resolve_sample_count(data, sample_count)
        if not self._has_minimum_samples(effective_sample_count):
            return None

        if self._cooldown_days_remaining() > 0:
            return None

        current_params = self._get_base_params(strategy_name)
        try:
            current_performance = self._evaluate_params(
                current_params, data, strategy_name
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("自动优化基线评估失败，按无提案继续: %s", exc)
            return None

        candidates = self._generate_candidates(strategy_name, current_params)
        best_params = current_params
        best_score = current_performance.get("research_score", 0)

        for candidate in candidates:
            try:
                score = self._evaluate_params(candidate, data, strategy_name).get(
                    "research_score", 0
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("自动优化候选评估失败，跳过候选: %s", exc)
                continue
            if score > best_score:
                best_score = score
                best_params = candidate

        improvement = best_score - current_performance.get("research_score", 0)
        if improvement > self.config.performance_threshold:
            result = self._record_evolution(
                strategy_name,
                current_params,
                best_params,
                improvement,
                "research_score_improvement",
                sample_count=effective_sample_count,
                walkforward_payload=walkforward_payload,
            )
            self._last_evolution_time = result.timestamp
            self._write_proposal(result)
            return result

        return None

    def _generate_candidates(
        self,
        strategy_name: str,
        current_params: Dict[str, float],
    ) -> List[Dict[str, float]]:
        param_spaces = self.config.param_spaces.get(strategy_name, {})
        candidates = []

        for param_name, bounds in param_spaces.items():
            if len(bounds) != 2:
                continue

            current_value = current_params.get(param_name, (bounds[0] + bounds[1]) / 2)

            candidate = current_params.copy()
            candidate[param_name] = max(bounds[0], min(bounds[1], current_value * 0.9))
            candidates.append(candidate)

            candidate = current_params.copy()
            candidate[param_name] = max(bounds[0], min(bounds[1], current_value * 1.1))
            candidates.append(candidate)

        # The setting limits parameter trials per evolution cycle. It is a
        # resource and overfitting control, not a runtime writeback switch.
        limit = max(0, int(self.config.max_evolution_per_cycle))
        return candidates[:limit] if limit else []

    def _evaluate_params(
        self,
        params: Dict[str, float],
        data: Dict[str, pd.DataFrame],
        strategy_name: str,
    ) -> Dict[str, float]:
        from aqsp.strategies.composite import CompositeStrategy

        # This is a proposal-only score from current deterministic rules. It is
        # intentionally not labelled Sharpe because it does not validate forward
        # returns or apply candidate params through a walk-forward engine.
        research_thresholds = self.thresholds.with_overrides(
            self._threshold_section(strategy_name), params
        )
        strategy = CompositeStrategy(thresholds=research_thresholds)
        scores = strategy.calculate_score(data)

        if not scores:
            return {"research_score": 0.0, "score_hit_rate": 0.0}

        score_values = list(scores.values())
        return {
            "research_score": float(np.mean(score_values)),
            "score_hit_rate": float(
                np.mean([1 if s > 0.5 else 0 for s in score_values])
            ),
        }

    def _record_evolution(
        self,
        strategy_name: str,
        old_params: Dict[str, float],
        new_params: Dict[str, float],
        improvement: float,
        reason: str,
        *,
        sample_count: int = 0,
        walkforward_payload: Mapping[str, object] | None = None,
    ) -> EvolutionResult:
        timestamp = now_shanghai()
        gate_evidence = self._build_gate_evidence(walkforward_payload)
        cooldown_days = self.config.check_interval_days
        result = EvolutionResult(
            strategy_name=strategy_name,
            old_params=old_params,
            new_params=new_params,
            performance_improvement=improvement,
            confidence=0.8,
            timestamp=timestamp,
            reason=reason,
            sample_count=sample_count,
            cooldown_days=cooldown_days,
            eligible_after=(timestamp + timedelta(days=cooldown_days)).isoformat(
                timespec="seconds"
            ),
            gate_evidence=gate_evidence,
            thresholds_version=getattr(self.thresholds, "version", None),
            status=self._proposal_status(gate_evidence),
            forward_performance_validated=False,
        )

        self.evolution_history.append(result)

        history_file = self.data_dir / "evolution_history.jsonl"
        entry = {
            "timestamp": result.timestamp.isoformat(timespec="seconds"),
            "strategy_name": result.strategy_name,
            "old_params": result.old_params,
            "new_params": result.new_params,
            "research_score_improvement": result.performance_improvement,
            "confidence": result.confidence,
            "reason": result.reason,
            "sample_count": result.sample_count,
            "cooldown_days": result.cooldown_days,
            "eligible_after": result.eligible_after,
            "thresholds_version": result.thresholds_version,
            "status": result.status,
            "applied": result.applied,
            "runtime_writeback": result.runtime_writeback,
            "forward_performance_validated": result.forward_performance_validated,
            "gate_evidence": result.gate_evidence,
        }

        append_jsonl(history_file, entry)
        return result

    def _apply_evolution(self, result: EvolutionResult) -> None:
        """Compatibility shim: serialize a proposal, never apply runtime state."""
        # Keep the legacy entry point behind the same gate as the main path.
        self._write_proposal(result)

    def _write_proposal(
        self,
        result: EvolutionResult,
    ) -> None:
        if not result.new_params:
            return
        if result.sample_count < self.config.min_samples:
            _LOGGER.warning(
                "拒绝写入自动优化提案：独立信号日不足 (%s/%s)",
                result.sample_count,
                self.config.min_samples,
            )
            return

        proposal_file = self.data_dir / "threshold_proposals.jsonl"
        gate_evidence = dict(result.gate_evidence)
        gate_status = str(gate_evidence.get("status", "missing"))
        proposal_status = self._proposal_status(gate_evidence)
        gate_reasons = gate_evidence.get("reasons")
        if not isinstance(gate_reasons, list) or not gate_reasons:
            gate_reasons = ["walkforward gate evidence missing"]
        cooldown_days = result.cooldown_days or self.config.check_interval_days
        eligible_after = result.eligible_after or (
            result.timestamp + timedelta(days=cooldown_days)
        ).isoformat(timespec="seconds")
        proposal_id = self._proposal_id(result)
        if self._proposal_exists(proposal_file, proposal_id):
            return
        append_jsonl(
            proposal_file,
            {
                "proposal_id": proposal_id,
                "timestamp": now_shanghai().isoformat(timespec="seconds"),
                "strategy_name": result.strategy_name,
                "old_params": result.old_params,
                "new_params": result.new_params,
                "confidence": result.confidence,
                "research_score_improvement": result.performance_improvement,
                "performance_metric": "research_score",
                "forward_performance_validated": False,
                "reason": result.reason,
                "sample_count": result.sample_count,
                "min_samples": self.config.min_samples,
                "sample_unit": "independent_signal_days",
                "cooldown_days": cooldown_days,
                "eligible_after": eligible_after,
                "validation_requirements": list(result.validation_requirements),
                "gate_status": gate_status,
                "gate_reasons": gate_reasons,
                "gate_evidence": gate_evidence,
                "thresholds_version": result.thresholds_version
                or getattr(self.thresholds, "version", None),
                "status": proposal_status,
                "applied": False,
                "proposal_only": True,
                "runtime_writeback": False,
                "thresholds_path": str(self.thresholds_path),
            },
        )

    @staticmethod
    def _proposal_status(gate_evidence: Mapping[str, object]) -> str:
        """Require a passing gate before a proposal can leave blocked state."""
        return (
            PROPOSAL_STATUS
            if gate_evidence.get("status") == "pass"
            else BLOCKED_PROPOSAL_STATUS
        )

    def _build_gate_evidence(
        self, payload: Mapping[str, object] | None
    ) -> Dict[str, Any]:
        if payload is None:
            payload = self._load_walkforward_payload()
        if payload is None:
            return {
                "status": "missing",
                "reasons": ["walkforward gate evidence missing"],
            }

        evidence = build_walkforward_gate_evidence(
            payload,
            today=now_shanghai().date(),
            expected_thresholds_version=getattr(self.thresholds, "version", None),
            require_assumption_audit=True,
        )
        return {
            "ok": evidence.ok,
            "status": evidence.status,
            "dsr": evidence.dsr,
            "pbo": evidence.pbo,
            "n_periods": evidence.n_periods,
            "run_date": evidence.run_date.isoformat()
            if evidence.run_date is not None
            else None,
            "data_end": evidence.data_end.isoformat()
            if evidence.data_end is not None
            else None,
            "thresholds_version": evidence.thresholds_version,
            "reasons": list(evidence.reasons),
        }

    def _load_walkforward_payload(self) -> Mapping[str, object] | None:
        gate_path = self.walkforward_gate_path
        if not gate_path.exists():
            return None
        try:
            payload = json.loads(gate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, Mapping) else None

    def _load_last_evolution_time(self) -> datetime | None:
        history_file = self.data_dir / "evolution_history.jsonl"
        if not history_file.exists():
            return None
        try:
            lines = history_file.read_text(encoding="utf-8").splitlines()
            if not lines:
                return None
            payload = json.loads(lines[-1])
            timestamp = payload.get("timestamp")
            if not timestamp:
                return None
            parsed = datetime.fromisoformat(timestamp)
            return parsed if parsed.tzinfo is not None else None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _count_data_samples(data: Mapping[str, object]) -> int:
        """Count independent executable signal dates across all symbols."""
        signal_days: set[str] = set()
        for frame in data.values():
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                continue
            valid = frame
            if "status" in valid.columns:
                status = valid["status"].astype(str).str.strip().str.lower()
                valid = valid[status != "not_executable"]
            if "executable" in valid.columns:
                executable = valid["executable"]
                executable = executable.map(
                    lambda value: (
                        str(value).strip().lower()
                        not in {"false", "0", "no", "not_executable"}
                    )
                )
                valid = valid[executable]
            if valid.empty:
                continue

            if "date" in valid.columns:
                dates = pd.to_datetime(valid["date"], errors="coerce")
                if "signal_day_group" in valid.columns:
                    groups = valid["signal_day_group"].dropna().astype(str).str.strip()
                    groups = groups[groups != ""]
                    if not groups.empty:
                        signal_days.update(groups.tolist())
                        continue
            elif isinstance(valid.index, pd.DatetimeIndex):
                dates = pd.Series(valid.index, index=valid.index)
            else:
                continue
            signal_days.update(dates.dropna().dt.strftime("%Y-%m-%d").tolist())
        return len(signal_days)

    @classmethod
    def _resolve_sample_count(
        cls,
        data: Mapping[str, object],
        sample_count: int | None,
    ) -> int:
        if sample_count is None:
            return cls._count_data_samples(data)
        if isinstance(sample_count, bool) or not isinstance(sample_count, int):
            return 0
        return max(0, sample_count)

    def _has_minimum_samples(self, sample_count: int | None) -> bool:
        return (
            sample_count is not None
            and not isinstance(sample_count, bool)
            and isinstance(sample_count, int)
            and sample_count >= self.config.min_samples
        )

    def _cooldown_days_remaining(self) -> int:
        if self._last_evolution_time is None:
            return 0
        elapsed = now_shanghai() - self._last_evolution_time
        remaining = timedelta(days=self.config.check_interval_days) - elapsed
        return max(0, int(np.ceil(remaining.total_seconds() / 86400)))

    @staticmethod
    def _threshold_section(strategy_name: str) -> str:
        return {
            "composite": "composite",
            "momentum": "momentum",
            "volume": "volume",
            "scoring": "scoring",
        }.get(strategy_name, strategy_name)

    @staticmethod
    def _proposal_id(result: EvolutionResult) -> str:
        payload = {
            "strategy_name": result.strategy_name,
            "old_params": result.old_params,
            "new_params": result.new_params,
            "timestamp": result.timestamp.isoformat(timespec="seconds"),
            "thresholds_version": result.thresholds_version,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]

    @staticmethod
    def _proposal_exists(proposal_file: Path, proposal_id: str) -> bool:
        if not proposal_file.exists():
            return False
        try:
            for line in proposal_file.read_text(encoding="utf-8").splitlines():
                payload = json.loads(line)
                if (
                    isinstance(payload, dict)
                    and payload.get("proposal_id") == proposal_id
                ):
                    return True
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return False

    def save_evolution_history(self, output_path: str) -> None:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        history_data = []
        for result in self.evolution_history:
            history_data.append(
                {
                    "timestamp": result.timestamp.isoformat(timespec="seconds"),
                    "strategy_name": result.strategy_name,
                    "old_params": result.old_params,
                    "new_params": result.new_params,
                    "research_score_improvement": result.performance_improvement,
                    "confidence": result.confidence,
                    "reason": result.reason,
                    "sample_count": result.sample_count,
                    "cooldown_days": result.cooldown_days,
                    "eligible_after": result.eligible_after,
                    "validation_requirements": list(result.validation_requirements),
                    "gate_evidence": result.gate_evidence,
                    "thresholds_version": result.thresholds_version,
                    "status": result.status,
                    "applied": result.applied,
                    "runtime_writeback": result.runtime_writeback,
                    "forward_performance_validated": result.forward_performance_validated,
                }
            )

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)


class ParameterOptimizer:
    def __init__(
        self,
        param_space: Dict[str, Tuple[float, float]],
    ) -> None:
        self.param_space = param_space

    def grid_search(
        self,
        evaluate_fn: Callable[[Dict[str, float]], float],
        n_points: int = 10,
    ) -> Dict[str, float]:
        import itertools

        param_grids = []
        for param_name, (low, high) in self.param_space.items():
            values = np.linspace(low, high, n_points)
            param_grids.append([(param_name, v) for v in values])

        best_params = {}
        best_score = -float("inf")

        for combo in itertools.product(*param_grids):
            params = {name: value for name, value in combo}
            try:
                score = evaluate_fn(params)
            except Exception:
                score = -float("inf")

            if score > best_score:
                best_score = score
                best_params = params.copy()

        return best_params

    def random_search(
        self,
        evaluate_fn: Callable[[Dict[str, float]], float],
        n_trials: int = 50,
    ) -> Dict[str, float]:
        rng = np.random.RandomState(42)

        best_params = {}
        best_score = -float("inf")

        for _ in range(n_trials):
            params = {}
            for param_name, (low, high) in self.param_space.items():
                params[param_name] = rng.uniform(low, high)

            try:
                score = evaluate_fn(params)
            except Exception:
                score = -float("inf")

            if score > best_score:
                best_score = score
                best_params = params.copy()

        return best_params

    def bayesian_optimization(
        self,
        evaluate_fn: Callable[[Dict[str, float]], float],
        n_trials: int = 30,
    ) -> Dict[str, float]:
        from aqsp.optimizer.param_optimizer import BayesianOptimizer, ParamSpace

        param_spaces = []
        for param_name, (low, high) in self.param_space.items():
            param_spaces.append(
                ParamSpace(
                    name=param_name,
                    low=low,
                    high=high,
                    step=(high - low) / 100,
                )
            )

        optimizer = BayesianOptimizer(param_spaces)
        result = optimizer.optimize(evaluate_fn, n_trials=n_trials)

        return result.best_params

    def evaluate_parameters(
        self,
        params: Dict[str, float],
        data: Dict[str, pd.DataFrame],
        forward_days: int = 5,
    ) -> float:
        from aqsp.strategies.composite import CompositeStrategy
        from aqsp.strategies.thresholds import load_thresholds

        base_thresholds = load_thresholds()

        strategy = CompositeStrategy(thresholds=base_thresholds)
        scores = strategy.calculate_score(data)

        if not scores:
            return 0.0

        score_values = list(scores.values())
        return float(np.mean(score_values))


class MarketRegimeAdapter:
    def __init__(self) -> None:
        self.regime_params: Dict[str, Dict[str, float]] = {}

    def load_regime_params(self, config_path: str) -> None:
        config_file = Path(config_path)
        if not config_file.exists():
            return

        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        evolution_data = data.get("evolution", {})
        regime_adaptation = evolution_data.get("regime_adaptation", {})
        self.regime_params = regime_adaptation.get("regime_params", {})

    def adapt_to_regime(
        self,
        base_params: Dict[str, float],
        regime: str,
        confidence: float,
    ) -> Dict[str, float]:
        if not self.regime_params:
            return base_params

        regime_config = self.regime_params.get(regime, {})
        if not regime_config:
            return base_params

        adapted_params = {}
        for param_name, base_value in base_params.items():
            boost_key = f"{param_name}_boost"
            adjust_key = f"{param_name}_adjust"

            if boost_key in regime_config:
                adapted_value = base_value * regime_config[boost_key]
            elif adjust_key in regime_config:
                adapted_value = base_value + regime_config[adjust_key]
            else:
                adapted_value = base_value

            adapted_params[param_name] = adapted_value

        return adapted_params

    def blend_params(
        self,
        params_a: Dict[str, float],
        params_b: Dict[str, float],
        weight: float,
    ) -> Dict[str, float]:
        weight = max(0.0, min(1.0, weight))

        blended = {}
        all_keys = set(params_a.keys()) | set(params_b.keys())

        for key in all_keys:
            value_a = params_a.get(key, 0.0)
            value_b = params_b.get(key, 0.0)
            blended[key] = value_a * (1 - weight) + value_b * weight

        return blended
