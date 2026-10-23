import type { AqspSnapshot } from "@/types/aqsp";
import { dailyViewFixture } from "./daily-view.test";
import { buildDailyView } from "./daily-view";
import { isAqspSnapshotStale } from "./snapshot-freshness";

const nowFixture = Date.parse("2026-10-23T10:00:00+08:00");
const futureFixture: AqspSnapshot = { ...dailyViewFixture, stale_after: "2099-10-23T11:00:00+08:00", recommendation_gate: { recommendation_allowed: true, status: "ready", reasons: [] } };
const expiredFixture: AqspSnapshot = { ...futureFixture, stale_after: "2026-10-23T09:59:59+08:00" };
const historyFixture: AqspSnapshot = { ...futureFixture, meta: { ...futureFixture.meta!, historical: true } };

export const snapshotFreshnessContract = {
  futureIsFresh: !isAqspSnapshotStale(futureFixture, nowFixture),
  falseMetaCannotExtendExpiry: isAqspSnapshotStale(expiredFixture, nowFixture),
  exactDeadlineExpires: isAqspSnapshotStale({ ...futureFixture, stale_after: "2026-10-23T10:00:00+08:00" }, nowFixture),
  historicalIsStale: isAqspSnapshotStale(historyFixture, nowFixture),
  trueMetaIsStale: isAqspSnapshotStale({ ...futureFixture, meta: { ...futureFixture.meta!, stale: true } }, nowFixture),
  missingDeadlineIsStale: isAqspSnapshotStale({ ...futureFixture, stale_after: "" }, nowFixture),
  invalidDeadlineIsStale: isAqspSnapshotStale({ ...futureFixture, stale_after: "invalid" }, nowFixture),
  metadataOptional: !isAqspSnapshotStale({ ...futureFixture, meta: undefined }, nowFixture),
  historicalViewDoesNotAllow: buildDailyView(historyFixture).gate.tone !== "ok",
  expiredViewDoesNotAllow: buildDailyView(expiredFixture).gate.tone !== "ok",
  currentViewAllowsWhenExplicit: buildDailyView(futureFixture).gate.tone === "ok",
};
