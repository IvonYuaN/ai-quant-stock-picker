// 安全归一化的契约断言（由 npm test 真正执行）。
//
// 这些函数是所有"外部数据 → 界面"的必经之路，出错的表现是整页白屏，
// 所以边界必须钉死。
import { asArray, asNullableNumber, asNumber, asRecord, asString, settleSection } from "./safe";

/**
 * `settleSection` 的三态契约。
 *
 * 守的是什么：`Promise.allSettled` 的失败段如果被折回 `null`，页面会把它渲染成
 * 「正在读取…」并永远转圈；写成「两段都失败才报错」更糟——单段失败连原因都没有。
 * 这类 bug 不崩页面、截图看不出来，只能靠断言钉住。
 */
const fulfilledOf = <T,>(value: T): PromiseSettledResult<T> => ({ status: "fulfilled", value });
const rejectedOf = (reason: unknown): PromiseSettledResult<never> => ({ status: "rejected", reason });

const readyRows = settleSection<{ code: string }>(fulfilledOf([{ code: "600000" }]), "指数");
const readyEmpty = settleSection<{ code: string }>(fulfilledOf<readonly { code: string }[]>([]), "指数");
const readyNull = settleSection<{ code: string }>(fulfilledOf<readonly { code: string }[] | null>(null), "指数");
const readyUndefined = settleSection<{ code: string }>(fulfilledOf<readonly { code: string }[] | undefined>(undefined), "指数");
const failedWithError = settleSection<{ code: string }>(rejectedOf(new Error("HTTP 502")), "指数");
const failedWithString = settleSection<{ code: string }>(rejectedOf("boom"), "指数");
const failedWithNothing = settleSection<{ code: string }>(rejectedOf(undefined), "指数");

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

  /* ---- settleSection：成功有数据 ---- */
  sectionFulfilledKeepsRows: readyRows.state === "ready" && readyRows.rows.length === 1,
  sectionFulfilledKeepsValue: readyRows.state === "ready" && readyRows.rows[0].code === "600000",

  /* ---- settleSection：成功但为空 = 源正常返回、今天确实没有，不是失败 ---- */
  sectionFulfilledEmptyStaysReady: readyEmpty.state === "ready" && readyEmpty.rows.length === 0,
  sectionFulfilledNullStaysReady: readyNull.state === "ready" && readyNull.rows.length === 0,
  sectionFulfilledUndefinedStaysReady: readyUndefined.state === "ready" && readyUndefined.rows.length === 0,

  /* ---- settleSection：失败必须带原因，且不能伪装成空表 ---- */
  sectionRejectedIsFailed: failedWithError.state === "failed",
  sectionRejectedCarriesReason:
    failedWithError.state === "failed" && failedWithError.message.includes("HTTP 502"),
  sectionRejectedNamesTheSection:
    failedWithError.state === "failed" && failedWithError.message.includes("指数"),
  sectionRejectedSaysEmptyIsNotNoData:
    failedWithError.state === "failed" && failedWithError.message.includes("不代表今天没有数据"),
  sectionNonErrorReasonStillReported:
    failedWithString.state === "failed" && failedWithString.message.includes("boom"),
  sectionEmptyReasonFallsBackToUnknown:
    failedWithNothing.state === "failed" && failedWithNothing.message.includes("未知原因"),
  sectionFailureIsDistinctFromEmpty: readyEmpty.state === "ready" && failedWithError.state === "failed",
};
