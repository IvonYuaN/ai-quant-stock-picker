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
        typeof record.hypothesis === "string" && record.hypothesis.trim()
          ? `假设：${record.hypothesis.trim()}`
          : "",
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

export function variantHoldingName(
  holding: NonNullable<AqspVariant["holdings"]>[number],
): string {
  return holding.display_name || holding.name || holding.symbol;
}

export function variantActionText(
  action: NonNullable<AqspVariant["recent_actions"]>[number],
): string {
  const side = action.action || action.side || "动作未记录";
  const name = action.display_name || action.name || action.symbol || "标的未记录";
  const quantity =
    typeof action.quantity === "number" && Number.isFinite(action.quantity)
      ? ` · ${action.quantity} 股`
      : "";
  return `${action.date || "日期未记录"} · ${side} · ${name}${quantity} · ${
    action.reason || "原因未记录"
  }`;
}

export function variantHoldingChangeText(
  today: AqspVariant["holdings"],
  previous: AqspVariant["previous_holdings"],
): string[] {
  if (today === undefined || previous === undefined) return ["今日/昨日持仓字段不完整，无法判断换票。"];
  const previousBySymbol = new Map(previous.map((holding) => [holding.symbol, holding]));
  const todayBySymbol = new Map(today.map((holding) => [holding.symbol, holding]));
  const changes: string[] = [];

  for (const holding of today) {
    const oldHolding = previousBySymbol.get(holding.symbol);
    const name = variantHoldingName(holding);
    if (!oldHolding) {
      changes.push(`新增：${name}（${holding.symbol}）`);
      continue;
    }
    if (oldHolding.quantity !== holding.quantity) {
      changes.push(`数量调整：${name}（${holding.symbol}）${oldHolding.quantity} → ${holding.quantity} 股`);
    }
  }
  for (const holding of previous) {
    if (!todayBySymbol.has(holding.symbol)) {
      changes.push(`移出：${variantHoldingName(holding)}（${holding.symbol}）`);
    }
  }
  return changes.length > 0 ? changes : ["持仓未变化。"];
}

function metric(value: number | null | undefined, suffix = "", digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "未形成";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}${suffix}`;
}

export function variantTechnicalEvidenceText(
  evidence: NonNullable<AqspVariant["technical_evidence"]>[number],
): string {
  const name = evidence.name || evidence.symbol || "标的未记录";
  const actionDate = evidence.execution_date || evidence.date || "执行日未记录";
  const signalDate = evidence.signal_date ? `信号 ${evidence.signal_date} → ` : "";
  const mode = evidence.mode_label || evidence.mode || "策略";
  const evidenceLabel = evidence.evidence_kind === "current_holding_snapshot" ? "持仓当日截面" : mode;
  const signalPrefix = evidence.evidence_kind === "current_holding_snapshot" ? "" : signalDate;
  return `${name} · ${evidenceLabel} · ${signalPrefix}${actionDate} · MACD ${metric(
    evidence.macd_hist,
  )} · KDJ-J ${metric(evidence.kdj_j, "", 0)} · 量比 ${metric(
    evidence.volume_ratio,
    "",
    2,
  )} · ATR ${metric(evidence.atr_pct, "%", 1)}`;
}
