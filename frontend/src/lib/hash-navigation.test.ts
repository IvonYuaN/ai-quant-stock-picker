import { scrollToHashTarget, scheduleHashTargetScroll, WATCHLIST_ANCHOR_ID } from "./hash-navigation";

const calls: unknown[] = [];
const browserDocument = {
  getElementById: (id: string) => id === WATCHLIST_ANCHOR_ID
    ? { scrollIntoView: (options?: ScrollIntoViewOptions) => calls.push(options) }
    : null,
} as unknown as Pick<Document, "getElementById">;

export const hashNavigationBrowserContract = {
  watchlistHashScrollsItsRenderedTarget: scrollToHashTarget(`#${WATCHLIST_ANCHOR_ID}`, browserDocument) &&
    JSON.stringify(calls[0]) === JSON.stringify({ block: "start" }),
  missingHashTargetDoesNotClaimNavigation: !scrollToHashTarget("#missing", browserDocument),
  emptyHashDoesNotScroll: !scrollToHashTarget("", browserDocument) && calls.length === 1,
};

let pending: FrameRequestCallback | undefined;
let mounted = false;
let scrolled = "";
let cancelled = false;
const deferredDocument = { getElementById: (id: string) => mounted ? { scrollIntoView: () => { scrolled = id; } } : null } as unknown as Pick<Document, "getElementById">;
const scheduler = {
  requestAnimationFrame: (callback: FrameRequestCallback) => { pending = callback; return 7; },
  cancelAnimationFrame: (frame: number) => { cancelled = frame === 7; pending = undefined; },
};
const cleanup = scheduleHashTargetScroll("#alerts", scheduler, deferredDocument);
const beforeMount = scrolled;
mounted = true;
pending?.(0);
const afterMount = scrolled;
cleanup();
const cleanupRecords = scheduleHashTargetScroll("#strategy-records", scheduler, deferredDocument);
pending?.(0);
const recordsTarget = scrolled;
cleanupRecords();
export const conditionalHashNavigationContracts = {
  delayedTargetDoesNotScrollBeforeMount: beforeMount === "",
  riskDeepLinkScrollsAfterMount: afterMount === "alerts",
  fullRecordsDeepLinkStillSupported: recordsTarget === "strategy-records",
  unmountCancelsScheduledScroll: cancelled && pending === undefined,
};
