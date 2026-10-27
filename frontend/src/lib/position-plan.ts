import type { ActionWorksheet, ReviewChoice } from "./api";

/** Source levels supply conditional observations, never inferred trade signals. */
export function planSteps(choice: ReviewChoice, basis: ActionWorksheet["basis"], selected?: { sell_price: number; buy_price: number; quantity: number }): string[] {
  const observedHigh = basis.references.find((ref) => ref.label === "本交易日已见高点") ?? basis.references.find((ref) => ref.label === "最近完成日高点");
  const observedLow = basis.references.find((ref) => ref.label === "本交易日已见低点") ?? basis.references.find((ref) => ref.label === "最近完成日低点");
  const high = selected && Number.isFinite(selected.sell_price) && selected.sell_price > 0 ? { label: "本批观察卖价，可调整", price: selected.sell_price } : observedHigh;
  const low = selected && Number.isFinite(selected.buy_price) && selected.buy_price > 0 ? { label: "本批观察回补价，可调整", price: selected.buy_price } : observedLow;
  const quantity = selected && Number.isInteger(selected.quantity) && selected.quantity > 0 ? `本批 ${selected.quantity} 股` : "数量由你设定";
  const price = (ref: { label: string; price: number } | undefined) => ref ? `${ref.price.toFixed(2)}（${ref.label}）` : "价位待补";
  const weakness = "如果大盘、板块和个股当天一起走弱，先暂停做 T，重新看是否需要减仓；单条提醒不等于必须清仓。";
  if (choice === "hold") return [
    `现在先做：先不加仓，观察价格能否回到 ${price(high)}；不要把成本价当成一定会回来的目标。`,
    `什么时候取消：价格继续跌破 ${price(low)} 且没有收回，或你无法接受继续亏损，就重新复核减仓。`,
    weakness,
  ];
  if (choice === "reduce") return [
    `现在先做：反弹接近 ${price(high)} 后转弱时，复核是否先减 ${quantity}；到价不等于自动卖出。`,
    `什么时候取消：如果直接跌破 ${price(low)}，不要为了等回本继续扛，重新复核是否降低仓位。`,
    "填入本批数量后，会显示卖出后剩余股数和预计盈亏；卖出后不会自动安排回补。",
    weakness,
  ];
  return [
    `现在先做：只有可卖底仓足够，且反弹接近 ${price(high)} 后转弱，才复核先卖 ${quantity}；否则先等。`,
    `什么时候继续：实际卖出成交后，回落接近 ${price(low)} 且止跌，才复核回补；回补数量不能超过实际卖出量。`,
    "先填数量和费用，确认扣除双边成本后仍有正差价；没有正差价就不算降低成本。",
    "卖出后如果持续上涨，取消低价回补计划；如果继续破低，暂停回补并重新检查趋势。当天新买的股份受 T+1 限制。",
    weakness,
  ];
}

export function worksheetExpired(basis: ActionWorksheet["basis"], now = Date.now()): boolean {
  // Display freshness is conservative; the API rechecks its source-time gate.
  const checked = Date.parse(basis.checked_at);
  return !Number.isFinite(checked) || now < checked || now - checked > 60_000;
}

/** Task wording for the main card; detailed reasoning remains in planSteps. */
export function beginnerPlan(choice: ReviewChoice, basis: ActionWorksheet["basis"], selected?: { sell_price: number; buy_price: number }): { next: string; then?: string; stop: string; paused: boolean } {
  const ref = (label: string, fallback: string) => basis.references.find((row) => row.label === label) ?? basis.references.find((row) => row.label === fallback);
  const sell = selected?.sell_price && Number.isFinite(selected.sell_price) && selected.sell_price > 0 ? selected.sell_price : ref("本交易日已见高点", "最近完成日高点")?.price;
  const buy = selected?.buy_price && Number.isFinite(selected.buy_price) && selected.buy_price > 0 ? selected.buy_price : ref("本交易日已见低点", "最近完成日低点")?.price;
  const price = (value: number | undefined) => value ? `${value.toFixed(2)} 元` : "待补的观察价";
  if (basis.quote_issue) return { next: "先更新行情，再看下一步。", stop: "旧价位暂时只作参考。", paused: true };
  if (choice !== "hold" && !basis.sellable_shares) return { next: "今天没有可卖的股票，先观察，不安排卖出。", stop: "等股份可以卖出后，再更新计划。当天买入的股票当天不能卖。", paused: true };
  if (choice === "t_watch" && basis.joint_weakness) return { next: "大盘、板块和这只股票都在走弱，先暂停做 T。", stop: "重新评估减仓，不因亏损继续加仓。", paused: true };
  if (!sell || !buy) return { next: "还缺少观察价，先更新行情。", stop: "价位补齐前，不用这份计划安排买卖。", paused: true };
  if (choice === "hold") return { next: `先不加仓，看反弹到 ${price(sell)} 附近能否站稳。`, stop: `跌破 ${price(buy)} 后仍未收回，重新评估减仓。`, paused: false };
  if (choice === "reduce") return { next: `反弹到 ${price(sell)} 附近又转弱，再考虑卖出一部分。`, stop: `如果直接跌破 ${price(buy)}，别只等反弹，重新评估风险。`, paused: false };
  return { next: `先等反弹到 ${price(sell)} 附近转弱，再考虑卖出部分可卖持仓。`, then: `卖出成交后，回落到 ${price(buy)} 附近止跌，才考虑买回同样或更少股数。`, stop: "卖出后持续上涨，或回落后继续破低，都取消原回补计划。", paused: false };
}

export const PLAN_CHOICES = {
  hold: { title: "先观察", benefit: "保留仓位，等走势更清楚", cost: "继续承担这只股票下跌的损失" },
  reduce: { title: "先减仓", benefit: "减少后续下跌影响的钱", cost: "锁定卖出部分的当前盈亏，也可能错过反弹" },
  t_watch: { title: "做 T 试算", benefit: "用部分持仓尝试赚取一次差价", cost: "要盯盘、付两次费用，卖出后可能买不回来" },
} as const;

/** Describe observable facts, never infer reversal, execution or suitability. */
export function planJourney(choice: ReviewChoice, worksheet: ActionWorksheet, prices: { sell_price: number; buy_price: number }, now = Date.now()): { label: string; detail: string; attention: boolean; saleReview: boolean } {
  const { basis, recorded_sale: sale, review_plan: plan } = worksheet;
  const saved = plan.position_snapshot;
  const positionChanged = !saved || ["position_id", "cost", "shares", "sellable_shares"].some((key) => saved[key as keyof typeof saved] !== worksheet.position_snapshot[key as keyof typeof saved]);
  if (sale && (worksheet.sale_requires_review ?? positionChanged)) return { label: "已回录卖出，先核对后续", detail: `${sale.date} 已记录卖出 ${sale.shares} 股，成交价 ${sale.price.toFixed(2)} 元；当前剩 ${basis.shares ?? "待核对"} 股。${choice === "t_watch" ? "这笔卖出未确认属于本次做 T，先核对余仓，不沿用旧试算继续买回。" : "剩余仓位仍有涨跌风险，不沿用卖出前的试算。"}`, attention: true, saleReview: true };
  if (worksheetExpired(basis, now) || basis.quote_issue || !basis.price) return { label: "先更新有效行情", detail: basis.quote_issue || "上次行情已不能用于当前判断，旧计划暂作参考。", attention: true, saleReview: false };
  if (choice !== "hold" && !basis.sellable_shares) return { label: "今天先观察", detail: "当前没有可卖股份；等可以卖出后，再重新核对计划。", attention: true, saleReview: false };
  if (basis.joint_weakness) return { label: choice === "t_watch" ? "先暂停做 T" : "先重新看风险", detail: "大盘、板块和个股同日走弱，不能只等反弹；先核对是否需要降低仓位。", attention: true, saleReview: false };
  const sell = Number.isFinite(prices.sell_price) && prices.sell_price > 0 ? prices.sell_price : basis.references.find((row) => row.label === "本交易日已见高点")?.price ?? basis.references.find((row) => row.label === "最近完成日高点")?.price;
  const buy = Number.isFinite(prices.buy_price) && prices.buy_price > 0 ? prices.buy_price : basis.references.find((row) => row.label === "本交易日已见低点")?.price ?? basis.references.find((row) => row.label === "最近完成日低点")?.price;
  const current = basis.price;
  if (!sell || !buy || !Number.isFinite(sell) || !Number.isFinite(buy) || sell <= 0 || buy <= 0) return { label: "先补观察价", detail: "没有有效参考价，还无法判断现在处于哪个位置。", attention: true, saleReview: false };
  if (current < buy) return { label: "已低于观察低价，重新看风险", detail: `当前 ${current.toFixed(2)} 元，低于你正在看的 ${buy.toFixed(2)} 元。是否收回尚未确认，先复核风险，做 T 不继续沿用原回补计划。`, attention: true, saleReview: false };
  if (current >= sell) return { label: choice === "hold" ? "已到观察高价，看能否站稳" : "已到观察卖价，转弱尚未确认", detail: `当前 ${current.toFixed(2)} 元，已到 ${sell.toFixed(2)} 元参考价。到价只说明位置变化，不能证明已经转弱或应该成交。`, attention: false, saleReview: false };
  return { label: choice === "hold" ? "观察反弹是否延续" : "还在等观察价", detail: `当前 ${current.toFixed(2)} 元，到 ${sell.toFixed(2)} 元还差 ${(sell - current).toFixed(2)} 元。${choice === "hold" ? "继续关注是否破低。" : "暂未到价，不需要把计划当成立即卖出。"}`, attention: false, saleReview: false };
}
