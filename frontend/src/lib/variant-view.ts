import type { AqspVariant } from "@/types/aqsp";

export function variantMoney(value: number | undefined): string {
  if (value == null || !Number.isFinite(value)) return "未提供";
  return `${value.toLocaleString("zh-CN", { maximumFractionDigits: 0 })} 元`;
}

export function variantPercent(value: number | undefined): string {
  if (value == null || !Number.isFinite(value)) return "未提供";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/** The producer currently serializes the strategy object into a JSON string. */
export function variantStrategyText(strategy: string | undefined, fallback: string): string {
  const raw = strategy?.trim();
  if (!raw) return "策略字段未提供";
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value === "object" && value !== null) {
      const record = value as Record<string, unknown>;
      const parts = [
        typeof record.id === "string" ? record.id : fallback,
        typeof record.mode === "string" ? record.mode : "",
        typeof record.lookback_days === "number" ? `回看 ${record.lookback_days} 日` : "",
      ].filter(Boolean);
      if (parts.length > 0) return parts.join(" · ");
    }
  } catch {
    // A plain strategy label is also a valid forward-compatible payload.
  }
  return raw;
}

export function variantHoldingsLabel(holdings: AqspVariant["holdings"]): string {
  if (holdings === undefined) return "持仓字段未提供";
  return holdings.length === 0 ? "当前无持仓" : `${holdings.length} 个持仓`;
}
