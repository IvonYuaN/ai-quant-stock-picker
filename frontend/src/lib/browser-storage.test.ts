import { createElement } from "react";
import { renderToString } from "react-dom/server.edge";
import { MemoryRouter } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { api, authHeaders, loadUserId } from "./api";
import { createBrowserIdentity, readLocalPreference, writeLocalPreference } from "./browser-session";

const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
const savedFetch = globalThis.fetch;
export const blockedBrowserStorageContract: Record<string, boolean> = {};
try {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new DOMException("storage blocked", "SecurityError"); } });
  const first = authHeaders()["X-User-Id"];
  blockedBrowserStorageContract.blockedStorageRetainsUniqueIdentity = !!first && first === loadUserId() && first === authHeaders()["X-User-Id"];
  let html = "";
  try { html = renderToString(createElement(MemoryRouter, null, createElement(Layout))); } catch { /* Assert the actual layout render, not an exception helper. */ }
  blockedBrowserStorageContract.layoutRendersWhenStorageGetterThrows = html.includes("aq-shell");
  blockedBrowserStorageContract.temporaryIdentityWarningIsVisible = html.includes("刷新") && html.includes("临时独立身份");
  blockedBrowserStorageContract.preferenceReadsFailSafely = readLocalPreference("vr-theme") === null;
  blockedBrowserStorageContract.preferenceWritesFailSafely = !writeLocalPreference("aqsp-sidebar", "collapsed");
  const requestIds: string[] = [];
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    requestIds.push(new Headers(init?.headers).get("X-User-Id") || "");
    return new Response(JSON.stringify({ data: {} }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  await api.portfolio();
  await api.portfolio();
  blockedBrowserStorageContract.everyRequestKeepsSessionIdentity = requestIds.length === 2 && requestIds.every((id) => id === first && !!id);

  const values = new Map<string, string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage });
  const persistent = createBrowserIdentity(() => storage, () => "generated-persistent");
  blockedBrowserStorageContract.firstVisitPersistsIdentity = persistent.userId() === "generated-persistent" && values.get("vr-user-id") === "generated-persistent" && !persistent.temporary();
  const reload = createBrowserIdentity(() => storage, () => "must-not-replace");
  blockedBrowserStorageContract.reloadReusesPersistedIdentity = reload.userId() === "generated-persistent" && !reload.temporary();
  values.set("vr-user-id", "another-page-identity");
  blockedBrowserStorageContract.pageDoesNotSwitchIdentityMidRequest = persistent.userId() === "generated-persistent";
  blockedBrowserStorageContract.storageRecoveryDoesNotSwitchTemporarySession = loadUserId() === first;
  blockedBrowserStorageContract.preferencesPersistWhenAvailable = writeLocalPreference("vr-theme", "light") && readLocalPreference("vr-theme") === "light";

  const denied = () => { throw new DOMException("storage blocked", "SecurityError"); };
  const firstSession = createBrowserIdentity(denied, () => "isolated-session-a");
  const secondSession = createBrowserIdentity(denied, () => "isolated-session-b");
  blockedBrowserStorageContract.blockedSessionsNeverShareDefault = firstSession.userId() === "isolated-session-a" && secondSession.userId() === "isolated-session-b" && firstSession.userId() !== secondSession.userId();
  blockedBrowserStorageContract.blockedSessionIsExplicitlyTemporary = firstSession.temporary() && secondSession.temporary();
  const readOnly = createBrowserIdentity(() => ({ getItem: () => null, setItem: denied }), () => "write-failure-id");
  blockedBrowserStorageContract.quotaFailureKeepsStableIdentity = readOnly.userId() === "write-failure-id" && readOnly.userId() === "write-failure-id" && readOnly.temporary();
  const silent = createBrowserIdentity(() => ({ getItem: () => null, setItem: () => {} }), () => "silent-write-id");
  blockedBrowserStorageContract.unconfirmedPersistenceIsTemporary = silent.userId() === "silent-write-id" && silent.temporary();
  const existingReadOnly = createBrowserIdentity(() => ({ getItem: () => "existing-read-only", setItem: denied }), () => "must-not-replace");
  blockedBrowserStorageContract.existingIdentitySurvivesWriteRestriction = existingReadOnly.userId() === "existing-read-only" && !existingReadOnly.temporary();
  const whitespace = createBrowserIdentity(() => ({ getItem: () => "   ", setItem: denied }), () => "non-empty-id");
  blockedBrowserStorageContract.whitespaceCannotBecomeSharedIdentity = whitespace.userId() === "non-empty-id" && whitespace.temporary();
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: { getItem: denied, setItem: denied } });
  blockedBrowserStorageContract.throwingMethodsFailSafely = readLocalPreference("vr-theme") === null && !writeLocalPreference("vr-theme", "dark");
  let generationRejected = false;
  try { createBrowserIdentity(denied, () => "   ").userId(); } catch { generationRejected = true; }
  blockedBrowserStorageContract.missingIdentityFailsClosed = generationRejected;
} finally {
  globalThis.fetch = savedFetch;
  if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
  else Reflect.deleteProperty(globalThis, "localStorage");
}
