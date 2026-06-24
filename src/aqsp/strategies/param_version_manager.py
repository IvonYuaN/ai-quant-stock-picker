from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aqsp.core.time import now_shanghai
from aqsp.utils.jsonl_io import advisory_lock, append_jsonl, atomic_write_text


@dataclass(frozen=True)
class ParamVersion:
    version_id: str
    timestamp: str
    params: Dict[str, Any]
    performance: Dict[str, float]
    regime: str
    description: str


@dataclass(frozen=True)
class VersionManagerConfig:
    max_versions: int = 10
    auto_rollback_on_failure: bool = True
    performance_drop_threshold: float = -0.1
    rollback_cooldown_days: int = 14


class ParamVersionManager:
    def __init__(
        self,
        versions_path: str = "data/param_versions.jsonl",
        config: Optional[VersionManagerConfig] = None,
    ) -> None:
        self.versions_path = Path(versions_path)
        self.versions_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config or VersionManagerConfig()
        self._last_rollback_time: Optional[datetime] = None

    def save_version(
        self,
        params: Dict[str, Any],
        performance: Dict[str, float],
        regime: str,
        description: str,
    ) -> str:
        version_id = str(uuid.uuid4())[:8]
        timestamp = now_shanghai().isoformat(timespec="seconds")

        version = ParamVersion(
            version_id=version_id,
            timestamp=timestamp,
            params=params,
            performance=performance,
            regime=regime,
            description=description,
        )

        entry = {
            "version_id": version.version_id,
            "timestamp": version.timestamp,
            "params": version.params,
            "performance": version.performance,
            "regime": version.regime,
            "description": version.description,
        }

        append_jsonl(self.versions_path, entry)

        self.cleanup_old_versions(keep_count=self.config.max_versions)

        return version_id

    def load_version(self, version_id: str) -> Optional[ParamVersion]:
        versions = self._load_all_versions()
        for version in versions:
            if version.version_id == version_id:
                return version
        return None

    def get_latest_version(self) -> Optional[ParamVersion]:
        versions = self._load_all_versions()
        if not versions:
            return None
        return versions[-1]

    def get_best_version(
        self,
        metric: str = "sharpe_ratio",
    ) -> Optional[ParamVersion]:
        versions = self._load_all_versions()
        if not versions:
            return None

        best_version = None
        best_value = -float("inf")

        for version in versions:
            value = version.performance.get(metric, 0.0)
            if value > best_value:
                best_value = value
                best_version = version

        return best_version

    def rollback_to_version(self, version_id: str) -> bool:
        if self._last_rollback_time is not None:
            elapsed = now_shanghai() - self._last_rollback_time
            if elapsed.days < self.config.rollback_cooldown_days:
                return False

        version = self.load_version(version_id)
        if version is None:
            return False

        self._last_rollback_time = now_shanghai()
        return True

    def list_versions(self, limit: int = 10) -> List[ParamVersion]:
        versions = self._load_all_versions()
        return versions[-limit:]

    def cleanup_old_versions(self, keep_count: int = 10) -> int:
        with advisory_lock(self.versions_path):
            versions = self._load_all_versions()
            if len(versions) <= keep_count:
                return 0

            versions_to_remove = versions[:-keep_count]
            removed_count = len(versions_to_remove)

            remaining_versions = versions[-keep_count:]
            text = "".join(
                json.dumps(
                    {
                        "version_id": version.version_id,
                        "timestamp": version.timestamp,
                        "params": version.params,
                        "performance": version.performance,
                        "regime": version.regime,
                        "description": version.description,
                    },
                    ensure_ascii=False,
                )
                + "\n"
                for version in remaining_versions
            )
            atomic_write_text(self.versions_path, text)

        return removed_count

    def _load_all_versions(self) -> List[ParamVersion]:
        if not self.versions_path.exists():
            return []

        versions = []
        with open(self.versions_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    version = ParamVersion(
                        version_id=data["version_id"],
                        timestamp=data["timestamp"],
                        params=data["params"],
                        performance=data["performance"],
                        regime=data["regime"],
                        description=data["description"],
                    )
                    versions.append(version)
                except (json.JSONDecodeError, KeyError):
                    continue

        return versions

    def get_version_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        versions = self.list_versions(limit)
        history = []
        for version in versions:
            history.append(
                {
                    "version_id": version.version_id,
                    "timestamp": version.timestamp,
                    "regime": version.regime,
                    "description": version.description,
                    "performance_summary": {
                        "sharpe_ratio": version.performance.get("sharpe_ratio", 0.0),
                        "win_rate": version.performance.get("win_rate", 0.0),
                        "avg_return": version.performance.get("avg_return", 0.0),
                    },
                }
            )
        return history

    def compare_versions(
        self,
        version_id_a: str,
        version_id_b: str,
    ) -> Dict[str, Any]:
        version_a = self.load_version(version_id_a)
        version_b = self.load_version(version_id_b)

        if version_a is None or version_b is None:
            return {"error": "版本不存在"}

        comparison = {
            "version_a": version_id_a,
            "version_b": version_id_b,
            "performance_diff": {},
            "param_diff": {},
        }

        for metric in ["sharpe_ratio", "win_rate", "avg_return", "max_drawdown"]:
            value_a = version_a.performance.get(metric, 0.0)
            value_b = version_b.performance.get(metric, 0.0)
            comparison["performance_diff"][metric] = {
                "version_a": value_a,
                "version_b": value_b,
                "diff": value_b - value_a,
            }

        all_params = set(version_a.params.keys()) | set(version_b.params.keys())
        for param in all_params:
            value_a = version_a.params.get(param)
            value_b = version_b.params.get(param)
            if value_a != value_b:
                comparison["param_diff"][param] = {
                    "version_a": value_a,
                    "version_b": value_b,
                }

        return comparison

    def should_rollback(
        self,
        current_performance: Dict[str, float],
        metric: str = "sharpe_ratio",
    ) -> bool:
        if not self.config.auto_rollback_on_failure:
            return False

        if self._last_rollback_time is not None:
            elapsed = now_shanghai() - self._last_rollback_time
            if elapsed.days < self.config.rollback_cooldown_days:
                return False

        best_version = self.get_best_version(metric)
        if best_version is None:
            return False

        current_value = current_performance.get(metric, 0.0)
        best_value = best_version.performance.get(metric, 0.0)

        if best_value == 0:
            return False

        drop = (current_value - best_value) / abs(best_value)
        return drop < self.config.performance_drop_threshold

    def get_rollback_target(
        self,
        current_performance: Dict[str, float],
        metric: str = "sharpe_ratio",
    ) -> Optional[ParamVersion]:
        if not self.should_rollback(current_performance, metric):
            return None

        return self.get_best_version(metric)

    def get_version_stats(self) -> Dict[str, Any]:
        versions = self._load_all_versions()
        if not versions:
            return {
                "total_versions": 0,
                "latest_timestamp": None,
                "best_sharpe": None,
                "avg_win_rate": 0.0,
            }

        sharpe_values = [v.performance.get("sharpe_ratio", 0.0) for v in versions]
        win_rates = [v.performance.get("win_rate", 0.0) for v in versions]

        return {
            "total_versions": len(versions),
            "latest_timestamp": versions[-1].timestamp,
            "best_sharpe": max(sharpe_values) if sharpe_values else None,
            "avg_win_rate": sum(win_rates) / len(win_rates) if win_rates else 0.0,
            "regimes_covered": list(set(v.regime for v in versions)),
        }

    def export_versions(self, output_path: str) -> None:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        versions = self._load_all_versions()
        export_data = []

        for version in versions:
            export_data.append(
                {
                    "version_id": version.version_id,
                    "timestamp": version.timestamp,
                    "params": version.params,
                    "performance": version.performance,
                    "regime": version.regime,
                    "description": version.description,
                }
            )

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

    def import_versions(self, input_path: str) -> int:
        input_file = Path(input_path)
        if not input_file.exists():
            return 0

        with open(input_file, "r", encoding="utf-8") as f:
            import_data = json.load(f)

        imported_count = 0
        existing_versions = {v.version_id for v in self._load_all_versions()}

        for data in import_data:
            if data["version_id"] in existing_versions:
                continue

            entry = {
                "version_id": data["version_id"],
                "timestamp": data["timestamp"],
                "params": data["params"],
                "performance": data["performance"],
                "regime": data["regime"],
                "description": data["description"],
            }

            append_jsonl(self.versions_path, entry)

            imported_count += 1

        return imported_count
