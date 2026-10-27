import type { TradeJournal, TradeReceipt, TradeAllocation } from "./api";

export function unusedReceiptShares(receipt: TradeReceipt, journal: TradeJournal): number {
  const used = journal.links.flatMap((link) => link.allocations).reduce((sum, allocation) => sum + (
    allocation.sell_receipt_id === receipt.receipt_id || allocation.buy_receipt_id === receipt.receipt_id ? allocation.shares : 0
  ), 0);
  return Math.max(0, receipt.shares - used);
}

export function allocationError(sell: TradeReceipt | undefined, buy: TradeReceipt | undefined, allocation: TradeAllocation, journal: TradeJournal): string {
  if (!sell || !buy) return "请明确选择卖出和买回的两笔成交";
  if (!Number.isFinite(allocation.shares) || allocation.shares <= 0) return "关联股数必须大于 0";
  if (allocation.shares > unusedReceiptShares(sell, journal) || allocation.shares > unusedReceiptShares(buy, journal)) return "关联股数超过尚未使用的成交数量";
  if (sell.date > buy.date) return "买回日期不能早于卖出日期";
  if (sell.date === buy.date && !allocation.confirm_same_day_order) return "同日成交需核对确实先卖、后买";
  if (sell.position_id !== buy.position_id && !allocation.confirm_cross_position) return "两笔属于不同仓位，需明确确认跨清仓重开的关联";
  return "";
}

export function actualFeeError(value: string): string {
  return value.trim() && (!Number.isFinite(Number(value)) || Number(value) < 0) ? "实际总费用须为非负数字；未知留空" : "";
}

export function netDifferenceText(value: number | null, matched: number): string {
  if (!matched) return "尚未关联实际买回，不计算完成价差";
  if (value === null) return "实际费用待补，净差价尚未确定";
  if (value < 0) return `已匹配部分少留下 ${Math.abs(value).toFixed(2)} 元`;
  if (value > 0) return `已匹配部分多留下 ${value.toFixed(2)} 元`;
  return "已匹配部分扣费后差价为 0 元";
}
