"""Run one persisted all-universe strategy scan outside the API process."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

import strategy_scans  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--state-path", required=True, type=Path)
    parser.add_argument("--tenant-id", required=True)
    args = parser.parse_args(argv)
    try:
        return strategy_scans.execute_run(args.run_id, args.state_path, args.tenant_id)
    except Exception as exc:
        state = strategy_scans._read(args.state_path)
        state.update(
            {
                "status": "failed",
                "phase": "failed",
                "message": f"策略扫描失败：{str(exc)[:500]}",
                "updated_at": strategy_scans.now_shanghai().isoformat(timespec="seconds"),
                "finished_at": strategy_scans.now_shanghai().isoformat(timespec="seconds"),
            }
        )
        strategy_scans._write(args.state_path, state)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
