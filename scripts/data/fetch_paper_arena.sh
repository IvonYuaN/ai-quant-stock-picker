#!/usr/bin/env bash
# fetch_paper_arena.sh — 在 prod（在线服务机）上执行：单向 pull runner 的影子账户产物。
#
# 背景（2026-11-25，#684）：prod 的 `data/paper_arena.json` 是 11-23 发版时的快照，
# 之后 runner 每天 15:10 更新 `paper_arena_api.json`，但没有回流通道 ⇒
# Web 上 `/api/aqsp/paper-arena` 一直显示 11-20 的旧净值，且后端误标 stale=false。
# 本脚本补上回流，复用 fetch_ic_diagnosis.sh 的单向 pull 范式。
#
# 权限红线（同 runner_fetch.sh / fetch_ic_diagnosis.sh）：prod 主动 ssh/rsync pull
# runner，runner 不需要任何连回生产的 SSH 权限（只读、不碰生产）。
#
# 消费闭环：拉到 `$DEST`（缺省 = `${RUNTIME_DATA_ROOT}/data/paper_arena.json`），
# `backend/paper_arena_bridge.py` 默认就读这个路径。字段名与 runner 产物完全一致，
# 不需要转换（schema_version= v1）。
#
# 新鲜度契约（防「旧产物冒充最新」—— 这是 #684 的根因，必须锁死）：
#   - runner 产物缺失       ⇒ exit 2，保留本地旧文件，不覆盖；
#   - generated_at 陈旧     ⇒ exit 3，保留本地旧文件，不覆盖（发版时的老快照至少还是自洽的）；
#   - 产物 generated_at 非法 / 解析失败 ⇒ exit 2（fail-closed，不覆盖）；
#   - 连不上 runner         ⇒ exit 1，保留本地旧文件；
#   - 新鲜                  ⇒ rsync 覆盖，exit 0。
#
# 退出码是 best-effort 语义：调用方（cron）只记日志，绝不因此阻断跑批。
#
# 用法（在 prod 上）：
#   DRY_RUN=1 bash scripts/fetch_paper_arena.sh          # 只判定，不实际拉
#   MAX_AGE_HOURS=36 DEST=... bash scripts/fetch_paper_arena.sh
#
# ⚠️ 本文件含中文，`$VAR` 后紧跟全角字符会让 bash 把多字节首字节吞进变量名
#    （set -u 下报 unbound variable）→ 一律写 ${VAR}。
set -euo pipefail

RUNNER_HOST="${RUNNER_HOST:-root@38.147.170.174}"
RUNNER_PORT="${RUNNER_PORT:-31777}"
RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
RUNTIME_DATA_ROOT="${AQSP_RUNTIME_DATA_ROOT:-${PROJECT_ROOT}/data}"
DEST="${DEST:-${RUNTIME_DATA_ROOT}/paper_arena.json}"
DRY_RUN="${DRY_RUN:-0}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-36}"

EXIT_OK=0
EXIT_ENV=1
EXIT_NO_RESULT=2
EXIT_STALE=3

RSYNC_SSH="ssh -p ${RUNNER_PORT} -o BatchMode=yes -o ConnectTimeout=10"
REMOTE_FILE="${RUNNER_ROOT}/shortline/paper_arena_api.json"

log() {
  local msg="[$(date '+%F %T')] $*"
  echo "$msg"
}

# probe 必须 drain stdin，且只发一条命令（避免 ssh 多次往返）。
# 让远端一次性回传：产物是否存在 + 文件大小 + generated_at + schema_version。
probe="$(
  $RSYNC_SSH "$RUNNER_HOST" "F='$REMOTE_FILE' bash -s" <<'REMOTE' 2>/dev/null
set -u
now=$(date +%s)
echo "NOW=${now}"
f="${F}"
if [ ! -f "$f" ]; then
  echo "PRESENT=0"
else
  echo "PRESENT=1"
  echo "MTIME=$(stat -c %Y "$f" 2>/dev/null || echo 0)"
  # 只取 generated_at 与 schema_version，避免整份 JSON 回传
  python3 - "$f" <<'PY' 2>/dev/null || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    print("GEN=" + str(d.get("generated_at") or ""))
    print("SCHEMA=" + str(d.get("schema_version") or ""))
except Exception:
    pass
PY
fi
REMOTE
)" || {
  log "❌ 无法连接 runner（${RUNNER_HOST}:${RUNNER_PORT}）；保留本地旧产物"
  exit "$EXIT_ENV"
}

field() { printf '%s\n' "$probe" | sed -n "s/^$1=//p" | head -1; }
NOW="$(field NOW)"; NOW="${NOW:-0}"
PRESENT="$(field PRESENT)"
MTIME="$(field MTIME)"; MTIME="${MTIME:-0}"
GEN="$(field GEN)"
SCHEMA="$(field SCHEMA)"

log "远端 ${RUNNER_HOST}:${REMOTE_FILE}"
log "  PRESENT=${PRESENT} gen=${GEN:-—} schema=${SCHEMA:-—}"

# ── 1) 存在性 / 结构 ──
if [ "$PRESENT" != "1" ]; then
  log "判定：NO_RESULT —— runner 无产物，保留本地旧文件"
  exit "$EXIT_NO_RESULT"
fi
if [ "$SCHEMA" != "v1" ] || [ -z "$GEN" ]; then
  log "判定：NO_RESULT —— 产物 schema 非 v1 或缺 generated_at，fail-closed 不覆盖"
  exit "$EXIT_NO_RESULT"
fi

# ── 2) 新鲜度：用 generated_at（ISO 8601 with offset）而非 mtime ──
# mtime 会随 rsync/cp 被重置，generated_at 是产物自述的唯一可信时间。
GEN_EPOCH="$(python3 -c '
import sys, datetime
s = sys.argv[1]
try:
    print(int(datetime.datetime.fromisoformat(s).timestamp()))
except Exception:
    print(0)
' "$GEN" 2>/dev/null || echo 0)"
if [ "$GEN_EPOCH" -le 0 ]; then
  log "判定：NO_RESULT —— generated_at 无法解析（${GEN}），fail-closed 不覆盖"
  exit "$EXIT_NO_RESULT"
fi
AGE_H=$(( (NOW - GEN_EPOCH) / 3600 ))
[ "$AGE_H" -lt 0 ] && AGE_H=0
log "  产物年龄 ${AGE_H}h（上限 ${MAX_AGE_HOURS}h）"
if [ "$AGE_H" -gt "$MAX_AGE_HOURS" ]; then
  log "判定：STALE —— ${AGE_H}h > ${MAX_AGE_HOURS}h，保留本地旧文件（旧快照至少自洽）"
  exit "$EXIT_STALE"
fi

if [ "$DRY_RUN" = "1" ]; then
  log "DRY_RUN=1，判定 OK（新鲜 ${AGE_H}h），未实际拉取"
  exit 0
fi

# ── 3) 拉取：先下到临时文件，校验 schema 后再原子替换 ──
mkdir -p "$(dirname "$DEST")"
TMP="${DEST}.tmp.$$"
trap 'rm -f "$TMP"' EXIT
if ! rsync -a -e "$RSYNC_SSH" "${RUNNER_HOST}:${REMOTE_FILE}" "$TMP"; then
  log "❌ rsync 失败；保留本地旧产物"
  exit "$EXIT_ENV"
fi
# 落盘后再验一次，防「传输中途 runner 重写了文件」
if ! python3 - "$TMP" <<'PY' >/dev/null 2>&1
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d.get("schema_version") == "v1", "schema"
assert d.get("generated_at"), "generated_at"
assert isinstance(d.get("sleeves"), list), "sleeves"
PY
then
  log "❌ 拉取产物校验失败；保留本地旧产物"
  exit "$EXIT_NO_RESULT"
fi
mv -f "$TMP" "$DEST"
trap - EXIT
log "✅ 影子账户产物回流完成：${REMOTE_FILE} → ${DEST}（generated_at=${GEN}）"
exit "$EXIT_OK"
