// 「刷新失败、保留上次成功数据」提示的契约断言（由 `npm test` 真正求值）。
//
// 守的是什么：轮询失败时 provider 刻意不清空 data（避免整页闪烁），
// 页面若不提示就等于把几小时前的快照当成"现在"。三条边界必须钉住：
//   ① 没快照时不走提示条（该走 HeroState）；② 切日期时不叠提示（内容区已是加载态）；
//   ③ 文案必须带上「上次更新的时点」，时点缺失时明说，不拿当前时间冒充。
import { refreshWarningText, shouldShowRefreshWarning } from "./refresh-warning";

const showOnStaleData = shouldShowRefreshWarning({ error: "研究快照加载失败", hasData: true, switching: false });
const hideWithoutData = shouldShowRefreshWarning({ error: "研究快照加载失败", hasData: false, switching: false });
const hideWhileSwitching = shouldShowRefreshWarning({ error: "研究快照加载失败", hasData: true, switching: true });
const hideWithoutError = shouldShowRefreshWarning({ error: null, hasData: true, switching: false });
const hideOnBlankError = shouldShowRefreshWarning({ error: "   ", hasData: true, switching: false });

const withStamp = refreshWarningText("HTTP 502", "11-25 14:03");
const withoutStamp = refreshWarningText("HTTP 502", "—");
const withoutReason = refreshWarningText("", "11-25 14:03");
const withoutBoth = refreshWarningText(null, "");

export const refreshWarningContract = {
  /* ---- 什么时候显示 ---- */
  showsOnStaleData: showOnStaleData,
  hidesWithoutData: !hideWithoutData,
  hidesWhileSwitching: !hideWhileSwitching,
  hidesWithoutError: !hideWithoutError,
  hidesOnBlankError: !hideOnBlankError,

  /* ---- 文案三要素：失败了 / 是旧数据 / 旧到什么时候 ---- */
  titleSaysRefreshFailed: withStamp.title.includes("刷新失败"),
  titleSaysDataIsRetained: withStamp.title.includes("上次成功数据"),
  detailCarriesReason: withStamp.detail.includes("HTTP 502"),
  detailCarriesStamp: withStamp.detail.includes("11-25 14:03"),

  /* ---- 时点缺失时不许拿当前时间冒充 ---- */
  missingStampSaysSo: withoutStamp.detail.includes("时点未记录"),
  missingStampIsNotDash: !withoutStamp.detail.includes("—"),

  /* ---- 原因缺失时也要说实话 ---- */
  missingReasonSaysSo: withoutReason.detail.includes("原因未记录"),
  missingEverythingStillHonest:
    withoutBoth.detail.includes("原因未记录") && withoutBoth.detail.includes("时点未记录"),
};
