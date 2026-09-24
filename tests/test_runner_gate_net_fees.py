"""runner_gate.sh 的 --net-fees 转发契约（#199）。

成本口径对照必须**走 runner_gate.sh**（才有共享业务机负载守卫），
所以父脚本那个 --net-fees 开关必须由 runner_gate.sh 转发下去。

锁定两点：
- 默认 NET_FEES=0（生产门禁仍是 legacy，不受影响）；
- NET_FEES=1 时才把 --net-fees 追加进 gate 命令。
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "runner_gate.sh"


def test_runner_gate_forwards_net_fees_only_when_enabled() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    # 默认关闭：生产门禁口径不变
    assert 'NET_FEES="${NET_FEES:-0}"' in script
    # 开关打开时才构造 --net-fees
    assert 'NET_FEES_ARGS=(--net-fees)' in script
    # 该数组确实被拼进 gate 命令（与 WINDOW_ARGS 同款 set -u 安全展开）
    assert '${NET_FEES_ARGS[@]+"${NET_FEES_ARGS[@]}"}' in script
    # 注释里写明是研究用途、不要在生产门禁开
    assert "研究对照" in script


def test_runner_gate_still_keeps_shared_host_load_guard() -> None:
    """转发不能把 #148 的让位守卫挤掉 —— 重跑必须在守卫保护下跑。"""
    script = SCRIPT.read_text(encoding="utf-8")
    assert "MAX_LOAD1" in script
    assert "LOAD_GUARD" in script
