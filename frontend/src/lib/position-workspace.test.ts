import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import { api, type ActionWorksheet, type PositionDiscussion } from "./api";
import { constraintsDraft, discussionIsCurrent, draftNeedsReview, ownedPositionForStock, parseConstraints, planReturnHref, positionPlanHref, readPlanWorksheet, saleReviewGuard, savePlanChoice, worksheetGuard } from "./position-workspace";
import { normalizePortfolio } from "./portfolio-view";
import { PositionRouteComparison } from "../components/aqsp/PositionRouteReview";
import { PositionActionPlan } from "../components/aqsp/PositionActionPlan";
import { PositionConstraintsForm } from "../components/aqsp/PositionConstraintsForm";
import { planJourney } from "./position-plan";
import { PositionSaleReview } from "../components/aqsp/PositionSaleReview";

const worksheetFixture: ActionWorksheet = {
  symbol: "002129", name: "合成持仓", position_id: "position-own", revision: "revision-1", archived: false,
  basis: { checked_at: "2026-10-27T10:00:00+08:00", quote_as_of: "2026-10-27T10:00:00+08:00", quote_source: "合成源", quote_issue: "", price: 9.2, cost: 12, shares: 1000, sellable_shares: 800, position_id: "position-own", joint_weakness: false, references: [] },
  position_snapshot: { position_id: "position-own", cost: 12, shares: 1000, sellable_shares: 800 },
  review_plan: {}, scenario: null, scenario_issue: "", analysis_basis_id: "basis-1",
  constraints: { attention: "unknown", extra_cash: null, loss_limit: null, risk_baseline: null },
  route_review: { title: "当前有风险要核对", summary: "缺少板块依据，不能据此要求清仓。", unrealized_pnl_pct: -.23, recovery_to_cost_pct: null,
    routes: [{ choice: "hold", status: "risk_review", reason: "继续持有仍承担这笔持仓的下跌风险", needs: [] }, { choice: "reduce", status: "review", reason: "当前可卖800股，卖出部分会锁定盈亏", needs: [] }, { choice: "t_watch", status: "paused", reason: "没有时间盯盘，暂停做 T 试算", needs: ["重新核对盯盘条件"] }],
    evidence: { market: { status: "unknown", summary: "大盘依据待补", as_of: null, source: "" }, sector: { status: "unknown", summary: "板块依据待补", as_of: null, source: "" }, stock: { status: "falling", summary: "个股走弱", as_of: "2026-10-27T10:00:00+08:00", source: "合成源" } }, missing_evidence: ["板块"] },
};
const ownedFixture = normalizePortfolio({ holdings: [{ code: "002129", position_id: "position-own", shares: 1000 }] }).holdings;
const draftFixture = { attention: "unknown" as const, extra_cash: "", loss_limit: "" };
const zero = parseConstraints({ ...draftFixture, extra_cash: "0", loss_limit: "0" });
let invalidAmountRejected = false;
try { parseConstraints({ ...draftFixture, extra_cash: "-1" }); } catch { invalidAmountRejected = true; }
const comparisonHtml = renderToStaticMarkup(createElement(PositionRouteComparison, { worksheet: worksheetFixture, target: { kind: "position", id: "position-own" }, busy: false, showModel: false, onChoose: () => undefined }));
const archivedHtml = renderToStaticMarkup(createElement(PositionRouteComparison, { worksheet: { ...worksheetFixture, archived: true }, target: { kind: "position", id: "position-own" }, busy: false, onChoose: () => undefined }));
const zeroHtml = renderToStaticMarkup(createElement(PositionConstraintsForm, { worksheet: { ...worksheetFixture, constraints: { attention: "unavailable", extra_cash: 0, loss_limit: 0, risk_baseline: null } }, disabled: false, onSaved: () => undefined }));
const baselineFixture = { price: 10, shares: 1000, market_value: 10000, as_of: worksheetFixture.basis.quote_as_of!, confirmed_at: worksheetFixture.basis.checked_at };
const exhaustedBudgetHtml = renderToStaticMarkup(createElement(PositionConstraintsForm, { worksheet: { ...worksheetFixture, constraints_issue: "新增亏损预算已耗尽", constraints: { attention: "unknown", extra_cash: null, loss_limit: 500, risk_baseline: baselineFixture } }, disabled: false, onSaved: () => undefined }));
const invalidBaselineHtml = renderToStaticMarkup(createElement(PositionConstraintsForm, { worksheet: { ...worksheetFixture, risk_baseline_requires_confirmation: true, constraints: { attention: "unknown", extra_cash: null, loss_limit: 500, risk_baseline: baselineFixture } }, disabled: false, onSaved: () => undefined }));
const archivedPlanHtml = renderToStaticMarkup(createElement(PositionActionPlan, { worksheet: { ...worksheetFixture, archived: true }, target: { kind: "position", id: "position-own" }, choice: "t_watch" }));
const personalPauseHtml = renderToStaticMarkup(createElement(PositionActionPlan, { worksheet: { ...worksheetFixture, review_plan: { choice: "t_watch" }, basis: { ...worksheetFixture.basis, checked_at: new Date().toISOString(), personal_pause_t: true } }, target: { kind: "position", id: "position-own" }, choice: "t_watch" }));
const modelFixture = { review_revision: "revision-1", analysis_basis_id: "basis-1" } as PositionDiscussion;
const soldFixture = { ...worksheetFixture, sale_requires_review: true, recorded_sale: { id: "sale-current", date: "2026-10-27", shares: 200, price: 9.3 } };
const saleGuard = saleReviewGuard(soldFixture);
const saleHtml = renderToStaticMarkup(createElement(PositionSaleReview, { worksheet: soldFixture, onWorksheet: () => undefined }));
const salePlanHtml = (["hold", "reduce", "t_watch"] as const).map((choice) => renderToStaticMarkup(createElement(PositionActionPlan, { choice, worksheet: { ...soldFixture, review_plan: { choice } } })));
const closedSaleHtml = renderToStaticMarkup(createElement(PositionSaleReview, { worksheet: { ...soldFixture, archived: true }, onWorksheet: () => undefined }));
let noSaleRejected = false, missingReceiptRejected = false, closedSaleRejected = false;
try { saleReviewGuard(worksheetFixture); } catch { noSaleRejected = true; }
try { saleReviewGuard({ ...soldFixture, recorded_sale: { date: "2026-10-27", shares: 200, price: 9.3 } }); } catch { missingReceiptRejected = true; }
try { saleReviewGuard({ ...soldFixture, archived: true }); } catch { closedSaleRejected = true; }
export const workspaceUserContracts = {
  saleReviewPinsReceiptAndPosition: saleGuard.expected_sale_id === "sale-current" && saleGuard.expected_revision === "revision-1" && saleGuard.expected_position === soldFixture.position_snapshot,
  saleReviewShowsLatestSaleAndRemainingExposure: saleHtml.includes("卖出 200 股") && saleHtml.includes("剩余 1000 股") && saleHtml.includes("可卖 800 股"),
  everyRouteCanReviewRemainingExposureWithoutFees: salePlanHtml.every((html) => html.includes("已核对余仓，重新分析策略") && html.includes("费用试算可稍后再做")),
  saleReviewDoesNotReviveOldWatch: saleHtml.includes("原观察条件停止沿用") && saleHtml.includes("重新分析并采用新条件"),
  closedSaleHasNoAction: closedSaleHtml === "",
  noSaleRejected, missingReceiptRejected, closedSaleRejected,
  preservesPositionIdentity: ownedFixture[0].position_id === "position-own",
  missingIdentityNeverInvented: normalizePortfolio({ holdings: [{ code: "002129" }] }).holdings[0].position_id === null,
  missingIdentityHasNoDeepLink: positionPlanHref(null) === null && positionPlanHref(" ") === null,
  noCodeBasedPlanFallback: positionPlanHref(ownedPositionForStock(ownedFixture, "600000")?.position_id) === null,
  unambiguousOwnershipLinks: ownedPositionForStock(ownedFixture, "002129")?.position_id === "position-own",
  ambiguousOwnershipDoesNotChoose: ownedPositionForStock([...ownedFixture, ...ownedFixture], "002129") === null,
  researchReturnPreserved: positionPlanHref("position-own", "/stock/002129?date=2026-10-26")?.includes(encodeURIComponent("/stock/002129?date=2026-10-26")) === true,
  unsafeReturnRejected: planReturnHref("//evil.test") === "/my/holdings" && planReturnHref("/my/holdings/other/plan") === "/my/holdings",
  skippedAmountsStayUnknown: parseConstraints(draftFixture).extra_cash === null && parseConstraints(draftFixture).loss_limit === null,
  explicitZeroRemainsZero: zero.extra_cash === 0 && zero.loss_limit === 0,
  invalidAmountRejected,
  zeroSurvivesDraftReload: constraintsDraft({ ...worksheetFixture, constraints: { attention: "unknown", extra_cash: 0, loss_limit: 0, risk_baseline: null } }).extra_cash === "0",
  staleDraftRequiresReview: draftNeedsReview("revision-1", { ...worksheetFixture, revision: "revision-2" }),
  quoteRefreshDoesNotInvalidateDraft: !draftNeedsReview("revision-1", { ...worksheetFixture, basis: { ...worksheetFixture.basis, price: 9.3 } }),
  conditionsInvalidateOldModel: !discussionIsCurrent(modelFixture, { ...worksheetFixture, revision: "revision-2" }),
  quoteChangeMakesModelHistorical: !discussionIsCurrent(modelFixture, { ...worksheetFixture, analysis_basis_id: "basis-2" }),
  matchingModelRemainsCurrent: discussionIsCurrent(modelFixture, worksheetFixture),
  compareBeforeChoosingShowsRealReason: comparisonHtml.includes("当前可卖800股") && comparisonHtml.includes("没有时间盯盘"),
  pausedRouteOnlyRecordsIntent: comparisonHtml.includes("仅记录做 T 观察意向"),
  archivedPlanHasNoInputsOrTradeControls: !archivedPlanHtml.includes("<button") && !archivedPlanHtml.includes("<input") && archivedPlanHtml.includes("这笔持仓已清仓"),
  personalPauseIsVisibleAndBlocksCalculation: personalPauseHtml.includes("个人条件下先暂停做 T") && /type="submit"[^>]*disabled/.test(personalPauseHtml),
  archivedComparisonHasNoActiveControls: !archivedHtml.includes("<button") && !archivedHtml.includes("模型设置"),
  zeroBudgetVisibleInInputs: zeroHtml.includes('value="0"') && zeroHtml.includes("没有时间盯盘"),
  exhaustedBudgetDoesNotSilentlyRestart: exhaustedBudgetHtml.includes("预算已耗尽") && !exhaustedBudgetHtml.includes("我确认以") && exhaustedBudgetHtml.includes("我明确要按当前持仓和价格重新起算"),
  invalidBaselineRequiresExplicitConfirmation: invalidBaselineHtml.includes("我确认以"),
  personalPauseOverridesArrival: planJourney("t_watch", { ...worksheetFixture, basis: { ...worksheetFixture.basis, personal_pause_t: true, price: 9.9 } }, { sell_price: 9.5, buy_price: 9 }, Date.parse(worksheetFixture.basis.checked_at)).label === "先核对个人条件",
};

const originalFetch = globalThis.fetch;
const calls: Array<{ url: string; method: string; body: Record<string, unknown> }> = [];
let rejectWrite = false;
globalThis.fetch = async (input, options) => {
  const url = String(input), method = options?.method ?? "GET";
  calls.push({ url, method, body: options?.body ? JSON.parse(String(options.body)) : {} });
  if (rejectWrite && method === "POST") return new Response(JSON.stringify({ detail: "计划版本已变化" }), { status: 409 });
  if (url.endsWith("/discussion/stream")) return new Response('{"event":"result","data":{"status":"complete"}}\n');
  return new Response(JSON.stringify({ data: url.endsWith("/action-plan") ? worksheetFixture : {} }));
};
let positionCalls: typeof calls = [], alertCalls: typeof calls = [], modelCall: typeof calls[number] | undefined, constraintsCall: typeof calls[number] | undefined, saleCall: typeof calls[number] | undefined;
let missingGuardRejected = false, archiveWriteRejected = false, staleWriteRejected = false;
try {
  await readPlanWorksheet({ kind: "position", id: "position-own" });
  await savePlanChoice({ kind: "position", id: "position-own" }, worksheetFixture, "hold");
  positionCalls = calls.splice(0);
  await savePlanChoice({ kind: "alert", id: "real-alert" }, worksheetFixture, "reduce");
  alertCalls = calls.splice(0);
  const guard = worksheetGuard(worksheetFixture);
  await api.savePositionConstraints("position-own", zero, guard.expected_position, guard.expected_revision, false, { price: worksheetFixture.basis.price, as_of: worksheetFixture.basis.quote_as_of });
  constraintsCall = calls.pop();
  await api.positionDiscussionStream("position-own", null, worksheetFixture.position_snapshot, () => undefined, new AbortController().signal, undefined, undefined, worksheetFixture.revision, "position");
  modelCall = calls.pop();
  await api.reviewPositionSale("position-own", saleGuard);
  saleCall = calls.pop();
  try { await savePlanChoice({ kind: "position", id: "position-own" }, { ...worksheetFixture, revision: undefined }, "hold"); } catch { missingGuardRejected = calls.length === 0; }
  try { await savePlanChoice({ kind: "position", id: "position-own" }, { ...worksheetFixture, archived: true }, "hold"); } catch { archiveWriteRejected = calls.length === 0; }
  rejectWrite = true;
  try { await savePlanChoice({ kind: "position", id: "position-own" }, worksheetFixture, "hold"); } catch { staleWriteRejected = calls.length === 1 && calls[0].method === "POST"; }
} finally { globalThis.fetch = originalFetch; }
export const workspaceTransportContracts = {
  saleReviewDoesNotSubmitChoiceFeesOrTrade: saleCall?.url === "/api/positions/position-own/sale-review" && saleCall.body.expected_sale_id === "sale-current" && Object.keys(saleCall.body).sort().join(",") === "expected_position,expected_revision,expected_sale_id",
  noAlertNeededForPosition: positionCalls.every((call) => call.url.startsWith("/api/positions/position-own/")),
  choiceOnlyIncludesSnapshotAndRevision: positionCalls.some((call) => call.method === "POST" && call.body.expected_revision === "revision-1" && (call.body.expected_position as { position_id: string }).position_id === "position-own"),
  legacyAlertAlsoUsesGuard: alertCalls[0].url === "/api/alerts/real-alert/plan" && alertCalls[0].body.expected_revision === "revision-1",
  zeroBudgetsSentAsZero: constraintsCall?.body.extra_cash === 0 && constraintsCall.body.loss_limit === 0,
  riskBaselinePinsConfirmedQuote: (constraintsCall?.body.expected_quote as { price: number; as_of: string }).price === 9.2 && (constraintsCall?.body.expected_quote as { price: number; as_of: string }).as_of === worksheetFixture.basis.quote_as_of,
  modelUsesRealPositionAndRevision: modelCall?.url === "/api/positions/position-own/discussion/stream" && modelCall.body.expected_revision === "revision-1" && modelCall.body.choice === null,
  missingGuardRejected, archiveWriteRejected, staleWriteRejected,
};
