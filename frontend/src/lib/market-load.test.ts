import { loadMarketSections, MARKET_SECTIONS, type MarketLoaders, type MarketLoadSnapshot } from "./market-load";
import { buildMarketView, marketStripSummary, sectionAvailability } from "./market-view";

function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<unknown>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}
function jobs() {
  const requests = Object.fromEntries(MARKET_SECTIONS.map((key) => [key, deferred()])) as Record<(typeof MARKET_SECTIONS)[number], ReturnType<typeof deferred>>;
  const signals: AbortSignal[] = [];
  const loaders = Object.fromEntries(MARKET_SECTIONS.map((key) => [key, (signal: AbortSignal) => {
    signals.push(signal);
    return requests[key].promise;
  }])) as MarketLoaders;
  return { requests, signals, loaders };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
const first = jobs();
const controller = new AbortController();
const updates: MarketLoadSnapshot[] = [];
const done = loadMarketSections(first.loaders, controller.signal, (state) => updates.push(state));
const initiallyPending = updates.length === 1 && updates[0].pending.length === 6 && updates[0].failed.length === 0;
first.requests.indices.resolve([{ name: "合成上证", price: 3000, change_pct: 1, change_amt: 30 }]);
await flush();
const indexOnly = updates[updates.length - 1]!;
const indexView = buildMarketView(indexOnly.input);
const indexBeforeSlowPeers = indexView.indices[0]?.name === "合成上证" && indexOnly.pending.length === 5
  && marketStripSummary(indexView) !== null && !indexOnly.pending.includes("indices");
first.requests.industry.resolve({ top: [{ rank: 1, name: "合成银行", code: "BK1", change_pct: 2, up_count: 3, down_count: 1, as_of: "2026-10-29T13:00:00+08:00" }], bottom: [], total: 1, updated: "2026-10-29T13:00:00+08:00" });
await flush();
const partial = updates[updates.length - 1]!;
const industryBeforeTurnover = buildMarketView(partial.input).industryTop[0]?.name === "合成银行" && partial.pending.includes("turnover");
first.requests.global.resolve([]);
first.requests.overview.reject(new Error("synthetic unavailable"));
first.requests.emotion.resolve(null);
first.requests.turnover.resolve({ stocks: [], updated: "2026-10-29T12:59:00+08:00" });
await done;
const complete = updates[updates.length - 1]!;
const failureSeparatedFromEmpty = complete.pending.length === 0 && complete.failed.join() === "overview"
  && complete.input.global?.length === 0 && !sectionAvailability(buildMarketView(complete.input)).global;
const sourceTimePreserved = complete.input.turnoverUpdated === "2026-10-29T12:59:00+08:00";
const snapshotsAreStable = updates[0].input.indices === undefined && indexOnly.pending.length === 5
  && indexOnly.input.industry === undefined && partial.input.indices?.length === 1;

// Even sources that ignore AbortSignal cannot publish after unmount or supersession.
const obsolete = jobs();
const obsoleteController = new AbortController();
const staleUpdates: MarketLoadSnapshot[] = [];
const obsoleteDone = loadMarketSections(obsolete.loaders, obsoleteController.signal, (state) => staleUpdates.push(state));
obsoleteController.abort();
for (const key of MARKET_SECTIONS) obsolete.requests[key].resolve([{ name: "旧数据" }]);
await obsoleteDone;
const cancelledNeverPublishesLate = staleUpdates.length === 1 && obsolete.signals.every((signal) => signal.aborted);
const newer = jobs();
const latestController = new AbortController();
let latest: MarketLoadSnapshot | null = null;
const latestDone = loadMarketSections(newer.loaders, latestController.signal, (state) => { latest = state; });
newer.requests.indices.resolve([{ name: "新数据", price: 3100, change_pct: 1, change_amt: 30 }]);
await flush();
const newerRequestStartsClean = latest !== null && (latest as MarketLoadSnapshot).input.indices?.[0]?.name === "新数据"
  && (latest as MarketLoadSnapshot).pending.includes("industry");
for (const key of MARKET_SECTIONS.filter((key) => key !== "indices")) newer.requests[key].resolve(null);
await latestDone;

// Cancellation is forwarded to actual requests, without changing pending into failure.
let abortedCalls = 0;
const cancel = new AbortController();
const abortUpdates: MarketLoadSnapshot[] = [];
const abortLoaders = Object.fromEntries(MARKET_SECTIONS.map((key) => [key, (signal: AbortSignal) => new Promise((_, reject) => {
  signal.addEventListener("abort", () => { abortedCalls += 1; reject(new DOMException("aborted", "AbortError")); }, { once: true });
})])) as MarketLoaders;
const abortDone = loadMarketSections(abortLoaders, cancel.signal, (state) => abortUpdates.push(state));
cancel.abort();
await abortDone;

export const marketLoadContracts = {
  initiallyPending,
  indexBeforeSlowPeers,
  industryBeforeTurnover,
  failureSeparatedFromEmpty,
  sourceTimePreserved,
  snapshotsAreStable,
  cancelledNeverPublishesLate,
  newerRequestStartsClean,
  sharedSignal: first.signals.length === 6 && first.signals.every((signal) => signal === controller.signal),
  abortsActualRequests: abortedCalls === 6 && abortUpdates.length === 1 && abortUpdates[0].failed.length === 0,
};
