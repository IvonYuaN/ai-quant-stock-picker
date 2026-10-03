#!/bin/bash
# AQSP concept_board Mac 兜底 —— 安装 / 卸载 / 状态（#4）
# 用法:
#   bash install_concept_board_mac_fallback.sh check      # 只预检不装
#   bash install_concept_board_mac_fallback.sh install    # 安装并加载 launchd (需老大拍板后执行)
#   bash install_concept_board_mac_fallback.sh uninstall  # 卸载并清除
#   bash install_concept_board_mac_fallback.sh run-once   # 立即跑一次 wrapper
set -euo pipefail

DIR="/Users/ivon/Documents/AI量化选股/scripts/launchd/concept_board_mac_fallback"
WRAPPER="${DIR}/aqsp_concept_board_mac_fallback.sh"
PLIST_SRC="${DIR}/com.aqsp.concept-board-mac-fallback.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/com.aqsp.concept-board-mac-fallback.plist"
LABEL="com.aqsp.concept-board-mac-fallback"
MODE="${1:-check}"

precheck() {
  local ok=0
  if [ -x "$WRAPPER" ]; then
    echo "  [OK] wrapper executable"
  else
    echo "  [FAIL] wrapper not executable"
    ok=1
  fi
  if [ -f "$PLIST_SRC" ]; then
    echo "  [OK] plist source present"
  else
    echo "  [FAIL] plist source missing"
    ok=1
  fi
  if /usr/libexec/PlistBuddy -c "Print" "$PLIST_SRC" >/dev/null 2>&1; then
    echo "  [OK] plist syntax valid"
  else
    echo "  [FAIL] plist syntax invalid"
    ok=1
  fi
  if ssh -o BatchMode=yes -o ConnectTimeout=10 aqsp-server 'true' 2>/dev/null; then
    echo "  [OK] aqsp-server SSH reachable"
  else
    echo "  [WARN] aqsp-server SSH unreachable (fallback script will skip push, install not blocked)"
  fi
  return $ok
}

case "$MODE" in
  check)
    echo "== precheck =="
    precheck
    ;;
  install)
    echo "== precheck =="
    precheck
    mkdir -p "$HOME/Library/LaunchAgents"
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load "$PLIST_DST"
    echo "installed and loaded $LABEL (weekdays 15:10 Beijing)."
    echo "to verify the full chain once right now: bash $WRAPPER"
    echo "to uninstall: bash $0 uninstall"
    ;;
  uninstall)
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "unloaded and removed $LABEL (wrapper and logs kept in repo)"
    ;;
  run-once)
    bash "$WRAPPER"
    ;;
  *)
    echo "usage: bash $0 {check|install|uninstall|run-once}"
    exit 2
    ;;
esac
