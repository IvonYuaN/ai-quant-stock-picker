// 关注股票（自选股）—— 只存本地 localStorage，不上传、不进仓库。
// 行情复用 /api/quote；复盘时把关注股行情一并喂给用户自己的 AI。

export const WATCHLIST_STORAGE_KEY = "vr-watchlist";
export const WATCHLIST_CHANGED_EVENT = "aqsp-watchlist-changed";
export const WATCHLIST_REMOVAL_LIMIT = 20;

const STORAGE_FORMAT = "aqsp-watchlist";

export interface WatchlistRemoval {
  readonly codes: readonly string[];
  readonly removed_at: string;
  /** 与服务端审计记录对应的幂等操作标识；旧本地记录可缺省。 */
  readonly operation_id?: string;
}

export interface WatchlistChangedDetail {
  readonly removed: readonly string[];
  readonly operation_id?: string;
}

interface WatchlistState {
  codes: string[];
  removals: WatchlistRemoval[];
}

function validCodes(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((code): code is string => typeof code === "string" && /^\d{6}$/.test(code)) : [];
}

function parseWatchState(raw: string | null): WatchlistState {
  let value: unknown;
  try { value = JSON.parse(raw || "[]"); }
  catch { return { codes: [], removals: [] }; }
  if (Array.isArray(value)) return { codes: validCodes(value), removals: [] };
  if (!value || typeof value !== "object" || !("format" in value) || value.format !== STORAGE_FORMAT || !("version" in value) || value.version !== 1) return { codes: [], removals: [] };
  const state = value as { codes?: unknown; removals?: unknown };
  const removals: WatchlistRemoval[] = [];
  for (const item of Array.isArray(state.removals) ? state.removals : []) {
    if (!item || typeof item !== "object") continue;
    const record = item as { codes?: unknown; removed_at?: unknown; operation_id?: unknown };
    const codes = [...new Set(validCodes(record.codes))];
    if (!codes.length || typeof record.removed_at !== "string" || !/(Z|[+-]\d{2}:\d{2})$/.test(record.removed_at) || !Number.isFinite(Date.parse(record.removed_at))) continue;
    const operationId = typeof record.operation_id === "string" && record.operation_id.trim()
      ? record.operation_id.trim()
      : undefined;
    removals.push({ codes, removed_at: record.removed_at, ...(operationId ? { operation_id: operationId } : {}) });
    if (removals.length === WATCHLIST_REMOVAL_LIMIT) break;
  }
  return { codes: validCodes(state.codes), removals };
}

export function loadWatch(): string[] {
  try {
    return parseWatchState(localStorage.getItem(WATCHLIST_STORAGE_KEY)).codes;
  } catch {
    return [];
  }
}

export function loadWatchRemovals(): WatchlistRemoval[] {
  try { return parseWatchState(localStorage.getItem(WATCHLIST_STORAGE_KEY)).removals; }
  catch { return []; }
}

export function saveWatch(codes: string[]): WatchlistChangedDetail {
  // One key keeps removal evidence atomic with the list and inside existing backups.
  const previous = parseWatchState(localStorage.getItem(WATCHLIST_STORAGE_KEY));
  const nextCodes = validCodes(codes);
  const nextSet = new Set(nextCodes);
  const removed = [...new Set(previous.codes.filter((code) => !nextSet.has(code)))];
  const operationId = removed.length ? makeRemovalOperationId() : undefined;
  const removals = removed.length
    ? [{ codes: removed, removed_at: new Date().toISOString(), operation_id: operationId }, ...previous.removals].slice(0, WATCHLIST_REMOVAL_LIMIT)
    : previous.removals;
  const payload = removals.length ? { format: STORAGE_FORMAT, version: 1, codes: nextCodes, removals } : nextCodes;
  localStorage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(payload));
  // storage only reaches other tabs; notify this workspace after persistence succeeds.
  if (typeof window !== "undefined") {
    const detail: WatchlistChangedDetail = { removed, ...(operationId ? { operation_id: operationId } : {}) };
    window.dispatchEvent(new CustomEvent<WatchlistChangedDetail>(WATCHLIST_CHANGED_EVENT, { detail }));
    return detail;
  }
  return { removed, ...(operationId ? { operation_id: operationId } : {}) };
}

function makeRemovalOperationId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return `watchlist-remove:${crypto.randomUUID()}`;
    }
  } catch {
    // Fall through to a local, collision-resistant fallback for restricted browsers.
  }
  return `watchlist-remove:${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

// 从任意文本里抽取 6 位 A 股代码（逗号 / 空格 / 换行 / 顿号分隔都行，方便一次粘贴一串）。
export function parseCodes(raw: string): string[] {
  const tokens = raw.split(/[^\d]+/).filter(Boolean);
  return Array.from(new Set(tokens.filter((t) => /^\d{6}$/.test(t))));
}

// 把用户输入的一串代码并入已有自选，返回去重后的新列表 + 实际新增数量。
export function addCodes(existing: string[], raw: string): { next: string[]; added: number } {
  const incoming = parseCodes(raw).filter((c) => !existing.includes(c));
  return { next: [...existing, ...incoming], added: incoming.length };
}
