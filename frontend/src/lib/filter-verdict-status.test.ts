// 「筛选器判读」回退标注的契约断言（由 `npm test` 真正求值）。
//
// 守的是什么：请求失败与「后端说工件缺失」是两件事，误诊会把用户引去查 runner，
// 而真正坏的是后端/网络。原实现 `.catch(() => {})` 把两者合并成同一句「工件未就绪」。
import { filterVerdictFallback } from "./filter-verdict-status";

const AS_OF = "2026-11-16";

const loading = filterVerdictFallback(null, "", AS_OF);
const failed = filterVerdictFallback(null, "HTTP 502", AS_OF);
const failedWithSnapshot = filterVerdictFallback({ available: true }, "连接不到后端", AS_OF);
const missingWithReason = filterVerdictFallback(
  { available: false, reason: "pit_cache/filter_verdict.json 缺失" },
  "",
  AS_OF,
);
const missingNoReason = filterVerdictFallback({ available: false }, "", AS_OF);
const missingBlankReason = filterVerdictFallback({ available: false, reason: "  " }, "", AS_OF);
const wronglyAsked = filterVerdictFallback({ available: true }, "", AS_OF);

export const filterVerdictStatusContract = {
  /* ---- 三态互不相同 ---- */
  loadingIsLoading: loading.state === "loading",
  failureIsFailed: failed.state === "failed",
  missingIsUnavailable: missingWithReason.state === "unavailable",
  loadingIsNotFailure: loading.state !== failed.state,
  // 关键：读取失败不能被说成「工件未就绪」
  failureIsNotUnavailable: failed.state !== missingWithReason.state,

  /* ---- 请求失败：必须点明是读取链路问题 ---- */
  failureLabelCarriesError: failed.state === "failed" && failed.label.includes("HTTP 502"),
  failureLabelSaysNotTheSameAsMissing:
    failed.state === "failed" && failed.label.includes("不等于工件未生成"),
  failureBeatsSnapshot: failedWithSnapshot.state === "failed",

  /* ---- 后端说缺失：透出它给的原因 ---- */
  missingLabelCarriesReason:
    missingWithReason.state === "unavailable" &&
    missingWithReason.label.includes("pit_cache/filter_verdict.json"),
  missingNoReasonSaysSo:
    missingNoReason.state === "unavailable" && missingNoReason.label.includes("后端未说明原因"),
  missingBlankReasonTreatedAsMissing:
    missingBlankReason.state === "unavailable" && missingBlankReason.label.includes("后端未说明原因"),

  /* ---- 标注必须带固化日期：不能让内嵌快照看起来像实时数据 ---- */
  everyLabelCarriesAsOf:
    loading.label.includes(AS_OF) &&
    failed.label.includes(AS_OF) &&
    missingWithReason.label.includes(AS_OF),

  /* ---- 不该问的时候问，也不能撒谎说工件未就绪 ---- */
  availableSnapshotDoesNotClaimMissing:
    wronglyAsked.state === "unavailable" && !wronglyAsked.label.includes("未就绪"),
};
