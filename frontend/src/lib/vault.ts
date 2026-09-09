// 本机私有数据的导出 / 导入。
//
// 「我的线」的数据只存在 localStorage，好处是天然与他人隔离、不进服务端；
// 代价是换设备、换浏览器、清缓存就会丢。这里提供一份 JSON 快照让用户自己搬。
//
// 安全边界：访问密钥（vr-access-key）属于凭据，**不进导出文件**。

export const VAULT_FORMAT = "aqsp-local-vault";
export const VAULT_VERSION = 1;

const EXPORT_KEYS = ["vr-watchlist", "vr-notes", "vr-theme", "aqsp-sidebar"] as const;

export interface VaultFile {
  format: string;
  version: number;
  exported_at: string;
  data: Record<string, string | null>;
}

export function exportVault(): VaultFile {
  const data: Record<string, string | null> = {};
  for (const key of EXPORT_KEYS) {
    data[key] = localStorage.getItem(key);
  }
  return {
    format: VAULT_FORMAT,
    version: VAULT_VERSION,
    exported_at: new Date().toISOString(),
    data,
  };
}

export type ImportResult = { ok: true; keys: number } | { ok: false; reason: string };

export function importVault(raw: string): ImportResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, reason: "不是合法的 JSON 文件" };
  }
  if (typeof parsed !== "object" || parsed === null) return { ok: false, reason: "文件内容为空" };
  const file = parsed as Partial<VaultFile>;
  if (file.format !== VAULT_FORMAT) return { ok: false, reason: "不是 AQSP 的备份文件" };
  if (!file.data || typeof file.data !== "object") return { ok: false, reason: "备份文件缺少数据段" };

  let keys = 0;
  for (const key of EXPORT_KEYS) {
    const value = file.data[key];
    if (typeof value === "string") {
      localStorage.setItem(key, value);
      keys += 1;
    }
  }
  return { ok: true, keys };
}

export function downloadVault(): void {
  const blob = new Blob([JSON.stringify(exportVault(), null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `aqsp-local-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
