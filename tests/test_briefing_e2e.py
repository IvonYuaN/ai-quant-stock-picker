from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_briefing_runs_without_llm():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False, dir="/tmp") as f:
        output_path = f.name
    try:
        result = subprocess.run(
            [sys.executable, "-m", "aqsp", "briefing", "--output", output_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        assert result.returncode == 0, (
            f"briefing failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        content = Path(output_path).read_text(encoding="utf-8")
        assert content.strip(), "briefing output empty"
        assert "#" in content, f"briefing not markdown: {content[:200]!r}"
    finally:
        Path(output_path).unlink(missing_ok=True)


def test_briefing_module_importable():
    from aqsp.briefing import generator

    assert generator is not None
