#!/usr/bin/env bash
# Shared scan window, Beijing time: 09:35-11:30 / 13:05-14:57 inclusive.
# The panel checks before acquiring the main lock; the worker checks again
# after waiting/syncing. Trading-day guards and the midday bridge remain separate.

aqsp_intraday_window_open() {
    local require_hours now_hm
    require_hours="$(printf '%s' "${AQSP_INTRADAY_REQUIRE_MARKET_HOURS:-true}" | tr '[:upper:]' '[:lower:]')"
    case "$require_hours" in
        1|true|yes|on) ;;
        *) return 0 ;; # Preserve the existing explicit diagnostic override.
    esac
    now_hm="${1:-$(date +%H%M)}"
    now_hm=$((10#$now_hm))
    { [ "$now_hm" -ge 935 ] && [ "$now_hm" -le 1130 ]; } ||
        { [ "$now_hm" -ge 1305 ] && [ "$now_hm" -le 1457 ]; }
}
