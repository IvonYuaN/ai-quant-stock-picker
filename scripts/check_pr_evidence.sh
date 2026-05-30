#!/usr/bin/env bash
# 在 PR 提交前运行：pytest + ruff + upload preflight，输出真实证据
# 用法：bash scripts/check_pr_evidence.sh > pr_evidence.txt 2>&1
#
# §17 要求 PR 证据是真实 exit code。原版用 `tail | echo $?` 取的是 tail 的
# exit code（永远 0），等于自欺。本脚本：
#   1. 用 ${PIPESTATUS[0]} 取管道首段（pytest / ruff）的真实退出码；
#   2. 工具不存在 → 显式 exit 非零，不要安静放过；
#   3. 上传 preflight 阻断 secrets/本地数据/运行态产物；
#   4. 任一工具失败 → 整脚本 exit 非零，方便 CI 直接失败。
#
# 注意：保留 set -u / 关闭 set -e（手动检查每步退出码，便于产出完整 evidence）

set -u
cd "$(dirname "$0")/.."

overall_exit=0

echo "=== git status ==="
git status --short
echo ""

echo "=== git diff --stat HEAD~1 ==="
git diff --stat HEAD~1 || echo "(no previous commit)"
echo ""

echo "=== pytest tests/ -q ==="
if ! command -v python3 >/dev/null 2>&1; then
    echo "[FAIL] python3 not found"
    overall_exit=127
elif ! python3 -c "import pytest" >/dev/null 2>&1; then
    echo "[FAIL] pytest not installed in current python3"
    overall_exit=127
else
    python3 -m pytest tests/ -q 2>&1 | tail -20
    pytest_exit=${PIPESTATUS[0]}
    echo "pytest_exit=${pytest_exit}"
    if [ "${pytest_exit}" -ne 0 ]; then
        overall_exit=${pytest_exit}
    fi
fi
echo ""

echo "=== ruff check . ==="
if ! python3 -c "import ruff" >/dev/null 2>&1 && ! command -v ruff >/dev/null 2>&1; then
    echo "[FAIL] ruff not installed"
    if [ "${overall_exit}" -eq 0 ]; then overall_exit=127; fi
else
    python3 -m ruff check . 2>&1 | tail -10
    ruff_exit=${PIPESTATUS[0]}
    echo "ruff_exit=${ruff_exit}"
    if [ "${ruff_exit}" -ne 0 ] && [ "${overall_exit}" -eq 0 ]; then
        overall_exit=${ruff_exit}
    fi
fi
echo ""

echo "=== upload preflight ==="
python3 -m scripts.preflight_upload 2>&1 | tail -20
preflight_exit=${PIPESTATUS[0]}
echo "preflight_exit=${preflight_exit}"
if [ "${preflight_exit}" -ne 0 ] && [ "${overall_exit}" -eq 0 ]; then
    overall_exit=${preflight_exit}
fi
echo ""

echo "=== generated at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "overall_exit=${overall_exit}"
exit "${overall_exit}"
