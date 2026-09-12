// 安全归一化的契约断言（由 npm test 真正执行）。
//
// 这些函数是所有"外部数据 → 界面"的必经之路，出错的表现是整页白屏，
// 所以边界必须钉死。
import { asArray, asNullableNumber, asNumber, asRecord, asString } from "./safe";

export const safeContract = {
  /* ---- asArray ---- */
  arrayPassesThrough: asArray<number>([1, 2]).length === 2,
  nonArrayBecomesEmpty: asArray<number>({}).length === 0,
  nullBecomesEmpty: asArray<number>(null).length === 0,
  stringIsNotAnArray: asArray<string>("abc").length === 0,
  emptyObjectBecomesEmptyArray: asArray<unknown>({}).length === 0,

  /* ---- asRecord ---- */
  objectPassesThrough: asRecord({ a: 1 }).a === 1,
  arrayIsNotARecord: Object.keys(asRecord([1, 2])).length === 0,
  nullBecomesEmptyRecord: Object.keys(asRecord(null)).length === 0,
  stringIsNotARecord: Object.keys(asRecord("x")).length === 0,

  /* ---- asNumber ---- */
  numberPassesThrough: asNumber(3.5) === 3.5,
  numericStringIsParsed: asNumber("12.5") === 12.5,
  zeroIsKept: asNumber(0) === 0,
  nanFallsBack: asNumber(Number.NaN) === 0,
  infinityFallsBack: asNumber(Number.POSITIVE_INFINITY) === 0,
  nullFallsBack: asNumber(null) === 0,
  garbageFallsBack: asNumber("abc") === 0,
  customFallbackIsUsed: asNumber(undefined, -1) === -1,

  /* ---- asNullableNumber：0 与"没提供"必须能区分 ---- */
  nullableKeepsZero: asNullableNumber(0) === 0,
  nullableNullForEmptyString: asNullableNumber("") === null,
  nullableNullForNull: asNullableNumber(null) === null,
  nullableNullForGarbage: asNullableNumber("abc") === null,
  nullableParsesNumericString: asNullableNumber("7.5") === 7.5,

  /* ---- asString ---- */
  stringPassesThrough: asString("x") === "x",
  emptyStringIsKept: asString("") === "",
  numberIsNotAString: asString(1) === "",
  stringFallbackIsUsed: asString(null, "未记录") === "未记录",
};
