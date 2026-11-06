import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Check, Pencil, Plus, Save, Trash2, X, Zap, Play } from "lucide-react";
import { Badge, EmptyState, LoadingState, SectionHeader, ToneCallout } from "@/components/ui/primitives";
import { api, type ConditionSignal, type SignalDefinition, type SignalDefinitionOptions, type SignalScanRun } from "@/lib/api";

type Draft = Pick<SignalDefinition, "id" | "name" | "kind" | "enabled" | "conditions"> & { revision?: number };

const emptyDraft = (): Draft => ({
  id: "",
  name: "",
  kind: "entry",
  enabled: true,
  conditions: [{ left: "close", op: ">", right: "field:prev_close", left_days: 0, right_days: 0 }],
});

const KIND_LABEL: Record<SignalDefinition["kind"], string> = { entry: "入场", exit: "出场", both: "出入通用" };
const dayInput = (date: Date) => {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
};

export function SignalLibraryPage() {
  const [items, setItems] = useState<SignalDefinition[]>([]);
  const [scanRuns, setScanRuns] = useState<SignalScanRun[]>([]);
  const [selectedSignalIds, setSelectedSignalIds] = useState<string[]>([]);
  const [symbolsInput, setSymbolsInput] = useState("");
  const [scanStart, setScanStart] = useState(() => dayInput(new Date(Date.now() - 29 * 86_400_000)));
  const [scanEnd, setScanEnd] = useState(() => dayInput(new Date()));
  const [scanning, setScanning] = useState(false);
  const [options, setOptions] = useState<SignalDefinitionOptions | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [armedDelete, setArmedDelete] = useState<string | null>(null);
  const deleteTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const editorOpen = draft !== null;

  async function refresh() {
    setError("");
    try {
      const [signals, catalog, scans] = await Promise.all([api.conditionSignals(), api.conditionSignalOptions(), api.signalScans()]);
      setItems(signals.items);
      setOptions(catalog);
      setScanRuns(scans.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "信号定义读取失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    return () => { if (deleteTimer.current) clearTimeout(deleteTimer.current); };
  }, []);

  useEffect(() => {
    if (!editorOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.querySelector<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled])")?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      openerRef.current?.focus();
    };
  }, [editorOpen]);

  function openEditor(next: Draft) {
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setError("");
    setDraft(next);
  }

  function handleEditorKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      setDraft(null);
      return;
    }
    if (event.key !== "Tab") return;
    const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ) ?? []);
    if (controls.length === 0) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function startDelete(id: string) {
    if (armedDelete === id) {
      if (deleteTimer.current) clearTimeout(deleteTimer.current);
      setArmedDelete(null);
      const item = items.find((row) => row.id === id);
      if (!item) return;
      void api.deleteConditionSignal(id, item.revision)
        .then(() => setItems((current) => current.filter((row) => row.id !== id)))
        .catch((reason) => setError(reason instanceof Error ? reason.message : "信号删除失败"));
      return;
    }
    if (deleteTimer.current) clearTimeout(deleteTimer.current);
    setArmedDelete(id);
    deleteTimer.current = setTimeout(() => setArmedDelete(null), 3000);
  }

  async function toggleEnabled(item: SignalDefinition) {
    setError("");
    try {
      const updated = await api.saveConditionSignal({
        id: item.id,
        name: item.name,
        kind: item.kind,
        enabled: !item.enabled,
        conditions: item.conditions,
        expected_revision: item.revision,
      });
      setItems((current) => current.map((row) => row.id === item.id ? updated : row));
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "信号状态保存失败";
      await refresh();
      setError(message);
    }
  }

  async function saveDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft) return;
    setSaving(true);
    setError("");
    try {
      const saved = await api.saveConditionSignal({
        id: draft.id.trim(), name: draft.name.trim(), kind: draft.kind,
        enabled: draft.enabled, conditions: draft.conditions,
        expected_revision: draft.revision,
      });
      setItems((current) => [saved, ...current.filter((row) => row.id !== saved.id)]);
      setDraft(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "信号保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function runScan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setScanning(true);
    setError("");
    try {
      const run = await api.runSignalScan({
        signal_ids: selectedSignalIds,
        symbols: symbolsInput.split(/[\s,，;；]+/).map((value) => value.trim()).filter(Boolean),
        start: scanStart,
        end: scanEnd,
      });
      setScanRuns((current) => [run, ...current.filter((item) => item.run_id !== run.run_id)]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "历史信号扫描失败");
    } finally {
      setScanning(false);
    }
  }

  async function deleteScan(runId: string) {
    try {
      await api.deleteSignalScan(runId);
      setScanRuns((current) => current.filter((item) => item.run_id !== runId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "扫描记录删除失败");
    }
  }

  function updateCondition(index: number, patch: Partial<ConditionSignal>) {
    setDraft((current) => current ? {
      ...current,
      conditions: current.conditions.map((condition, row) => row === index ? { ...condition, ...patch } : condition),
    } : current);
  }

  return <main className="aq-page">
    <SectionHeader title="条件信号" description="可复核的日线布尔条件定义" count={`${items.length} 条`} icon={Zap}>
      <button type="button" className="aq-btn aq-btn-primary shrink-0 flex-nowrap whitespace-nowrap" onClick={() => openEditor(emptyDraft())}>
        <Plus aria-hidden="true" />新建信号
      </button>
    </SectionHeader>

    <ToneCallout tone="neutral" title="研究范围" detail="历史扫描使用本地 raw 不复权日线，仅作为条件命中证据；不会写入正式台账、候选评分或交易结果。" />
    {error ? <ToneCallout tone="warn" title="操作未完成" detail={error} /> : null}

    <section className="aq-section" aria-label="历史信号扫描">
      <div className="aq-section-head"><div><h2>历史扫描</h2><p>最多 5 个入场信号、20 个标的、120 个自然日；每次最多保留 500 条命中。</p></div><Badge tone="neutral">{scanRuns.length}/20 次</Badge></div>
      <form className="grid gap-4" onSubmit={(event) => void runScan(event)}>
        <fieldset className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" disabled={scanning}>
          <legend className="mb-2 text-xs font-medium">启用的入场信号</legend>
          {items.filter((item) => item.enabled && (item.kind === "entry" || item.kind === "both")).map((item) => <label className="flex min-w-0 items-center gap-2 text-sm" key={item.id}>
            <input type="checkbox" checked={selectedSignalIds.includes(item.id)} disabled={!selectedSignalIds.includes(item.id) && selectedSignalIds.length >= 5} onChange={(event) => setSelectedSignalIds((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} />
            <span className="truncate">{item.name}</span><span className="aq-muted text-xs">r{item.revision}</span>
          </label>)}
          {!items.some((item) => item.enabled && (item.kind === "entry" || item.kind === "both")) ? <p className="aq-muted text-sm">没有可扫描的启用入场信号。</p> : null}
        </fieldset>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(14rem,2fr)_1fr_1fr_auto] lg:items-end">
          <label className="space-y-1 text-xs">股票代码<input className="aq-input aq-input-block" value={symbolsInput} onChange={(event) => setSymbolsInput(event.target.value)} placeholder="例如 600519, 000001" required /></label>
          <label className="space-y-1 text-xs">开始日期<input className="aq-input aq-input-block" type="date" value={scanStart} onChange={(event) => setScanStart(event.target.value)} required /></label>
          <label className="space-y-1 text-xs">结束日期<input className="aq-input aq-input-block" type="date" value={scanEnd} onChange={(event) => setScanEnd(event.target.value)} required /></label>
          <button type="submit" className="aq-btn aq-btn-primary flex-nowrap whitespace-nowrap" disabled={scanning || selectedSignalIds.length === 0 || scanRuns.length >= 20}><Play aria-hidden="true" />{scanning ? "扫描中" : "运行扫描"}</button>
        </div>
      </form>
      <div className="mt-5 space-y-3">
        {scanRuns.length === 0 ? <p className="aq-muted text-sm">还没有扫描记录。</p> : scanRuns.map((run) => <article className="aq-card" key={run.run_id}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold">{run.start} 至 {run.end}</h3><Badge tone={run.status === "ready" ? "ok" : run.status === "blocked" ? "warn" : "neutral"}>{run.status === "ready" ? "完成" : run.status === "blocked" ? "阻断" : "部分完成"}</Badge><Badge>{run.hit_count} 条命中</Badge></div>
              <p className="aq-muted mt-1 text-xs">{run.signals.map((signal) => `${signal.name} r${signal.revision}`).join("、")} · {run.scanned_symbols}/{run.requested_symbols} 个标的 · raw 不复权</p></div>
            <button type="button" className="aq-btn aq-btn-icon shrink-0" aria-label="删除扫描记录" title="删除扫描记录" onClick={() => void deleteScan(run.run_id)}><Trash2 aria-hidden="true" /></button>
          </div>
          {run.hits.length ? <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[32rem] text-left text-xs"><thead><tr className="aq-muted border-b border-border"><th className="py-2 pr-3">日期</th><th className="py-2 pr-3">代码</th><th className="py-2 pr-3">不复权收盘</th><th className="py-2">命中信号</th></tr></thead><tbody>{run.hits.map((hit) => <tr className="border-b border-border/60" key={`${hit.symbol}-${hit.date}`}><td className="py-2 pr-3 font-mono">{hit.date}</td><td className="py-2 pr-3 font-mono">{hit.symbol}</td><td className="py-2 pr-3 font-mono">{hit.raw_close.toFixed(2)}</td><td className="py-2">{hit.signals.map((signal) => signal.name).join("、")}</td></tr>)}</tbody></table></div> : null}
          {run.hits_truncated ? <p className="mt-2 text-xs text-amber-700">结果达到 500 条展示上限。</p> : null}
          {run.blockers.length ? <details className="mt-3 text-xs"><summary className="cursor-pointer">数据阻断（{run.blockers.length}）</summary><ul className="mt-2 space-y-1">{run.blockers.map((blocker, index) => <li className="break-words" key={`${blocker.symbol}-${index}`}>{blocker.symbol}：{blocker.reason}</li>)}</ul></details> : null}
        </article>)}
      </div>
    </section>

    <section className="aq-section" aria-label="信号定义列表">
      <div className="aq-section-head">
        <div><h2>信号库</h2><p>定义以租户隔离保存；修改采用修订号保护。</p></div>
        {options ? <Badge tone="neutral">raw 行情字段 {options.fields.length}</Badge> : null}
      </div>
      {loading ? <LoadingState label="读取信号定义…" /> : items.length === 0 ? <EmptyState title="还没有条件信号" detail="新建一个由行情字段组成的日线条件。" /> :
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {items.map((item) => <article className="aq-card" key={item.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2"><h3 className="truncate text-sm font-semibold">{item.name}</h3><Badge>{KIND_LABEL[item.kind]}</Badge><Badge tone={item.enabled ? "warn" : "neutral"}>{item.enabled ? "已启用" : "已停用"}</Badge></div>
                <code className="aq-muted mt-1 block break-all text-xs">{item.id} · r{item.revision}</code>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <button type="button" className="aq-btn aq-btn-icon" aria-label={item.enabled ? "停用信号" : "启用信号"} title={item.enabled ? "停用" : "启用"} onClick={() => void toggleEnabled(item)}><Zap aria-hidden="true" /></button>
                <button type="button" className="aq-btn aq-btn-icon" aria-label={`编辑${item.name}`} title="编辑" onClick={() => openEditor({ ...item, conditions: item.conditions.map((row) => ({ ...row })) })}><Pencil aria-hidden="true" /></button>
                <button type="button" className={`aq-btn aq-btn-icon${armedDelete === item.id ? " aq-btn-danger" : ""}`} aria-label={armedDelete === item.id ? "再次点击确认删除" : `删除${item.name}`} title={armedDelete === item.id ? "再次点击确认" : "删除"} onClick={() => startDelete(item.id)}>{armedDelete === item.id ? <Check aria-hidden="true" /> : <Trash2 aria-hidden="true" />}</button>
              </div>
            </div>
            <div className="mt-3 space-y-1 border-t border-border pt-3">
              {item.conditions.map((condition, index) => <div className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-2 text-xs" key={`${condition.left}-${index}`}>
                <span className="aq-muted">{index === 0 ? "当" : "且"}</span>
                <code className="break-words">{formatCondition(condition)}</code>
              </div>)}
            </div>
          </article>)}
        </div>}
    </section>

    {draft && options ? <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-3" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) setDraft(null); }}>
      <section ref={dialogRef} onKeyDown={handleEditorKeyDown} tabIndex={-1} className="w-full max-w-3xl overflow-hidden rounded-lg border border-border shadow-2xl" style={{ backgroundColor: "hsl(var(--card))" }} role="dialog" aria-modal="true" aria-labelledby="signal-editor-title">
        <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-5">
          <div><h2 id="signal-editor-title" className="text-base font-semibold">{draft.revision ? "编辑信号" : "新建信号"}</h2><p className="aq-muted mt-1 text-xs">定义仅基于已支持的 raw 日线字段。</p></div>
          <button type="button" className="aq-btn aq-btn-icon" aria-label="关闭编辑器" onClick={() => setDraft(null)} disabled={saving}><X aria-hidden="true" /></button>
        </header>
        <form onSubmit={(event) => void saveDraft(event)}>
          <div className="max-h-[70vh] space-y-4 overflow-y-auto px-4 py-4 sm:px-5">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <label className="space-y-1 text-xs">信号标识<input className="aq-input aq-input-block font-mono" value={draft.id} maxLength={40} disabled={!!draft.revision || saving} onChange={(event) => setDraft({ ...draft, id: event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "") })} required /></label>
              <label className="space-y-1 text-xs">名称<input className="aq-input aq-input-block" value={draft.name} maxLength={120} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} required /></label>
              <label className="space-y-1 text-xs">用途<select className="aq-input aq-input-block" value={draft.kind} disabled={saving} onChange={(event) => setDraft({ ...draft, kind: event.target.value as SignalDefinition["kind"] })}><option value="entry">入场</option><option value="exit">出场</option><option value="both">出入通用</option></select></label>
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-medium">触发条件</h3><p className="aq-muted text-xs">所有条件同时满足；字段引用格式为 field:字段名。</p></div><button type="button" className="aq-btn aq-btn-icon shrink-0" aria-label="添加条件" title="添加条件" disabled={saving || draft.conditions.length >= options.max_conditions} onClick={() => setDraft({ ...draft, conditions: [...draft.conditions, { left: options.fields[0] ?? "close", op: ">", right: "0", left_days: 0, right_days: 0 }] })}><Plus aria-hidden="true" /></button></div>
              {draft.conditions.map((condition, index) => <div className="grid grid-cols-1 items-end gap-2 border-b border-border pb-3 sm:grid-cols-[minmax(0,1fr)_6rem_minmax(0,1fr)_5rem_5rem_auto]" key={index}>
                <label className="space-y-1 text-xs">字段<select className="aq-input aq-input-block" value={condition.left} disabled={saving} onChange={(event) => updateCondition(index, { left: event.target.value })}>{options.fields.map((field) => <option key={field} value={field}>{field}</option>)}</select></label>
                <label className="space-y-1 text-xs">关系<select className="aq-input aq-input-block" value={condition.op} disabled={saving} onChange={(event) => updateCondition(index, { op: event.target.value })}>{options.operators.map((op) => <option key={op} value={op}>{op}</option>)}</select></label>
                <label className="space-y-1 text-xs">值<input className="aq-input aq-input-block font-mono" value={String(condition.right)} disabled={saving} onChange={(event) => updateCondition(index, { right: event.target.value })} placeholder="数值或 field:close" required /></label>
                <label className="space-y-1 text-xs">左偏移<input className="aq-input aq-input-block" type="number" min={0} max={options.max_days} value={condition.left_days} disabled={saving} onChange={(event) => updateCondition(index, { left_days: Number(event.target.value) })} /></label>
                <label className="space-y-1 text-xs">右偏移<input className="aq-input aq-input-block" type="number" min={0} max={options.max_days} value={condition.right_days} disabled={saving} onChange={(event) => updateCondition(index, { right_days: Number(event.target.value) })} /></label>
                <button type="button" className="aq-btn aq-btn-icon" aria-label={`删除第 ${index + 1} 条条件`} title="删除条件" disabled={saving || draft.conditions.length <= 1} onClick={() => setDraft({ ...draft, conditions: draft.conditions.filter((_, row) => row !== index) })}><Trash2 aria-hidden="true" /></button>
              </div>)}
            </div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.enabled} disabled={saving} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />启用定义</label>
            {error ? <ToneCallout tone="warn" title="保存失败" detail={error} /> : null}
          </div>
          <footer className="flex justify-end gap-2 border-t border-border px-4 py-3 sm:px-5"><button type="button" className="aq-btn flex-nowrap whitespace-nowrap" onClick={() => setDraft(null)} disabled={saving}>取消</button><button type="submit" className="aq-btn aq-btn-primary flex-nowrap whitespace-nowrap" disabled={saving}><Save aria-hidden="true" />{saving ? "保存中" : "保存定义"}</button></footer>
        </form>
      </section>
    </div> : null}
  </main>;
}

function formatCondition(condition: ConditionSignal): string {
  const left = withOffset(condition.left, condition.left_days);
  const rawRight = String(condition.right);
  const right = rawRight.startsWith("field:") ? withOffset(rawRight.slice(6), condition.right_days) : rawRight;
  return `${left} ${condition.op} ${right}`;
}

function withOffset(field: string, days: number): string {
  return days > 0 ? `${field} (前 ${days} 日)` : field;
}
