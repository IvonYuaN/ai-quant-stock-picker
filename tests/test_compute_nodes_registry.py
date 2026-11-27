"""计算节点登记表的 fail-closed 静态契约。"""

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = PROJECT_ROOT / "config" / "compute_nodes.yaml"


def _load_registry() -> dict:
    payload = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_compute_nodes_registry_refuses_unregistered_nodes() -> None:
    payload = _load_registry()

    assert payload["policy"]["unregistered_node_action"] == "refuse"
    pending = payload["pending_registration"]["required_fields"]
    assert {"ssh_host_or_ip", "ssh_port", "ssh_user", "resource_limits"} <= set(
        pending
    )


def test_compute_nodes_registry_keeps_production_server_out_of_heavy_workloads() -> None:
    payload = _load_registry()
    nodes = {node["id"]: node for node in payload["nodes"]}

    assert nodes["aqsp-server"]["role"] == "online_service"
    assert nodes["aqsp-server"]["heavy_workloads_allowed"] is False
    assert nodes["aqsp-runner"]["heavy_workloads_allowed"] is True


def test_compute_nodes_registry_has_runner_resource_limits() -> None:
    payload = _load_registry()
    runner = next(node for node in payload["nodes"] if node["id"] == "aqsp-runner")
    limits = runner["resource_limits"]

    assert limits == {
        "cpu_quota_percent": 150,
        "memory_max_gib": 4,
        "memory_swap_max_gib": 0,
        "tasks_max": 256,
        "runtime_max_seconds": 57600,
    }
