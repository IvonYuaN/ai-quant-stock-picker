#!/usr/bin/env bash
# 在 PR 提交前运行：pytest + ruff，输出真实证据
# 用法：bash scripts/check_pr_evidence.sh > pr_evidence.txt 2>&1
set -e
cd "$(dirname "$0")/.."

echo "=== git status ==="
git status --short
echo ""

echo "=== git diff --stat HEAD~1 ==="
git diff --stat HEAD~1 || echo "(no previous commit)"
echo ""

echo "=== pytest tests/ -q ==="
python3 -m pytest tests/ -q 2>&1 | tail -20
echo "pytest_exit=$?"
echo ""

echo "=== ruff check src/ tests/ ==="
python3 -m ruff check src/ tests/ 2>&1 | tail -10
echo "ruff_exit=$?"
echo ""

echo "=== generated at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
