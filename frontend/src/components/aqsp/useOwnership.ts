// 所有权（持仓 / 自选）读取。
//
// 把「我的线」的数据接到「系统线」的候选上，让选股时一眼看出
// 哪些是**已经持有 / 已经关注**的，避免重复研究。
//
// 持仓读不到（后端未启动 / 未录入）时只降级为自选，**不影响选股本身**。
import { useEffect, useState } from "react";
import { api, type Holding } from "@/lib/api";
import { loadWatch, WATCHLIST_CHANGED_EVENT, WATCHLIST_STORAGE_KEY } from "@/lib/watchlist";
import { UNKNOWN_OWNERSHIP, ownershipFromCodes, type OwnershipSets } from "@/lib/ownership";
import { PORTFOLIO_CHANGED_EVENT } from "@/lib/operation";

export function useOwnership(ownedHoldings?: readonly Holding[] | null): OwnershipSets {
  const [sets, setSets] = useState<OwnershipSets>(UNKNOWN_OWNERSHIP);

  useEffect(() => {
    let alive = true;
    const syncWatchlist = () => {
      setSets((prev) => ({ ...prev, watchlist: new Set(loadWatch()) }));
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === WATCHLIST_STORAGE_KEY || event.key === null) syncWatchlist();
    };
    syncWatchlist();
    window.addEventListener(WATCHLIST_CHANGED_EVENT, syncWatchlist);
    window.addEventListener("storage", onStorage);

    const syncHoldings = () => {
      if (ownedHoldings !== undefined) {
        setSets(ownershipFromCodes(ownedHoldings === null ? null : ownedHoldings.filter((row) => row.shares > 0).map((row) => row.code), new Set(loadWatch())));
        return;
      }
      void api
      .portfolio()
      .then((data) => {
        if (!alive) return;
        setSets(ownershipFromCodes(data.holdings.filter((row) => row.shares > 0).map((row) => row.code), new Set(loadWatch())));
      })
      .catch(() => {
        if (alive) setSets(ownershipFromCodes(null, new Set(loadWatch())));
      }); };
    syncHoldings();
    window.addEventListener(PORTFOLIO_CHANGED_EVENT, syncHoldings);

    return () => {
      alive = false;
      window.removeEventListener(WATCHLIST_CHANGED_EVENT, syncWatchlist);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(PORTFOLIO_CHANGED_EVENT, syncHoldings);
    };
  }, [ownedHoldings]);

  return ownedHoldings === undefined ? sets : ownershipFromCodes(
    ownedHoldings === null ? null : ownedHoldings.filter((row) => row.shares > 0).map((row) => row.code), sets.watchlist,
  );
}
