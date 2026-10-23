/** Research links carry the exact snapshot date across the workbench. */
function dateQuery(date?: string): string {
  if (!date) return "";
  const parsed = new Date(`${date}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date.startsWith("0000") ||
    !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
    throw new RangeError("研究日期必须为有效的 YYYY-MM-DD");
  }
  return `?date=${encodeURIComponent(date)}`;
}

export function stockResearchHref(symbol: string, date?: string): string {
  if (!/^\d{6}$/.test(symbol)) throw new RangeError("股票代码必须为 6 位数字");
  return `/stock/${symbol}${dateQuery(date)}`;
}

export function todayResearchHref(date?: string): string {
  return `/today${dateQuery(date)}`;
}
