import type { ActionWorksheet, ReviewChoice } from "./api";

/** Source levels supply conditional observations, never inferred trade signals. */
export function planSteps(choice: ReviewChoice, basis: ActionWorksheet["basis"], selected?: { sell_price: number; buy_price: number; quantity: number }): string[] {
  const observedHigh = basis.references.find((ref) => ref.label === "本交易日已见高点") ?? basis.references.find((ref) => ref.label === "最近完成日高点");
  const observedLow = basis.references.find((ref) => ref.label === "本交易日已见低点") ?? basis.references.find((ref) => ref.label === "最近完成日低点");
  const high = selected && Number.isFinite(selected.sell_price) && selected.sell_price > 0 ? { label: "本批观察卖价，可调整", price: selected.sell_price } : observedHigh;
  const low = selected && Number.isFinite(selected.buy_price) && selected.buy_price > 0 ? { label: "本批观察回补价，可调整", price: selected.buy_price } : observedLow;
  const quantity = selected && Number.isInteger(selected.quantity) && selected.quantity > 0 ? `本批 ${selected.quantity} 股` : "数量由你设定";
  const price = (ref: { label: string; price: number } | undefined) => ref ? `${ref.price.toFixed(2)}（${ref.label}）` : "价位待补";
  const weakness = "大盘、所属板块与个股同日同步走弱时，暂停回补并优先复核减仓退出；单项风险线触发仍须独立复核。";
  if (choice === "hold") return [
    `暂不加仓；对照 ${price(low)} 观察是否继续破低，对照 ${price(high)} 观察反弹能否延续。`,
    "跌破观察低点且未收回、原买入依据失效，或损失超出你能承受的范围时，改为减仓复核；不把成本价当必然回归的目标。",
    weakness,
  ];
  if (choice === "reduce") return [
    `反弹接近 ${price(high)} 却未能继续走强时，复核是否分批减仓（${quantity}）；价格是观察锚点，到价不等于卖出信号。`,
    `若继续跌破 ${price(low)}，不坚持等反弹；结合既有风险线复核是否降低仓位。`,
    "在下方填本批卖出数量，核对卖出后剩余股数及亏损；下一批取决于新行情，不默认等分仓位。卖出后不安排自动回补。",
    weakness,
  ];
  return [
    `先卖：反弹接近 ${price(high)} 且冲高回落时，复核用可卖底仓做一笔差价（${quantity}）；未出现该条件就等待。`,
    `后买：已实际卖出后，回落接近 ${price(low)} 且止跌时才复核回补；买价和数量可调整，回补不超过该笔实际卖出量。`,
    "先比较净差价：卖出收入减回补支出，再扣双边佣金、卖出税费和滑点；若净差价不为正，就没有降低经济成本。",
    "卖出后持续上涨：取消原低价回补计划，重新评估，不追高保证买回；继续破低：暂停回补，重查趋势。当天回补的股份受 T+1 限制。",
    weakness,
  ];
}

export function worksheetExpired(basis: ActionWorksheet["basis"], now = Date.now()): boolean {
  // Display freshness is conservative; the API rechecks its source-time gate.
  const checked = Date.parse(basis.checked_at);
  return !Number.isFinite(checked) || now < checked || now - checked > 60_000;
}
