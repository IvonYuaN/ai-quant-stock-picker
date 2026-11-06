import type { AqspSnapshot } from "@/types/aqsp";

export type SnapshotFreshness = "fresh" | "stale" | "unknown";

export function snapshotFreshness(snapshot: AqspSnapshot, now = Date.now()): SnapshotFreshness {
  if (snapshot.meta?.historical || snapshot.meta?.stale) return "stale";
  const deadline = Date.parse(snapshot.stale_after);
  if (!Number.isFinite(deadline)) return "unknown";
  return now < deadline ? "fresh" : "stale";
}

/** A server flag cannot extend the snapshot's actual expiry time. */
export function isAqspSnapshotStale(snapshot: AqspSnapshot, now = Date.now()): boolean {
  return snapshotFreshness(snapshot, now) !== "fresh";
}
