#!/bin/bash
# AQSP concept_board Mac 家宽收盘兜底（#4 方案，待老大拍板后才允许 install）
#
# 背景：东财概念板块（push2.eastmoney.com）对 runner/prod 海外/机房 IP 间歇 502，
# 唯一稳定 200 通路 = Mac 家宽（09-27 坐实 + 09-28 中继 4/4 全败实证）。
# 本任务：Mac 工作日 15:10（收盘后板块数据定型）本地抓取 concept_board.csv，
# sanity 过线且与 prod 现存文件不同 ⇒ scp 推 prod pit_cache（best-effort 补漏，
# 与 prod 08:20 event-data 主链互补：主链成功则 Mac 端推的是同值/被跳过，无冲突）。
#
# 红线自守卫：
#   - 只写 prod `/opt/aqsp/data/pit_cache/concept_board.csv` 这一个文件（EventCalendar 数据源），
#     不动打分/排序/下单、不动 cron/yaml/权重、不装包；
#   - 推送前 ssh 一次只读压力复核（prod load1 ≤ 3、available ≥ 400M），不过线 = 放弃本轮（次日再兜）；
#   - scp 后必核（远端 wc -l + md5 与本地逐位一致），防「scp 静默 no-op」老坑。
set -euo pipefail

export LANG="en_US.UTF-8"
export TZ="Asia/Shanghai"

PROJECT_ROOT="/Users/ivon/Documents/AI量化选股"
PY="${PROJECT_ROOT}/.venv/bin/python"
LOCAL_CACHE_DIR="${PROJECT_ROOT}/data/pit_cache"
CSV="${LOCAL_CACHE_DIR}/concept_board.csv"
LOG_DIR="${PROJECT_ROOT}/logs/launchd"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/concept-board-fallback-$(date +%Y-%m-%d).log"

PROD_HOST="aqsp-server"                       # ~/.ssh/config：root@8.130.124.238
PROD_CSV="/opt/aqsp/data/pit_cache/concept_board.csv"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# ---- 0) 非工作日不推（launchd 已限工作日，双保险）----
case "$(date +%u)" in
  6|7) log "周末，跳过"; exit 0 ;;
esac

# ---- 1) Mac 本地抓取（.venv 有 aqsp 全套依赖；AQSP_RUNTIME_DATA_ROOT 指 repo data/）
# 注意：fetch_concept_board.py 无 CLI 参数（load(force=True) 写死、不解析 argv）；
# 缓存写 AQSP_RUNTIME_DATA_ROOT/pit_cache/concept_board.csv ----
export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}"
export AQSP_RUNTIME_DATA_ROOT="${PROJECT_ROOT}/data"
cd "$PROJECT_ROOT"
log "开始 Mac 本地抓取 concept_board（东财家宽通路）"
"$PY" scripts/data/fetch_concept_board.py >>"$LOG_FILE" 2>&1 || {
  log "本地抓取失败（网络/解析），本轮放弃，次日再兜"; exit 0; }

# ---- 2) sanity：非空 + 行数下限（东财概念板块 500+，保守 100 行）----
if [ ! -s "$CSV" ]; then log "CSV 为空，放弃推送"; exit 0; fi
ROWS=$(wc -l < "$CSV" | tr -d ' ')
if [ "$ROWS" -lt 100 ]; then log "CSV 行数 ${ROWS} < 100，疑似坏数据，放弃推送"; exit 0; fi

LOCAL_MD5=$(md5 -q "$CSV")

# ---- 3) prod 压力复核（只读一次，不过线即放弃本轮）----
PRESSURE=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$PROD_HOST" \
  'awk "{print \$1}" /proc/loadavg; free -m | awk "NR==2{print \$7}"' 2>/dev/null || true)
LOAD1=$(echo "$PRESSURE" | sed -n '1p' || true)
AVAIL=$(echo "$PRESSURE" | sed -n '2p' || true)
log "prod 压力：load1=${LOAD1:-?} available=${AVAIL:-?}M"
if ! awk -v l="${LOAD1:-99}" 'BEGIN{exit !(l<=3)}'; then
  log "prod load1=${LOAD1} 超限(≤3)，本轮放弃（次日再兜），不循环探活"; exit 0; fi
if ! awk -v m="${AVAIL:-0}" 'BEGIN{exit !(m>=400)}'; then
  log "prod available=${AVAIL}M 低于 400M，本轮放弃"; exit 0; fi

# ---- 4) 与 prod 现存同值 ⇒ 跳过（东财无新数据 / 主链已成功 的典型形态）----
REMOTE_MD5=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$PROD_HOST" \
  "md5sum ${PROD_CSV} 2>/dev/null | awk '{print \$1}'" 2>/dev/null || true)
if [ "${LOCAL_MD5}" = "${REMOTE_MD5}" ] && [ -n "${REMOTE_MD5}" ]; then
  log "与 prod 现存同值（md5 ${LOCAL_MD5:0:8}…），无需推送"; exit 0; fi

# ---- 5) 推送 + 必核（防 scp 静默 no-op）----
log "推送 concept_board.csv（${ROWS} 行，md5 ${LOCAL_MD5:0:8}…）→ prod"
scp -q "$CSV" "${PROD_HOST}:${PROD_CSV}.mac-fallback.tmp"
ssh -o BatchMode=yes "$PROD_HOST" \
  "mv ${PROD_CSV}.mac-fallback.tmp ${PROD_CSV} && chown root:root ${PROD_CSV} 2>/dev/null || true; md5sum ${PROD_CSV} | awk '{print \$1}'; wc -l < ${PROD_CSV}" > /tmp/aqsp_mac_push_check.$$
VERIFY=$(cat /tmp/aqsp_mac_push_check.$$; rm -f /tmp/aqsp_mac_push_check.$$)
VERIFY_MD5=$(echo "$VERIFY" | sed -n '1p')
VERIFY_ROWS=$(echo "$VERIFY" | sed -n '2p' | tr -d ' ')
if [ "${VERIFY_MD5}" != "${LOCAL_MD5}" ] || [ "${VERIFY_ROWS}" != "${ROWS}" ]; then
  log "推送校验失败（md5=${VERIFY_MD5:-?} rows=${VERIFY_ROWS:-?}），文件已回滚前状态请人工看"; exit 1; fi
log "推送成功并已核：prod ${PROD_CSV}（${VERIFY_ROWS} 行，md5 ${VERIFY_MD5:0:8}…）"
exit 0
