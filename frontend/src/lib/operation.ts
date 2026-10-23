export const PORTFOLIO_CHANGED_EVENT = "aqsp:portfolio-changed";
export const ALERTS_CHANGED_EVENT = "aqsp:alerts-changed";
export const RECORD_OPERATION_EVENT = "aqsp:record-operation";

export interface OperationTarget {
  direction?: "buy" | "sell";
  code?: string;
  alertId?: string;
}

export interface OperationInput {
  direction: "buy" | "sell";
  code: string;
  price: string;
  shares: string;
  date: string;
}

export function shanghaiDate(current = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(current);
  return ["year", "month", "day"].map((key) => parts.find((part) => part.type === key)?.value).join("-");
}

export function operationError(input: OperationInput, today: string): string {
  if (!/^\d{6}$/.test(input.code.trim())) return "请输入 6 位股票代码";
  if (!Number.isFinite(Number(input.price)) || Number(input.price) <= 0) return "成交价格必须大于 0";
  if (!Number.isFinite(Number(input.shares)) || Number(input.shares) <= 0) return "成交数量必须大于 0";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(input.date) || !Number.isFinite(Date.parse(input.date)) || new Date(`${input.date}T00:00:00Z`).toISOString().slice(0, 10) !== input.date) return "请输入有效成交日期";
  if (input.date > today) return "成交日期不能晚于上海当日";
  return "";
}

export function requestOperation(target: OperationTarget = {}): void {
  window.dispatchEvent(new CustomEvent(RECORD_OPERATION_EVENT, { detail: target }));
}

export function announcePortfolioChange(): void {
  window.dispatchEvent(new Event(PORTFOLIO_CHANGED_EVENT));
  window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
}
