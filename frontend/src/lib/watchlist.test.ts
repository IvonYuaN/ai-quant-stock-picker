import { addCodes, loadWatch, parseCodes, saveWatch, WATCHLIST_CHANGED_EVENT, WATCHLIST_STORAGE_KEY } from "./watchlist";

const storageDescriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
const values = new Map<string, string>();
const target = new EventTarget();
let failWrite = false;
let failRead = false;
let events = 0;
let persistedWhenNotified = false;
target.addEventListener(WATCHLIST_CHANGED_EVENT, () => {
  events += 1;
  persistedWhenNotified = values.get(WATCHLIST_STORAGE_KEY) === '["600519","000858"]';
});

export const watchlistStorageContract: Record<string, boolean> = {};
try {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem(key: string): string | null {
        if (failRead) throw new Error("storage blocked");
        return values.get(key) ?? null;
      },
      setItem(key: string, value: string): void {
        if (failWrite) throw new Error("storage full");
        values.set(key, value);
      },
    },
  });
  Object.defineProperty(globalThis, "window", { configurable: true, value: target });

  watchlistStorageContract.preservesExistingStorageKey = WATCHLIST_STORAGE_KEY === "vr-watchlist";
  watchlistStorageContract.emptyStorageReturnsEmptyList = loadWatch().length === 0;
  values.set(WATCHLIST_STORAGE_KEY, '["600519","000858","bad","1234567"]');
  watchlistStorageContract.loadsLegacyListAndFiltersInvalidCodes = loadWatch().join(",") === "600519,000858";
  values.set(WATCHLIST_STORAGE_KEY, "not json");
  watchlistStorageContract.corruptStorageReturnsEmptyList = loadWatch().length === 0;
  values.set(WATCHLIST_STORAGE_KEY, '{"codes":["600519"]}');
  watchlistStorageContract.nonArrayStorageReturnsEmptyList = loadWatch().length === 0;
  failRead = true;
  watchlistStorageContract.blockedStorageReturnsEmptyList = loadWatch().length === 0;
  failRead = false;

  saveWatch(["600519", "000858"]);
  watchlistStorageContract.roundTripPreservesCodeOrder = loadWatch().join(",") === "600519,000858";
  watchlistStorageContract.successNotifiesCurrentTab = events === 1;
  watchlistStorageContract.notificationFollowsSuccessfulPersistence = persistedWhenNotified;

  failWrite = true;
  let rejected = false;
  try { saveWatch(["300750"]); } catch { rejected = true; }
  watchlistStorageContract.failedSaveThrowsForRecoverableUi = rejected;
  watchlistStorageContract.failedSaveDoesNotNotify = events === 1;
  watchlistStorageContract.failedSaveKeepsPreviousRecords = loadWatch().join(",") === "600519,000858";
  failWrite = false;

  saveWatch([]);
  watchlistStorageContract.removingFinalCodePersistsEmptyArray = loadWatch().length === 0;
  watchlistStorageContract.removingFinalCodeNotifies = events === 2;
} finally {
  if (storageDescriptor) Object.defineProperty(globalThis, "localStorage", storageDescriptor);
  else Reflect.deleteProperty(globalThis, "localStorage");
  if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
  else Reflect.deleteProperty(globalThis, "window");
}

const original = ["600519"];
const addition = addCodes(original, "600519, 000858\n300750、000858");
export const watchlistParsingContract = {
  acceptsExistingSeparators: parseCodes("600519,000858 300750\n601318、600036").length === 5,
  deduplicatesIncomingCodes: parseCodes("600519 600519").join(",") === "600519",
  rejectsPartialOrOversizedCodes: parseCodes("12345 1234567").length === 0,
  preservesLeadingZeros: parseCodes("000001,000858").join(",") === "000001,000858",
  emptyInputReturnsEmptyList: parseCodes("").length === 0,
  onlyNewCodesAreAdded: addition.next.join(",") === "600519,000858,300750" && addition.added === 2,
  existingListIsNotMutated: original.join(",") === "600519",
  duplicateInputIsNoop: addCodes(original, "600519").added === 0,
};
