import { createCatalystResource, type CatalystResourceState } from "./catalyst-resource";
import { fetchCatalyst, type CatalystData } from "./catalyst-client";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const requests: Array<ReturnType<typeof deferred<CatalystData>>> = [];
const signals: AbortSignal[] = [];
const states: CatalystResourceState[] = [];
const resource = createCatalystResource((state) => states.push(state), (signal) => {
  signals.push(signal);
  const request = deferred<CatalystData>();
  requests.push(request);
  return request.promise;
});
const last = () => states[states.length - 1];
const first = resource.refresh();
export const catalystResourceContract: Record<string, boolean> = {
  loadingImmediately: last().loading && last().data === null && last().error === "",
};
const second = resource.refresh();
catalystResourceContract.refreshAbortsPreviousRequest = signals[0].aborted && !signals[1].aborted;
const fresh: CatalystData = { date: "2026-10-23", source_status: "ok", events: [] };
requests[1].resolve(fresh);
await second;
catalystResourceContract.currentReplyBecomesVisible = last().data === fresh && !last().loading;
const count = states.length;
requests[0].resolve({ date: "2026-10-22", events: [] });
await first;
catalystResourceContract.lateSuccessCannotOverwriteNewerReply = states.length === count && last().data === fresh;
const third = resource.refresh();
catalystResourceContract.refreshKeepsKnownDataWhileLoading = last().data === fresh && last().loading;
requests[2].reject(new Error("server contains private diagnostic or CLI command"));
await third;
catalystResourceContract.failurePreservesLastSuccess = last().data === fresh && !last().loading && !!last().error;
catalystResourceContract.failureDoesNotExposeDiagnostic = !last().error.includes("private diagnostic") && !last().error.includes("CLI");
const fourth = resource.refresh();
const fifth = resource.refresh();
requests[3].reject(new Error("late failure"));
await fourth;
catalystResourceContract.lateFailureCannotEndCurrentLoading = last().loading && last().error === "";
requests[4].resolve({ date: "2026-10-24", source_status: "partial", events: [] });
await fifth;
catalystResourceContract.retryCanReplacePriorFailure = last().data?.source_status === "partial" && !last().error && !last().loading;
const sixth = resource.refresh();
const beforeDispose = states.length;
resource.dispose();
requests[5].resolve(fresh);
await sixth;
catalystResourceContract.unmountAbortsAndSuppressesReplies = signals[5].aborted && states.length === beforeDispose;
await resource.refresh();
catalystResourceContract.disposedResourceDoesNotStartAnotherRequest = requests.length === 6;

const originalFetch = globalThis.fetch;
const abort = new AbortController();
let caught: unknown;
const abortedError = new DOMException("aborted", "AbortError");
try {
  globalThis.fetch = async () => { abort.abort(); throw abortedError; };
  try { await fetchCatalyst(abort.signal); } catch (error) { caught = error; }
  catalystResourceContract.clientPreservesAbortReason = caught === abortedError;
} finally { globalThis.fetch = originalFetch; }
