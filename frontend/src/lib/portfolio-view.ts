// 持仓台账的归一化。
//
// 后端 `GET /api/portfolio` 正常返回完整结构，但接口异常/降级时可能给出 `{}` 或部分字段。
// 页面直接读 `data.holdings.length` 会当场抛错白屏（本次实测确实崩了），
// 所以入口统一归一化：数组缺失给空数组、数字缺失给 0、名称缺失回退成代码。
import type { ClosedPosition, Holding, PortfolioData } from "./api";
import { asArray, asNumber, asNullableNumber, asRecord, asString } from "./safe";

function normalizeHolding(raw: unknown): Holding {
  const record = asRecord(raw);
  const code = asString(record.code);
  return {
    code,
    name: asString(record.name, code),
    price: asNumber(record.price),
    shares: asNumber(record.shares),
    cost: asNumber(record.cost),
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
