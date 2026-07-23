import type { AqspVariant, AqspVariantHolding } from "@/types/aqsp";

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
        typeof record.hypothesis === "string" ? record.hypothesis : "",
      ].filter(Boolean);
      if (parts.length > 0) return parts.join(" · ");
    }
  } catch {
    // A plain strategy label is also a valid forward-compatible payload.
  }
  return raw;
}

export function variantStrategyLogic(strategy: string | undefined, fallback: string): string {
  const raw = strategy?.trim();
  if (!raw) return `${fallback}：策略参数未记录`;
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value === "object" && value !== null) {
      const record = value as Record<string, unknown>;
      const mode = typeof record.mode === "string" ? record.mode : "";
      const lookback = typeof record.lookback_days === "number" ? `${record.lookback_days}日` : "";
      const entry = typeof record.entry_return_pct === "number" ? record.entry_return_pct : undefined;
      const bias = typeof record.max_bias_pct === "number" ? record.max_bias_pct : undefined;
      if (mode === "reversion") return `均值回归：收盘低于${lookback}均线，回撤达到 ${entry ?? "—"}%；乖离上限 ${bias ?? "—"}%`;
      if (mode === "breakout" || mode === "volume_breakout") return `突破策略：突破${lookback}高点并确认量能；乖离上限 ${bias ?? "—"}%`;
      if (mode === "volume") return `成交量突破：突破${lookback}日高点并要求量比放大；乖离上限 ${bias ?? "—"}%`;
      if (mode === "macd") return `MACD趋势：DIF/DEA与柱体转强，收益门槛 ${entry ?? "—"}%；乖离上限 ${bias ?? "—"}%`;
      if (mode === "kdj") return `KDJ修复：K上穿D且J未过热，收益门槛 ${entry ?? "—"}%；乖离上限 ${bias ?? "—"}%`;
      if (mode === "bollinger") return `布林回归：收盘触及下轨，回撤门槛 ${entry ?? "—"}%；乖离上限 ${bias ?? "—"}%`;
      if (mode === "rsi") return `RSI修复：RSI低于弱势阈值并观察反弹；乖离上限 ${bias ?? "—"}%`;
      if (mode === "ema_cross") return `EMA交叉：12日EMA站上26日EMA且MACD柱体改善；乖离上限 ${bias ?? "—"}%`;
      if (mode === "obv") return `OBV能量：OBV站上自身均线并由价格趋势确认；乖离上限 ${bias ?? "—"}%`;
      if (mode === "donchian") return `唐奇安突破：突破${lookback}日通道上沿，收益门槛 ${entry ?? "—"}%；乖离上限 ${bias ?? "—"}%`;
      if (mode === "vwap") return `成交额加权：收盘站上${lookback}日成交额加权成本线；乖离上限 ${bias ?? "—"}%`;
      if (mode === "low_vol") return `低波策略：趋势成立且回看 ${lookback}；乖离上限 ${bias ?? "—"}%`;
      if (lookback) return `趋势策略：站上${lookback}均线，收益门槛 ${entry ?? "—"}%；乖离上限 ${bias ?? "—"}%`;
    }
  } catch {
    // A plain strategy label remains a valid compatibility payload.
  }
  return raw;
}

export function variantHoldingName(
  holding: AqspVariantHolding,
  candidateNames: ReadonlyMap<string, string> = new Map(),
): string {
  return holding.name?.trim() || holding.display_name?.trim() || candidateNames.get(holding.symbol) || holding.symbol || "名称未记录";
}

export function variantAdjustmentReasons(
  current: AqspVariant["holdings"] | null,
  previous: AqspVariant["previous_holdings"],
  candidateNames: ReadonlyMap<string, string> = new Map(),
): string[] {
  if (current == null || previous == null) return ["昨日持仓未记录，暂无法比较换票原因"];
  const currentBySymbol = new Map(current.map((holding) => [holding.symbol, holding]));
  const previousBySymbol = new Map(previous.map((holding) => [holding.symbol, holding]));
  const added = current.filter((holding) => !previousBySymbol.has(holding.symbol));
  const removed = previous.filter((holding) => !currentBySymbol.has(holding.symbol));
  const continued = current.filter((holding) => previousBySymbol.has(holding.symbol));
  const reasons: string[] = [];
  if (added.length > 0) reasons.push(`新增：${added.map((holding) => variantHoldingName(holding, candidateNames)).join("、")}`);
  if (removed.length > 0) reasons.push(`移出：${removed.map((holding) => variantHoldingName(holding, candidateNames)).join("、")}`);
  if (continued.length > 0) reasons.push(`继续持有：${continued.map((holding) => variantHoldingName(holding, candidateNames)).join("、")}`);
  return reasons.length > 0 ? reasons : ["持仓未变化"];
}

export function variantAdjustmentEvidence(variant: AqspVariant): string[] {
  const adjustmentEvidence = (variant.adjustments ?? []).flatMap((adjustment) =>
    adjustment.evidence.map((evidence) => `${adjustment.symbol}：${evidence}`),
  );
  if (adjustmentEvidence.length > 0) return adjustmentEvidence;
  if ((variant.recent_actions ?? []).length > 0) return [...(variant.recent_actions ?? [])];
  return ["成交证据未记录"];
}
