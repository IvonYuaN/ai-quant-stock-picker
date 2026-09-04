"""Tests for PR #77 monitor config rendering helper.

`scripts/monitor_render.sh` 暴露 `render_monitor_config src dest`：把 yaml
字面量里的 ${AQSP_RUNTIME_DATA_ROOT} 等占位符替换成当前 shell env 的值。
该函数是 server_monitor.sh 在跑 `aqsp monitor --config ...` 之前的关键预处理，
让字面量 cache_path 解析到真实 runtime data 目录（PR #77 修复根因）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RENDER_HELPER = PROJECT_ROOT / "scripts" / "monitor_render.sh"


def _have_renderer() -> bool:
    return shutil.which("envsubst") is not None or shutil.which("python3") is not None


def _run_render(src: Path, dest: Path, env: dict[str, str]) -> int:
    """通过 subprocess 跑 bash 调函数（与生产 server_monitor.sh 一致）。"""
    source_script = (
        f'set -euo pipefail; source "{RENDER_HELPER}"; '
        f'render_monitor_config "{src}" "{dest}"'
    )
    proc = subprocess.run(
        ["bash", "-c", source_script],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"render exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return 0


def _write_yaml(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_render_helper_defines_function() -> None:
    """monitor_render.sh 必须定义 render_monitor_config + 携带 set -euo pipefail。"""
    head = RENDER_HELPER.read_text(encoding="utf-8")
    assert RENDER_HELPER.is_file()
    assert "set -euo pipefail" in head
    assert "render_monitor_config()" in head


@pytest.mark.skipif(not _have_renderer(), reason="envsubst 与 python 均不可用")
def test_render_substitutes_declared_runtime_root(tmp_path: Path) -> None:
    """声明的 ${AQSP_RUNTIME_DATA_ROOT} 被替换为真实路径，注释 / 字段保留。

    同一测试覆盖三件事：
    - 替换声明的占位符（生产路径）
    - 注释 / description / version / severity 等无关文本保持原样（yaml 结构不破）
    - envsubst 未声明 ${VAR} → 空（生产路径）
    """
    src = tmp_path / "src.yaml"
    dest = tmp_path / "rendered.yaml"
    _write_yaml(
        src,
        "# 顶部注释必须保留\n"
        "version: '1.3.0'\n"
        "monitors:\n"
        "  - name: stale_data\n"
        "    description: 数据滞后超过阈值\n"
        "    params:\n"
        "      cache_path: ${AQSP_RUNTIME_DATA_ROOT}/cache.db\n"
        "      gate_path: ${AQSP_RUNTIME_DATA_ROOT}/walkforward_gate.json\n"
        "      required: false\n"
        "    severity: critical\n",
    )
    _run_render(
        src,
        dest,
        {"AQSP_RUNTIME_DATA_ROOT": "/opt/aqsp/data"},
    )

    rendered = dest.read_text(encoding="utf-8")
    # 声明的占位符已替换
    assert "/opt/aqsp/data/cache.db" in rendered
    assert "/opt/aqsp/data/walkforward_gate.json" in rendered
    # 无关文本原样保留（保护 yaml 结构不被破坏）
    assert "# 顶部注释必须保留" in rendered
    assert "数据滞后超过阈值" in rendered
    assert "version: '1.3.0'" in rendered
    assert "required: false" in rendered
    assert "severity: critical" in rendered
    # 未声明的 ${VAR} 不应在渲染里残留（envsubst 会替换成空）
    assert "${AQSP_RUNTIME_DATA_ROOT}" not in rendered


def test_render_missing_source_returns_error(tmp_path: Path) -> None:
    """源文件不存在返回非 0 exit，调用方按需降级。"""
    if not _have_renderer():
        pytest.skip("envsubst 与 python 均不可用")
    missing = tmp_path / "nope.yaml"
    dest = tmp_path / "out.yaml"
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{RENDER_HELPER}"; render_monitor_config "{missing}" "{dest}"',
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "源文件不存在" in proc.stderr


def test_monitors_yaml_uses_runtime_data_root_placeholder() -> None:
    """config/monitors.yaml 已切换到 env 占位（PR #77 核心改动）。

    所有 data/* 字面量必须替换为 ${AQSP_RUNTIME_DATA_ROOT}/...，避免 prod release
    下相对路径解析到 release_root/data（不存在 → skipped）。
    """
    monitors = (PROJECT_ROOT / "config" / "monitors.yaml").read_text(encoding="utf-8")
    # 期望已替换
    assert "${AQSP_RUNTIME_DATA_ROOT}/cache.db" in monitors
    assert "${AQSP_RUNTIME_DATA_ROOT}/walkforward_gate.json" in monitors
    assert "${AQSP_RUNTIME_DATA_ROOT}/walkforward_production_status.json" in monitors
    assert "${AQSP_RUNTIME_DATA_ROOT}/predictions.jsonl" in monitors
    assert "${AQSP_RUNTIME_DATA_ROOT}/paper_trades.jsonl" in monitors
    # 老字面量不能残留
    assert "cache_path: data/cache.db" not in monitors
    assert "gate_path: data/walkforward_gate.json" not in monitors
    assert "ledger_path: data/predictions.jsonl" not in monitors


def test_monitors_yaml_version_bumped() -> None:
    """monitors.yaml version 字段必须 bump（AGENTS.md §3.5 阈值约束）。"""
    monitors = (PROJECT_ROOT / "config" / "monitors.yaml").read_text(encoding="utf-8")
    assert 'version: "1.3.0"' in monitors


def test_server_monitor_sources_render_helper() -> None:
    """server_monitor.sh 必须 source render_helper + 调用函数 + 切 MONITOR_CONFIG。"""
    script = (PROJECT_ROOT / "scripts" / "server_monitor.sh").read_text(
        encoding="utf-8"
    )
    assert 'source "$MONITOR_RENDER_HELPER"' in script
    assert "render_monitor_config" in script
    # 渲染成功后 MONITOR_CONFIG 必须切换到 RENDERED_CONFIG 路径（根因修复核心）
    assert 'MONITOR_CONFIG="${RENDERED_CONFIG}"' in script
    # trap 必须清理渲染副本（避免 /tmp 残留）
    assert 'rm -f "$RENDERED_CONFIG"' in script
