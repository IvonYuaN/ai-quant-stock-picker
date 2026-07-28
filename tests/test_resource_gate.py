from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "resource_gate.py"
spec = importlib.util.spec_from_file_location("resource_gate", SCRIPT)
assert spec and spec.loader
resource_gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = resource_gate
spec.loader.exec_module(resource_gate)


def test_resource_gate_accepts_idle_host_when_resources_meet_limits() -> None:
    decision = resource_gate.evaluate_resources(
        resource_gate.HostResources(2, 0.8, 1024),
        min_free_memory_mb=768,
        max_load_per_cpu=0.7,
        blocked_locks=(),
    )

    assert decision.accepted is True
    assert decision.detail == "capacity available"


def test_resource_gate_rejects_busy_host_before_optional_heavy_task() -> None:
    decision = resource_gate.evaluate_resources(
        resource_gate.HostResources(2, 1.6, 1024),
        min_free_memory_mb=768,
        max_load_per_cpu=0.7,
        blocked_locks=(),
    )

    assert decision.accepted is False
    assert "load/core" in decision.detail


def test_resource_gate_rejects_active_runtime_lock_before_optional_heavy_task(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "server-runtime.lock"
    lock.mkdir()

    decision = resource_gate.evaluate_resources(
        resource_gate.HostResources(2, 0.1, 4096),
        min_free_memory_mb=768,
        max_load_per_cpu=0.7,
        blocked_locks=(lock,),
    )

    assert decision.accepted is False
    assert str(lock) in decision.detail
