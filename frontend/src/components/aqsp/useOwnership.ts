// 所有权（持仓 / 自选）读取。
//
// 把「我的线」的数据接到「系统线」的候选上，让选股时一眼看出
// 哪些是**已经持有 / 已经关注**的，避免重复研究。
//
// 持仓读不到（后端未启动 / 未录入）时只降级为自选，**不影响选股本身**。
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { loadWatch, WATCHLIST_CHANGED_EVENT, WATCHLIST_STORAGE_KEY } from "@/lib/watchlist";
import { EMPTY_OWNERSHIP, type OwnershipSets } from "@/lib/ownership";
import { PORTFOLIO_CHANGED_EVENT } from "@/lib/operation";

export function useOwnership(): OwnershipSets {
  const [sets, setSets] = useState<OwnershipSets>(EMPTY_OWNERSHIP);

  useEffect(() => {
    let alive = true;
    const syncWatchlist = () => {
      setSets((prev) => ({ holdings: prev.holdings, watchlist: new Set(loadWatch()) }));
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === WATCHLIST_STORAGE_KEY || event.key === null) syncWatchlist();
    };
    syncWatchlist();
    window.addEventListener(WATCHLIST_CHANGED_EVENT, syncWatchlist);
    window.addEventListener("storage", onStorage);

    const syncHoldings = () => { void api
      .portfolio()
      .then((data) => {
        if (!alive) return;
        setSets({
          holdings: new Set(data.holdings.map((row) => row.code)),
          // 重新读一次：期间用户可能刚改过自选
          watchlist: new Set(loadWatch()),
        });
      })
      .catch(() => {
        /* 持仓不可用时保留自选即可，不打扰选股 */
      }); };
    syncHoldings();
    window.addEventListener(PORTFOLIO_CHANGED_EVENT, syncHoldings);

    return () => {
      alive = false;
      window.removeEventListener(WATCHLIST_CHANGED_EVENT, syncWatchlist);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(PORTFOLIO_CHANGED_EVENT, syncHoldings);
    };
  }, []);

  return sets;
}
