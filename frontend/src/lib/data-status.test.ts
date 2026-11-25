// 侧栏数据状态四态的契约断言（由 `npm test` 真正求值）。
//
// 守的是什么：这是全站唯一的**全局**健康指示点。把「读取失败」渲染成「暂无数据」，
// 等于把唯一能提示故障的地方变成噪音 —— 用户会以为今天没数据，而实际上后端已经挂了。
import { dataStatusIsProblem, dataStatusTone, sidebarDataStatus } from "./data-status";

const loading = sidebarDataStatus({ loading: true, hasData: false, error: null });
const loadingWithStale = sidebarDataStatus({ loading: true, hasData: true, error: "HTTP 502" });
const ready = sidebarDataStatus({ loading: false, hasData: true, error: null });
const failedNoData = sidebarDataStatus({ loading: false, hasData: false, error: "HTTP 502" });
const failedWithData = sidebarDataStatus({ loading: false, hasData: true, error: "HTTP 502" });
const genuinelyEmpty = sidebarDataStatus({ loading: false, hasData: false, error: null });
const blankError = sidebarDataStatus({ loading: false, hasData: false, error: "   " });

export const dataStatusContract = {
  /* ---- 四态互不相同 ---- */
  loadingTone: loading.tone === "loading",
  readyTone: ready.tone === "ready",
  errorTone: failedNoData.tone === "error",
  emptyTone: genuinelyEmpty.tone === "empty",
  // 核心：失败 ≠ 空
  failureIsNotEmpty: failedNoData.tone !== genuinelyEmpty.tone,
  failureLabelIsNotNoData: failedNoData.label !== genuinelyEmpty.label,

  /* ---- 文案 ---- */
  loadingSaysReading: loading.label === "读取中",
  readySaysConnected: ready.label === "数据已接入",
  failureWithoutDataSaysReadFailed: failedNoData.label === "读取失败",
  failureWithDataSaysRefreshFailed: failedWithData.label === "刷新失败",
  emptySaysNoData: genuinelyEmpty.label === "暂无数据",

  /* ---- 优先级：正在读 > 失败 > 有数据 ---- */
  loadingBeatsFailure: loadingWithStale.tone === "loading",
  failureBeatsData: failedWithData.tone === "error",

  /* ---- 空白 error 不算失败（避免 `error: ""` 被当成故障） ---- */
  blankErrorIsNotFailure: blankError.tone === "empty",

  /* ---- 指示点类名 ---- */
  errorDotIsDistinct:
    failedNoData.dotClass !== genuinelyEmpty.dotClass &&
    failedNoData.dotClass !== ready.dotClass,
  errorDotUsesErrorClass: failedNoData.dotClass.includes("aq-dot-error"),

  /* ---- 语气映射 ---- */
  readyIsOk: dataStatusTone(ready) === "ok",
  errorIsWarn: dataStatusTone(failedNoData) === "warn",
  emptyIsNeutral: dataStatusTone(genuinelyEmpty) === "neutral",
  onlyErrorIsProblem: dataStatusIsProblem(failedNoData) && !dataStatusIsProblem(genuinelyEmpty),
};
