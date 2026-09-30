// 双窗因子 IC 滚动判决的展示模型（proposal-only 监控面）。
//
// 把后端 /aqsp/ic-dual-verdict 的原始 payload 归一成「一行状态条」可读的视图：
//   达标 X 因子 · 观察 Y 因子（方向稳待功效）· 翻向 Z 因子，
//   连续 N 日达标事件 + next_candidate。
//
// 语义钉死（方案 B §六 + 09-28 判决同型）：
//   - hit      = 本窗判决 sign_match && dual_significant（本数据日通过）
//   - pending  = 方向一致(sign_match)但双 |t| 未双达标（=09-28「方向7/7一致但无一达标」态，
//               这是最值得盯的「快达标」观察态）
//   - miss     = 两窗 IC 翻向(!sign_match)
//   - streak   = 连续达标数据日数（as_of_b 去重计数，静态期同 as_of 只算 1 日）
// 🔴 红线：纯展示，本层绝不写回打分/排序/下单、不触发任何参数变更。

import type {
  IcDualFactorVerdict,
  IcDualVerdictPayload,
} from "@/lib/api";

export type DualFactorStatus = "hit" | "pending" | "miss";

export interface DualFactorView {
  name: string;
  status: DualFactorStatus;
  sign_match: boolean;
  dual_significant: boolean;
  hit: boolean;
  streak: number;
  t_a: number | null;
  t_b: number | null;
}

export interface DualVerdictView {
  available: boolean;
  streak_n: number;
  as_of_a: string;
  as_of_b: string;
  event: string | null;
  next_candidate: string | null;
  factors: DualFactorView[];
  hitCount: number;
  pendingCount: number;
  missCount: number;
  total: number;
}

/** 单因子状态归类（纯函数）：hit > pending > miss。 */
export function dualFactorStatus(f: IcDualFactorVerdict): DualFactorStatus {
  if (f.hit) return "hit";
  if (f.sign_match) return "pending";
  return "miss";
}

/**
 * 归一化整份双窗 payload → 视图（纯函数，null / !available / latest 缺均安全降级）。
 * factors 排序：达标(hit) 优先 → 观察(pending) → 翻向(miss)，同组内 streak 降序、再按名。
 */
export function dualVerdictSummary(
  payload: IcDualVerdictPayload | null,
): DualVerdictView {
  const streak_n = payload?.streak_n ?? 5;
  const latest = payload?.latest ?? null;
  const available = payload?.available === true && latest !== null;

  const factors: DualFactorView[] = [];
  let hitCount = 0;
  let pendingCount = 0;
  let missCount = 0;

  if (available && latest) {
    const entries = Object.entries(latest.factors ?? {});
    for (const [name, f] of entries) {
      const status = dualFactorStatus(f);
      factors.push({
        name,
        status,
        sign_match: !!f?.sign_match,
        dual_significant: !!f?.dual_significant,
        hit: !!f?.hit,
        streak: Number(latest.streaks?.[name] ?? 0),
        t_a: f?.t_a ?? null,
        t_b: f?.t_b ?? null,
      });
      if (status === "hit") hitCount += 1;
      else if (status === "pending") pendingCount += 1;
      else missCount += 1;
    }
    const rank: Record<DualFactorStatus, number> = { hit: 0, pending: 1, miss: 2 };
    factors.sort((a, b) => {
      if (rank[a.status] !== rank[b.status]) return rank[a.status] - rank[b.status];
      if (a.streak !== b.streak) return b.streak - a.streak;
      return a.name.localeCompare(b.name);
    });
  }

  return {
    available,
    streak_n,
    as_of_a: latest?.as_of_a ?? "",
    as_of_b: latest?.as_of_b ?? "",
    event: available ? latest!.event : null,
    next_candidate: available ? latest!.next_candidate : null,
    factors,
    hitCount,
    pendingCount,
    missCount,
    total: factors.length,
  };
}
