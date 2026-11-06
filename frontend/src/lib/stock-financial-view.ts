export type FinancialDisplayValue = number | string | null | undefined;

export function formatStockFinancialValue(
  value: FinancialDisplayValue,
  percentage = false,
): string {
  if (value == null || value === "") return "—";
  if (typeof value === "number" && !Number.isFinite(value)) return "—";
  const raw = typeof value === "string" ? value.trim() : "";
  if (/^(nan|[-+]?infinity|null|none)$/i.test(raw)) return "—";
  const numeric = typeof value === "number" ? value : Number(raw);
  if (!Number.isFinite(numeric)) return String(value).trim() || "—";
  const formatted = new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 2,
  }).format(numeric);
  return percentage ? formatted + "%" : formatted;
}

export function stockFinancialPeriod(value: string | null | undefined): string {
  if (!value) return "报告期未标注";
  const compact = value.replace(/-/g, "");
  if (/^\d{8}$/.test(compact)) {
    return compact.slice(0, 4) + "-" + compact.slice(4, 6) + "-" + compact.slice(6, 8);
  }
  return value;
}
