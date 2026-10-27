import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import { api, type ActionWorksheet, type FollowupObservation } from "./api";
import { draftNeedsReview } from "./position-workspace";
import { followupForRiskGroup, followupResponseIsCurrent, followupsWithoutRiskCards, followupView, saveFollowupMode, visibleFollowups } from "./position-followup";
import { groupPositionAlerts, normalizeAlerts } from "./alert-view";
import { normalizePortfolio } from "./portfolio-view";
import { FollowupStatus, PositionFollowupPanel } from "../components/aqsp/PositionFollowupPanel";

const observationFixture: FollowupObservation = {
  position_id: "holding-a", symbol: "600000", name: "合成持仓", state: "price_watch", title: "已到保存的观察价", detail: "只说明报价位置，转弱仍待核对。",
  observed_at: "2026-10-27T10:00:00+08:00", quote_as_of: "2026-10-27T09:59:58+08:00", revision: "plan-1", plan_key: "key-1",
  position_snapshot: { position_id: "holding-a", cost: 12, shares: 1000, sellable_shares: 800 },
};
const worksheetFixture: ActionWorksheet = {
  symbol: "600000", position_id: "holding-a", revision: "plan-1", archived: false,
  position_snapshot: observationFixture.position_snapshot,
  basis: { price: 9, cost: 12, shares: 1000, sellable_shares: 800, position_id: "holding-a", checked_at: "2026-10-27T10:05:00+08:00", quote_as_of: "2026-10-27T10:04:58+08:00", quote_source: "合成源", quote_issue: "", joint_weakness: false, references: [] },
  review_plan: { choice: "t_watch" }, scenario: null, scenario_issue: "",
  followup: { mode: "off", last_check: observationFixture, requires_refresh: false, issue: "" },
};
const render = (worksheet: ActionWorksheet, disabled = false) => renderToStaticMarkup(createElement(PositionFollowupPanel, { worksheet, disabled, onSaved: () => undefined }));
const normalHtml = render(worksheetFixture);
const missingHtml = render({ ...worksheetFixture, followup: { ...worksheetFixture.followup!, last_check: null } });
const staleHtml = render({ ...worksheetFixture, revision: "plan-2", followup: { ...worksheetFixture.followup!, requires_refresh: true } });
const failedHtml = render({ ...worksheetFixture, followup: { ...worksheetFixture.followup!, issue: "行情源未能完成本次检查" } }, true);
const archiveHtml = render({ ...worksheetFixture, archived: true });
const holdingsFixture = normalizePortfolio({ holdings: [{ code: "600000", position_id: "holding-a", shares: 1000 }] }).holdings;
const groupsFixture = groupPositionAlerts(normalizeAlerts({ events: [{ id: "event-a", symbol: "600000", position_id: "holding-a", status: "OPEN", sellable_now: true }] }), holdingsFixture);
const oldGroupsFixture = groupsFixture.map((row) => ({ ...row, latest: { ...row.latest, position_id: "holding-old" } }));
export const followupDisplayContracts = {
  backgroundTimeIsIndependent: normalHtml.includes("后台检查") && normalHtml.includes("09:59") && !normalHtml.includes("10:04"),
  observationIsNotTradeSignal: normalHtml.includes("只说明报价位置") && !normalHtml.includes("立即卖出"),
  missingSnapshotIsExplicit: missingHtml.includes("尚无这笔持仓的后台检查记录") && !missingHtml.includes("已到保存"),
  oldSnapshotIsHistorical: staleHtml.includes("这条记录已旧") && staleHtml.includes("当前页行情更新不会改写"),
  sourceFailureDoesNotBecomeAllClear: failedHtml.includes("行情源未能完成") && failedHtml.includes('disabled=""'),
  settingsCollapsedByDefault: /<details class="aq-followup-settings">/.test(normalHtml),
  backgroundDoesNotDuplicatePrimaryPlan: /<details class="aq-followup-details">/.test(normalHtml),
  configurationIsNotDelivery: normalHtml.includes("不代表通知已送达") && !normalHtml.includes("已成功发送"),
  homeStaleRecordDoesNotClaimCurrent: renderToStaticMarkup(createElement(FollowupStatus, { observation: { ...observationFixture, requires_refresh: true }, compact: true })).includes("当前状态待重查"),
  scheduleDoesNotPromiseRealtime: normalHtml.includes("可能延后") && normalHtml.includes("不代表实时逐笔监听"),
  archiveHasNoModeControl: !archiveHtml.includes("<select") && !archiveHtml.includes("通知设置") && archiveHtml.includes("清仓后停止跟进"),
  foreignSnapshotNeverDisplayed: followupView({ ...worksheetFixture, position_id: "holding-b" }).observation === null,
  changedRevisionMarksRecordOld: followupView({ ...worksheetFixture, revision: "plan-2" }).stale,
  quoteReadDoesNotManufactureCheck: followupView({ ...worksheetFixture, basis: { ...worksheetFixture.basis, price: 10 } }).observation === observationFixture,
  currentRootVisibleOnce: visibleFollowups([observationFixture, observationFixture], holdingsFixture).length === 1,
  clearedAndReopenedRootsAreIsolated: visibleFollowups([{ ...observationFixture, position_id: "holding-old" }], holdingsFixture).length === 0,
  quietStatesNeverAddedToHome: (["waiting", "unplanned", "missing_prices", "exited"] as const).every((state) => visibleFollowups([{ ...observationFixture, state }], holdingsFixture).length === 0),
  riskCardMergesSameRootStatus: followupForRiskGroup([observationFixture], groupsFixture[0]) === observationFixture && followupsWithoutRiskCards([observationFixture], groupsFixture).length === 0,
  oldEventDoesNotConsumeNewRoot: !followupForRiskGroup([observationFixture], oldGroupsFixture[0]) && followupsWithoutRiskCards([observationFixture], oldGroupsFixture).length === 1,
  lateCrossPositionSaveRejected: !followupResponseIsCurrent(worksheetFixture, { ...worksheetFixture, position_id: "holding-b" }, worksheetFixture),
  lateOldRevisionSaveRejected: !followupResponseIsCurrent(worksheetFixture, { ...worksheetFixture, revision: "plan-2" }, worksheetFixture),
  lateSupersededModeRejected: !followupResponseIsCurrent(worksheetFixture, { ...worksheetFixture, followup: { ...worksheetFixture.followup!, mode: "daily" } }, worksheetFixture),
  responseCannotSwapRoot: !followupResponseIsCurrent(worksheetFixture, worksheetFixture, { ...worksheetFixture, position_id: "holding-b" }),
  modeChangeDoesNotInvalidatePlanDraft: !draftNeedsReview("plan-1", { ...worksheetFixture, followup: { ...worksheetFixture.followup!, mode: "daily" } }),
};

const originalFetch = globalThis.fetch;
const calls: Array<{ url: string; method: string; body: Record<string, unknown> }> = [];
let conflict = false;
let conflictRejected = false, archiveRejected = false, missingModeRejected = false, mismatchedIdentityRejected = false;
globalThis.fetch = async (input, options) => {
  calls.push({ url: String(input), method: options?.method ?? "GET", body: options?.body ? JSON.parse(String(options.body)) : {} });
  if (conflict) return new Response(JSON.stringify({ detail: "通知模式已变化，请更新核对" }), { status: 409 });
  return new Response(JSON.stringify({ data: String(input).endsWith("/position-followups") ? { items: [observationFixture] } : worksheetFixture }));
};
let returnedFixture: ActionWorksheet | undefined;
try {
  await api.positionFollowups();
  returnedFixture = await saveFollowupMode(worksheetFixture, "changes");
  conflict = true;
  try { await saveFollowupMode(worksheetFixture, "daily"); } catch (error) { conflictRejected = error instanceof Error && error.message.includes("通知模式已变化"); }
  const count = calls.length;
  try { await saveFollowupMode({ ...worksheetFixture, archived: true }, "changes"); } catch { archiveRejected = calls.length === count; }
  try { await saveFollowupMode({ ...worksheetFixture, followup: undefined }, "changes"); } catch { missingModeRejected = calls.length === count; }
  try { await saveFollowupMode({ ...worksheetFixture, position_id: "holding-b" }, "changes"); } catch { mismatchedIdentityRejected = calls.length === count; }
} finally { globalThis.fetch = originalFetch; }
export const followupTransportContracts = {
  summaryReadDoesNotRunCheck: calls[0].url === "/api/position-followups" && calls[0].method === "GET",
  modeWriteUsesDedicatedEndpoint: calls[1].url === "/api/positions/holding-a/followup" && calls[1].method === "POST",
  modeUsesIndependentCas: calls[1].body.mode === "changes" && calls[1].body.expected_mode === "off" && calls[1].body.expected_revision === "plan-1",
  modePinsPosition: (calls[1].body.expected_position as { position_id: string }).position_id === "holding-a",
  modeDoesNotWriteChoiceOrParameters: !("choice" in calls[1].body) && !("parameters" in calls[1].body),
  successKeepsRevision: returnedFixture?.revision === "plan-1",
  conflictDoesNotRetry: conflictRejected && calls.length === 3,
  archiveRejected, missingModeRejected, mismatchedIdentityRejected,
};
