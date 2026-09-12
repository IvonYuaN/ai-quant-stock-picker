// 数值格式化与 A 股涨跌配色的契约断言（由 npm test 真正执行）。
import { changeClass, changeTone, formatMoney, formatRate, formatSignedPct, formatYi } from "./format";

export const formatContract = {
  /* ---- 亿：成交额 / 市值 ---- */
  yiConvertsFromYuan: formatYi(1.5e8) === "1.5 亿",
  yiHandlesZero: formatYi(0) === "0.0 亿",
  yiRejectsNull: formatYi(null) === "—",
  yiRejectsNaN: formatYi(Number.NaN) === "—",

  /* ---- 百分比 ---- */
  signedPctAddsPlus: formatSignedPct(2.5) === "+2.50%",
  signedPctKeepsMinus: formatSignedPct(-1) === "-1.00%",
  signedPctZeroIsPlus: formatSignedPct(0) === "+0.00%",
  signedPctRejectsNull: formatSignedPct(null) === "—",
  rateConvertsToOneDecimal: formatRate(0.625) === "62.5%",
  rateRejectsNull: formatRate(null) === "—",

  /* ---- 金额 ---- */
  moneyUsesThousandsSeparator: formatMoney(1234.5).includes("1,234.50"),
  moneyRejectsNull: formatMoney(null) === "未提供",

  /* ---- A 股口径：涨红跌绿（不要用 ok/danger 表达涨跌） ---- */
  positiveIsWarnTone: changeTone(1) === "warn",
  negativeIsOkTone: changeTone(-1) === "ok",
  zeroIsNeutral: changeTone(0) === "neutral",
  nullIsNeutral: changeTone(null) === "neutral",
  positiveClassIsUp: changeClass(1) === "aq-tone-up",
  negativeClassIsDown: changeClass(-1) === "aq-tone-down",
  zeroClassIsEmpty: changeClass(0) === "",
  nullClassIsEmpty: changeClass(null) === "",
};
