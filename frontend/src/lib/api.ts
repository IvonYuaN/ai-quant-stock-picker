// AQSP 后端 API 客户端。/api → vite 代理到本地 FastAPI（默认 8900）。
// 后端未启动或数据源异常时抛 ApiError，页面据此优雅降级。
import type { AqspLifecycleIndex, AqspSnapshotEnvelope, AqspSnapshotView, InfoStreamData } from "@/types/aqsp";

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
const USER_ID = "vr-user-id";

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

// 取本浏览器身份；首次访问时生成并固化一个 UUID，后续复用。
export function loadUserId(): string {
  try {
    let id = localStorage.getItem(USER_ID);
    if (!id) {
      id = (crypto.randomUUID?.() ?? `u-${Date.now()}-${Math.random().toString(36).slice(2)}`);
      localStorage.setItem(USER_ID, id);
    }
    return id;
  } catch {
    return "";
  }
}

export function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const k = loadAccessKey();
  if (k) headers["Authorization"] = `Bearer ${k}`;
  // 本浏览器身份：公网/鉴权模式下让持仓、研报按访客隔离（本地单用户模式后端忽略此头）。
  const uid = loadUserId();
  if (uid) headers["X-User-Id"] = uid;
  return headers;
}

export interface MyReport {
  id: string; name: string; industry: string; size: number; ext: string; ts: number;
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
  method: "GET" | "POST" | "PUT" | "DELETE" = "GET",
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
  method: "GET" | "POST" | "PUT" | "DELETE" = "GET",
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
  up: number; down: number; flat: number; zt: number; zt_real: number; dt: number; dt_real: number;
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
  market_value: number; pnl: number; pnl_pct: number;
}
export interface ClosedPosition {
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
  events: (code: string) => get<EventCalendarData>(`/events?code=${code}`),
  catalyst: () => get<CatalystData>("/catalyst"),
  portfolio: () => get<PortfolioData>("/portfolio"),
  addHolding: (code: string, shares: number, cost: number) => request<PortfolioData>("/portfolio/holding", "POST", { code, shares, cost }),
  removeHolding: (code: string) => request<PortfolioData>(`/portfolio/holding?code=${code}`, "DELETE"),
  refreshPortfolio: () => request<PortfolioData>("/portfolio/refresh", "POST"),
  closePosition: (code: string, date: string, price: number, shares: number, cost: number) =>
    request<PortfolioData>("/portfolio/close", "POST", { code, date, price, shares, cost }),
  removeClosed: (index: number) => request<PortfolioData>(`/portfolio/close?index=${index}`, "DELETE"),
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
