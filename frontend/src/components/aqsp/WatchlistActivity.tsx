import { useEffect, useState } from "react";
import { api, type WatchlistRemovalRecord } from "@/lib/api";
import { loadWatchRemovals, WATCHLIST_CHANGED_EVENT, WATCHLIST_STORAGE_KEY } from "@/lib/watchlist";

function removalKey(record: WatchlistRemovalRecord): string {
  if (record.operation_id) return `operation:${record.operation_id}`;
  return `legacy:${record.removed_at}:${record.codes.join(",")}`;
}

function mergeRemovals(local: readonly WatchlistRemovalRecord[], remote: readonly WatchlistRemovalRecord[]): WatchlistRemovalRecord[] {
  const seen = new Set<string>();
  return [...local, ...remote]
    .filter((record) => {
      const key = removalKey(record);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((left, right) => Date.parse(right.removed_at) - Date.parse(left.removed_at))
    .slice(0, 20);
}

export function WatchlistActivity() {
  const [removals, setRemovals] = useState<WatchlistRemovalRecord[]>(loadWatchRemovals);
  useEffect(() => {
    let refreshVersion = 0;
    const refresh = () => {
      const version = ++refreshVersion;
      const local = loadWatchRemovals();
      setRemovals(local);
      void api.watchlistRemovals().then((remote) => {
        if (version === refreshVersion) setRemovals(mergeRemovals(local, remote));
      }).catch(() => {
        // The browser journal remains useful when the API is offline.
      });
    };
    refresh();
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
    <p className="aq-note">移出记录来自本机与当前浏览器身份的服务端审计；持仓风险规则仍按持仓跟进。</p>
  </details>;
}
