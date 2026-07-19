#!/usr/bin/env python3
"""Push a branch and report an unambiguous GitHub result."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


COMMAND_TIMEOUT_SECONDS = 30


def _run(root: Path, command: list[str]) -> subprocess.CompletedProcess[str]:
    timeout = float(os.environ.get("AQSP_GIT_COMMAND_TIMEOUT_SECONDS", COMMAND_TIMEOUT_SECONDS))
    try:
        return subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = " ".join(
            part.decode(errors="replace") if isinstance(part, bytes) else str(part)
            for part in (exc.stdout, exc.stderr)
            if part
        )
        return subprocess.CompletedProcess(command, 124, "", f"command timed out after {timeout:g}s: {output}".strip())


def push(
    root: Path, *, remote: str, branch: str, dry_run: bool = False
) -> tuple[int, dict[str, object]]:
    identity = _run(root, ["git", "rev-parse", "HEAD"])
    commit = identity.stdout.strip()
    if identity.returncode != 0 or not commit:
        return 1, {
            "status": "failed",
            "remote": remote,
            "branch": branch,
            "exit_code": identity.returncode or 1,
            "reason": "local_commit_unavailable",
            "output": (identity.stderr or "").strip(),
        }
    status = _run(root, ["git", "status", "--porcelain", "--untracked-files=all"])
    if status.returncode != 0 or status.stdout.strip():
        return 1, {
            "status": "failed",
            "remote": remote,
            "branch": branch,
            "commit": commit,
            "exit_code": status.returncode or 1,
            "reason": "worktree_dirty" if status.stdout.strip() else "git_status_failed",
            "output": (status.stdout + status.stderr).strip(),
        }
    command = ["git", "push", "--porcelain", remote, f"HEAD:refs/heads/{branch}"]
    if dry_run:
        command.insert(2, "--dry-run")
    result = _run(root, command)
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    payload = {
        "status": ("dry_run_passed" if dry_run else "pushed")
        if result.returncode == 0
        else "failed",
        "remote": remote,
        "branch": branch,
        "commit": commit,
        "exit_code": result.returncode,
        "output": output,
    }
    if result.returncode == 0 and not dry_run:
        remote_result = _run(
            root,
            ["git", "ls-remote", remote, f"refs/heads/{branch}"],
        )
        remote_commit = remote_result.stdout.split(maxsplit=1)[0] if remote_result.stdout.strip() else ""
        payload["remote_commit"] = remote_commit
        if remote_result.returncode != 0 or remote_commit != commit:
            payload["status"] = "failed"
            payload["reason"] = "remote_commit_mismatch"
            payload["output"] = "\n".join(
                part.strip()
                for part in (output, remote_result.stdout, remote_result.stderr)
                if part.strip()
            )
            return 1, payload
    return result.returncode, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="push_with_report")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not args.skip_preflight:
        try:
            preflight = subprocess.run(
                ["python3", "-m", "scripts.preflight_upload"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                timeout=float(
                    os.environ.get(
                        "AQSP_GIT_COMMAND_TIMEOUT_SECONDS", COMMAND_TIMEOUT_SECONDS
                    )
                ),
            )
        except subprocess.TimeoutExpired as exc:
            output = " ".join(
                part.decode(errors="replace") if isinstance(part, bytes) else str(part)
                for part in (exc.stdout, exc.stderr)
                if part
            )
            preflight = subprocess.CompletedProcess(
                ["python3", "-m", "scripts.preflight_upload"],
                124,
                "",
                f"preflight timed out after {COMMAND_TIMEOUT_SECONDS:g}s: {output}".strip(),
            )
        if preflight.returncode != 0:
            output = "\n".join(
                part.strip() for part in (preflight.stdout, preflight.stderr) if part.strip()
            )
            payload = {
                "status": "preflight_failed",
                "remote": args.remote,
                "branch": args.branch,
                "exit_code": preflight.returncode,
                "output": output,
            }
            if args.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(f"github_push_status=preflight_failed remote={args.remote} branch={args.branch} exit_code={preflight.returncode}")
                if output:
                    print(output)
                print("GitHub push was not attempted because upload preflight failed.")
            return 1
    code, payload = push(root, remote=args.remote, branch=args.branch, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"github_push_status={payload['status']} remote={args.remote} branch={args.branch} exit_code={code}")
        if payload["output"]:
            print(payload["output"])
    if code:
        print("GitHub push failed; canonical release must not be claimed as published.", flush=True)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
