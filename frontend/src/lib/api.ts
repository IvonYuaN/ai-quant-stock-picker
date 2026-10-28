// AQSP 后端 API 客户端。/api → vite 代理到本地 FastAPI（默认 8900）。
// 后端未启动或数据源异常时抛 ApiError，页面据此优雅降级。
import type { AqspLifecycleIndex, AqspSnapshotEnvelope, AqspSnapshotView, InfoStreamData } from "@/types/aqsp";
import { createBrowserIdentity } from "./browser-session";
import { readDiscussionStream } from "./discussion-stream";

export type {
  AqspAgentDiscussion,
  AqspAgentResult,
  AqspCandidate,
  AqspVariant,
  AqspVariantHolding,
  AqspCrossMarket,
  AqspMarketContext,
  AqspMessage,
  AqspPhase,
  AqspResearchChain,
  AqspRecommendationGate,
  AqspSnapshot,
  AqspSnapshotEnvelope,
  AqspSnapshotMeta,
  AqspSnapshotView,
  AqspSourceHealth,
} from "@/types/aqsp";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export type AqspRequestOptions = Pick<RequestInit, "signal">;

export function isAqspAbortError(reason: unknown): boolean {
  return (
    typeof reason === "object" &&
    reason !== null &&
    "name" in reason &&
    reason.name === "AbortError"
  );
}

// 后端访问密钥（对应后端部署时的 VR_API_KEY，公网部署防蹭用）。只存本地浏览器。
const ACCESS_KEY = "vr-access-key";

// 本浏览器稳定身份：用于在「同一把共享 Key 下」让不同访客的数据互相隔离
// （后端按 X-User-Id 切分目录）。与自选/笔记一样只存本地浏览器，换浏览器即另一人。
const browserIdentity = createBrowserIdentity();

export function loadAccessKey(): string {
  try {
    return localStorage.getItem(ACCESS_KEY) || "";
  } catch {
    return "";
  }
}

export function saveAccessKey(key: string) {
  try {
    if (key) localStorage.setItem(ACCESS_KEY, key);
    else localStorage.removeItem(ACCESS_KEY);
  } catch {
    /* 隐私模式等场景 localStorage 不可用 */
  }
}

// 本页固定一个身份；存储失败时仅当前页面保留，绝不退回共享默认租户。
export function loadUserId(): string {
  return browserIdentity.userId();
}

export function hasTemporaryUserIdentity(): boolean { return browserIdentity.temporary(); }

export function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const k = loadAccessKey();
  if (k) headers["Authorization"] = `Bearer ${k}`;
  // 本浏览器身份：公网/鉴权模式下让持仓、研报按访客隔离（本地单用户模式后端忽略此头）。
  headers["X-User-Id"] = loadUserId();
  return headers;
}

export interface MyReport {
  id: string; name: string; industry: string; size: number; ext: string; ts: number;
}

export interface WatchlistRemovalRecord {
  id?: string;
  operation_id?: string;
  codes: readonly string[];
  removed_at: string;
}

// 下载/预览研报：带鉴权头 fetch → blob → 触发浏览器下载（<a download> 无法带 Authorization，故走 blob）。
export async function downloadReport(id: string, name: string): Promise<void> {
  const resp = await fetch(`/api/myreports/file/${id}`, { headers: authHeaders() });
  if (!resp.ok) throw new ApiError(`下载失败 HTTP ${resp.status}`, resp.status);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function hasDataField(payload: unknown): payload is { data: unknown } {
  return typeof payload === "object" && payload !== null && "data" in payload;
}

function errorDetail(payload: unknown): string | undefined {
  if (typeof payload !== "object" || payload === null || !("detail" in payload)) return undefined;
  return typeof payload.detail === "string" ? payload.detail : undefined;
}

async function requestPayload(
  path: string,
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH" = "GET",
  body?: unknown,
  options?: AqspRequestOptions,
): Promise<unknown> {
  let resp: Response;
  const headers: Record<string, string> = { ...authHeaders() };
  const opts: RequestInit = { method, signal: options?.signal };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (Object.keys(headers).length > 0) opts.headers = headers;
  try {
    resp = await fetch(`/api${path}`, opts);
  } catch (reason) {
    if (isAqspAbortError(reason)) throw reason;
    throw new ApiError("连接不到后端，请先启动 backend（uvicorn app:app --port 8900）", 0);
  }
  let payload: unknown = null;
  try {
    payload = await resp.json();
  } catch {
    /* 非 JSON 响应 */
  }
  if (!resp.ok) {
    if (resp.status === 401) {
      throw new ApiError("后端开启了访问鉴权（VR_API_KEY）：请在「接入 AI」页底部填写后端访问密钥", 401);
    }
    throw new ApiError(errorDetail(payload) || `HTTP ${resp.status}`, resp.status);
  }
  return payload;
}

async function request<T>(
  path: string,
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH" = "GET",
  body?: unknown,
): Promise<T> {
  const payload = await requestPayload(path, method, body);
  return (hasDataField(payload) && payload.data != null ? payload.data : payload) as T;
}

const get = <T>(path: string) => request<T>(path, "GET");
const getEnvelope = <T>(path: string, options?: AqspRequestOptions): Promise<T> =>
  requestPayload(path, "GET", undefined, options) as Promise<T>;

export interface Quote {
  name: string; price: number; last_close: number; change_pct: number;
  pe_ttm: number; pb: number; mcap_yi: number; turnover_pct: number;
  limit_up: number; limit_down: number;
}

export interface IndexQuote {
  name: string; price: number; change_pct: number; change_amt: number;
  /** 证据链（#317 W3）：该行行情的成交时间（源给，非取数时刻）；null = 源未给 */
  as_of?: string | null;
}

export interface MarketSentiment {
  up: number; down: number; flat: number;
  /**
   * 涨停/跌停家数（东财涨停池口径）。null = 池子取数失败 —— 不是「0 家涨停」，
   * 前端必须显示「—」而不是 0（三态纪律：#417 换源后二者来源不同，失败不互相冒充）。
   */
  zt: number | null; zt_real: number; dt: number | null; dt_real: number;
  active: string; breadth: string; speculation: string; date: string;
}
export interface SectorFlow {
  name: string; pct: number; net: number; inflow: number; outflow: number; firms: number;
}
export interface MarketOverview {
  sentiment: MarketSentiment; sectors: SectorFlow[]; updated: string;
}

// 短线情绪：连板梯队 / 最高连板 / 炸板率 / 封板率 / 晋级率 / 涨跌停家数 + 连板股清单（客观公开榜单）
export interface EmotionTier { boards: number; count: number; plus: boolean }
export interface LianbanStock {
  code: string; name: string; boards: number;
  price: number; pct: number; amount: number | null; float_cap: number | null; industry: string;
}
export interface ShortTermEmotion {
  date: string;
  zt_count: number; dt_count: number; zb_count: number;
  max_boards: number; lianban_count: number;
  ladder: EmotionTier[];
  lianban_stocks: LianbanStock[];
  seal_rate: number | null; break_rate: number | null; promotion_rate: number | null;
  yzt_count: number;
}

// 全市场成交额榜（客观公开榜单）
export interface TurnoverStock {
  code: string; name: string;
  price: number | null; pct: number | null;
  amount: number | null; mcap: number | null; float_cap: number | null; industry: string;
  /** 该行行情时间（ISO，东财 f124）；null = 源未给。 */
  as_of?: string | null;
}
export interface TurnoverTop { stocks: TurnoverStock[]; updated: string }

/**
 * 一个数据源的运行状态（后端 `backend/source_health.py` 的台账行）。
 *
 * ⚠️ 这是**观测**，不是数据本身：`healthy=false` 说明这个主机最近的请求都失败了，
 * 因此**由它供数的板块为空不代表「今天没有数据」**。二者在界面上必须能分开。
 */
export interface DataSourceHealthRow {
  source: string;
  healthy: boolean;
  consecutive_errors: number;
  ok_count: number;
  error_count: number;
  last_ok_at: string | null;
  last_error_at: string | null;
  last_error: string;
  down_since: string | null;
}

export interface HealthPayload {
  ok: boolean;
  service: string;
  version: string;
  release_sha: string;
  sources: DataSourceHealthRow[];
}

/** 单条解禁预警（后端 `GET /api/events` 的 `data.upcoming_unlocks` 元素）。 */
export interface UpcomingUnlockEvent {
  symbol: string; name: string; event_type: string; event_date: string;
  days_until: number; severity: string; ratio: number | null; detail: string;
}

/** 单条龙虎榜佐证（`data.recent_longhubang` 元素）。net_amount 为 null = 净额未披露。 */
export interface RecentLonghubangEvent {
  symbol: string; name: string; event_type: string; event_date: string;
  days_ago: number; net_amount: number | null; interpretation: string; detail: string;
}

/** 单条停复牌（`data.suspend_resumes` 元素）。state ∈ resuming_soon | recently_resumed | suspended。 */
export interface SuspendResumeEvent {
  symbol: string; name: string; suspend_date: string; resume_date: string;
  state: string; suspend_type: string; reason: string; detail: string;
}

/** 单条业绩预告（`data.recent_earnings_forecasts` 元素，以公告日做 no-look-ahead）。 */
export interface EarningsForecastEvent {
  symbol: string; name: string; notice_date: string; report_date: string;
  forecast_type: string; days_ago: number; detail: string;
}

/** 单条分红送转（`data.upcoming_dividends` 元素，即将除权除息）。 */
export interface DividendPlanEvent {
  symbol: string; name: string; ex_dividend_date: string;
  days_until: number; progress: string; detail: string;
}

/** 事件日历（pit_cache 只读）。缺缓存 ≠ 没事件，用 has_*_data 区分。 */
export interface EventCalendarData {
  symbol: string; as_of: string;
  unlock_horizon_days: number; longhubang_lookback_days: number;
  has_unlock_data: boolean; has_longhubang_data: boolean;
  has_suspend_resume_data?: boolean;
  has_earnings_forecast_data?: boolean;
  has_dividend_plan_data?: boolean;
  upcoming_unlocks: UpcomingUnlockEvent[];
  recent_longhubang: RecentLonghubangEvent[];
  suspend_resumes?: SuspendResumeEvent[];
  recent_earnings_forecasts?: EarningsForecastEvent[];
  upcoming_dividends?: DividendPlanEvent[];
}

/** 单条催化事件（后端 `GET /api/catalyst` 的 `data.events` 元素）。 */
export interface CatalystEvent {
  title: string;
  summary: string;
  source: string;
  published_at: string;
  symbol: string;
  name: string;
  impact: "positive" | "negative" | "neutral";
  category: string;
  weight: number;
  confidence: number;
  verification: string;
  url: string;
  affected_sectors: string[];
  affected_symbols: string[];
  transmission_path: string[];
  transmission_hypothesis: string;
  time_horizon: string;
}

/** 催化事件中枢（`GET /api/catalyst` 的 `data` 部分）。失败降级时 events 可能为空。 */
export interface CatalystData {
  schema_version?: string;
  date?: string;
  generated_at: string | null;
  source_status: string;
  event_status?: string;
  warnings: string[];
  events: CatalystEvent[];
  market_clues?: string[];
  region_statuses?: Record<string, string>;
  source_statuses?: Record<string, string>;
}

export interface Holding {
  code: string; name: string; price: number; shares: number; cost: number;
  position_id?: string | null;
  /** 开仓日期 YYYY-MM-DD（P2-8；P2-8 前录入的老持仓无此字段 ⇒ ""）。T+1 可卖性据此推导。 */
  opened_at: string;
  market_value: number; pnl: number; pnl_pct: number;
  sellable_shares?: number;
  t1_locked_shares?: number;
  risk_rules?: PositionAlertRule[];
}
export type AlertFrequency = "once" | "daily" | "always";
export interface PositionAlertRule {
  id: string; position_id: string; symbol: string;
  level: "L1" | "L2" | "L4" | "L5";
  enabled: boolean; param_value: number; trigger_price: number | null;
  freq_mode: AlertFrequency; origin: "AUTO_DEFAULT" | "MANUAL";
  effective_from: string | null; created_at: string; updated_at: string;
  ignore_streak: number; frequency_note: string;
}
export interface PositionAlertRules {
  rules: PositionAlertRule[];
  unsupported_levels: string[];
}
export interface ClosedPosition {
  position_id?: string | null;
  code: string; name: string; date: string; price: number; shares: number; cost: number;
  pnl: number; pnl_pct: number;
}
export interface PortfolioData {
  holdings: Holding[];
  totals: { market_value: number; cost: number; pnl: number; pnl_pct: number };
  closed: ClosedPosition[];
  realized_pnl: number;
  updated: string; last_refresh: string | null;
}

export interface TradeReceipt {
  receipt_id: string; operation_id: string; tenant: string; position_id: string;
  code: string; direction: "buy" | "sell"; date: string;
  price: number; shares: number; actual_fees: number | null; recorded_at: string;
}
export interface TradeAllocation {
  sell_receipt_id: string; buy_receipt_id: string; shares: number;
  confirm_same_day_order: boolean; confirm_cross_position: boolean;
}
export interface TradeLink { link_id: string; allocations: TradeAllocation[]; }
export interface TradeSummary {
  sold_shares: number; bought_back_shares: number; unmatched_sell_shares: number;
  gross_difference: number; allocated_fees: number | null; net_difference: number | null;
  unmatched_sell_proceeds: number;
  allocations: (TradeAllocation & { gross_difference: number; allocated_fees: number | null; net_difference: number | null })[];
}
export interface TradeJournal {
  revision: string; code: string;
  position: { position_id: string | null; revision: string; shares: number; cost: number; sellable_shares: number };
  receipts: TradeReceipt[]; links: TradeLink[]; summary: TradeSummary;
}
export interface TradeInput {
  operation_id: string; direction: "buy" | "sell"; code: string; date: string;
  price: number; shares: number; actual_fees: number | null;
  expected_position_id: string | null; expected_position_revision: string; alert_id?: string;
}
export interface TradeFeeInput { operation_id: string; expected_revision: string; actual_fees: number | null; }
export interface TradeLinkInput { operation_id: string; expected_revision: string; allocations: TradeAllocation[]; }

// P0-2 该卖提醒（后端 `backend/alert_events.py`，PRD 7.2 alert_event）
/** 单条该卖提醒事件（收件箱数据源；站内红点 = status===OPEN）。 */
export interface AlertEvent {
  id: string;
  level: string;            // "L1" | "L5"
  symbol: string;
  position_id: string | null;
  trigger_at: number;      // 现价
  threshold_at: number;    // 止损位
  pnl_pct: number;
  sellable_now: boolean;   // T+1 下今日能否执行
  action_hint: string;     // "该卖" | "明日生效" | "观察"
  t1_note: string;
  basis_json: { rule: string; actual: string; evidence: string; chart?: MacdChartEvidence };
  status: "OPEN" | "HANDLED" | "IGNORED" | "EXPIRED";
  ignore_streak: number;
  frequency_note?: string;
  freq_mode?: AlertFrequency;
  review_plan?: { choice: "hold" | "reduce" | "t_watch"; updated_at: string };
  position_context?: PositionDecisionContext;
  created_at: string;
  updated_at: string;
}
export interface MacdChartEvidence {
  kind: "macd_dead_cross";
  price_adjustment: "none";
  trigger_date: string;
  source: string;
  fast: number; slow: number; signal: number;
  points: { date: string; close: number; dif: number; dea: number }[];
}
export type ReviewChoice = "hold" | "reduce" | "t_watch";
export interface PlanParameters {
  quantity: number; sell_price: number; buy_price: number | null;
  commission_rate: number; min_commission: number; stamp_tax_rate: number;
  slippage_rate: number; fees_confirmed: boolean;
}
export interface PositionModelConfig { baseURL: string; apiKey: string; model: string }
export interface PositionModelCatalog { catalog_id: string; current_model: string; models: string[]; checked_at: string; listed: boolean | null; directory_complete?: boolean; issue: string; notice: string }
export interface DiscussionAction { action: "observe" | "sell_review" | "buy_review" | "pause"; price_ref: string | null; condition: string; invalidation: string }
export type WatchComparator = "at_or_above" | "at_or_below";
export interface StrategyWatch {
  action: DiscussionAction["action"]; price: number; comparator: WatchComparator;
  condition: string; invalidation: string;
  price_ref: { label: string; price: number; source: string; as_of: string };
  source_generated_at: string; saved_at: string;
  position_snapshot?: ActionWorksheet["position_snapshot"];
  constraints?: PositionConstraints;
  trade_revision?: string;
  evidence_snapshot?: Partial<Record<"market" | "sector" | "stock", { status?: string }>>;
}
export interface StrategyWatchInput {
  choice: ReviewChoice; source_discussion: "comparison" | "plan";
  generated_at: string; action_index: number; comparator: WatchComparator;
  expected_position: ActionWorksheet["position_snapshot"];
  expected_revision: string; expected_analysis_basis_id: string;
}
/** Server-built comparison; original prices are context, never new action refs. */
export interface StrategyReviewContext {
  kind: "candidate" | "position";
  status: "adopted" | "not_adopted" | "identity_mismatch";
  original: null | {
    choice?: string | null; condition?: string | null; invalidation?: string | null;
    comparator?: WatchComparator | null; price_ref?: string | CandidatePriceReference | null;
    saved_at?: string | null; evidence_snapshot?: Record<string, unknown> | null;
    snapshot_complete: boolean;
  };
  current: { evidence_snapshot?: Record<string, unknown>; price_refs?: Record<string, CandidatePriceReference> };
  changes: Array<{ category: string; label: string; before: unknown; after: unknown }>;
  missing_evidence: string[]; material_changed: boolean; quote_updated: boolean; notice: string;
}
export interface PositionDiscussion {
  review_context?: StrategyReviewContext;
  review_revision?: string;
  summary_issue?: string;
  status: "complete" | "partial" | "failed"; generated_at: string; model: string; mode: string;
  choice: ReviewChoice | null; position_snapshot: ActionWorksheet["position_snapshot"]; quote_as_of: string | null;
  analysis_basis_id?: string;
  evidence_snapshot?: { quote: { price: number | null; as_of: string | null; source: string }; market: { status?: string; summary?: string; source?: string; as_of?: string }; sector: { status?: string; summary?: string; source?: string; as_of?: string }; stock?: { status?: string; summary?: string; source?: string; as_of?: string } };
  price_refs: Record<string, { label: string; price: number; as_of: string; source: string }>;
  rounds: Array<{ stage: string; opinions: Array<{ role: string; label: string; status: string; issue?: string;
    opinion?: { summary: string; evidence_ids: string[]; uncertainties: string[]; actions: DiscussionAction[]; reply_to: string[]; challenge: string } }> }>;
  summary: { conclusion: string; agreements: string[]; disagreements: string[]; missing_evidence: string[]; actions: DiscussionAction[];
    routes?: Array<{ choice: ReviewChoice; judgment: "investigate" | "not_now" | "insufficient"; reason: string; evidence_ids: string[]; needs: string[] }>;
    focus_choice?: ReviewChoice | null; focus_reason?: string } | null;
  notice: string;
}
export type CandidateChoice = "pullback" | "confirmation" | "defer";
export interface CandidatePriceReference { label: string; price: number; as_of: string; source: string }
export interface CandidateCondition { choice: CandidateChoice; price_ref: string | null; condition: string; invalidation: string }
export interface CandidateDiscussion {
  review_context?: StrategyReviewContext;
  id: string; status: "complete" | "partial" | "failed"; generated_at: string; model: string; mode: string;
  resource_version: number; evidence_version: string; research_date: string | null; quote_as_of: string | null;
  price_refs: Record<string, CandidatePriceReference>; evidence_snapshot: Record<string, unknown>;
  rounds: Array<{ stage: string; opinions: Array<{ role: string; label: string; status: string; issue?: string;
    opinion?: { summary: string; evidence_ids: string[]; uncertainties: string[]; actions: CandidateCondition[]; reply_to: string[]; challenge: string } }> }>;
  summary: null | { conclusion: string; agreements: string[]; disagreements: string[]; missing_evidence: string[];
    routes: Array<{ choice: CandidateChoice; judgment: "investigate" | "not_now" | "insufficient"; reason: string; evidence_ids: string[]; needs: string[] }>;
    focus_choice: CandidateChoice | null; focus_reason: string; actions: CandidateCondition[] };
  summary_issue: string; elapsed_seconds: number; notice: string;
}
export interface CandidateWorkspace {
  symbol: string; name: string; version: number; evidence_version: string; research_date: string | null;
  ownership: { status: "unowned" | "owned" | "unavailable"; position_id: string | null };
  available: boolean; entry_blocked: boolean; new_entry_blocked?: boolean; new_entry_block_reason?: string;
  coverage_note?: string; issues: string[];
  quote: { price?: number | null; as_of?: string | null; quote_as_of?: string | null; source?: string };
  quote_issue: string; evidence: Record<string, unknown>; price_refs: Record<string, CandidatePriceReference>;
  discussion: CandidateDiscussion | null;
  source_refresh?: { state: "updated" | "cached" | "partial" | "busy" | "unavailable"; issue: string };
  analysis: { status: "idle" | "running" | "complete" | "partial" | "failed" | "cancelled"; stage: string; run_id: string | null };
  watch: null | { choice: CandidateChoice; condition: string; invalidation: string; price: number | null; price_ref: string | null;
    source_ref: CandidatePriceReference | null; comparator: WatchComparator | null; adopted_at: string; discussion_id: string; status: "active" | "stopped" };
  observation: null | { state: "waiting" | "price_watch" | "needs_review" | "evidence_unavailable" | "deferred" | "stopped";
    title: string; detail: string; observed_at: string; quote_as_of: string | null; reason: string };
}
export interface CandidateDiscussionInput { expected_version: number; expected_evidence_version: string; llm?: PositionModelConfig; server_model?: string }
export interface CandidateAdoptionInput {
  expected_version: number; expected_evidence_version: string; discussion_id: string; generated_at: string;
  choice: CandidateChoice; action_index: number | null; comparator: WatchComparator | null; operation_id: string;
}

export interface PositionConstraints {
  attention: "available" | "occasional" | "unavailable" | "unknown";
  extra_cash: number | null;
  loss_limit: number | null;
  risk_baseline: { price: number; shares: number; market_value: number; as_of: string; confirmed_at: string } | null;
}
export interface PlanTarget { kind: "position" | "alert"; id: string }
export type FollowupMode = "off" | "changes" | "daily";
export interface FollowupObservation {
  requires_refresh?: boolean; issue?: string;
  reason?: string; joint_pause_pending?: boolean;
  position_id: string; symbol: string; name: string;
  state: "waiting" | "price_watch" | "below_watch" | "paused" | "needs_review" | "evidence_unavailable" | "missing_prices" | "exited" | "unplanned";
  title: string; detail: string; observed_at: string; quote_as_of: string | null;
  revision: string; plan_key: string; position_snapshot: ActionWorksheet["position_snapshot"];
}
export interface ActionWorksheet {
  symbol: string;
  current_risk?: Array<{ status: string; level: string }>;
  trade_revision?: string;
  position_id?: string;
  name?: string;
  revision?: string;
  archived?: boolean;
  followup?: { mode: FollowupMode; last_check: FollowupObservation | null; requires_refresh: boolean; issue: string; current_plan_key?: string };
  constraints?: PositionConstraints;
  constraints_issue?: string;
  risk_baseline_requires_confirmation?: boolean;
  quantity_bounds?: { sellable_shares: number; buy_lot: number; extra_cash_limit: number | null; note: string };
  basis: { checked_at: string; quote_as_of: string | null; quote_source: string; quote_issue: string;
    price: number | null; cost: number | null; shares: number | null; sellable_shares: number | null;
    position_id: string | null; joint_weakness: boolean; personal_pause_t?: boolean;
    references: Array<{ label: string; price: number; as_of: string; source: string }> };
  position_snapshot: { position_id: string | null; cost: number; shares: number; sellable_shares: number };
  review_plan: { choice?: ReviewChoice; watch?: StrategyWatch; watch_stopped?: boolean; parameters?: PlanParameters; discussion?: PositionDiscussion; position_snapshot?: ActionWorksheet["position_snapshot"] };
  recorded_sale?: { date: string; price: number; shares: number } | null;
  sale_requires_review?: boolean;
  scenario: { gross_spread: number; estimated_fees: number; net_cash: number; realized_pnl: number | null; economic_cost: number | null;
    remaining_shares: number; cost_reduction_per_share: number | null; round_trip_positive: boolean | null; note: string } | null;
  scenario_issue: string;
  route_review?: { title: string; summary: string; unrealized_pnl_pct: number | null; recovery_to_cost_pct: number | null;
    routes: Array<{ choice: ReviewChoice; status: "review" | "risk_review" | "paused" | "unknown"; reason: string; needs: string[] }>;
    evidence: { market: DecisionEvidence; sector: DecisionEvidence; stock: DecisionEvidence }; missing_evidence: string[] };
  comparison?: PositionDiscussion | null;
  analysis_basis_id?: string;
}
export interface DecisionEvidence {
  status: string; summary: string; as_of: string | null; source: string; evidence?: unknown;
}
export interface PositionDecisionContext {
  name: string; opened_at: string | null; holding_trade_days: number | null;
  market: DecisionEvidence; sector: DecisionEvidence; stock: DecisionEvidence;
  assessment: { stance: string; title: string; reason: string; t_plan: { eligible: boolean | null; reason: string } };
}
/** `GET /api/alerts` 的 `data`。 */
export interface AlertData {
  events: AlertEvent[];
  open_count: number;
  last_checked_at?: string | null;
  last_check?: RiskCheckCoverage | null;
}
export interface RiskCheckCoverage {
  checked: string[];
  skipped: Array<{ symbol: string; level: string; reason: string }>;
}
/** `POST /api/live-risk/check` 的 `data`。 */
export interface LiveRiskCheckResult {
  reminders: Array<Record<string, unknown>>;
  new_events: AlertEvent[];
  notify: string[];
  notify_suppressed: boolean;
  checks?: RiskCheckCoverage;
  last_checked_at?: string;
}

// 行业涨跌幅：东财对停牌/无数据的板块会给 "-" 占位串，故不能只声明 number。
export interface IndustryRow { rank: number; name: string; change_pct: number | string; code: string; up_count: number; down_count: number }
export interface IndustryData { top: IndustryRow[]; bottom: IndustryRow[]; total: number }

// 全球市场（美股 / 港股，使用东财域内公开行情源）
export interface GlobalIndex {
  key: string; name: string; region: string;
  price: number | null; change_pct: number | null;
  /** 证据链（#317 W3）：该行行情的更新时间；美股档是上一交易日收盘、港股档是港股收盘 */
  as_of?: string | null;
}

/** 单个策略的表现（后端复用 ledger learner 计算，口径唯一）。 */
export interface PerformanceStrategy {
  name: string;
  independent_signal_days: number;
  total_picks: number;
  win_count: number;
  /** 命中率 —— §8 允许的主指标 */
  hit_rate: number;
  /** 冷启动期（独立信号日 < 30）为 false，此时前端不得展示命中率 */
  displayable: boolean;
  weight_base: number;
  weight_confidence: number;
  /** ↓ PnL 派生，仅观测/告警用（§8 禁止作为主指标） */
  avg_return_pct: number;
  max_drawdown: number;
  sharpe_ratio: number;
}

export interface PerformanceDecayAlert {
  strategy: string;
  lookback_days: number;
  decay_days: number;
  recent_win_rate: number;
  recent_avg_return_pct: number;
  severity: string;
  recommendation: string;
}

/**
 * 票级复盘明细（`recent_picks`）：最近 N 笔已结算（validated）信号。
 * 回答"上次具体选了哪只票、事后结果如何"——策略级聚合给不出的颗粒度。
 * 字段均为台账原值直读，不做二次计算。
 */
export interface PerformanceRecentPick {
  symbol: string;
  name: string;
  /** 信号日 YYYY-MM-DD（复盘按此新→旧排序） */
  signal_date: string;
  /** 了结日 YYYY-MM-DD（可能为空） */
  exit_date: string;
  /** 绝对收益 %（相对入场价）；缺失为 null */
  return_pct: number | null;
  /** 超额收益 %（相对基准）；缺失为 null —— 与"真的是 0"区分开 */
  excess_return_pct: number | null;
  /** 是否命中（观测收益为正） */
  win: boolean;
  /** 了结原因：horizon_close / take_profit / stop_loss */
  exit_reason: string;
  /** 关联策略列表 */
  strategies: string[];
}

export interface PerformanceFreshness {
  latest_signal_date: string;
  ledger_updated_at: string;
  /** 最新信号日之后过去的交易日数（按交易日历，长假不算） */
  trading_days_since_latest: number | null;
  stale: boolean;
  stale_after_trading_days: number;
}

export interface PerformancePayload {
  schema_version: string;
  generated_at: string;
  available: boolean;
  reason: string;
  cold_start: {
    is_cold_start: boolean;
    min_independent_signal_days: number;
    independent_signal_days: number;
    max_strategy_signal_days: number;
  };
  /** 台账新鲜度：区分"正在积累"与"已停止更新" */
  freshness: PerformanceFreshness;
  overall: {
    observations: number;
    win_count: number;
    hit_rate: number;
    displayable: boolean;
  } | null;
  strategies: PerformanceStrategy[];
  decay_alerts: PerformanceDecayAlert[];
  /**
   * 票级复盘明细（最近 N 笔 validated 信号）。可选：旧版后端 payload 没有此键，
   * 前端归一化时落到空数组，复盘表如实显示"暂无"，不白屏。
   */
  recent_picks?: PerformanceRecentPick[];
  status_counts: Record<string, number>;
  notes: string[];
}


/** 单条复盘记录（后端 `aqsp.review`，落 `reviews.jsonl`）。 */
export interface ReviewRecord {
  id: string;
  signal_id: string;
  date: string;
  symbol: string;
  rating: number;
  tags: string[];
  notes: string;
  created_at: string;
  updated_at: string;
}



/** `GET /api/aqsp/closing-review` 的 `data.sections` 元素：单段收评（市场级只读）。 */
export interface ClosingReviewSection {
  key: string;
  title: string;
  /** builder 产出的 markdown（首行自带 `## 段标题`；缺数据时为空串）。 */
  markdown: string;
  /** false = 该段缺数据/读失败（fail-soft），前端置灰折叠。 */
  available: boolean;
}

/** `GET /api/aqsp/closing-review` 的 `data`：收评日报 6 段市场级聚合。 */
export interface ClosingReviewPayload {
  as_of: string;
  sections: ClosingReviewSection[];
}


export const api = {
  performance: () => get<PerformancePayload>("/aqsp/performance"),
  aqspSnapshot: (date?: string, options?: AqspRequestOptions): Promise<AqspSnapshotView> =>
    getEnvelope<AqspSnapshotEnvelope>(
      date ? `/aqsp/snapshot?date=${encodeURIComponent(date)}` : "/aqsp/snapshot",
      options,
    )
      .then(({ data, meta }) => ({ ...data, meta })),
  aqspLifecycle: (date?: string): Promise<AqspLifecycleIndex> =>
    get<AqspLifecycleIndex>(
      date ? `/aqsp/candidate-lifecycle?date=${encodeURIComponent(date)}` : "/aqsp/candidate-lifecycle",
    ),
  infoStream: (force = false): Promise<InfoStreamData> =>
    get<InfoStreamData>(force ? "/aqsp/info-stream?force=true" : "/aqsp/info-stream"),
  indices: () => get<IndexQuote[]>("/indices"),
  marketOverview: () => get<MarketOverview>("/market/overview"),
  emotion: () => get<ShortTermEmotion>("/market/emotion"),
  turnoverTop: () => get<TurnoverTop>("/market/turnover-top"),
  globalIndices: () => get<GlobalIndex[]>("/global/indices"),
  /** 数据源健康台账（进程内、重启清空）。只做观测，不影响 `ok`。 */
  health: () => get<HealthPayload>("/health"),
  events: (code: string) => get<EventCalendarData>(`/events?code=${code}`),
  catalyst: () => get<CatalystData>("/catalyst"),
  portfolio: () => get<PortfolioData>("/portfolio"),
  recordWatchlistRemoval: (codes: string[], operation_id: string) =>
    request<{ record: WatchlistRemovalRecord }>(
      "/watchlist/removals",
      "POST",
      { codes, operation_id },
    ),
  watchlistRemovals: (limit = 20) => get<WatchlistRemovalRecord[]>(`/watchlist/removals?limit=${limit}`),
  blocks: (code: string) => get<{ total: number; boards: Array<{ name: string; code: string; change_pct: number; lead_stock: string }>; concept_tags: string[] }>(`/blocks?code=${encodeURIComponent(code)}`),
  addHolding: (code: string, shares: number, cost: number, opened_at?: string, operation_id?: string) => request<PortfolioData>("/portfolio/holding", "POST", { code, shares, cost, opened_at, operation_id }),
  removeHolding: (code: string) => request<PortfolioData>(`/portfolio/holding?code=${code}`, "DELETE"),
  refreshPortfolio: () => request<PortfolioData>("/portfolio/refresh", "POST"),
  closePosition: (code: string, date: string, price: number, shares: number, cost: number, operation_id?: string) =>
    request<PortfolioData>("/portfolio/close", "POST", { code, date, price, shares, cost, operation_id }),
  riskRules: () => get<PositionAlertRules>("/portfolio/risk-rules"),
  updateRiskRule: (id: string, patch: Partial<Pick<PositionAlertRule, "enabled" | "param_value" | "freq_mode">>) =>
    request<PositionAlertRule>(`/portfolio/risk-rules/${encodeURIComponent(id)}`, "PATCH", patch),
  closeAlertPosition: (id: string, date: string, price: number, shares: number) =>
    request<{ event: AlertEvent; portfolio: PortfolioData }>(`/alerts/${encodeURIComponent(id)}/close`, "POST", { date, price, shares }),
  tradeJournal: (code: string) => get<TradeJournal>(`/portfolio/trades?code=${encodeURIComponent(code)}`),
  recordTrade: (input: TradeInput) => request<TradeJournal>("/portfolio/trades", "POST", input),
  updateTradeFees: (receiptId: string, input: TradeFeeInput) => request<TradeJournal>(`/portfolio/trades/${encodeURIComponent(receiptId)}/fees`, "PATCH", input),
  saveTradeLink: (linkId: string, input: TradeLinkInput) => request<TradeJournal>(`/portfolio/trade-links/${encodeURIComponent(linkId)}`, "PUT", input),
  removeClosed: (index: number) => request<PortfolioData>(`/portfolio/close?index=${index}`, "DELETE"),

  // ---- P0-2 该卖提醒（LIVE 风控 → alert_event → 站内收件箱 + 外发）----
  alerts: (opts?: { status?: string; symbol?: string }): Promise<AlertData> => {
    const qs = new URLSearchParams();
    if (opts?.status) qs.set("status", opts.status);
    if (opts?.symbol) qs.set("symbol", opts.symbol);
    const q = qs.toString();
    return get<AlertData>(q ? `/alerts?${q}` : "/alerts");
  },
  alertTransition: (id: string, action: "handled" | "ignored", note = "") =>
    request<AlertEvent>(
      `/alerts/${encodeURIComponent(id)}?action=${action}&note=${encodeURIComponent(note)}`,
      "PATCH",
    ),
  alertReviewPlan: (id: string, choice: ReviewChoice, parameters?: PlanParameters, expected_position?: ActionWorksheet["position_snapshot"], expected_revision?: string) =>
    request<NonNullable<AlertEvent["review_plan"]>>(`/alerts/${encodeURIComponent(id)}/plan`, "POST", { choice, parameters, expected_position, expected_revision }),
  alertActionPlan: (id: string) => get<ActionWorksheet>(`/alerts/${encodeURIComponent(id)}/action-plan`),
  positionActionPlan: (id: string) => get<ActionWorksheet>(`/positions/${encodeURIComponent(id)}/action-plan`),
  refreshStrategyEvidence: (id: string) => request<ActionWorksheet>(`/positions/${encodeURIComponent(id)}/strategy-evidence`, "POST", {}),
  adoptStrategyWatch: (id: string, input: StrategyWatchInput) => request<ActionWorksheet>(`/positions/${encodeURIComponent(id)}/strategy-watch`, "POST", input),
  stopStrategyWatch: (id: string, expected_position: ActionWorksheet["position_snapshot"], expected_revision: string) => request<ActionWorksheet>(`/positions/${encodeURIComponent(id)}/strategy-watch/stop`, "POST", { expected_position, expected_revision }),
  positionFollowups: () => get<{ items: FollowupObservation[] }>("/position-followups"),
  savePositionFollowup: (id: string, mode: FollowupMode, expected_mode: FollowupMode, expected_position: ActionWorksheet["position_snapshot"], expected_revision: string) =>
    request<ActionWorksheet>(`/positions/${encodeURIComponent(id)}/followup`, "POST", { mode, expected_mode, expected_position, expected_revision }),
  savePositionPlan: (id: string, choice: ReviewChoice, expected_position: ActionWorksheet["position_snapshot"], expected_revision: string, parameters?: PlanParameters) =>
    request<ActionWorksheet["review_plan"]>(`/positions/${encodeURIComponent(id)}/plan`, "POST", { choice, parameters, expected_position, expected_revision }),
  savePositionConstraints: (id: string, constraints: Pick<PositionConstraints, "attention" | "extra_cash" | "loss_limit">, expected_position: ActionWorksheet["position_snapshot"], expected_revision: string, reset_risk_baseline = false, expected_quote?: { price: number | null; as_of: string | null }) =>
    request<ActionWorksheet>(`/positions/${encodeURIComponent(id)}/constraints`, "POST", { ...constraints, expected_position, expected_revision, reset_risk_baseline, expected_quote }),
  candidateObservations: () => get<{ items: CandidateWorkspace[] }>("/candidate-observations"),
  candidateObservation: (symbol: string) => get<CandidateWorkspace>(`/candidate-observations/${encodeURIComponent(symbol)}`),
  refreshCandidateEvidence: (symbol: string) => request<CandidateWorkspace>(`/candidate-observations/${encodeURIComponent(symbol)}/strategy-evidence`, "POST", {}),
  candidateDiscussionStream: async (symbol: string, input: CandidateDiscussionInput, progress: (stage: string) => void, signal: AbortSignal): Promise<CandidateDiscussion> => {
    const response = await fetch(`/api/candidate-observations/${encodeURIComponent(symbol)}/discussion/stream`, {
      method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, signal, body: JSON.stringify(input),
    });
    if (!response.ok) throw new ApiError(errorDetail(await response.json().catch(() => null)) || `分析请求失败 HTTP ${response.status}`, response.status);
    return readDiscussionStream<CandidateDiscussion>(response, progress);
  },
  adoptCandidateObservation: (symbol: string, input: CandidateAdoptionInput) => request<CandidateWorkspace>(`/candidate-observations/${encodeURIComponent(symbol)}/adopt`, "POST", input),
  stopCandidateObservation: (symbol: string, expected_version: number, operation_id: string) => request<CandidateWorkspace>(`/candidate-observations/${encodeURIComponent(symbol)}/stop`, "POST", { expected_version, operation_id }),
  positionModels: (refresh = false) => get<PositionModelCatalog>(`/position-models${refresh ? "?refresh=true" : ""}`),
  positionDiscussion: (id: string, choice: ReviewChoice | null, expected_position: ActionWorksheet["position_snapshot"], llm?: PositionModelConfig, server_model?: string, expected_revision?: string) =>
    request<PositionDiscussion>("/alerts/" + encodeURIComponent(id) + "/discussion", "POST", { choice, expected_position, expected_revision, llm, server_model }),
  positionDiscussionStream: async (id: string, choice: ReviewChoice | null, expected_position: ActionWorksheet["position_snapshot"], progress: (stage: string) => void, signal: AbortSignal, llm?: PositionModelConfig, server_model?: string, expected_revision?: string, targetKind: PlanTarget["kind"] = "alert"): Promise<PositionDiscussion> => {
    const response = await fetch(`/api/${targetKind === "position" ? "positions" : "alerts"}/${encodeURIComponent(id)}/discussion/stream`, {
      method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, signal,
      body: JSON.stringify({ choice, expected_position, expected_revision, llm, server_model }),
    });
    if (!response.ok) throw new ApiError(errorDetail(await response.json().catch(() => null)) || `分析请求失败 HTTP ${response.status}`, response.status);
    return readDiscussionStream<PositionDiscussion>(response, progress);
  },
  runLiveRiskCheck: (holding_days?: Record<string, number>, notify = true) =>
    request<LiveRiskCheckResult>("/live-risk/check", "POST", { holding_days, notify }),
  quote: (codes: string) => get<Record<string, Quote>>(`/quote?codes=${codes}`),
  industry: (top = 20) => get<IndustryData>(`/industry?top=${top}`),
  myReports: () => get<MyReport[]>("/myreports"),
  uploadReport: (name: string, contentB64: string) =>
    request<MyReport>("/myreports", "POST", { name, content_b64: contentB64 }),
  deleteReport: (id: string) => request<{ ok: boolean }>(`/myreports/${id}`, "DELETE"),

  // ---- 复盘笔记（aqsp.review；`signal_id` 引用台账行的 `id`）----
  reviews: () => get<ReviewRecord[]>("/reviews"),
  // 收评日报 6 段（市场级只读聚合，fail-soft）
  closingReview: () => get<ClosingReviewPayload>("/aqsp/closing-review"),
};
