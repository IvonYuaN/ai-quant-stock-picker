"""辩论表现追踪模块 - 追踪Agent预测准确率"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from aqsp.briefing.agent_roles import AgentRole, agent_role_label
from aqsp.briefing.debate import AgentPerformanceMetrics
from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import append_jsonl


class DebatePerformanceTracker:
    """追踪辩论中各Agent的预测表现"""

    PERFORMANCE_WINDOW_DAYS = 21  # 3周窗口

    def __init__(self, storage_path: str = "data/debate_performance.jsonl"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._performance_cache: dict[str, AgentPerformanceMetrics] = {}
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
                key = f"{role.value}_{agent_id[:8]}"

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

            except (json.JSONDecodeError, KeyError):
                continue

    def get_agent_metrics(
        self, role: AgentRole, agent_id: str
    ) -> AgentPerformanceMetrics:
        """获取指定Agent的性能指标"""
        key = f"{role.value}_{agent_id[:8]}"

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
    ) -> None:
        """记录一次预测结果"""
        metrics = self.get_agent_metrics(role, agent_id)
        metrics.total_predictions += 1
        if was_correct:
            metrics.correct_predictions += 1

        if metrics.total_predictions > 5:
            if metrics.correct_predictions / metrics.total_predictions > 0.6:
                metrics.bias_toward = "bullish"
            elif metrics.correct_predictions / metrics.total_predictions < 0.4:
                metrics.bias_toward = "bearish"
            else:
                metrics.bias_toward = "neutral"

        self._persist_record(role, agent_id, predicted_stance, was_correct)

    def _persist_record(
        self,
        role: AgentRole,
        agent_id: str,
        predicted_stance: str,
        was_correct: bool,
    ) -> None:
        """持久化单条记录"""
        record = {
            "agent_id": agent_id,
            "role": role.value,
            "predicted_stance": predicted_stance,
            "was_correct": was_correct,
            "created_at": now_shanghai().isoformat(timespec="seconds"),
        }

        append_jsonl(self.storage_path, record)

    def calculate_adjustment_weight(
        self,
        role: AgentRole,
        agent_id: str,
        regime: str = "unknown",
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

        return max(-0.15, min(0.30, weight))

    def _calculate_time_decay(self, role: AgentRole, agent_id: str) -> float:
        """计算时间衰减因子：新数据权重高，旧数据权重低"""
        key = f"{role.value}_{agent_id[:8]}"
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
    ) -> dict[AgentRole, float]:
        """获取所有Agent的调整权重"""
        return {
            role: self.calculate_adjustment_weight(role, agent_id, regime)
            for role, agent_id in agent_ids.items()
        }

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
