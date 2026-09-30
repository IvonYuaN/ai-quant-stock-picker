// 双窗因子 IC 判决展示模型（lib/dual-verdict-view）的契约断言。
//
// 钉住的铁律（方案 B §六 + 09-28 判决同型）：
//   1. 状态归类优先级 hit > pending > miss：本窗 hit ⇒ 即使 sign_match 也判 hit；
//      未 hit 但 sign_match（方向稳待功效）⇒ pending；两窗翻向 ⇒ miss。
//   2. 计数：hitCount + pendingCount + missCount = total，三态互斥且无遗漏。
//   3. 排序：达标优先 → 观察 → 翻向；同组内 streak 降序、再按名。
//   4. fail-soft：null / available=false / latest=null ⇒ 全降级空视图（total=0、
//      available=false），绝不抛错、绝不臆造因子。
//   5. 红线：本层纯展示，不产生任何「写回打分/排序/下单」副作用。
import { dualFactorStatus, dualVerdictSummary } from "./dual-verdict-view";
import type { IcDualFactorVerdict, IcDualVerdictPayload } from "@/lib/api";

function mv(hit: boolean, sign_match: boolean, dual: boolean): IcDualFactorVerdict {
  return {
    mean_a: 0,
    t_a: 0,
    n_a: 73,
    mean_b: 0,
    t_b: 0,
    n_b: 73,
    sign_match,
    dual_significant: dual,
    hit,
  };
}

function payload(
  factors: Record<string, IcDualFactorVerdict>,
  streaks: Record<string, number>,
  opts?: Partial<NonNullable<IcDualVerdictPayload["latest"]>> & {
    available?: boolean;
    stale?: boolean;
  },
): IcDualVerdictPayload {
  const { stale, ...latestOpts } = opts ?? {};
  return {
    available: opts?.available ?? true,
    latest: {
      run_at: "2026-09-30T02:00:00Z",
      as_of_a: "2026-05-25",
      as_of_b: "2026-09-24",
      window_days: 365,
      step: 5,
      n_sections_a: 73,
      n_sections_b: 73,
      factors,
      hits: Object.entries(factors)
        .filter(([, v]) => v.hit)
        .map(([k]) => k),
      streaks,
      event: null,
      next_candidate: null,
      ...latestOpts,
    } as NonNullable<IcDualVerdictPayload["latest"]>,
    streak_n: 5,
    ...(stale === undefined ? {} : { stale }),
  };
}

/* ---- 1. 状态归类优先级 ---- */
const statusHitWinsOverPending: boolean =
  dualFactorStatus(mv(true, true, true)) === "hit";
const statusPendingWhenSignOnly: boolean =
  dualFactorStatus(mv(false, true, false)) === "pending";
const statusMissWhenFlipped: boolean = dualFactorStatus(mv(false, false, false)) === "miss";

/* ---- 2. 计数三态互斥无遗漏 ---- */
const p2 = payload(
  {
    htf: mv(true, true, true), // hit
    momentum: mv(false, true, false), // pending
    rps: mv(false, false, false), // miss
  },
  { htf: 3, momentum: 1, rps: 0 },
);
const v2 = dualVerdictSummary(p2);
const countsSumToTotal: boolean =
  v2.hitCount + v2.pendingCount + v2.missCount === v2.total;
const hitCountIs1: boolean = v2.hitCount === 1;
const pendingCountIs1: boolean = v2.pendingCount === 1;
const missCountIs1: boolean = v2.missCount === 1;
const totalCountIs3: boolean = v2.total === 3;

/* ---- 3. 排序：hit 优先 → pending → miss，同组 streak 降序 → 名 ---- */
const p3 = payload(
  {
    a_hit_hi: mv(true, true, true),
    b_hit_lo: mv(true, true, true),
    c_pend_hi: mv(false, true, false),
    d_pend_lo: mv(false, true, false),
    e_miss: mv(false, false, false),
  },
  { a_hit_hi: 4, b_hit_lo: 2, c_pend_hi: 4, d_pend_lo: 1, e_miss: 0 },
);
const order3 = dualVerdictSummary(p3).factors.map((f) => f.name);
const hitGroupSortedByStreakDesc: boolean =
  order3[0] === "a_hit_hi" && order3[1] === "b_hit_lo";
const pendingGroupSortedByStreakDesc: boolean =
  order3[2] === "c_pend_hi" && order3[3] === "d_pend_lo";
const missTrailing: boolean = order3[4] === "e_miss";
const overallOrder: string[] = [...order3];
const orderIsCorrect: boolean =
  overallOrder.join(",") ===
  "a_hit_hi,b_hit_lo,c_pend_hi,d_pend_lo,e_miss";

/* ---- 4. fail-soft 降级 ---- */
const nullPayload: boolean = dualVerdictSummary(null).total === 0;
const unavailablePayload: boolean = dualVerdictSummary(payload({}, {}, { available: false }))
  .available === false;
const unavailableTotalZero: boolean =
  dualVerdictSummary(payload({}, {}, { available: false })).total === 0;
const eventPassthrough: boolean =
  dualVerdictSummary(
    payload({ htf: mv(true, true, true) }, { htf: 5 }, { event: "revisit_family", next_candidate: "htf" }),
  ).event === "revisit_family";
const nextCandidatePassthrough: boolean =
  dualVerdictSummary(
    payload({ htf: mv(false, true, false) }, { htf: 4 }, { next_candidate: "htf" }),
  ).next_candidate === "htf";

/* ---- 5. 新鲜度护栏 stale 透传（M5：端点把陈旧可用产物抑制成 available=false + stale=true） ---- */
// stale=true ⇒ 即便 latest 有内容，视图也判 unavailable + stale（区别真「暂无数据」）。
const staleSuppressed = dualVerdictSummary(
  payload({ htf: mv(true, true, true) }, { htf: 5 }, { available: false, stale: true }),
);
const staleFlagIsTrue: boolean = staleSuppressed.stale === true;
const staleStillUnavailable: boolean = staleSuppressed.available === false;
// 真「暂无数据」：available=false 但 stale 缺省 ⇒ stale=false（不糊弄文案）。
const noDataFlagFalse: boolean =
  dualVerdictSummary(payload({}, {}, { available: false })).stale === false;
// available=true ⇒ stale 必 false（新鲜时不标陈旧）。
const freshFlagFalse: boolean =
  dualVerdictSummary(payload({ htf: mv(true, true, true) }, { htf: 5 })).stale === false;

export const dualVerdictContract = {
  /* 1. 状态归类 */
  statusHitWinsOverPending,
  statusPendingWhenSignOnly,
  statusMissWhenFlipped,
  /* 2. 计数 */
  countsSumToTotal,
  hitCountIs1,
  pendingCountIs1,
  missCountIs1,
  totalCountIs3,
  /* 3. 排序 */
  hitGroupSortedByStreakDesc,
  pendingGroupSortedByStreakDesc,
  missTrailing,
  orderIsCorrect,
  /* 4. fail-soft */
  nullPayload,
  unavailablePayload,
  unavailableTotalZero,
  eventPassthrough,
  nextCandidatePassthrough,
  /* 5. 新鲜度护栏 stale 透传 */
  staleFlagIsTrue,
  staleStillUnavailable,
  noDataFlagFalse,
  freshFlagFalse,
};

// 供调试直读（run-contracts 递归收集布尔）
export const dualVerdictDebug = { order3, orderIsCorrect };
