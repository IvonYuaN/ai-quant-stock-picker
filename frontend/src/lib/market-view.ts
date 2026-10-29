// 市场环境展示模型。
//
// 后端早就有指数、涨跌家数、短线情绪（连板梯队/炸板率/封板率/晋级率）、成交额榜、行业榜、
// 隔夜外围六个接口，前端此前一个都没用 —— 于是"今天大盘什么脸色"这个判断前提整块缺失。
//
// 这里把它们收敛成一个 `MarketView`，并显式区分「接口失败」与「接口返回空」：
// 两个都表现为空数组/ null，但前者要提示可重试、后者要说明数据源没覆盖，不能混为一谈。
import type {
  DataSourceHealthRow,
  GlobalIndex,
  IndexQuote,
  IndustryData,
  IndustryRow,
  LianbanStock,
  MarketOverview,
  SectorFlow,
  ShortTermEmotion,
  TurnoverStock,
} from "./api";
import type { Tone } from "./daily-view";
import { asArray } from "./safe";

/* ------------------------------------------------------------------ 视图类型 */

export interface IndexRow {
  key: string;
  name: string;
  price: number;
  changePct: number;
  changeAmt: number;
}

export interface GlobalIndexRow {
  key: string;
  name: string;
  region: string;
  price: number | null;
  changePct: number | null;
}

export interface SectorFlowRow {
  name: string;
  pct: number;
  net: number;
  firms: number;
}

export interface LadderRow {
  boards: number;
  count: number;
  plus: boolean;
}

export interface LianbanRow {
  code: string;
  name: string;
  boards: number;
  price: number;
  pct: number;
  amount: number | null;
  floatCap: number | null;
  industry: string;
}

export interface TurnoverRow {
  code: string;
  name: string;
  price: number | null;
  pct: number | null;
  amount: number | null;
  mcap: number | null;
  floatCap: number | null;
  industry: string;
}

export interface IndustryRowView {
  rank: number;
  name: string;
  code: string;
  changePct: number | null;
  upCount: number;
  downCount: number;
  asOf: string | null;
}

export interface BreadthView {
  up: number;
  down: number;
  flat: number;
  total: number;
  source: string;
  asOf: string;
  /** 涨停家数（东财涨停池口径）；null = 池子取数失败，不是「0 家涨停」。 */
  zt: number | null;
  ztReal: number;
  /** 跌停家数；null = 池子取数失败，不是「0 家跌停」。 */
  dt: number | null;
  dtReal: number;
  active: string;
  breadth: string;
  speculation: string;
  date: string;
  /** 上涨/下跌比；分母为 0 时返回 null，不伪装成 0。 */
  upDownRatio: number | null;
}

export interface EmotionView {
  date: string;
  ztCount: number;
  dtCount: number;
  zbCount: number;
  maxBoards: number;
  lianbanCount: number;
  yztCount: number;
  ladder: LadderRow[];
  lianbanStocks: LianbanRow[];
  sealRate: number | null;
  breakRate: number | null;
  promotionRate: number | null;
}

export interface MarketView {
  indices: IndexRow[];
  global: GlobalIndexRow[];
  breadth: BreadthView | null;
  sectorsTop: SectorFlowRow[];
  sectorsBottom: SectorFlowRow[];
  emotion: EmotionView | null;
  turnover: TurnoverRow[];
  /**
   * 成交额榜的**数据时间**：后端从东财 clist 的 `f124`（逐行行情时间）取最新一条，
   * 不是取数时刻。盘中比当前时间晚几秒，收盘后停在收盘时刻，盘前停在上一交易日。
   * 空串 = 源未标注（后端不会用取数时刻兜底）。
   */
  turnoverUpdated: string;
  /**
   * 大盘指数的**数据时间**（该组里最新的一条行情时间）；空串 = 源未给。
   * 与 `updated`（取数时刻）不是一回事：盘中它是成交时间，收盘后它停在收盘时刻。
   */
  indicesUpdated: string;
  /** 隔夜外围的**数据时间**（美股档是上一交易日收盘、港股档是港股收盘）。 */
  globalUpdated: string;
  industryTop: IndustryRowView[];
  industryBottom: IndustryRowView[];
  industryTotal: number;
  /** 行业榜供应商行情时点；未提供 f124 时留空，不用 API 取数时间代替。 */
  industryUpdated: string;
  updated: string;
}

export interface MarketInput {
  indices?: readonly IndexQuote[];
  global?: readonly GlobalIndex[];
  overview?: MarketOverview | null;
  emotion?: ShortTermEmotion | null;
  turnover?: readonly TurnoverStock[];
  /** turnover 接口自带的**数据时间**（W3 证据链：榜单逐块标注数据日期）。 */
  turnoverUpdated?: string;
  industry?: IndustryData | null;
}

/* ------------------------------------------------------------------ 派生 */

function toIndexRow(item: IndexQuote, index: number): IndexRow {
  return {
    key: `${item.name}-${index}`,
    name: item.name,
    price: item.price,
    changePct: item.change_pct,
    changeAmt: item.change_amt,
  };
}

function toGlobalRow(item: GlobalIndex): GlobalIndexRow {
  return {
    key: item.key || item.name,
    name: item.name,
    region: item.region,
    price: item.price,
    changePct: item.change_pct,
  };
}

/**
 * 一组行情行里最新的 `as_of`（#317 W3）；全都缺失返回空串。
 *
 * 按 `Date.parse` 得到的**瞬时**比较，不按字符串比较：偏移量写法不同时
 * （`09:00+08:00` vs `02:00+00:00`）字符串比较会给出错误答案。
 * 解析不出的值直接跳过 —— 宁可少标注一条，也不把无法解释的字符串当时间用。
 */
function latestAsOf(rows: readonly { as_of?: string | null }[]): string {
  let best = "";
  let bestTs = Number.NEGATIVE_INFINITY;
  for (const row of rows) {
    const raw = row.as_of;
    if (!raw) continue;
    const ts = Date.parse(raw);
    if (!Number.isFinite(ts)) continue;
    if (ts > bestTs) {
      bestTs = ts;
      best = raw;
    }
  }
  return best;
}

function toSectorRow(item: SectorFlow): SectorFlowRow {
  return { name: item.name, pct: item.pct, net: item.net, firms: item.firms };
}

function toLianbanRow(item: LianbanStock): LianbanRow {
  return {
    code: item.code,
    name: item.name,
    boards: item.boards,
    price: item.price,
    pct: item.pct,
    amount: item.amount,
    floatCap: item.float_cap,
    industry: item.industry,
  };
}

function toTurnoverRow(item: TurnoverStock): TurnoverRow {
  return {
    code: item.code,
    name: item.name,
    price: item.price,
    pct: item.pct,
    amount: item.amount,
    mcap: item.mcap,
    floatCap: item.float_cap,
    industry: item.industry,
  };
}

/** 行业涨跌幅可能是数字也可能是 "-" 之类的占位串，统一成 number | null。 */
function toIndustryRow(item: IndustryRow): IndustryRowView {
  const raw = typeof item.change_pct === "number" ? item.change_pct : Number(item.change_pct);
  return {
    rank: item.rank,
    name: item.name,
    code: item.code,
    changePct: Number.isFinite(raw) ? raw : null,
    upCount: item.up_count,
    downCount: item.down_count,
    asOf: item.as_of ?? null,
  };
}

function buildBreadth(overview: MarketOverview | null | undefined): BreadthView | null {
  const s = overview?.sentiment;
  if (!s || s.coverage_complete !== true || s.source !== "东方财富全A逐只行情宽度") return null;
  // The API verifies source age on every read, including old SWR caches. The
  // client also rejects legacy/partial payloads; a pool date proves no coverage.
  const { up, down, flat, total, as_of: asOf, latest_source_at: latest } = s;
  if (![up, down, flat, total].every((value) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0) ||
      total === undefined || up + down + flat > total || up + down + flat <= 0) return null;
  const sourceTime = (stamp: unknown): stamp is string => typeof stamp === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(stamp) && Number.isFinite(Date.parse(stamp));
  if (!sourceTime(asOf) || !sourceTime(latest) || Date.parse(asOf) > Date.parse(latest)) return null;
  return {
    up,
    down,
    flat: s.flat ?? 0,
    total,
    source: s.source,
    asOf,
    zt: s.zt ?? null,
    ztReal: s.zt_real ?? 0,
    dt: s.dt ?? null,
    dtReal: s.dt_real ?? 0,
    active: s.active ?? "",
    breadth: s.breadth ?? "",
    speculation: s.speculation ?? "",
    date: s.date ?? "",
    upDownRatio: down > 0 ? up / down : null,
  };
}

function buildEmotion(emotion: ShortTermEmotion | null | undefined): EmotionView | null {
  if (!emotion || !emotion.date) return null;
  return {
    date: emotion.date,
    ztCount: emotion.zt_count ?? 0,
    dtCount: emotion.dt_count ?? 0,
    zbCount: emotion.zb_count ?? 0,
    maxBoards: emotion.max_boards ?? 0,
    lianbanCount: emotion.lianban_count ?? 0,
    yztCount: emotion.yzt_count ?? 0,
    ladder: asArray<LadderRow>(emotion.ladder).map((tier) => ({
      boards: tier.boards,
      count: tier.count,
      plus: tier.plus,
    })),
    lianbanStocks: asArray<LianbanStock>(emotion.lianban_stocks).map(toLianbanRow),
    sealRate: emotion.seal_rate ?? null,
    breakRate: emotion.break_rate ?? null,
    promotionRate: emotion.promotion_rate ?? null,
  };
}

export function buildMarketView(input: MarketInput): MarketView {
  const sectors = asArray<SectorFlow>(input.overview?.sectors).map(toSectorRow);
  return {
    indices: asArray<IndexQuote>(input.indices).map(toIndexRow),
    global: asArray<GlobalIndex>(input.global).map(toGlobalRow),
    breadth: buildBreadth(input.overview),
    // 资金轮动看头尾：净流入前列与净流出前列
    sectorsTop: sectors.slice(0, 6),
    sectorsBottom: sectors.slice(-6).reverse(),
    emotion: buildEmotion(input.emotion),
    turnover: asArray<TurnoverStock>(input.turnover).map(toTurnoverRow),
    turnoverUpdated: input.turnoverUpdated ?? "",
    indicesUpdated: latestAsOf(asArray<IndexQuote>(input.indices)),
    globalUpdated: latestAsOf(asArray<GlobalIndex>(input.global)),
    industryTop: asArray<IndustryRow>(input.industry?.top).map(toIndustryRow),
    industryBottom: asArray<IndustryRow>(input.industry?.bottom)
      .map(toIndustryRow)
      .reverse(),
    industryTotal: input.industry?.total ?? 0,
    industryUpdated: latestAsOf([
      ...asArray<IndustryRow>(input.industry?.top),
      ...asArray<IndustryRow>(input.industry?.bottom),
    ]),
    updated: input.overview?.updated ?? "",
  };
}

/** 今日页顶部的紧凑环境摘要：只保留"判断能不能动"最需要的几项。 */
export interface MarketStripSummary {
  indices: IndexRow[];
  breadth: BreadthView | null;
  global: GlobalIndexRow[];
  /** 一句话环境定性，用于页面顶部的语气提示。 */
  headline: string;
  tone: Tone;
}

export function marketStripSummary(view: MarketView | null): MarketStripSummary | null {
  if (!view) return null;
  if (view.indices.length === 0 && !view.breadth && view.global.length === 0 &&
      view.industryTop.length === 0 && view.industryBottom.length === 0 &&
      view.sectorsTop.length === 0 && view.sectorsBottom.length === 0) return null;

  const breadth = view.breadth;
  const indexTotal = view.indices.length;

  let tone: Tone = "neutral";
  let headline = "市场环境未记录";
  if (breadth) {
    // Reuse the authenticated server band; do not reinterpret a label as the
    // number of decliners or maintain a second set of classification cutoffs.
    if (["普涨", "偏强"].includes(breadth.breadth)) {
      tone = "ok";
    } else if (["冰点", "偏弱"].includes(breadth.breadth)) {
      tone = "warn";
    }
    headline = `市场宽度${breadth.breadth || "未判定"} · 上涨 ${breadth.up} / 下跌 ${breadth.down}`;
  } else if (indexTotal > 0) {
    // 指数涨跌不是全市场宽度或所属板块方向的替身。
    tone = "neutral";
    headline = `市场宽度未取得 · 仅有 ${indexTotal} 个指数报价`;
  } else if (view.industryTop.length || view.industryBottom.length) {
    headline = "大盘方向未取得 · 当前只有行业榜数据";
  }

  return { indices: view.indices, breadth, global: view.global, headline, tone };
}

/* ------------------------------------------------- 各板块是否"确实有数据" */

/**
 * 区分"接口失败"与"接口返回空"。
 * 两者在数据上都是空，但提示文案完全不同，不能混为一谈。
 */
export interface SectionAvailability {
  indices: boolean;
  breadth: boolean;
  /** 板块资金流。**必须与 breadth 分开**：二者来自不同源，一块空了不该连坐另一块。 */
  sectors: boolean;
  emotion: boolean;
  turnover: boolean;
  industry: boolean;
  global: boolean;
}

export function sectionAvailability(view: MarketView): SectionAvailability {
  return {
    indices: view.indices.length > 0,
    breadth: view.breadth !== null,
    sectors: view.sectorsTop.length > 0 || view.sectorsBottom.length > 0,
    emotion: view.emotion !== null,
    turnover: view.turnover.length > 0,
    industry: view.industryTop.length > 0 || view.industryBottom.length > 0,
    global: view.global.length > 0,
  };
}

/* ------------------------------------------------------- 数据源健康（为什么空） */

/**
 * 主机 → 人话标签。只影响**展示**，不参与任何判断；认不出的主机原样显示。
 *
 * 为什么要这张表：页面上「行业榜为空」有两种完全不同的原因 ——
 * ①今天真的没数据；②这个源连不上。用户看到的却是同一句「未返回」。
 */
const SOURCE_LABELS: Readonly<Record<string, string>> = {
  "push2.eastmoney.com": "东财行情（指数 / 榜单 / 外围）",
  "push2his.eastmoney.com": "东财板块日K",
  "push2delay.eastmoney.com": "东财延时行情",
  "push2ex.eastmoney.com": "东财涨停板",
  "datacenter-web.eastmoney.com": "东财数据中心",
  "searchapi.eastmoney.com": "东财代码搜索",
  "qt.gtimg.cn": "腾讯行情",
  "legulegu.com": "乐咕乐股（涨跌家数）",
  "data.10jqka.com.cn": "同花顺（板块资金流）",
};

export interface SourceHealthNotice {
  /** 不可达的源数量（不是展示出来的条数，可能更多）。 */
  unhealthy: number;
  title: string;
  detail: string;
}

/** 最多点名几个源；再多就只报个数，避免把提示条撑成一段日志。 */
const MAX_NAMED_SOURCES = 3;

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

function shortStamp(iso: string | null): string {
  // 后端给的是带 +08:00 的 ISO（上海时区）。这里只取「MM-DD HH:MM」，
  // 不做时区换算——换算会引入浏览器本地时区，反而可能把北京时间挪走。
  const match = (iso ?? "").match(/(\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : "";
}

/**
 * 把后端台账收敛成一条提示；全健康（或还没记过任何源）时返回 `null`。
 *
 * ⚠️ 这条提示的**唯一职责**是纠正一句假陈述：面板为空 ≠ 今天没数据。
 * 所以文案里必须同时出现「哪个源坏了」和「所以空不代表没数据」两件事，
 * 只说前者，用户仍会以为是行情本身的问题。
 */
export function summarizeSourceHealth(
  rows: readonly DataSourceHealthRow[] | null | undefined,
): SourceHealthNotice | null {
  const unhealthy = (rows ?? []).filter((row) => row && !row.healthy);
  if (unhealthy.length === 0) return null;

  const named = unhealthy.slice(0, MAX_NAMED_SOURCES).map((row) => {
    const since = shortStamp(row.down_since);
    const times = row.consecutive_errors > 0 ? `连续 ${row.consecutive_errors} 次失败` : "失败";
    return `${sourceLabel(row.source)}（${times}${since ? `，自 ${since}` : ""}）`;
  });
  const rest = unhealthy.length - named.length;
  const list = rest > 0 ? `${named.join("、")} 等 ${unhealthy.length} 个源` : named.join("、");

  return {
    unhealthy: unhealthy.length,
    title: "数据源不可达",
    detail: `${list} 取数失败。由它们供数的板块为空是源侧原因，不代表今天没有数据；可稍后刷新重试。`,
  };
}
