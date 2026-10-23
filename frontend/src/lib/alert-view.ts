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
import type { AlertData, AlertEvent } from "./api";
import { asArray, asNumber, asRecord, asString } from "./safe";

const STATUS_VALUES = new Set(["OPEN", "HANDLED", "IGNORED", "EXPIRED"]);

function normalizeBasis(raw: unknown): AlertEvent["basis_json"] {
  const record = asRecord(raw);
  return {
    rule: asString(record.rule),
    actual: asString(record.actual),
    evidence: asString(record.evidence),
  };
}

function normalizeStatus(raw: unknown): AlertEvent["status"] {
  const s = asString(raw);
  return STATUS_VALUES.has(s) ? (s as AlertEvent["status"]) : "OPEN";
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
    basis_json: normalizeBasis(record.basis_json),
    status: normalizeStatus(record.status),
    ignore_streak: asNumber(record.ignore_streak),
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
