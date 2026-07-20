#!/usr/bin/env bash
# Static validation for the AQSP systemd deployment bundle.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR="${PROJECT_ROOT}/deploy/systemd"

assert_file() {
    [[ -f "$1" ]] || { echo "FAIL missing: $1" >&2; exit 1; }
}

assert_exec() {
    [[ -x "$1" ]] || { echo "FAIL not executable: $1" >&2; exit 1; }
}

for script in \
    health_vibe_research.sh \
    start_vibe_research_service.sh \
    stop_vibe_research_service.sh \
    rollback_vibe_research.sh \
    install_vibe_research_systemd.sh; do
    path="${PROJECT_ROOT}/scripts/${script}"
    assert_file "$path"
    assert_exec "$path"
    bash -n "$path"
done

for script in \
    check_release_consistency.py \
    write_release_manifest.py \
    push_with_report.py \
    check_runtime_storage.py; do
    path="${PROJECT_ROOT}/scripts/${script}"
    assert_file "$path"
    python3 -m py_compile "$path"
done
echo "PASS release identity and publish checks"

rg -q 'releases|current|rollback|--apply' \
    "${PROJECT_ROOT}/scripts/check_runtime_storage.py"
echo "PASS runtime storage cleanup guard"

for unit in \
    aqsp-vibe-research-api.service \
    aqsp-vibe-research-preview.service \
    aqsp-vibe-research.target \
    aqsp-vibe-research.env.example; do
    assert_file "${SYSTEMD_DIR}/${unit}"
done

rendered_dir="$(mktemp -d)"
trap 'rm -rf "$rendered_dir"' EXIT
for unit in aqsp-vibe-research-api.service aqsp-vibe-research-preview.service; do
    sed \
        -e 's|@AQSP_PROJECT_ROOT@|/opt/aqsp|g' \
        -e 's|@AQSP_VIBE_USER@|aqsp-vibe|g' \
        -e 's|@AQSP_VIBE_GROUP@|aqsp-vibe|g' \
        -e 's|@AQSP_VENV_DIR@|/opt/aqsp/.venv-vibe-research|g' \
        -e 's|@AQSP_ENV_FILE@|/etc/aqsp/vibe-research.env|g' \
        -e 's|@AQSP_LOG_DIR@|/opt/aqsp/logs/vibe-research|g' \
        -e 's|@AQSP_NPM_BIN@|/usr/bin/npm|g' \
        "${SYSTEMD_DIR}/${unit}" >"${rendered_dir}/${unit}"
    ! rg -v '^\s*#' "${rendered_dir}/${unit}" | rg -q '@AQSP_[A-Z_]+@'
done
rg -q '^User=aqsp-vibe$' "${rendered_dir}/aqsp-vibe-research-api.service"
rg -q 'PYTHONPATH=/opt/aqsp/src:/opt/aqsp/backend' \
    "${rendered_dir}/aqsp-vibe-research-api.service"
rg -q '/opt/aqsp/.venv-vibe-research/bin/python' \
    "${rendered_dir}/aqsp-vibe-research-api.service"
echo "PASS template rendering: isolated user, venv and PYTHONPATH"

rg -q 'port 8900|--port 8900' "${SYSTEMD_DIR}/aqsp-vibe-research-api.service"
rg -q 'port 5899|--port 5899' "${SYSTEMD_DIR}/aqsp-vibe-research-preview.service"
rg -q 'AQSP_RESEARCH_SURFACE_SNAPSHOT' "${SYSTEMD_DIR}/aqsp-vibe-research.env.example"
rg -q -- '--skip-snapshot' "${PROJECT_ROOT}/scripts/start_vibe_research.sh"
rg -q 'start_vibe_research.sh' "${PROJECT_ROOT}/scripts/start_dashboard.sh"
! rg -q 'proxy_pass http://127.0.0.1:8501' "${PROJECT_ROOT}/deploy/nginx/aqsp-dashboard.conf" "${PROJECT_ROOT}/deploy/nginx/vibe-research-mainline.conf"
for nginx_config in \
    "${PROJECT_ROOT}/deploy/nginx/aqsp-dashboard.conf" \
    "${PROJECT_ROOT}/deploy/nginx/vibe-research-mainline.conf"; do
    rg -q 'location /' "$nginx_config"
    rg -q 'proxy_pass http://127.0.0.1:5899' "$nginx_config"
    rg -q 'location \^~ /api/' "$nginx_config"
    rg -q 'proxy_pass http://127.0.0.1:8900' "$nginx_config"
done
rg -q -- '--allow-stale-snapshot' "${SYSTEMD_DIR}/aqsp-vibe-research-api.service"
rg -q 'AQSP_VIBE_USER|AQSP_VIBE_VENV_DIR|python3 -m venv' \
    "${PROJECT_ROOT}/scripts/install_vibe_research_systemd.sh"
rg -q 'pip install -e .*\[api\]' "${PROJECT_ROOT}/scripts/install_vibe_research_systemd.sh"
rg -q 'setfacl|d:u:|父目录' "${PROJECT_ROOT}/scripts/install_vibe_research_systemd.sh"
rg -q 'PYTHONPATH=.*src.*backend' \
    "${PROJECT_ROOT}/scripts/install_vibe_research_systemd.sh" \
    "${SYSTEMD_DIR}/aqsp-vibe-research-api.service"
rg -q 'VIBE_RESEARCH_PYTHON_BIN|VIBE_RESEARCH_NPM_BIN' \
    "${SYSTEMD_DIR}/aqsp-vibe-research-api.service" \
    "${SYSTEMD_DIR}/aqsp-vibe-research-preview.service" \
    "${PROJECT_ROOT}/scripts/health_vibe_research.sh"
if rg -n 'User=aqsp$|/opt/aqsp/\.venv/bin/python' "${SYSTEMD_DIR}"; then
    echo "FAIL deployment template assumes the unverified root runtime" >&2
    exit 1
fi

LEGACY_PYTHON="${AQSP_LEGACY_VENV_PYTHON:-/opt/aqsp/.venv/bin/python}"
if [[ -x "$LEGACY_PYTHON" ]]; then
    if "$LEGACY_PYTHON" -c 'import fastapi' >/dev/null 2>&1; then
        echo "INFO legacy venv can import fastapi; installer still provisions an isolated venv"
    else
        echo "PASS legacy venv lacks fastapi and is not used by the Vibe units"
    fi
else
    echo "SKIP legacy venv evidence: ${LEGACY_PYTHON} is not present locally"
fi
rg -q 'StandardOutput=append:.*/api\.log' "${SYSTEMD_DIR}/aqsp-vibe-research-api.service"
rg -q 'StandardOutput=append:.*/frontend\.log' "${SYSTEMD_DIR}/aqsp-vibe-research-preview.service"
rg -q 'ExecStartPre=/usr/bin/env VIBE_RESEARCH_PYTHON_BIN=.*--port-guard' \
    "${SYSTEMD_DIR}/aqsp-vibe-research-api.service"
rg -q 'ExecStartPre=/usr/bin/env VIBE_RESEARCH_PYTHON_BIN=.*VIBE_RESEARCH_NPM_BIN=.*--port-guard' \
    "${SYSTEMD_DIR}/aqsp-vibe-research-preview.service"

if rg -n '(^|[;&|[:space:]])nohup([[:space:]]|$)|pkill|killall|(^|[;&|[:space:]])ssh[[:space:]]' \
    "${PROJECT_ROOT}/scripts/health_vibe_research.sh" \
    "${PROJECT_ROOT}/scripts/start_vibe_research_service.sh" \
    "${PROJECT_ROOT}/scripts/stop_vibe_research_service.sh" \
    "${PROJECT_ROOT}/scripts/rollback_vibe_research.sh" \
    "${SYSTEMD_DIR}"; then
    echo "FAIL forbidden process/remote control command found" >&2
    exit 1
fi

if command -v systemd-analyze >/dev/null 2>&1; then
    systemd-analyze verify \
        "${rendered_dir}/aqsp-vibe-research-api.service" \
        "${rendered_dir}/aqsp-vibe-research-preview.service" \
        "${SYSTEMD_DIR}/aqsp-vibe-research.target"
else
    echo "SKIP systemd-analyze: command unavailable"
fi

echo "Vibe-Research deployment bundle validation passed."
