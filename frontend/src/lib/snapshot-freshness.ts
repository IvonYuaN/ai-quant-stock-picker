import type { AqspSnapshot } from "@/types/aqsp";

/** A server flag cannot extend the snapshot's actual expiry time. */
export function isAqspSnapshotStale(snapshot: AqspSnapshot, now = Date.now()): boolean {
  const deadline = Date.parse(snapshot.stale_after);
  return Boolean(snapshot.meta?.historical || snapshot.meta?.stale) ||
    !Number.isFinite(deadline) || now >= deadline;
}
