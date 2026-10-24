// P0-2/P1-2 该卖提醒收件箱的归一化 + 视图派生。
//
// 后端 `GET /api/alerts` 正常返回 {events, open_count}，但异常/降级时可能给
// 出 {} 或部分字段（alert_event 簿损坏、新版后端未部署等）。页面直接读
// `data.events.length` 会当场白屏。入口统一归一化：数组缺失给空、状态缺失
// 给 OPEN、数字缺失给 0。
//
// 收件箱行的分级（Q3=A T+1 语义）由 `buildAlertInbox` 派生：
//   - 只展示 OPEN（已处理/已忽略不进收件箱，避免信任被"处理过的旧提醒"稀释）
//   - 可立即卖（sellable_now=true）→ actionable「该卖」
//   - 当日买入卖不掉（sellable_now=false）→ watch「明日生效 / 观察 + T+1 标注」
// 两区分开，用户一眼能分清"今天能动的"和"今天只能看着的"。
import type { AlertData, AlertEvent, DecisionEvidence, Holding, PositionDecisionContext } from "./api";
import { asArray, asNumber, asRecord, asString } from "./safe";
import { normalizeMacdChart } from "./alert-chart";

const STATUS_VALUES = new Set(["OPEN", "HANDLED", "IGNORED", "EXPIRED"]);

function normalizeBasis(raw: unknown, level: string, checkedAt: string): AlertEvent["basis_json"] {
  const record = asRecord(raw);
  const chart = level === "L4" ? normalizeMacdChart(record.chart, checkedAt) : undefined;
  return {
    rule: asString(record.rule),
    actual: asString(record.actual),
    evidence: asString(record.evidence),
    ...(chart ? { chart } : {}),
  };
}

function normalizeStatus(raw: unknown): AlertEvent["status"] {
  const s = asString(raw);
  return STATUS_VALUES.has(s) ? (s as AlertEvent["status"]) : "OPEN";
}

function normalizeContext(raw: unknown): PositionDecisionContext | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const row = asRecord(raw);
  const evidence = (value: unknown): DecisionEvidence => {
    const item = asRecord(value);
    return { status: asString(item.status) || "unknown", summary: asString(item.summary) || "依据待补", as_of: typeof item.as_of === "string" ? item.as_of : null, source: asString(item.source) };
  };
  const assessment = asRecord(row.assessment);
  const tPlan = asRecord(assessment.t_plan);
  return {
    name: asString(row.name), opened_at: typeof row.opened_at === "string" ? row.opened_at : null,
    holding_trade_days: typeof row.holding_trade_days === "number" && Number.isInteger(row.holding_trade_days) && row.holding_trade_days >= 0 ? row.holding_trade_days : null,
    market: evidence(row.market), sector: evidence(row.sector), stock: evidence(row.stock),
    assessment: { stance: asString(assessment.stance), title: asString(assessment.title), reason: asString(assessment.reason), t_plan: { eligible: typeof tPlan.eligible === "boolean" ? tPlan.eligible : null, reason: asString(tPlan.reason) } },
  };
}

export function normalizeAlertEvent(raw: unknown): AlertEvent {
  const record = asRecord(raw);
  return {
    id: asString(record.id),
    level: asString(record.level),
    symbol: asString(record.symbol),
    position_id: typeof record.position_id === "string" ? record.position_id : null,
    trigger_at: asNumber(record.trigger_at),
    threshold_at: asNumber(record.threshold_at),
    pnl_pct: asNumber(record.pnl_pct),
    sellable_now: record.sellable_now === true,
    action_hint: asString(record.action_hint),
    t1_note: asString(record.t1_note),
    basis_json: normalizeBasis(record.basis_json, asString(record.level), asString(record.updated_at) || asString(record.created_at)),
    status: normalizeStatus(record.status),
    ignore_streak: asNumber(record.ignore_streak),
    frequency_note: asString(record.frequency_note),
    review_plan: ["hold", "reduce", "t_watch"].includes(asString(asRecord(record.review_plan).choice)) ? { choice: asRecord(record.review_plan).choice as "hold" | "reduce" | "t_watch", updated_at: asString(asRecord(record.review_plan).updated_at) } : undefined,
    position_context: normalizeContext(record.position_context),
    created_at: asString(record.created_at),
    updated_at: asString(record.updated_at),
  };
}

/** 归一化 `GET /api/alerts` 的 `data`；异常负载给空收件箱而非崩。 */
export function normalizeAlerts(raw: unknown): AlertData {
  const record = asRecord(raw);
  const events = asArray<unknown>(record.events).map(normalizeAlertEvent);
  // open_count 缺失时用明细自己数（0 会被误读成"确实没有"，但这里只是缺字段）。
  const openCount =
    Number.isFinite(Number(record.open_count)) ? asNumber(record.open_count) :
    events.filter((e) => e.status === "OPEN").length;
  const check = asRecord(record.last_check);
  return {
    events, open_count: openCount,
    last_checked_at: typeof record.last_checked_at === "string" ? record.last_checked_at : null,
    last_check: record.last_check ? {
      checked: asArray<unknown>(check.checked).map((item) => asString(item)),
      skipped: asArray<unknown>(check.skipped).map((item) => { const row = asRecord(item); return { symbol: asString(row.symbol), level: asString(row.level), reason: asString(row.reason) }; }),
    } : null,
  };
}

export interface AlertInboxView {
  /** OPEN 且今日可卖（sellable_now）——「该卖」区。 */
  actionable: AlertEvent[];
  /** OPEN 且今日不可卖（T+1 当日买入）——「明日生效 / 观察」区。 */
  watch: AlertEvent[];
  openCount: number;
}

/** 从归一化后的 AlertData 派生收件箱两区（只取 OPEN，按 created_at 倒序）。 */
export function buildAlertInbox(alerts: AlertData): AlertInboxView {
  const open = alerts.events.filter((e) => e.status === "OPEN");
  const byTime = (a: AlertEvent, b: AlertEvent) =>
    asString(b.created_at).localeCompare(asString(a.created_at));
  const actionable = open.filter((e) => e.sellable_now).sort(byTime);
  const watch = open.filter((e) => !e.sellable_now).sort(byTime);
  return { actionable, watch, openCount: open.length };
}

export interface PositionAlertGroup {
  key: string;
  symbol: string;
  name: string;
  events: AlertEvent[];
  latest: AlertEvent;
  holding?: Holding;
  sellable: boolean;
}

/** 同仓多规则只占一条；换仓身份与 T+1 状态不同不能误合并。 */
export function groupPositionAlerts(alerts: AlertData, holdings: readonly Holding[] = []): PositionAlertGroup[] {
  const groups = new Map<string, PositionAlertGroup>();
  for (const event of alerts.events.filter((row) => row.status === "OPEN")) {
    // 旧事件可能没有 position_id；同代码已有新仓时按当前仓身份归并，
    // 避免一次风险检查在收件箱显示两张相同股票卡。不同 T+1 可卖状态仍分区保留。
    const currentPositionId = holdings.find((row) => row.code === event.symbol)?.position_id;
    const positionKey = event.position_id ?? currentPositionId ?? "legacy";
    const key = `${event.symbol}:${positionKey}:${event.sellable_now}`;
    let group = groups.get(key);
    if (!group) {
      const holding = holdings.find((row) => row.code === event.symbol && (!event.position_id || row.position_id === event.position_id || row.risk_rules?.some((rule) => rule.position_id === event.position_id)));
      group = { key, symbol: event.symbol, name: event.position_context?.name || (holding?.name && holding.name !== event.symbol ? holding.name : "名称待补"), events: [], latest: event, holding, sellable: event.sellable_now };
      groups.set(key, group);
    }
    group.events.push(event);
    if (event.updated_at > group.latest.updated_at) group.latest = event;
  }
  return [...groups.values()].sort((a, b) => b.latest.updated_at.localeCompare(a.latest.updated_at)).map((group) => ({ ...group, events: group.events.sort((a, b) => a.level.localeCompare(b.level)) }));
}

export function formatAlertPrice(value: number): string {
  return Number.isFinite(value) && value > 0 ? value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4, useGrouping: false }) : "—";
}

export const ALERT_LEVEL_NAMES: Record<string, string> = { L1: "成本止损", L2: "均线走弱", L4: "MACD 走弱", L5: "移动止损" };
