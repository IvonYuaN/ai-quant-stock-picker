// 持仓台账的归一化。
//
// 后端 `GET /api/portfolio` 正常返回完整结构，但接口异常/降级时可能给出 `{}` 或部分字段。
// 页面直接读 `data.holdings.length` 会当场抛错白屏（本次实测确实崩了），
// 所以入口统一归一化：数组缺失给空数组、数字缺失给 0、名称缺失回退成代码。
import type { ClosedPosition, Holding, PortfolioData } from "./api";
import { asArray, asNumber, asNullableNumber, asRecord, asString } from "./safe";

export function shanghaiDate(now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export function holdingDateError(value: string, currentDay = shanghaiDate()): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "请选择开仓日期";
  const parsed = new Date(`${value}T00:00:00Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    return "开仓日期无效，请重新选择";
  }
  return value > currentDay ? "开仓日期不能晚于今天（北京时间）" : "";
}

export function holdingT1Status(openedAt: string, currentDay = shanghaiDate()): {
  kind: "today" | "prior" | "legacy" | "invalid";
  tone: "ok" | "warn" | "neutral";
  label: string;
} {
  if (!openedAt) return { kind: "legacy", tone: "neutral", label: "未录日期 · 老仓" };
  if (holdingDateError(openedAt, currentDay)) return { kind: "invalid", tone: "warn", label: "日期待核实" };
  if (openedAt === currentDay) return { kind: "today", tone: "warn", label: "今日新仓 · T+1" };
  return { kind: "prior", tone: "neutral", label: "往日开仓" };
}

function normalizeHolding(raw: unknown): Holding {
  const record = asRecord(raw);
  const code = asString(record.code);
  return {
    code,
    name: asString(record.name, code),
    price: asNumber(record.price),
    shares: asNumber(record.shares),
    cost: asNumber(record.cost),
    // 仅 null/缺字段兼容旧仓；显式空串、数字等必须保留为异常，和后端 409 对齐。
    opened_at: record.opened_at == null ? "" : asString(record.opened_at, "日期无效") || "日期无效",
    market_value: asNumber(record.market_value),
    pnl: asNumber(record.pnl),
    pnl_pct: asNumber(record.pnl_pct),
  };
}

function normalizeClosed(raw: unknown): ClosedPosition {
  const record = asRecord(raw);
  const code = asString(record.code);
  return {
    code,
    name: asString(record.name, code),
    date: asString(record.date),
    price: asNumber(record.price),
    shares: asNumber(record.shares),
    cost: asNumber(record.cost),
    pnl: asNumber(record.pnl),
    pnl_pct: asNumber(record.pnl_pct),
  };
}

export function normalizePortfolio(raw: unknown): PortfolioData {
  const record = asRecord(raw);
  const totals = asRecord(record.totals);
  const holdings = asArray<unknown>(record.holdings).map(normalizeHolding);
  const closed = asArray<unknown>(record.closed).map(normalizeClosed);

  // 后端没给汇总时，用明细自己算一遍，而不是显示 0 —— 显示 0 会被误读成"没亏没赚"。
  const marketValue = asNullableNumber(totals.market_value) ?? holdings.reduce((sum, row) => sum + row.market_value, 0);
  const cost = asNullableNumber(totals.cost) ?? holdings.reduce((sum, row) => sum + row.cost * row.shares, 0);
  const pnl = asNullableNumber(totals.pnl) ?? marketValue - cost;
  const pnlPct = asNullableNumber(totals.pnl_pct) ?? (cost !== 0 ? (pnl / cost) * 100 : 0);

  return {
    holdings,
    totals: { market_value: marketValue, cost, pnl, pnl_pct: pnlPct },
    closed,
    realized_pnl:
      asNullableNumber(record.realized_pnl) ?? closed.reduce((sum, row) => sum + row.pnl, 0),
    updated: asString(record.updated),
    last_refresh: typeof record.last_refresh === "string" ? record.last_refresh : null,
  };
}
