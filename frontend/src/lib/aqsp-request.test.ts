import { api, isAqspAbortError, normalizeAqspSnapshotEnvelope } from "./api";
import { aqspContractFixture } from "./aqsp-contract";

// The frontend package currently uses TypeScript compilation as its test gate.
// Keep the cancellation contract type-checked without issuing a network request.
const controller = new AbortController();
type SnapshotOptions = Parameters<typeof api.aqspSnapshot>[1];
const snapshotRequestOptions: NonNullable<SnapshotOptions> = {
  signal: controller.signal,
};

export const aqspRequestCancellationContract = {
  acceptsSignal: snapshotRequestOptions.signal === controller.signal,
  recognizesAbort: isAqspAbortError(Object.assign(new Error(), { name: "AbortError" })),
};

const normalizedSnapshot = normalizeAqspSnapshotEnvelope({
  ...aqspContractFixture,
  data: {
    ...aqspContractFixture.data,
    candidates: [{ symbol: "000001" }],
  },
});

export const aqspSnapshotEnvelopeContract = {
  unwrapsCandidateData: normalizedSnapshot.candidates.length === 1,
  preservesMetadata: normalizedSnapshot.meta.historical === false && normalizedSnapshot.meta.stale === false,
};