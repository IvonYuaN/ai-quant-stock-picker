// 影子账户策略判定的契约断言（由 node scripts/run-contracts.mjs 求值）。
//
// 核心不变量：**超额算不出来（null）≠ 超额为零/为负**。
// `research/paper_ledger.py` 会在「有结算笔数但没有任何基准超额」时把 `exc_mean`
// 留成 `None`，这时原实现 `(row.exc_mean ?? 0) > 0` 会把该策略标成「无超额」——
// 一个确定的否定结论，而同一行的数字列显示的是「—」。
import type { PaperArenaSleeve } from "./api";
import { isAsOfStale, sleeveVerdict, unknownExcessCount } from "./paper-arena-view";

type Sleeve = Pick<PaperArenaSleeve, "n" | "exc_mean" | "q_exc">;

const unopened: Sleeve = { n: 0, exc_mean: null, q_exc: null };
const missingExcess: Sleeve = { n: 7, exc_mean: null, q_exc: 0.02 };
const zeroExcess: Sleeve = { n: 7, exc_mean: 0, q_exc: 0.02 };
const survivedRow: Sleeve = { n: 7, exc_mean: 1.2, q_exc: 0.05 };
const uncorrectedRow: Sleeve = { n: 7, exc_mean: 1.2, q_exc: 0.5 };
const negativeRow: Sleeve = { n: 7, exc_mean: -0.3, q_exc: 0.05 };

const unknownVerdict = sleeveVerdict(missingExcess);
const noEdgeVerdict = sleeveVerdict(zeroExcess);
const survivedVerdict = sleeveVerdict(survivedRow);
const uncorrectedVerdict = sleeveVerdict(uncorrectedRow);

export const paperArenaViewContract = {
  // 一笔都没结算：未开仓，既不是通过也不是无超额
  unopenedIsUnopened: sleeveVerdict(unopened).kind === "unopened",

  // 有结算笔数但超额算不出来 ⇒ 未知（不是「无超额」）
  missingExcessIsUnknown: unknownVerdict.kind === "unknown",
  missingExcessLabelIsNotNoEdge: unknownVerdict.label !== "无超额",
  missingExcessLabelSaysUnknown: unknownVerdict.label === "超额未知",
  // 未知不能染成否定色：它不是证据
  missingExcessToneIsNeutral: unknownVerdict.tone === "neutral",
  // 未知必须带解释，否则用户只会看到「超额未知」四个字不知为何
  missingExcessHasDetail: unknownVerdict.kind === "unknown" && unknownVerdict.detail.length > 0,

  // 🔴 关键对照：真的等于 0 才算「无超额」，与 null 必须分开
  zeroExcessIsNoEdge: noEdgeVerdict.kind === "no-edge",
  zeroExcessDiffersFromMissing: unknownVerdict.kind !== noEdgeVerdict.kind,

  // 非有限值（NaN/Infinity）等同未知，不能被当成 0 参与比较
  nanExcessIsUnknown: sleeveVerdict({ n: 3, exc_mean: Number.NaN, q_exc: null }).kind === "unknown",
  infiniteExcessIsUnknown:
    sleeveVerdict({ n: 3, exc_mean: Number.POSITIVE_INFINITY, q_exc: null }).kind === "unknown",

  // 🔴 正向路径只保留 kind（下游计数用），**不能恢复成绿色判决词**。
  // 账本按「封死也照买」记账，P1/P6 在这里 q=0 显著，却被推送闸门判 rejected。
  survivedIsSurvived: sleeveVerdict(survivedRow).kind === "survived",
  survivedToneIsNeutral: sleeveVerdict(survivedRow).tone === "neutral",
  survivedLabelIsNotVerdict: sleeveVerdict(survivedRow).label !== "扛住多重检验",
  survivedLabelIsStatOnly: sleeveVerdict(survivedRow).label === "超额显著",
  survivedHasScopeDetail: "detail" in survivedVerdict && survivedVerdict.detail.includes("不等于策略过门"),
  uncorrectedIsUncorrected: sleeveVerdict(uncorrectedRow).kind === "uncorrected",
  uncorrectedLabelIsNotPass: sleeveVerdict(uncorrectedRow).label !== "未校正通过",
  uncorrectedHasScopeDetail: "detail" in uncorrectedVerdict && uncorrectedVerdict.detail.includes("不等于策略过门"),
  negativeIsNoEdge: sleeveVerdict(negativeRow).kind === "no-edge",
  negativeToneIsWarn: sleeveVerdict(negativeRow).tone === "warn",

  // q 缺失但超额为正：不能算「超额显著」
  missingQIsNotSurvived: sleeveVerdict({ n: 5, exc_mean: 2.0, q_exc: null }).kind !== "survived",

  // 汇总披露：只数「有结算笔数且超额未知」的，未开仓的不算
  unknownCountCountsMissing: unknownExcessCount([missingExcess, survivedRow]) === 1,
  unknownCountExcludesUnopened: unknownExcessCount([unopened, survivedRow]) === 0,
  unknownCountExcludesRealZero: unknownExcessCount([zeroExcess, negativeRow]) === 0,
  unknownCountCountsAll: unknownExcessCount([missingExcess, unopened, zeroExcess, missingExcess]) === 2,
};

/** #684 前端独立陈旧防线：后端 stale=false 也不能掩盖旧的 as_of。 */
export const asOfStaleContract = {
  // prod 真实场景：as_of=2026-11-20、今天 2026-11-25 ⇒ 必须判旧
  prodStaleCase: isAsOfStale("2026-11-20", "2026-11-25"),
  // 当天/隔天正常
  freshSameDay: !isAsOfStale("2026-11-25", "2026-11-25"),
  freshOneDay: !isAsOfStale("2026-11-24", "2026-11-25"),
  // 4 天自然日（周末 + 周一）不误报
  weekendNotStale: !isAsOfStale("2026-11-21", "2026-11-25"),
  // 没有日期不判旧（由「未取得」显式态负责）
  missingNotStale: !isAsOfStale(null, "2026-11-25"),
  badFormatNotStale: !isAsOfStale("not-a-date", "2026-11-25"),
};
