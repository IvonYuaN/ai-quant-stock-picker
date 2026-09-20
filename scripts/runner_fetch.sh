#!/usr/bin/env bash
# runner_fetch.sh — 在 prod（在线服务机）上执行：把计算节点(runner)的 gate 结果拉回本地。
#
# 为什么是 prod 主动 pull 而不是 runner 回传：这样 runner 不需要任何连回生产的 SSH 权限，
# 权限面最小（prod → runner 单向），符合「runner 只读、不碰生产」的红线。
#
# 用法（在 prod 上）：
#   bash scripts/runner_fetch.sh                 # 拉取到 /opt/aqsp/data/gate_run/
#   DRY_RUN=1 bash scripts/runner_fetch.sh       # 只判定并打印，不拉
#   MAX_AGE_HOURS=48 bash scripts/runner_fetch.sh
#
# 退出码契约（cron / 监控据此区分「没跑」与「跑了没结果」）：
#   0  成功：RESULT_READY 新鲜 且 gate sidecar 存在 → 落 runner.* 前缀
#   1  环境错误：连不上 runner（网络 / 主机密钥 / 公钥）
#   2  跳过：runner 因业务负载让位，本轮根本没跑（skip.log 有本轮记录）
#   3  跑了没结果：status=timeout/failed/error/running，无 gate 产物
#   4  产物陈旧：有 gate 产物，但 RESULT_READY 缺失或超过 MAX_AGE_HOURS
#
# ⚠️ 2026-09-19 修复的缺陷（旧版行为）：
#   旧版只按「远端文件存在」就拉，不校验 runner_gate.sh 已经落好的 RESULT_READY 标记，
#   于是 runner 上 9 月的旧 report.md / gate_summary.md 会被拉成 runner.* 前缀，
#   在 prod 侧伪造出「本周有结果」。旧版还**从不**回传 walkforward_production_status.json，
#   导致 prod 无法区分「本轮超时」与「本轮没跑」。
#   现在：RESULT_READY 是唯一新鲜度证据；陈旧产物一律隔离进 runner.stale/ 而非伪装成当前结果。
#   prod 自己的判定输入（data/walkforward_gate.json 等）**不被本脚本改写** —— 晋升由人工/后续 PR 决定。
#
# ⚠️ 写 shell 注意：本文件含中文，`$VAR` 后紧跟全角字符会让 bash 把多字节首字节吞进
#   变量名（`set -u` 下报 unbound variable）→ 一律写 `${VAR}`。
#   契约由 tests/test_runner_fetch_script.py 覆盖。
set -euo pipefail

RUNNER_HOST="${RUNNER_HOST:-root@38.147.170.174}"
RUNNER_PORT="${RUNNER_PORT:-31777}"
RUNNER_ROOT="${RUNNER_ROOT:-/opt/aqsp-runner}"
GATE_DIR="${GATE_DIR:-/opt/aqsp/data/gate_run}"
DRY_RUN="${DRY_RUN:-0}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-36}"
# 生效中的双门 sidecar（通知门禁经 AQSP_WALKFORWARD_GATE_PATH 读它）。与 gate_run/ 不同目录。
GATE_TARGET="${AQSP_WALKFORWARD_GATE_PATH:-/opt/aqsp/data/walkforward_gate.json}"
# 提升时的结构校验上限，与 MAX_GATE_AGE_DAYS 同口径（注意：不是上面的 MAX_AGE_HOURS）。
GATE_MAX_AGE_DAYS="${GATE_MAX_AGE_DAYS:-35}"
PROMOTE_PY="${PROMOTE_PY:-/opt/aqsp-vibe-venv/bin/python}"
PROMOTE_ENABLED="${AQSP_FETCH_PROMOTE:-1}"

EXIT_OK=0
EXIT_ENV=1
EXIT_SKIPPED=2
EXIT_NO_RESULT=3
EXIT_STALE=4

RSYNC_SSH="ssh -p ${RUNNER_PORT} -o BatchMode=yes"
log() { printf '[fetch %s] %s\n' "$(date '+%F %T')" "$*"; }

# ── 1) 一次 ssh 取回全部远端证据 ────────────────────────────────────────────────
# 单次往返：避免「探到一半断线」导致判定基于残缺事实。heredoc 用引号定界（远端展开）。
probe="$(
  $RSYNC_SSH "$RUNNER_HOST" "OUT='$RUNNER_ROOT/gate_run' RUNNER_ROOT='$RUNNER_ROOT' bash -s" <<'REMOTE'
set -u
now=$(date +%s)
echo "NOW=$now"

ready="$OUT/RESULT_READY"
if [ -f "$ready" ]; then
  echo "READY_PRESENT=1"
  echo "READY_MTIME=$(stat -c %Y "$ready" 2>/dev/null || echo 0)"
  echo "READY_VALUE=$(head -1 "$ready" 2>/dev/null || echo '')"
else
  echo "READY_PRESENT=0"
fi

gate="$OUT/walkforward_gate.json"
if [ -f "$gate" ]; then
  echo "GATE_PRESENT=1"
  echo "GATE_MTIME=$(stat -c %Y "$gate" 2>/dev/null || echo 0)"
else
  echo "GATE_PRESENT=0"
fi

status="$OUT/walkforward_production_status.json"
if [ -f "$status" ]; then
  echo "STATUS_PRESENT=1"
  echo "STATUS_MTIME=$(stat -c %Y "$status" 2>/dev/null || echo 0)"
  echo "STATUS_VALUE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("status",""))' "$status" 2>/dev/null || echo unknown)"
  echo "STATUS_DETAIL=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("detail",""))' "$status" 2>/dev/null || echo '')"
else
  echo "STATUS_PRESENT=0"
fi

echo "SKIP_TAIL=$(tail -1 "$OUT/skip.log" 2>/dev/null || echo '')"
echo "SHA=$(cat "$RUNNER_ROOT/RELEASE_SHA" 2>/dev/null || echo unknown)"
REMOTE
)" || { log "❌ 无法连接 runner（${RUNNER_HOST}:${RUNNER_PORT}）"; exit "$EXIT_ENV"; }

field() { printf '%s\n' "$probe" | sed -n "s/^$1=//p" | head -1; }

NOW="$(field NOW)"
READY_PRESENT="$(field READY_PRESENT)"
READY_MTIME="$(field READY_MTIME)"
READY_VALUE="$(field READY_VALUE)"
GATE_PRESENT="$(field GATE_PRESENT)"
GATE_MTIME="$(field GATE_MTIME)"
STATUS_PRESENT="$(field STATUS_PRESENT)"
STATUS_MTIME="$(field STATUS_MTIME)"
STATUS_VALUE="$(field STATUS_VALUE)"
STATUS_DETAIL="$(field STATUS_DETAIL)"
SKIP_TAIL="$(field SKIP_TAIL)"
RUNNER_SHA="$(field SHA)"

NOW="${NOW:-0}"
READY_MTIME="${READY_MTIME:-0}"
GATE_MTIME="${GATE_MTIME:-0}"
STATUS_MTIME="${STATUS_MTIME:-0}"

age_hours() {  # $1 = mtime epoch；输出整数小时（负数归 0）
  local mtime="${1:-0}"
  [ "$mtime" -gt 0 ] 2>/dev/null || { echo ""; return; }
  local delta=$(( NOW - mtime ))
  [ "$delta" -lt 0 ] && delta=0
  echo $(( delta / 3600 ))
}

age_minutes() {  # $1 = mtime epoch；输出整数分钟
  local mtime="${1:-0}"
  [ "$mtime" -gt 0 ] 2>/dev/null || { echo ""; return; }
  local delta=$(( NOW - mtime ))
  [ "$delta" -lt 0 ] && delta=0
  echo $(( delta / 60 ))
}

# 包装器心跳默认 60s（run_production_walkforward_gate.py --heartbeat-seconds）。
# 这里只报告「多久没更新」这个事实，**不自己判定进程死活** —— 那是既有函数
# _running_status_is_stale() / repair_stale_running_status() 的口径，不许另算一套。
HEARTBEAT_GRACE_MIN="${HEARTBEAT_GRACE_MIN:-15}"

READY_AGE="$(age_hours "$READY_MTIME")"
GATE_AGE="$(age_hours "$GATE_MTIME")"
STATUS_AGE_MIN="$(age_minutes "$STATUS_MTIME")"

log "远端 ${RUNNER_HOST}:${RUNNER_ROOT}"
log "  runner 代码 SHA : ${RUNNER_SHA:-unknown}"
log "  RESULT_READY    : present=${READY_PRESENT} age=${READY_AGE:-—}h value=${READY_VALUE:-—}"
log "  gate sidecar    : present=${GATE_PRESENT} age=${GATE_AGE:-—}h"
log "  本轮 status     : ${STATUS_VALUE:-（无状态文件）} / ${STATUS_DETAIL:-—}（状态文件 ${STATUS_AGE_MIN:-—} 分钟未更新）"
[ -n "${SKIP_TAIL:-}" ] && log "  skip.log 末行   : ${SKIP_TAIL}"

# ── 2) 判定：RESULT_READY 是唯一新鲜度证据 ──────────────────────────────────────
verdict=""
exit_code=""
reason=""

is_fresh() {  # RESULT_READY 存在且年龄 ≤ MAX_AGE_HOURS
  [ "$READY_PRESENT" = "1" ] || return 1
  [ -n "$READY_AGE" ] || return 1
  [ "$READY_AGE" -le "$MAX_AGE_HOURS" ]
}

if [ "$GATE_PRESENT" = "1" ] && is_fresh; then
  verdict="FRESH"; exit_code="$EXIT_OK"
  reason="RESULT_READY 新鲜（${READY_AGE}h ≤ ${MAX_AGE_HOURS}h）且 gate sidecar 存在"
elif [ "$GATE_PRESENT" = "1" ]; then
  verdict="STALE"; exit_code="$EXIT_STALE"
  reason="有 gate 产物但 RESULT_READY present=${READY_PRESENT} age=${READY_AGE:-—}h 超过 ${MAX_AGE_HOURS}h —— 视为上一轮遗留，不当作本周结果"
else
  verdict="NO_RESULT"; exit_code="$EXIT_NO_RESULT"
  reason="runner 上没有 gate sidecar"
  case "${STATUS_VALUE:-}" in
    timeout|failed|error) reason="${reason}；本轮 status=${STATUS_VALUE}（${STATUS_DETAIL:-无 detail}）" ;;
    running|preparing_child|inspecting_coverage|blocked_*) reason="${reason}；本轮 status=${STATUS_VALUE}（仍在跑/被阻塞）" ;;
    "") : ;;
    *) reason="${reason}；本轮 status=${STATUS_VALUE}" ;;
  esac
  # 状态写着 running 但很久没更新 → 只提示「疑似」，判定交给既有 repair_stale_running_status()
  if [ "${STATUS_VALUE:-}" = "running" ] && [ -n "${STATUS_AGE_MIN:-}" ] \
     && [ "$STATUS_AGE_MIN" -gt "$HEARTBEAT_GRACE_MIN" ]; then
    reason="${reason}；⚠️ 状态文件已 ${STATUS_AGE_MIN} 分钟未更新（心跳 60s），疑似进程已死 —— 需跑 repair_stale_running_status() 复核，别当作「还在跑」"
  fi
  # 让位跳过：gate 与 status 都没更新，但 skip.log 有本轮记录
  if [ -n "${SKIP_TAIL:-}" ] && [ "$STATUS_PRESENT" != "1" ]; then
    verdict="SKIPPED"; exit_code="$EXIT_SKIPPED"
    reason="runner 因业务负载让位，本轮未跑：${SKIP_TAIL}"
  fi
fi

log "判定：${verdict} —— ${reason}"

# 落一份机器可读的判定结果（供监控/cron 消费，沿用 server_sync 的 result.env 约定）
write_result() {
  mkdir -p "$GATE_DIR"
  {
    echo "status=$1"
    echo "exit_code=$2"
    echo "verdict=$3"
    echo "reason=$4"
    echo "ready_age_hours=${READY_AGE:-}"
    echo "gate_age_hours=${GATE_AGE:-}"
    echo "runner_status=${STATUS_VALUE:-}"
    echo "status_age_minutes=${STATUS_AGE_MIN:-}"
    echo "runner_sha=${RUNNER_SHA:-unknown}"
    echo "max_age_hours=$MAX_AGE_HOURS"
    echo "checked_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  } > "$GATE_DIR/runner_fetch_result.env"
}

if [ "$DRY_RUN" = "1" ]; then
  log "DRY_RUN=1，未拉取（判定 exit_code=${exit_code}）"
  exit "$exit_code"
fi

# ── 3) 执行拉取 ────────────────────────────────────────────────────────────────
# 状态文件**无论判定如何都回传**：它是 prod 区分「超时 / 没跑 / 成功」的唯一证据。
mkdir -p "$GATE_DIR"
if [ "$STATUS_PRESENT" = "1" ]; then
  rsync -a -e "$RSYNC_SSH" \
    "$RUNNER_HOST:$RUNNER_ROOT/gate_run/walkforward_production_status.json" \
    "$GATE_DIR/runner.walkforward_production_status.json"
  log "已拉取状态文件 → $GATE_DIR/runner.walkforward_production_status.json"
fi

if [ "$verdict" = "FRESH" ]; then
  for f in report.md walkforward_gate.json gate_summary.md; do
    if $RSYNC_SSH "$RUNNER_HOST" "test -f '$RUNNER_ROOT/gate_run/$f'"; then
      rsync -a -e "$RSYNC_SSH" "$RUNNER_HOST:$RUNNER_ROOT/gate_run/$f" "$GATE_DIR/runner.$f"
      log "已拉取 → $GATE_DIR/runner.$f"
    else
      log "⚠️ 远端缺 ${f}（gate 未产出该文件）"
    fi
  done

  if [ -f "$GATE_DIR/runner.walkforward_gate.json" ] && [ -f "$GATE_DIR/extract_gate_summary.py" ]; then
    /opt/aqsp-vibe-venv/bin/python "$GATE_DIR/extract_gate_summary.py" \
      --gate "$GATE_DIR/runner.walkforward_gate.json" \
      --report "$GATE_DIR/runner.report.md" \
      --output "$GATE_DIR/runner_summary.md" || log "摘要生成失败（可忽略）"
    [ -f "$GATE_DIR/runner_summary.md" ] && log "摘要：$GATE_DIR/runner_summary.md"
  fi

  # ── 3b) 提升：把拉回的 sidecar 变成 prod 生效的那一份 ──────────────────────────
  # 三段式的最后一环此前是**手工**的：这里只落 runner.* 前缀，等人工改名。但 prod 侧
  # 早已没有门禁（0 22 * * 6 已注释），**没有任何任务会写** $GATE_TARGET，于是 runner
  # 每周算出的判定**永远不会生效**，通知门禁一直读着上一次人工留下的旧 sidecar
  # （且那份是 stable/5 变体，结构上永远过不了 MIN_CSCV_VARIANTS=8）。
  #
  # 提升是**有约束的**，不是「未经验证就悄悄生效」：结构校验（新鲜度 / 字段 /
  # held-out / 窗口一致性）通过才提升，且旧文件归档到 gate_run/archived/ 可回滚。
  # **判定未过门不算失败** —— 门禁本来就该 fail，提升的是「一份真实完整的判定」。
  # 详见 scripts/promote_gate_sidecar.py。AQSP_FETCH_PROMOTE=0 可关闭。
  if [ "$PROMOTE_ENABLED" = "1" ] && [ -f "$GATE_DIR/runner.walkforward_gate.json" ]; then
    LOCAL_RELEASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    set +e
    PYTHONPATH="$LOCAL_RELEASE/src:$LOCAL_RELEASE" "$PROMOTE_PY" \
      "$LOCAL_RELEASE/scripts/promote_gate_sidecar.py" \
      --fetched "$GATE_DIR/runner.walkforward_gate.json" \
      --target "$GATE_TARGET" \
      --archive-dir "$GATE_DIR/archived" \
      --max-age-days "$GATE_MAX_AGE_DAYS" > "$GATE_DIR/promote_result.txt" 2>&1
    promote_rc=$?
    set -e
    log "提升 sidecar: $(cat "$GATE_DIR/promote_result.txt" 2>/dev/null || echo '(无输出)')"
    if [ "$promote_rc" -ne 0 ]; then
      log "⚠️ 未提升（rc=${promote_rc}）—— 生效的仍是上一份 sidecar；可人工核对后把 runner.walkforward_gate.json 覆盖到 $GATE_TARGET"
    fi
  fi
else
  # 陈旧/无产物：隔离留证，绝不落成 runner.* 以免被误读为本周结果
  quarantine="$GATE_DIR/runner.stale/$(date -u '+%Y%m%dT%H%M%SZ')"
  mkdir -p "$quarantine"
  for f in report.md walkforward_gate.json gate_summary.md; do
    if $RSYNC_SSH "$RUNNER_HOST" "test -f '$RUNNER_ROOT/gate_run/$f'"; then
      rsync -a -e "$RSYNC_SSH" "$RUNNER_HOST:$RUNNER_ROOT/gate_run/$f" "$quarantine/$f" || true
      log "已隔离陈旧 ${f} → ${quarantine}/${f}"
    fi
  done
  printf '%s\n' "$reason" > "$quarantine/REASON.txt"
  log "⚠️ 本轮无新鲜结果，产物已隔离到 ${quarantine}（不落 runner.* 前缀）"
fi

case "$exit_code" in
  "$EXIT_OK")        write_result ok      "$EXIT_OK"        "$verdict" "$reason" ;;
  "$EXIT_SKIPPED")   write_result skipped "$EXIT_SKIPPED"   "$verdict" "$reason" ;;
  "$EXIT_NO_RESULT") write_result missing "$EXIT_NO_RESULT" "$verdict" "$reason" ;;
  *)                 write_result stale   "$EXIT_STALE"     "$verdict" "$reason" ;;
esac

log "完成（exit_code=${exit_code}）。runner.* 为留证副本；sidecar 是否已生效见上面的「提升 sidecar」一行。"
exit "$exit_code"
