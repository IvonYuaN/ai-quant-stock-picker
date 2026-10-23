/** Browser identity is pinned for this page, including when storage is blocked. */
export interface BrowserIdentity {
  userId(): string;
  temporary(): boolean;
}

export function createBrowserIdentity(
  storage: () => Pick<Storage, "getItem" | "setItem"> = () => localStorage,
  generateId: () => string = () => crypto.randomUUID(),
): BrowserIdentity {
  let current: string | undefined;
  let temporary = false;
  const userId = (): string => {
    if (current) return current;
    try {
      const saved = storage().getItem("vr-user-id")?.trim();
      if (saved) { current = saved; return current; }
    } catch { /* Keep a distinct in-memory identity rather than a shared tenant. */ }
    current = generateId().trim();
    if (!current) throw new Error("无法创建独立访客身份，请使用支持安全随机数的浏览器");
    try {
      storage().setItem("vr-user-id", current);
      temporary = storage().getItem("vr-user-id") !== current;
    } catch { temporary = true; }
    return current;
  };
  return { userId, temporary: () => { userId(); return temporary; } };
}

/** UI preferences may fail without taking down the decision workspace. */
export function readLocalPreference(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

export function writeLocalPreference(key: string, value: string): boolean {
  try { localStorage.setItem(key, value); return true; } catch { return false; }
}
