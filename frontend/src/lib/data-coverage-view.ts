import type { DataCoveragePayload } from "./api";

export interface DataCoverageVerdict {
  tone: "ok" | "warn" | "neutral";
  title: string;
  detail: string;
  usable: boolean;
}

/** Turn raw cross-section coverage into a verdict that never overstates completeness. */
export function dataCoverageVerdict(
  payload: DataCoveragePayload | null,
  error?: string,
): DataCoverageVerdict {
  if (error) {
    return {
      tone: "neutral",
      usable: false,
      title: "覆盖状态不可核验",
      detail: `${error}。此时不得把任何日线日期当作完整市场日。`,
    };
  }
  if (!payload) {
    return {
      tone: "neutral",
      usable: false,
      title: "正在核验日线覆盖…",
      detail: "核验完成前，不把库内最大日期当作完整市场日。",
    };
  }
  if (!payload.available) {
    return {
      tone: "warn",
      usable: false,
      title: "本机 raw 日线库不可用",
      detail: payload.reason || "未找到本机 raw 日线数据库。",
    };
  }
  const total = payload.total_symbols ?? 0;
  const covered = payload.symbols_on_max_date ?? 0;
  const pct = ((payload.coverage_pct ?? 0) * 100).toFixed(2);
  const threshold = ((payload.min_coverage_ratio ?? 0) * 100).toFixed(0);
  const sentinels = (payload.sentinel_symbols ?? []).slice(0, 3).join("、") || "无";
  if (!payload.market_date_usable) {
    return {
      tone: "warn",
      usable: false,
      title: "数据不完整，已禁止当作完整市场日使用",
      detail: `库内最新日 ${payload.market_max_date ?? "—"} 只有 ${covered}/${total} 只标的覆盖（${pct}%），低于 ${threshold}% 门槛；达到该日的标的：${sentinels}。依赖市场日线的功能已全部 fail-closed。`,
    };
  }
  return {
    tone: "ok",
    usable: true,
    title: "最新市场日横截面覆盖通过",
    detail: `${payload.verified_market_date ?? payload.market_max_date ?? "—"} 覆盖 ${covered}/${total} 只标的（${pct}%），达到 ${threshold}% 门槛。`,
  };
}
