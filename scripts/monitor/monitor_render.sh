#!/usr/bin/env bash
# Monitor config rendering helpers for server_monitor.sh (PR #77).
#
# 把 config/monitors.yaml 字面量里的 ${AQSP_RUNTIME_DATA_ROOT} 等占位符替换成
# 当前 shell env 的值，输出到临时副本。调用方负责 trap cleanup。
#
# 行为约定：
# - 命令可用：envsubst(gettext-base, Ubuntu/Debian/RHEL 标准包) — 未声明 ${VAR}
#   替换为空串，匹配 CI 兼容语义（cache_path 变 `/cache.db` 绝对路径，仍 skipped）。
# - fallback：Python string.Template.safe_substitute — 未声明占位符保留 ${VAR}
#   字面量（CI 兼容语义）。
# - 两者都不可用 → return 1（监控继续跑，但 yaml 未渲染）。

set -euo pipefail

# render_monitor_config src dest
#   src  : 源 monitors.yaml 文件
#   dest : 渲染后副本的目标文件
# 返回 0 表示渲染成功；非 0 表示 envsubst/Python 都不可用，调用方按需跳过。
render_monitor_config() {
    local src="$1"
    local dest="$2"

    if ! [ -f "$src" ]; then
        printf '[monitor_render] 源文件不存在: %s\n' "$src" >&2
        return 2
    fi

    if command -v envsubst >/dev/null 2>&1; then
        envsubst < "$src" > "$dest"
        return 0
    fi

    if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then
        local py_bin
        py_bin="$(command -v python3 || command -v python)"
        "$py_bin" - "$src" "$dest" <<'PY'
import os
import sys
from string import Template

src, dest = sys.argv[1], sys.argv[2]
with open(src, encoding="utf-8") as f:
    body = f.read()
# safe_substitute：未声明占位符保留 ${VAR} 字面量（CI 兼容）。
out = Template(body).safe_substitute(os.environ)
with open(dest, "w", encoding="utf-8") as f:
    f.write(out)
PY
        return 0
    fi

    printf '[monitor_render] envsubst 与 python 均不可用，无法渲染: %s\n' "$src" >&2
    return 1
}
