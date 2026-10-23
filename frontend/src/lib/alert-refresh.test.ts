import { AQSP_LIVE_REFRESH_INTERVAL_MS, subscribeAlertRefresh, type AlertRefreshSource } from "./alert-refresh";
import { ALERTS_CHANGED_EVENT } from "./operation";

let visible = true;
let reads = 0;
let interval = 0;
const listeners = new Map<string, () => void>();
const source: AlertRefreshSource = {
  visible: () => visible,
  interval: (fn, ms) => { interval = ms; listeners.set("tick", fn); return () => { listeners.delete("tick"); }; },
  listen: (event, fn) => { listeners.set(event, fn); return () => { listeners.delete(event); }; },
};
const stop = subscribeAlertRefresh(() => { reads++; }, source);
const initial = reads;
visible = false;
listeners.get("tick")?.(); listeners.get("focus")?.();
const hidden = reads;
visible = true;
listeners.get("visibilitychange")?.(); listeners.get(ALERTS_CHANGED_EVENT)?.(); listeners.get("tick")?.();
const refreshed = reads;
stop();

export const alertRefreshContract = {
  readsInitially: initial === 1,
  reusesVisibleRefreshInterval: interval === AQSP_LIVE_REFRESH_INTERVAL_MS,
  hiddenPageDoesNotRead: hidden === initial,
  visibleChangesAndTickRead: refreshed === initial + 3,
  removesAllListeners: listeners.size === 0,
};
