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
