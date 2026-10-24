import { scrollToHashTarget, WATCHLIST_ANCHOR_ID } from "./hash-navigation";

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
