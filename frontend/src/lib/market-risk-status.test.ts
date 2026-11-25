// 市场风险三态判定的契约断言（由 `npm test` 真正求值）。
//
// 守的是什么：后端 `available:false` 会带 `reason`（"样本不足（需 ≥65 个交易日且 ≥300 只票）"），
// 这是**可行动的信息**。页面原来统一渲染「暂无数据」，等于把它丢了；
// 而「正在读取」与「请求失败」也被折进同一句，用户分不清该等还是该查。
import { marketRiskStatus, marketRiskStatusText } from "./market-risk-status";

const loading = marketRiskStatus(null, "");
const failed = marketRiskStatus(null, "HTTP 503");
const ready = marketRiskStatus({ available: true }, "");
const unavailableWithReason = marketRiskStatus(
  { available: false, reason: "样本不足（需 ≥65 个交易日且 ≥300 只票）" },
  "",
);
const unavailableNoReason = marketRiskStatus({ available: false }, "");
const unavailableBlankReason = marketRiskStatus({ available: false, reason: "   " }, "");
const errorBeatsSnapshot = marketRiskStatus({ available: true }, "连接不到后端");

export const marketRiskStatusContract = {
  /* ---- 三态必须互不相同 ---- */
  loadingIsLoading: loading.state === "loading",
  failureIsFailed: failed.state === "failed",
  unavailableIsUnavailable: unavailableWithReason.state === "unavailable",
  readyIsReady: ready.state === "ready",
  loadingIsNotUnavailable: loading.state !== unavailableWithReason.state,
  failureIsNotUnavailable: failed.state !== unavailableWithReason.state,

  /* ---- 后端给的原因必须原样透出，不能被「暂无数据」吃掉 ---- */
  backendReasonIsShown:
    unavailableWithReason.state === "unavailable" &&
    unavailableWithReason.text.includes("需 ≥65 个交易日"),
  backendReasonIsNotPlaceholder: marketRiskStatusText(unavailableWithReason) !== "暂无数据",
  noReasonSaysSoInsteadOfNoData:
    unavailableNoReason.state === "unavailable" &&
    unavailableNoReason.text.includes("没有说明原因"),
  blankReasonTreatedAsMissing:
    unavailableBlankReason.state === "unavailable" &&
    unavailableBlankReason.text.includes("没有说明原因"),

  /* ---- 文案 ---- */
  loadingTextSaysReading: marketRiskStatusText(loading).includes("正在读取"),
  failureTextIsTheError: marketRiskStatusText(failed) === "HTTP 503",
  readyTextIsEmpty: marketRiskStatusText(ready) === "",

  /* ---- 有快照也不能盖掉失败：请求失败时我们并不知道市场面 ---- */
  errorTakesPrecedence: errorBeatsSnapshot.state === "failed",
};
