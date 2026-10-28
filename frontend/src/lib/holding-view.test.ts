import type { AlertEvent, AqspCandidate, Holding } from "./api";
import { buildHoldingGroups, normalizeHoldingSectors, currentHoldingCandidates } from "./holding-view";
import { dailyViewFixture } from "./daily-view.test";

const holdingFixture = (code: string): Holding => ({
  code, name: code, price: 10, cost: 11, shares: 100, opened_at: "2026-10-22",
  market_value: 1000, pnl: -100, pnl_pct: -9.09,
});
const alertFixture = (symbol: string, status: AlertEvent["status"] = "OPEN"): AlertEvent => ({
  id: `${symbol}-${status}`, symbol, status, level: "L1", position_id: null,
  trigger_at: 10, threshold_at: 10.45, pnl_pct: -9.09, sellable_now: false,
  action_hint: "观察", t1_note: "今日新仓", basis_json: { rule: "L1", actual: "10", evidence: "" },
  ignore_streak: 0, created_at: "2026-10-23T10:00:00+08:00", updated_at: "2026-10-23T10:00:00+08:00",
});
const candidateFixture = (symbol: string): AqspCandidate => ({
  symbol, display_name: symbol, score: 70, research_status: "ready", next_step: "", context: "",
  deterministic_reasons: [], strategies: [], evidence_status: "ready",
});
const inputFixture = {
  holdings: [holdingFixture("000001"), holdingFixture("600519")],
  watchlist: ["000858", "600519", "300750", "000858"],
  alerts: [alertFixture("600519"), alertFixture("000858", "HANDLED"), alertFixture("999999")],
  candidates: [candidateFixture("300750"), candidateFixture("600519")],
  sectors: { "600519": ["白酒"], "000858": ["白酒"] },
};
const groups = buildHoldingGroups(inputFixture);
const flat = groups.flatMap((group) => group.rows);
export const holdingGroupContract = {
  riskThenHoldingThenCandidateWatchThenWatch: groups.map((group) => group.kind).join() === "triggered,holding,candidate-watch,watch",
  triggeredHoldingComesFirst: groups[0].rows[0]?.code === "600519",
  t1LockedRiskStillVisible: groups[0].rows[0]?.alerts[0]?.sellable_now === false,
  untriggeredHoldingSecond: groups[1].rows[0]?.code === "000001",
  candidateWatchThird: groups[2].rows[0]?.code === "300750",
  handledRiskDoesNotReopenInView: groups[3].rows[0]?.code === "000858" && groups[3].rows[0]?.alerts.length === 0,
  holdingAndWatchDoNotDuplicate: flat.filter((row) => row.code === "600519").length === 1,
  dualOwnershipIsKept: groups[0].rows[0]?.watched === true && Boolean(groups[0].rows[0]?.holding),
  duplicateWatchDoesNotDuplicate: flat.filter((row) => row.code === "000858").length === 1,
  unrelatedOldAlertsDoNotCreatePositions: flat.every((row) => row.code !== "999999"),
  candidateMatchesKeptOnHolding: groups[0].rows[0]?.candidate?.symbol === "600519",
  sectorFilterKeepsOrder: buildHoldingGroups({ ...inputFixture, sector: "白酒" }).flatMap((group) => group.rows.map((row) => row.code)).join() === "600519,000858",
  missingSectorHasEmptyResult: buildHoldingGroups({ ...inputFixture, sector: "银行" }).every((group) => group.rows.length === 0),
  emptySourcesHaveFourEmptyGroups: buildHoldingGroups({ holdings: [], watchlist: [], alerts: [], candidates: [] }).every((group) => group.rows.length === 0),
  inputOrderNotMutated: inputFixture.holdings[0].code === "000001" && inputFixture.watchlist[0] === "000858",
  emptyCandidateSourceDoesNotClaimTodayMatch: buildHoldingGroups({ ...inputFixture, candidates: [] })[2].rows.length === 0,
  boardShapesNormalize: normalizeHoldingSectors({ concept_tags: ["白酒", ""], boards: [{ name: "白酒" }, { name: "食品" }, null] }).join() === "白酒,食品",
  invalidBoardsAreEmpty: normalizeHoldingSectors({ concept_tags: "bad", boards: [null, 1, { name: 3 }] }).length === 0,
  absentBoardsAreEmpty: normalizeHoldingSectors(null).length === 0,
  missingSnapshotHasNoCurrentMatches: currentHoldingCandidates(null).length === 0,
  expiredSnapshotHasNoCurrentMatches: currentHoldingCandidates({ ...dailyViewFixture, stale_after: "2026-10-22T10:00:00+08:00" }, Date.parse("2026-10-23T10:00:00+08:00")).length === 0,
  historicalSnapshotHasNoCurrentMatches: currentHoldingCandidates({ ...dailyViewFixture, stale_after: "2099-10-23T10:00:00+08:00", meta: { ...dailyViewFixture.meta!, historical: true } }).length === 0,
  invalidatedCandidateHasNoCurrentMatch: currentHoldingCandidates({ ...dailyViewFixture, stale_after: "2099-10-23T10:00:00+08:00", meta: { ...dailyViewFixture.meta!, historical: false, stale: false }, candidate_changes: [{ symbol: dailyViewFixture.candidates[0].symbol, display_name: "", status: "invalidated" }] }).every((row) => row.symbol !== dailyViewFixture.candidates[0].symbol),
  freshCandidateRetainsMatch: currentHoldingCandidates({ ...dailyViewFixture, stale_after: "2099-10-23T10:00:00+08:00", meta: { ...dailyViewFixture.meta!, historical: false, stale: false }, candidate_changes: [] }).length === dailyViewFixture.candidates.length,
  missingCoverageRetainsExistingMatch: currentHoldingCandidates({ ...dailyViewFixture, candidates: dailyViewFixture.candidates.map((row) => ({ ...row, coverage_note: "本轮无新研究" })), stale_after: "2099-10-23T10:00:00+08:00", meta: { ...dailyViewFixture.meta!, historical: false, stale: false }, candidate_changes: [] }).length === dailyViewFixture.candidates.length,
};
