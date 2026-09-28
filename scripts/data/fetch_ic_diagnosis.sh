#!/usr/bin/env bash
# fetch_ic_diagnosis.sh — 在 prod（在线服务机）上执行：单向 pull runner 的因子 IC 诊断。
#
# 权限红线（同 runner_fetch.sh）：prod 主动 ssh/rsync pull runner，
# runner 不需要任何连回生产的 SSH 权限（只读、不碰生产）。
# 消费闭环：拉到 $DEST_DIR（缺省 = 收评日报 build_factor_ic_section 的写读同源路径
#   ${AQSP_RUNTIME_DATA_ROOT:-$PROJECT_ROOT/data}/pit_cache/factor_ic/），
#   日报「因子 IC 健康」段直接读，缺产物时该段自动不渲染（降级安全）。
#
# 新鲜度契约（IC_READY 是唯一新鲜度证据，与 runner_gate 的 RESULT_READY 同范式）：
#   - IC_READY 不存在            ⇒ 本轮 runner 没产出（未跑/让位/失败）：保留本地旧产物，exit 2
#   - IC_READY 陈旧(>MAX_AGE_H)  ⇒ 旧产物伪装风险：保留本地旧产物，exit 3（不当成「有新结果」）
#   - 新鲜                       ⇒ rsync 拉取 4 个产物，exit 0
# 退出码是 best-effort 语义：调用方（daily 链路）只记日志、绝不因此阻断跑批。
#
# 用法（在 prod 上）：
#   DRY_RUN=1 bash scripts/fetch_ic_diagnosis.sh        # 只判定并打印，不实际拉
#   MAX_AGE_HOURS=36 DEST_DIR=... bash scripts/fetch_ic_diagnosis.sh
#
# ⚠️ 本文件含中文，`$VAR` 后紧跟全角字符会让 bash 把多字节首字节吞进变量名
#    （set -u 下报 unbound variable）→ 一律写 ${VAR}。契约由 tests/test_fetch_ic_diagnosis_script.py 覆盖。
set -euo pipefail

# ── 参数（全部 env 可覆盖，与 runner_fetch.sh 同一套变量名） ─────────────────
RUNNER_HOST="${RUNNER_HOST:-root@38.147.170.174}"
RUNNER_PORT="${RUNNER_PORT:-31777}"
RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
PROJECT_ROOT="${AQSP_PROJECT_ROOT:-/opt/aqsp}"
RUNTIME_DATA_ROOT="${AQSP_RUNTIME_DATA_ROOT:-${PROJECT_ROOT}/data}"
DEST_DIR="${DEST_DIR:-${RUNTIME_DATA_ROOT}/pit_cache/factor_ic}"
DRY_RUN="${DRY_RUN:-0}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-36}"
LOG_FILE="${LOG_FILE:-}"

EXIT_OK=0
EXIT_NO_RESULT=2   # 无 IC_READY（没跑 / 让位 / 失败）——保留本地旧产物
EXIT_STALE=3       # IC_READY 陈旧——保留本地旧产物，绝不冒充「本周有新结果」
EXIT_ENV=1          # 连不上 runner（网络 / 主机密钥 / 公钥）

RSYNC_SSH="ssh -p ${RUNNER_PORT} -o BatchMode=yes"
REMOTE_DIR="$RUNNER_ROOT/pit_cache/factor_ic"

log() {
  local msg="[$(date '+%F %T')] $*"
  echo "$msg"
  if [ -n "$LOG_FILE" ]; then
    mkdir -p "$(dirname "$LOG_FILE")"
    echo "$msg" >>"$LOG_FILE"
  fi
}

# ── 1) 一次 ssh 取回全部远端证据（单往返，避免「探到一半断线」判定残缺） ──
probe="$(
  $RSYNC_SSH "$RUNNER_HOST" "OUT='$REMOTE_DIR' bash -s" <<'REMOTE'
set -u
now=$(date +%s)
echo "NOW=$now"
ready="$OUT/IC_READY"
if [ -f "$ready" ]; then
  echo "READY_PRESENT=1"
  echo "READY_MTIME=$(stat -c %Y "$ready" 2>/dev/null || echo 0)"
  echo "READY_VALUE=$(head -1 "$ready" 2>/dev/null || echo '')"
else
  echo "READY_PRESENT=0"
fi
json="$OUT/factor_ic_latest.json"
if [ -f "$json" ]; then
  echo "JSON_PRESENT=1"
  echo "JSON_MTIME=$(stat -c %Y "$json" 2>/dev/null || echo 0)"
else
  echo "JSON_PRESENT=0"
fi
# 状态文件（ic_diagnosis_runner.sh 跑的最近一次结果标记，若存在）
status="$OUT/ic_status.json"
if [ -f "$status" ]; then
  echo "STATUS_VALUE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("status",""))' "$status" 2>/dev/null || echo unknown)"
else
  echo "STATUS_VALUE="
fi
REMOTE
)" || {
  log "❌ 无法连接 runner（${RUNNER_HOST}:${RUNNER_PORT}）；保留本地旧产物"
  exit "$EXIT_ENV"
}

field() { printf '%s\n' "$probe" | sed -n "s/^$1=//p" | head -1; }
NOW="$(field NOW)"
READY_PRESENT="$(field READY_PRESENT)"
READY_MTIME="$(field READY_MTIME)"
JSON_PRESENT="$(field JSON_PRESENT)"
STATUS_VALUE="$(field STATUS_VALUE)"
NOW="${NOW:-0}"
READY_MTIME="${READY_MTIME:-0}"

age_hours() {  # $1 = mtime epoch；输出整数小时（负数归 0）
  local mtime="${1:-0}"
  [ "$mtime" -gt 0 ] 2>/dev/null || { echo ""; return; }
  local delta=$(( NOW - mtime ))
  [ "$delta" -lt 0 ] && delta=0
  echo $(( delta / 3600 ))
}

READY_AGE="$(age_hours "$READY_MTIME")"
log "远端 ${RUNNER_HOST}:${REMOTE_DIR}"
log "  IC_READY     : present=${READY_PRESENT} age=${READY_AGE:-—}h value=${STATUS_VALUE:+run-status=${STATUS_VALUE:-—}}"

# ── 2) 判定：IC_READY 是唯一新鲜度证据（陈旧一律保留本地旧产物，不冒充） ──
if [ "$READY_PRESENT" != "1" ]; then
  log "判定：NO_RESULT —— runner 无 IC_READY（没跑 / load 让位 / 失败），保留本地旧产物"
  exit "$EXIT_NO_RESULT"
fi
if [ -n "$READY_AGE" ] && [ "$READY_AGE" -gt "$MAX_AGE_HOURS" ]; then
  log "判定：STALE —— IC_READY 已 ${READY_AGE}h > ${MAX_AGE_HOURS}h，视为上轮遗留，保留本地旧产物"
  exit "$EXIT_STALE"
fi
if [ "$JSON_PRESENT" != "1" ]; then
  log "判定：NO_RESULT —— 有 IC_READY 但 JSON 产物缺失（runner 侧异常），保留本地旧产物"
  exit "$EXIT_NO_RESULT"
fi

if [ "$DRY_RUN" = "1" ]; then
  log "DRY_RUN=1，判定 OK（新鲜 ${READY_AGE:-?}h），未实际拉取"
  exit 0
fi

# ── 3) 执行拉取（4 个产物，全部存在才拉；缺个别文件不视为整体失败） ──
mkdir -p "$DEST_DIR"
PULLED=0
for f in factor_ic_latest.json ic_history.jsonl report.md IC_READY; do
  if $RSYNC_SSH "$RUNNER_HOST" "test -f '$REMOTE_DIR/$f'"; then
    rsync -a -e "$RSYNC_SSH" "$RUNNER_HOST:$REMOTE_DIR/$f" "$DEST_DIR/"
    PULLED=$((PULLED + 1))
    log "已拉取 → $DEST_DIR/$f"
  else
    log "⚠️ 远端缺 ${f}（跳过，保留本地同名字文件）"
  fi
done

if [ "$PULLED" -eq 0 ]; then
  log "❌ 远端无任何 IC 产物（与判定矛盾），保留本地旧产物"
  exit "$EXIT_NO_RESULT"
fi
log "IC 诊断回流完成：${PULLED} 个文件 → $DEST_DIR"
exit 0
