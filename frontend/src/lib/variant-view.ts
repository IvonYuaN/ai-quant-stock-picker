import type { AqspVariant } from "@/types/aqsp";

const MODE_LABELS: Readonly<Record<string, string>> = {
  atr_trend: "ATR 趋势约束",
  breakout: "前高突破",
  defensive_range: "ATR 防守区间",
  kdj_rebound: "KDJ 超跌修复",
  low_vol: "低波趋势",
  macd_cross: "MACD 动能转强",
  momentum: "动量跟随",
  pullback: "趋势回踩承接",
  relative_strength: "横向强势",
  reversion: "均值回归",
  trend: "温和趋势",
  volume_breakout: "放量突破",
  volume_dry_pullback: "缩量回踩",
};

type VariantStrategy = {
  mode: string;
  hypothesis: string;
  lookbackDays?: number;
  entryReturnPct?: number;
  maxBiasPct?: number;
  maxPositions?: number;
  positionWeight?: number;
};

function parseVariantStrategy(strategy: string | undefined): VariantStrategy | null {
  const raw = strategy?.trim();
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return null;
    const record = value as Record<string, unknown>;
    return {
      mode: typeof record.mode === "string" ? record.mode : "",
      hypothesis: typeof record.hypothesis === "string" ? record.hypothesis : "",
      lookbackDays: typeof record.lookback_days === "number" ? record.lookback_days : undefined,
      entryReturnPct: typeof record.entry_return_pct === "number" ? record.entry_return_pct : undefined,
      maxBiasPct: typeof record.max_bias_pct === "number" ? record.max_bias_pct : undefined,
      maxPositions: typeof record.max_positions === "number" ? record.max_positions : undefined,
      positionWeight: typeof record.position_weight === "number" ? record.position_weight : undefined,
    };
  } catch {
    return null;
  }
}

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

export function variantDisplayName(variant: Pick<AqspVariant, "label" | "strategy" | "variant_id">): string {
  const parsed = parseVariantStrategy(variant.strategy);
  if (!parsed) return variant.label || variant.variant_id;
  const mode = MODE_LABELS[parsed.mode] || variant.label || variant.variant_id;
  const period = parsed.lookbackDays == null ? "" : `${parsed.lookbackDays} 日`;
  const portfolio = parsed.maxPositions == null ? "" : `${parsed.maxPositions} 股组合`;
  return [mode, period, portfolio].filter(Boolean).join(" · ");
}

export function variantStrategyLogic(variant: Pick<AqspVariant, "strategy">): string {
  return parseVariantStrategy(variant.strategy)?.hypothesis || "策略假设未记录";
}

export function variantStrategyParameters(variant: Pick<AqspVariant, "strategy">): string {
  const parsed = parseVariantStrategy(variant.strategy);
  if (!parsed) return "关键参数未记录";
  const parts = [
    parsed.entryReturnPct == null ? "" : `入场动能 ${parsed.entryReturnPct.toFixed(1)}%`,
    parsed.maxBiasPct == null ? "" : `最大乖离 ${parsed.maxBiasPct.toFixed(1)}%`,
    parsed.positionWeight == null ? "" : `单票权重 ${(parsed.positionWeight * 100).toFixed(0)}%`,
  ].filter(Boolean);
  return parts.join(" · ") || "关键参数未记录";
}

export function variantHoldingsSummary(variant: Pick<AqspVariant, "holdings">): string {
  const holdings = variant.holdings;
  if (holdings === undefined) return "持仓字段未提供";
  if (holdings.length === 0) return "当前无持仓";
  const visible = holdings.slice(0, 2).map((holding) =>
    `${holding.name || holding.symbol} · ${holding.entry_date || "建仓日未记录"} · ${holding.holding_days ?? 0} 天`,
  );
  const remaining = holdings.length - visible.length;
  return `${holdings.length} 只｜${visible.join("；")}${remaining > 0 ? `；另 ${remaining} 只` : ""}`;
}

export function variantHoldingsLabel(holdings: AqspVariant["holdings"]): string {
  if (holdings === undefined) return "持仓字段未提供";
  return holdings.length === 0 ? "当前无持仓" : `${holdings.length} 个持仓`;
}
