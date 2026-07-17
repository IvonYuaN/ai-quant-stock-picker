"""Offline contract tests for the Vibe Research health preflight."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/health_vibe_research.sh"


def _snapshot() -> dict[str, object]:
    return {
        "schema_version": "v1",
        "generated_at": "2026-07-17T08:00:00+08:00",
        "stale_after": "2099-01-01T00:00:00+08:00",
        "selected_date": "2026-07-17",
        "available_dates": ["2026-07-17"],
        "candidates": [
            {
                "symbol": "600001",
                "display_name": "600001 示例",
                "score": 72.5,
                "research_status": "纸面复核",
                "next_step": "确认量能",
                "context": "测试",
            }
        ],
        "debates": [
            {
                "symbol": "600001",
                "display_name": "600001 示例",
                "conclusion": "维持纸面复核",
                "primary_risk_gate": "量能",
                "next_trigger": "放量",
                "active_roles": ["risk"],
            }
        ],
        "summaries": ["测试"],
        "source": {"effective": "fixture", "latest_trade_date": "2026-07-17", "lag_days": 0, "status": "fresh"},
        "coldstart": {"status": "ready", "detail": "fixture"},
        "messages": [
            {
                "title": "测试消息",
                "summary": "测试",
                "impact": "中性",
                "category": "市场",
                "source": "fixture",
                "published_at": "2026-07-17T09:00:00+08:00",
                "affected_symbols": ["600001"],
            }
        ],
    }


def _run(tmp_path: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    stubs = tmp_path / "python-stubs"
    stubs.mkdir(exist_ok=True)
    (stubs / "fastapi.py").write_text("", encoding="utf-8")
    (stubs / "uvicorn.py").write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        AQSP_RESEARCH_SURFACE_SNAPSHOT=str(path),
        VIBE_RESEARCH_PYTHON_BIN=sys.executable,
        PYTHONPATH=os.pathsep.join(
            part for part in (str(stubs), env.get("PYTHONPATH", "")) if part
        ),
    )
    return subprocess.run(
        [str(SCRIPT), "--component", "api", "--preflight-only"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_health_vibe_research_accepts_final_snapshot_offline(tmp_path: Path) -> None:
    result = _run(tmp_path, _snapshot())
    assert result.returncode == 0, result.stderr
    assert "schema/mapping" in result.stdout


def test_health_vibe_research_rejects_snapshot_when_stale(tmp_path: Path) -> None:
    payload = _snapshot()
    payload["generated_at"] = "2020-01-01T00:00:00+08:00"
    payload["stale_after"] = "2020-01-02T00:00:00+08:00"
    result = _run(tmp_path, payload)
    assert result.returncode != 0
    assert "过期" in result.stderr


def test_health_vibe_research_rejects_unmapped_debate_and_keeps_industry_message(
    tmp_path: Path,
) -> None:
    payload = _snapshot()
    payload["debates"] = [{**payload["debates"][0], "symbol": "600002"}]  # type: ignore[index]
    result = _run(tmp_path, payload)
    assert result.returncode != 0
    assert "未映射" in result.stderr

    payload = _snapshot()
    payload["messages"] = [
        {**payload["messages"][0], "affected_symbols": ["600002"]}  # type: ignore[index]
    ]
    result = _run(tmp_path, payload)
    assert result.returncode == 0, result.stderr


def test_health_vibe_research_rejects_incomplete_schema(tmp_path: Path) -> None:
    payload = _snapshot()
    del payload["coldstart"]
    result = _run(tmp_path, payload)
    assert result.returncode != 0
    assert "coldstart" in result.stderr
