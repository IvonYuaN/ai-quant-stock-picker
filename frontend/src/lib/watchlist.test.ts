import { addCodes, loadWatch, loadWatchRemovals, parseCodes, saveWatch, WATCHLIST_CHANGED_EVENT, WATCHLIST_REMOVAL_LIMIT, WATCHLIST_STORAGE_KEY } from "./watchlist";
import { exportVault, importVault } from "./vault";

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
  watchlistStorageContract.removalRecordsOnlyActuallyRemovedCodes = loadWatchRemovals()[0].codes.join(",") === "600519,000858";
  watchlistStorageContract.removalTimestampHasTimezone = /Z$/.test(loadWatchRemovals()[0].removed_at);
  const savedRemoval = values.get(WATCHLIST_STORAGE_KEY);
  watchlistStorageContract.reloadPreservesRemovalRecord = JSON.parse(savedRemoval || "{}").removals.length === 1 && loadWatchRemovals().length === 1;
  saveWatch([]);
  saveWatch(["300750"]);
  saveWatch(["300750"]);
  watchlistStorageContract.additionsAndDuplicateSavesDoNotRecordRemoval = loadWatchRemovals().length === 1;
  saveWatch(["000858", "300750"]);
  saveWatch(["300750", "000858"]);
  watchlistStorageContract.reorderingDoesNotRecordRemoval = loadWatchRemovals().length === 1;
  saveWatch(["000858"]);
  watchlistStorageContract.newRemovalIsNewestAndPreservesEarlierHistory = loadWatchRemovals().length === 2 && loadWatchRemovals()[0].codes.join(",") === "300750";

  const backup = JSON.stringify(exportVault());
  values.clear();
  const imported = importVault(backup);
  watchlistStorageContract.existingBackupRoundTripIncludesListAndRemovalHistory = imported.ok && loadWatch().join(",") === "000858" && loadWatchRemovals().length === 2;

  const beforeFailedSave = values.get(WATCHLIST_STORAGE_KEY);
  const beforeFailedEvents = events;
  failWrite = true;
  rejected = false;
  try { saveWatch([]); } catch { rejected = true; }
  failWrite = false;
  watchlistStorageContract.failedRemovalIsAtomicAndDoesNotNotify = rejected && values.get(WATCHLIST_STORAGE_KEY) === beforeFailedSave && events === beforeFailedEvents && loadWatchRemovals().length === 2;
  failRead = true;
  rejected = false;
  try { saveWatch([]); } catch { rejected = true; }
  failRead = false;
  watchlistStorageContract.failedReadDoesNotOverwriteOrInventHistory = rejected && values.get(WATCHLIST_STORAGE_KEY) === beforeFailedSave && events === beforeFailedEvents;

  for (let index = 0; index <= WATCHLIST_REMOVAL_LIMIT; index++) {
    const code = String(600000 + index);
    saveWatch([code]);
    saveWatch([]);
  }
  watchlistStorageContract.removalHistoryIsBounded = loadWatchRemovals().length === WATCHLIST_REMOVAL_LIMIT;
  watchlistStorageContract.boundedHistoryKeepsNewestRemoval = loadWatchRemovals()[0].codes[0] === String(600000 + WATCHLIST_REMOVAL_LIMIT);

  values.set(WATCHLIST_STORAGE_KEY, JSON.stringify({ format: "aqsp-watchlist", version: 1, codes: ["000001", "bad"], removals: [{ codes: ["bad"], removed_at: "2026-10-23T01:00:00Z" }, { codes: ["600000"], removed_at: "invalid" }, { codes: ["600001"], removed_at: "2026-10-23T10:00:00" }, null, { codes: ["600002", "600002", "bad"], removed_at: "2026-10-23T01:00:00Z" }] }));
  watchlistStorageContract.envelopePreservesLegacyCodeValidation = loadWatch().join(",") === "000001";
  watchlistStorageContract.malformedRemovalRecordsAreIgnored = loadWatchRemovals().length === 1 && loadWatchRemovals()[0].codes.join(",") === "600002";
  values.set(WATCHLIST_STORAGE_KEY, JSON.stringify({ format: "aqsp-watchlist", version: 2, codes: ["000001"], removals: [] }));
  watchlistStorageContract.unknownEnvelopeVersionIsNotMisread = loadWatch().length === 0 && loadWatchRemovals().length === 0;
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
