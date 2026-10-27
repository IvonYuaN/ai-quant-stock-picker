// Bounded NDJSON reader. Progress is advisory; only a result record completes a request.
export async function readDiscussionStream<T>(response: Response, progress: (stage: string) => void): Promise<T> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("分析进度连接不可用，请刷新核对结果。");
  const decoder = new TextDecoder();
  let pending = "";
  let size = 0;
  function record(line: string): { data: T } | undefined {
    if (!line.trim()) return;
    const row = JSON.parse(line);
    if (row.event === "error") throw new Error(typeof row.detail === "string" ? row.detail : "分析未完成，请刷新核对结果。");
    if (row.event === "progress" && typeof row.stage === "string") progress(row.stage);
    if (row.event === "result" && row.data && typeof row.data === "object") return { data: row.data as T };
  }
  try {
    while (true) {
      const { done, value } = await reader.read();
      size += value?.byteLength ?? 0;
      if (size > 2_000_000) throw new Error("分析响应过大，请刷新核对结果。");
      pending += decoder.decode(value, { stream: !done });
      const lines = pending.split("\n");
      pending = lines.pop() ?? "";
      for (const line of lines) {
        const result = record(line);
        if (result) return result.data;
      }
      if (done) {
        const result = record(pending);
        if (result) return result.data;
        throw new Error("分析连接已结束，未收到最终结果，请刷新核对。");
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
