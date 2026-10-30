import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import type { ActionWorksheet, PositionDiscussion, StrategyReviewContext } from "./api";
import { positionChangeEvidenceText, positionChangeView } from "./position-change-review";
import { PositionActionPlan } from "../components/aqsp/PositionActionPlan";
import { PositionDiscussionPanel } from "../components/aqsp/PositionDiscussionPanel";
import { PositionChangeReview } from "../components/aqsp/StrategyReviewContextPanel";

const stamp = "2026-10-30T10:00:00+08:00";
const reference = { label: "合成高点", price: 10, as_of: stamp, source: "合成源" };
const contextFixture: StrategyReviewContext = {
  kind: "position", status: "adopted", original: { choice: "hold", condition: "原持有前提", invalidation: "板块走弱需复核", saved_at: stamp, price_ref: reference, snapshot_complete: true },
  current: {}, changes: [{ category: "sector", label: "板块依据变化", before: { status: "neutral", source: "合成行业源" }, after: { status: "falling", source: "合成行业源" } }],
  missing_evidence: [], material_changed: true, quote_updated: false, notice: "合成历史对比，不是当前买卖依据",
};
const worksheetFixture: ActionWorksheet = {
  symbol: "600000", name: "合成持仓", position_id: "p1", revision: "r1", archived: false,
  basis: { checked_at: stamp, quote_as_of: stamp, quote_source: "合成源", quote_issue: "", price: 9.8, cost: 12, shares: 1000, sellable_shares: 1000, position_id: "p1", joint_weakness: false, references: [reference] },
  position_snapshot: { position_id: "p1", cost: 12, shares: 1000, sellable_shares: 1000 },
  review_plan: { choice: "hold", watch: { action: "observe", comparator: "at_or_above", price: 10, condition: "先核对完整前提", invalidation: "板块走弱先重核", price_ref: reference, source_generated_at: stamp, saved_at: stamp } },
  strategy_review_context: contextFixture, scenario: null, scenario_issue: "",
};
const discussionFixture: PositionDiscussion = { status: "failed", generated_at: stamp, model: "synthetic", mode: "合成", choice: "hold", position_snapshot: worksheetFixture.position_snapshot, quote_as_of: stamp, price_refs: {}, rounds: [], summary: null, notice: "合成模型失败", review_context: { ...contextFixture, original: { ...contextFixture.original!, condition: "模型旧上下文标记" } } };
const planHtml = (worksheet: ActionWorksheet) => renderToStaticMarkup(createElement(PositionActionPlan, { choice: "hold", worksheet }));
const reviewHtml = (worksheet: ActionWorksheet) => renderToStaticMarkup(createElement(PositionChangeReview, { worksheet }));
const sectorHtml = reviewHtml(worksheetFixture);
const sourceFixture = { ...contextFixture, changes: [{ category: "quote_source", label: "行情来源或有效性变化", before: { source: "合成源", available: true }, after: { source: "", available: false } }] };
const sourceHtml = reviewHtml({ ...worksheetFixture, strategy_review_context: sourceFixture });
const missingHtml = reviewHtml({ ...worksheetFixture, strategy_review_context: { ...contextFixture, original: { ...contextFixture.original!, snapshot_complete: false }, changes: [], material_changed: false, missing_evidence: ["原大盘证据未保存"] } });
const unchangedFixture = { ...contextFixture, changes: [], material_changed: false, quote_updated: true };
const unchangedHtml = reviewHtml({ ...worksheetFixture, strategy_review_context: unchangedFixture });
const riskHtml = planHtml({ ...worksheetFixture,
  basis: { ...worksheetFixture.basis, checked_at: new Date().toISOString(), quote_as_of: new Date().toISOString() },
  review_plan: { ...worksheetFixture.review_plan, watch: { ...worksheetFixture.review_plan.watch!, position_snapshot: worksheetFixture.position_snapshot } },
  current_risk: [{ level: "L1", status: "triggered" }],
  strategy_review_context: { ...unchangedFixture, quote_updated: false },
});
const failedHtml = planHtml({ ...worksheetFixture, review_plan: { ...worksheetFixture.review_plan, discussion: discussionFixture } });
const completeFixture: PositionDiscussion = { ...discussionFixture, status: "complete", summary: { conclusion: "合成模型结论", agreements: [], disagreements: [], missing_evidence: [], actions: [] } };
const completeHtml = planHtml({ ...worksheetFixture, review_plan: { ...worksheetFixture.review_plan, discussion: completeFixture } });
const stoppedHtml = planHtml({ ...worksheetFixture, review_plan: { choice: "hold", watch_stopped: true, discussion: completeFixture }, strategy_review_context: null });
const mismatchHtml = reviewHtml({ ...worksheetFixture, strategy_review_context: { ...contextFixture, status: "identity_mismatch", original: { ...contextFixture.original!, condition: "别笔持仓隐私条件" } } });
const legacyHtml = renderToStaticMarkup(createElement(PositionDiscussionPanel, { choice: "hold", disabled: false, worksheet: { ...worksheetFixture, strategy_review_context: undefined, review_plan: { ...worksheetFixture.review_plan, discussion: completeFixture } } }));
const serverContextFixture = { ...contextFixture, material_changed: false };

export const positionChangeDisplayContracts = {
  sectorChangeReadableBeforeConditions: sectorHtml.includes("板块依据变化") && sectorHtml.includes("中性") && sectorHtml.includes("走弱") && sectorHtml.includes("先核对板块"),
  detailedHistoryCollapsed: sectorHtml.includes('<details class="aq-strategy-review-context">') && !sectorHtml.includes('<details class="aq-strategy-review-context" open'),
  sourceLossStaysUnavailable: sourceHtml.includes("来源可用") && sourceHtml.includes("来源不可用") && sourceHtml.includes("先核对行情来源"),
  missingOriginalEvidenceStaysUnknown: missingHtml.includes("原判断还不能完整核验") && missingHtml.includes("原大盘证据未保存") && !missingHtml.includes("未发现实质依据变化"),
  quoteTickIsNotMaterialInvalidation: unchangedHtml.includes("这本身不代表原方案失效") && !unchangedHtml.includes("判断依据有变化"),
  emptyChangeIsCompact: positionChangeView({ ...worksheetFixture, strategy_review_context: unchangedFixture })?.changes.length === 0,
  unchangedDoesNotClearExistingRisk: riskHtml.includes("当前风险线仍触发，重新核对持有依据") && riskHtml.includes("不代表买卖前提已满足或风险已经解除") && !riskHtml.includes("继续按已采用条件观察"),
  serverMaterialFlagIsNotRecomputed: positionChangeView({ ...worksheetFixture, strategy_review_context: serverContextFixture })?.changes.length === 0,
  modelFailureDoesNotHideIndependentChange: failedHtml.includes("板块依据变化") && failedHtml.includes("尚无通过校验的汇总结论") && failedHtml.indexOf("采用后发生了什么变化") < failedHtml.indexOf("先核对完整前提"),
  successfulModelDoesNotRepeatOldContext: !completeHtml.includes("模型旧上下文标记") && (completeHtml.match(/<summary>原方案与当前依据对比<\/summary>/g) ?? []).length === 1,
  legacyModelContextStillReadable: legacyHtml.includes("模型旧上下文标记"),
  stoppedWatchHasNoCurrentChangeAdvice: !stoppedHtml.includes("采用后发生了什么变化") && !stoppedHtml.includes("模型旧上下文标记") && stoppedHtml.includes("这项策略跟踪已停止"),
  stoppedFlagSuppressesStaleContext: positionChangeView({ ...worksheetFixture, review_plan: { ...worksheetFixture.review_plan, watch_stopped: true } }) === null,
  archivedHasNoCurrentChangeAdvice: !planHtml({ ...worksheetFixture, archived: true }).includes("采用后发生了什么变化"),
  notAdoptedDoesNotInventComparison: positionChangeView({ ...worksheetFixture, strategy_review_context: { ...contextFixture, status: "not_adopted" } }) === null,
  missingContextCompatible: positionChangeView({ ...worksheetFixture, strategy_review_context: null }) === null,
  identityMismatchDoesNotExposeOtherPosition: mismatchHtml.includes("变化保持未知") && !mismatchHtml.includes("别笔持仓隐私条件") && !mismatchHtml.includes("中性"),
  normalizedIndexDirectionsPreserved: positionChangeEvidenceText({ status: "strong", indices: [{ name: "合成指数", direction: "down" }] }).includes("下跌"),
  unavailableDoesNotMeanSell: !sourceHtml.includes("直接卖出") && !sourceHtml.includes("必须清仓"),
};
