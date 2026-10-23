import { ALERTS_CHANGED_EVENT } from "./operation";

export const AQSP_LIVE_REFRESH_INTERVAL_MS = 60_000;

export interface AlertRefreshSource {
  visible: () => boolean;
  interval: (listener: () => void, ms: number) => () => void;
  listen: (event: "focus" | "visibilitychange" | typeof ALERTS_CHANGED_EVENT, listener: () => void) => () => void;
}

/** 可见页只读取提醒；恢复焦点/手动刷新立即同步，不触发检查或外发通知。 */
export function subscribeAlertRefresh(refresh: () => void, source: AlertRefreshSource): () => void {
  const read = () => { if (source.visible()) refresh(); };
  const cleanup = [
    source.interval(read, AQSP_LIVE_REFRESH_INTERVAL_MS),
    ...(["focus", "visibilitychange", ALERTS_CHANGED_EVENT] as const).map((event) => source.listen(event, read)),
  ];
  read();
  return () => cleanup.forEach((stop) => stop());
}

export function browserAlertRefreshSource(): AlertRefreshSource {
  return {
    visible: () => document.visibilityState !== "hidden",
    interval: (listener, ms) => { const timer = window.setInterval(listener, ms); return () => window.clearInterval(timer); },
    listen: (event, listener) => {
      const target = event === "visibilitychange" ? document : window;
      target.addEventListener(event, listener);
      return () => target.removeEventListener(event, listener);
    },
  };
}
