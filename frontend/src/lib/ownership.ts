// 所有权联动：把「系统线的候选」和「我的线的持仓/自选」对上。
//
// 为什么需要：选股时最容易浪费时间的地方，是重新研究一只**你本来就持有/关注**的票。
// 系统线与我的线此前是两套互不相干的信息，这里把它们接起来。
//
// 纯逻辑（不碰 DOM / 不发请求），数据获取在 components/aqsp/useOwnership.ts。
export interface OwnershipSets {
  holdings: ReadonlySet<string>;
  watchlist: ReadonlySet<string>;
}

export type OwnedKind = "holding" | "watchlist" | null;

export const EMPTY_OWNERSHIP: OwnershipSets = {
  holdings: new Set<string>(),
  watchlist: new Set<string>(),
};

/** 持仓优先于自选：真金白银比"关注"更值得在列表里先看到。 */
export function ownedKind(code: string, own: OwnershipSets): OwnedKind {
  if (own.holdings.has(code)) return "holding";
  if (own.watchlist.has(code)) return "watchlist";
  return null;
}

export interface OwnedSummary {
  holding: number;
  watchlist: number;
  /** 既没持有也没关注 —— 选股时通常最想先看的"新面孔" */
  fresh: number;
}

export function summarizeOwned(
  codes: readonly string[],
  own: OwnershipSets,
): OwnedSummary {
  let holding = 0;
  let watchlist = 0;
  let fresh = 0;
  for (const code of codes) {
    const kind = ownedKind(code, own);
    if (kind === "holding") holding += 1;
    else if (kind === "watchlist") watchlist += 1;
    else fresh += 1;
  }
  return { holding, watchlist, fresh };
}

/** 候选里有多少是"已经持有的" —— 用于提醒用户别重复研究。 */
export function alreadyHeldCount(codes: readonly string[], own: OwnershipSets): number {
  return codes.filter((code) => own.holdings.has(code)).length;
}
