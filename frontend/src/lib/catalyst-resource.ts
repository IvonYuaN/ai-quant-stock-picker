import { fetchCatalyst, type CatalystData } from "./catalyst-client";

export interface CatalystResourceState {
  data: CatalystData | null;
  loading: boolean;
  error: string;
}

/** Each mounted section owns its request; late replies cannot replace a refresh. */
export function createCatalystResource(
  publish: (state: CatalystResourceState) => void,
  request: (signal: AbortSignal) => Promise<CatalystData> = fetchCatalyst,
) {
  let state: CatalystResourceState = { data: null, loading: false, error: "" };
  let revision = 0;
  let disposed = false;
  let controller: AbortController | null = null;
  const update = (next: CatalystResourceState) => { state = next; publish(state); };
  return {
    async refresh(): Promise<void> {
      if (disposed) return;
      controller?.abort();
      const active = new AbortController();
      controller = active;
      const run = ++revision;
      const current = () => !disposed && revision === run && !active.signal.aborted;
      update({ ...state, loading: true, error: "" });
      try {
        const data = await request(active.signal);
        if (current()) update({ data, loading: false, error: "" });
      } catch {
        if (current()) update({ ...state, loading: false, error: "催化报告读取失败，请稍后重试" });
      }
    },
    dispose(): void {
      disposed = true;
      revision += 1;
      controller?.abort();
    },
  };
}
