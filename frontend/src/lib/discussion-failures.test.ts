import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import type { PositionDiscussion } from "./api";
import { discussionFailureReasons } from "./discussion-failures";
import { DiscussionFailures } from "../components/aqsp/DiscussionFailures";

const failed: PositionDiscussion = { status: "failed", generated_at: "2026-10-29T10:00:00+08:00", model: "synthetic", mode: "合成", choice: null,
  position_snapshot: { position_id: "p1", shares: 1000, sellable_shares: 1000, cost: 10 }, quote_as_of: null, price_refs: {}, summary: null,
  summary_issue: "有效意见不足两名角色", summary_failure_kind: "insufficient_roles", notice: "合成",
  rounds: [{ stage: "independent", opinions: [
    { role: "trend", label: "trend", status: "failed", failure_kind: "timeout" },
    { role: "risk", label: "risk", status: "failed", failure_kind: "invalid_output", contract_issue_code: "action_numbers", issue: "private-model-output" },
    { role: "t_review", label: "t_review", status: "failed", failure_kind: "invalid_output", contract_issue_code: "action_evidence" },
  ] }] };
const reasons = discussionFailureReasons(failed);
const failedHtml = renderToStaticMarkup(createElement(DiscussionFailures, { result: failed }));
const summary = { conclusion: "合成结论", agreements: [], disagreements: [], missing_evidence: [], actions: [] };
const partialHtml = renderToStaticMarkup(createElement(DiscussionFailures, { result: { ...failed, status: "partial", summary } }));
const resolved = discussionFailureReasons({ ...failed, rounds: [...failed.rounds, { stage: "challenge", opinions: [{ role: "risk", label: "risk", status: "ok" }] }] });
const legacy = discussionFailureReasons({ ...failed, rounds: [{ stage: "independent", opinions: [{ role: "trend", label: "unsafe-label", status: "failed", issue: "private-model-output" }] }] });
const unknown = discussionFailureReasons({ ...failed, rounds: [{ stage: "independent", opinions: [{ role: "__proto__", label: "unsafe-label", status: "failed", contract_issue_code: "toString", failure_kind: "constructor" }] }] });
const summaryFailed = discussionFailureReasons({ ...failed, rounds: [], summary_failure_kind: "invalid_output", summary_contract_issue_code: "route_contract" });
export const discussionFailureContracts = {
  allThreeRealFailureShapesHaveReadableReasons: reasons.length === 3 && reasons[0].detail.includes("响应超时") && reasons[1].detail.includes("股数") && reasons[2].detail.includes("历史统计"),
  rejectedTextNeverEchoed: !failedHtml.includes("private-model-output"),
  failureIsVisibleWithoutExpandingRawRoles: failedHtml.includes("本次分析未完成的原因") && !failedHtml.includes("<details"),
  failedAnalysisCannotCreateAdoptionAction: !failedHtml.includes("采用这项条件") && !failedHtml.includes("<button"),
  successfulSummaryKeepsSecondaryFailuresCollapsed: partialHtml.startsWith("<details ") && !partialHtml.includes(" open=") && partialHtml.includes("3 项分析未完成"),
  sameRoleFailureDoesNotSurviveSuccessfulLaterReply: resolved.length === 2 && resolved.every(row => row.label !== "风险复核"),
  noRepeatedInsufficientRolesError: reasons.every(row => row.label !== "汇总"),
  summaryContractFailureIsDistinguished: summaryFailed.length === 1 && summaryFailed[0].label === "汇总" && summaryFailed[0].detail.includes("完整比较"),
  legacyFailuresRemainReadableWithoutEchoingRawReason: legacy[0].detail === "输出未通过证据和条件核验" && legacy[0].label === "趋势分析",
  unknownCodesCannotReadPrototypeProperties: unknown[0].detail === "输出未通过证据和条件核验" && unknown[0].label === "其他角色",
  originalPlanAndUserDataNeedNoChanges: failedHtml.includes("原方案未变") && failedHtml.includes("无需修改持仓或费用"),
  completeResultAddsNoWarning: renderToStaticMarkup(createElement(DiscussionFailures, { result: { ...failed, rounds: [], status: "complete", summary, summary_failure_kind: "" } })) === "",
};
