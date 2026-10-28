import type { StrategyReviewContext } from "./api";

const labels: Record<string, string> = {
  hold: "继续持有观察", reduce: "分步减仓", t_watch: "做 T 观察", pullback: "等待回落", confirmation: "等待确认", defer: "暂缓关注",
  weak: "偏弱", strong: "偏强", neutral: "中性", falling: "走弱", rising: "走强", flat: "持平", unknown: "未知", unavailable: "不可用",
  blocked: "门禁阻塞", allowed: "门禁通过", investigate: "可进一步核对", not_now: "当前不宜", insufficient: "依据不足",
  fresh: "新鲜", stale: "已过期", synthetic: "合成数据", available: "可用", occasional: "只能偶尔看盘",
  pass: "通过", fail: "未通过", triggered: "已触发", not_triggered: "未触发", open: "待复核", OPEN: "待复核",
  L1: "成本止损", L2: "均线走弱", L4: "MACD 走弱", L5: "移动止损",
  up: "上涨", down: "下跌",
};
const fields: Record<string, string> = {
  summary: "依据", status: "状态", source: "来源", as_of: "时点", price: "价格", condition: "条件", invalidation: "失效条件",
  name: "名称", display_name: "股票名", symbol: "股票代码", label: "参考", choice: "路线", market: "大盘", sector: "板块", stock: "个股",
  recommendation_allowed: "研究资格", reasons: "原因", score: "确定性评分", deterministic_reasons: "评分依据", freshness: "数据新鲜度",
  data_source: "数据来源", data_fetched_at: "数据时点", industry: "行业", version: "依据版本", effective_from: "生效时间",
  cost: "持仓成本（元）", shares: "持有股数", sellable_shares: "可卖股数", attention: "盯盘条件", extra_cash: "新增资金上限（元）",
  loss_limit: "新增亏损上限（元）", risk_baseline: "风险起算依据", market_value: "持仓市值（元）", confirmed_at: "确认时点",
  rules: "风险规则", level: "风险类型", threshold: "阈值", actual: "检查值", current_facts: "本次风险事实", detail: "详情", reason: "原因",
  breadth: "市场宽度", indices: "指数背景", direction: "涨跌方向",
};

/** Evidence stays plain text; arbitrary server objects never become model conclusions. */
export function reviewEvidenceText(value: unknown, depth = 0): string {
  if (value == null || value === "") return "未保存或不可用";
  if (typeof value === "string") return labels[value] || value;
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "不可用";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map((item) => reviewEvidenceText(item, depth + 1)).join("；") || "未保存或不可用";
  if (typeof value === "object" && depth < 5) {
    const content = Object.entries(value).filter(([key]) => key in fields).map(([key, item]) => {
      const attention = key === "attention" && typeof item === "string"
        ? ({ available: "能持续看盘", occasional: "只能偶尔看一眼", unavailable: "没有时间盯盘", unknown: "还不确定" } as Record<string, string>)[item] : null;
      return `${fields[key]}：${attention || reviewEvidenceText(item, depth + 1)}`;
    });
    return content.join("；") || "详细字段未在此摘要展开";
  }
  return "详细字段未在此摘要展开";
}

export function strategyReviewView(context: StrategyReviewContext | undefined): { title: string; state: string; originalVisible: boolean } | null {
  if (!context || context.status === "not_adopted") return null;
  if (context.status === "identity_mismatch") return { title: "原方案身份不一致", state: "原记录不属于当前对象，不能沿用；请重新分析当前条件。", originalVisible: false };
  if (!context.original) return { title: "原方案依据待补", state: "未取得原采用记录，不能从当前值倒推原方案。", originalVisible: false };
  return { title: "原方案与这次依据有什么变化", originalVisible: true,
    state: context.material_changed ? "实质依据已有变化，结合这次 AI 结论决定继续等待、调整路线或暂缓。" : context.missing_evidence.length ? "原依据存在缺口，尚不能确认原方案是否仍然成立；先核对下方缺失项。" : context.quote_updated ? "报价更新，未记录到实质依据变化；报价变化本身不代表原方案已失效。" : "本次未记录到实质依据变化，仍需核对缺失依据与条件。" };
}

export function originalReviewPrice(context: StrategyReviewContext): string {
  const ref = context.original?.price_ref;
  // Never resolve a historical reference ID through current.price_refs.
  return ref && typeof ref === "object" && Number.isFinite(ref.price) && ref.price > 0
    ? `${ref.price.toFixed(2)} 元 · ${ref.label} · ${ref.source} · ${ref.as_of}` : "原观察价依据未完整保存";
}
