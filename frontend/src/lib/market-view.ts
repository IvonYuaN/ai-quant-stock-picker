// 市场环境展示模型。
//
// 后端早就有指数、涨跌家数、短线情绪（连板梯队/炸板率/封板率/晋级率）、成交额榜、行业榜、
// 隔夜外围六个接口，前端此前一个都没用 —— 于是"今天大盘什么脸色"这个判断前提整块缺失。
//
// 这里把它们收敛成一个 `MarketView`，并显式区分「接口失败」与「接口返回空」：
// 两个都表现为空数组/ null，但前者要提示可重试、后者要说明数据源没覆盖，不能混为一谈。
import type {
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
}

export interface BreadthView {
  up: number;
  down: number;
  flat: number;
  zt: number;
  ztReal: number;
  dt: number;
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
  industryTop: IndustryRowView[];
  industryBottom: IndustryRowView[];
  industryTotal: number;
  updated: string;
}

export interface MarketInput {
  indices?: readonly IndexQuote[];
  global?: readonly GlobalIndex[];
  overview?: MarketOverview | null;
  emotion?: ShortTermEmotion | null;
  turnover?: readonly TurnoverStock[];
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
  };
}

function buildBreadth(overview: MarketOverview | null | undefined): BreadthView | null {
  const s = overview?.sentiment;
  if (!s) return null;
  const up = s.up ?? 0;
  const down = s.down ?? 0;
  return {
    up,
    down,
    flat: s.flat ?? 0,
    zt: s.zt ?? 0,
    ztReal: s.zt_real ?? 0,
    dt: s.dt ?? 0,
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
    industryTop: asArray<IndustryRow>(input.industry?.top).map(toIndustryRow),
    industryBottom: asArray<IndustryRow>(input.industry?.bottom)
      .map(toIndustryRow)
      .reverse(),
    industryTotal: input.industry?.total ?? 0,
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
  if (view.indices.length === 0 && !view.breadth && view.global.length === 0) return null;

  const breadth = view.breadth;
  const indexUp = view.indices.filter((item) => item.changePct > 0).length;
  const indexTotal = view.indices.length;

  let tone: Tone = "neutral";
  let headline = "市场环境未记录";
  if (breadth) {
    const ratio = breadth.upDownRatio;
    if (breadth.breadth === "普涨" || (ratio != null && ratio >= 2.5)) {
      tone = "ok";
      headline = `普涨格局 · 上涨 ${breadth.up} / 下跌 ${breadth.down}`;
    } else if (breadth.breadth === "冰点" || (ratio != null && ratio < 0.7)) {
      tone = "warn";
      headline = `普跌格局 · 上涨 ${breadth.up} / 下跌 ${breadth.down}`;
    } else {
      tone = "neutral";
      headline = `中性震荡 · 上涨 ${breadth.up} / 下跌 ${breadth.down}`;
    }
  } else if (indexTotal > 0) {
    tone = indexUp > indexTotal / 2 ? "ok" : indexUp === 0 ? "warn" : "neutral";
    headline = `${indexTotal} 个指数中 ${indexUp} 个上涨`;
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
  emotion: boolean;
  turnover: boolean;
  industry: boolean;
  global: boolean;
}

export function sectionAvailability(view: MarketView): SectionAvailability {
  return {
    indices: view.indices.length > 0,
    breadth: view.breadth !== null,
    emotion: view.emotion !== null,
    turnover: view.turnover.length > 0,
    industry: view.industryTop.length > 0 || view.industryBottom.length > 0,
    global: view.global.length > 0,
  };
}
