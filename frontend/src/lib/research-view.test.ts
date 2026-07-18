import type { AqspAgentResult, AqspSnapshot } from "./api";
import {
  debateProcessText,
  dedupeResearchText,
  mergeAvailableResearchDates,
  messageSourceUrl,
  sameResearchText,
  snapshotConclusion,
  snapshotMatchesSelectedDate,
} from "./research-view";

const emptySnapshot = {
  schema_version: "v1",
  generated_at: "2026-07-15T09:30:00+08:00",
  selected_date: "2026-07-15",
  available_dates: ["2026-07-15"],
  candidates: [],
  debates: [],
  summaries: [],
  source: { effective: "", latest_trade_date: "", lag_days: 0, status: "" },
  coldstart: { status: "", detail: "" },
  stale_after: "",
  message_status: "未产出",
  messages: [],
  market_context: null,
} satisfies AqspSnapshot;

const debateWithoutProcess = {
  symbol: "000001",
  display_name: "示例对象",
  conclusion: "",
  primary_risk_gate: "",
  next_trigger: "",
  active_roles: ["风险视角"],
  round_count: 2,
  bull_count: 0,
  bear_count: 1,
  neutral_count: 1,
  process_summary: "",
} satisfies AqspAgentResult;

// Compile-time and deterministic checks run through the package test command.
export const researchViewContractChecks = {
  emptyConclusion: snapshotConclusion(emptySnapshot) === "",
  processFallback: debateProcessText(debateWithoutProcess) === "2 轮讨论 · 角色 风险视角",
  selectedDateMatches: snapshotMatchesSelectedDate(emptySnapshot, "2026-07-15"),
  selectedDateRejectsPreviousSnapshot: !snapshotMatchesSelectedDate(emptySnapshot, "2026-07-14"),
  emptySelectionAcceptsCurrentSnapshot: snapshotMatchesSelectedDate(emptySnapshot, ""),
  duplicateResearchTextCollapses: dedupeResearchText(["  过程摘要  ", "过程   摘要", "另一条"]).join("|") === "过程摘要|另一条",
  equalResearchTextMatches: sameResearchText("标题", "标题"),
  differentResearchTextDoesNotMatch: !sameResearchText("标题", "摘要"),
  dateIndexCompletesSnapshotDates: mergeAvailableResearchDates(["2026-07-14"], ["2026-07-14", "2026-07-11"]).join("|") === "2026-07-14|2026-07-11",
  legacyMessageUrlRemainsVisible: messageSourceUrl({ url: "https://example.test/news" }) === "https://example.test/news",
};
