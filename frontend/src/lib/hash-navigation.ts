/** Scroll to a client-rendered hash target after its route has mounted. */
export const WATCHLIST_ANCHOR_ID = "watchlist";

export function scrollToHashTarget(
  hash: string,
  documentRef: Pick<Document, "getElementById"> = document,
): boolean {
  if (!hash.startsWith("#") || hash.length < 2) return false;
  const target = documentRef.getElementById(hash.slice(1));
  if (!target) return false;
  target.scrollIntoView({ block: "start" });
  return true;
}
