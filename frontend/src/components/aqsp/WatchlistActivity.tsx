import { useEffect, useState } from "react";
import { loadWatchRemovals, WATCHLIST_CHANGED_EVENT, WATCHLIST_STORAGE_KEY } from "@/lib/watchlist";

export function WatchlistActivity() {
  const [removals, setRemovals] = useState(loadWatchRemovals);
  useEffect(() => {
    const refresh = () => setRemovals(loadWatchRemovals());
    const onStorage = (event: StorageEvent) => {
      if (event.key === WATCHLIST_STORAGE_KEY || event.key === null) refresh();
    };
    window.addEventListener(WATCHLIST_CHANGED_EVENT, refresh);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(WATCHLIST_CHANGED_EVENT, refresh);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  if (!removals.length) return null;
  return <details className="aq-alert-history" aria-label="自选移除记录">
    <summary>自选移除记录（最近 {removals.length} 次）</summary>
    {removals.map((record, index) => <p key={`${record.removed_at}:${index}`}>
      <time dateTime={record.removed_at}>{new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(record.removed_at))}</time>
      {` · ${record.codes.length} 只自选已移出监控范围 · ${record.codes.join("、")}`}
    </p>)}
    <p className="aq-note">本机自选操作记录；持仓风险规则仍按持仓跟进。</p>
  </details>;
}
