import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Download, ListFilter, Pencil, Play, Plus, RefreshCw, Save, Trash2, X } from "lucide-react";
import { Badge, EmptyState, LoadingState, SectionHeader, StatePanel, Tag } from "@/components/ui/primitives";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { api, authHeaders, type PerformancePayload, type StrategyCatalogPayload, type StrategyDefinition, type StrategyDefinitionOptions, type StrategyOosEvidence, type StrategyScanRun } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";
import {
  strategyHitRateLabel, buildRerunView, configRevisionLabel, configSymbolLabel,
  configWindowLabel, lastRerunLabel, rerunStatusTone,
  type RerunOutcome, type SavedBacktestConfig,
} from "@/lib/strategy-workbench-view";

const STATUS_LABELS: Record<string, string> = {
  implemented: "已实现",
  implemented_partial: "部分实现",
  runtime: "运行中",
  data_factor_only: "仅数据因子",
  research_absorbed: "已吸收研究待验证",
  research_only: "研究项",
};

type StrategyDraft = Pick<StrategyDefinition, "id" | "name" | "description" | "expression" | "exit_expression" | "enabled"> & { score_expression: string; revision?: number };

const emptyStrategyDraft = (): StrategyDraft => ({
  id: "",
  name: "",
  description: "",
  expression: "",
  exit_expression: "",
  score_expression: "",
  enabled: true,
});

// 保存候选回测参数 / 一键复测：本地网关（端点由主线在 app.py / api.ts 统一接线）。
// 独立前缀 /api/aqsp/strategy-backtest-configs，避免与 /strategy-backtests/{run_id} 路由冲突。
const CONFIGS_API = "/api/aqsp/strategy-backtest-configs";

async function configsRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders() };
  if (init?.body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(`${CONFIGS_API}${path}`, { ...init, headers });
  } catch {
    throw new Error("连接不到后端，无法读取已保存回测配置");
  }
  let payload: unknown = null;
  try { payload = await response.json(); } catch { /* 非 JSON 响应 */ }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      && typeof (payload as { detail?: unknown }).detail === "string"
      ? (payload as { detail: string }).detail
      : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  const data = payload && typeof payload === "object" && "data" in payload
    ? (payload as { data: unknown }).data
    : payload;
  return data as T;
}

function listSavedConfigs(): Promise<{ items: SavedBacktestConfig[] }> {
  return configsRequest<{ items: SavedBacktestConfig[] }>("");
}

function rerunSavedConfig(configId: string, expectedRevision: number): Promise<RerunOutcome> {
  return configsRequest<RerunOutcome>(
    `/${encodeURIComponent(configId)}/rerun`,
    { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) },
  );
}

function SavedConfigRerunSection() {
  const [configs, setConfigs] = useState<SavedBacktestConfig[]>([]);
  const [outcomes, setOutcomes] = useState<Record<string, RerunOutcome>>({});
  const [busyId, setBusyId] = useState("");
  const [sectionError, setSectionError] = useState("");
  const [sectionLoading, setSectionLoading] = useState(true);

  async function refreshConfigs() {
    try {
      const result = await listSavedConfigs();
      setConfigs(result.items);
      setSectionError("");
    } catch (reason) {
      setSectionError(reason instanceof Error ? reason.message : "已保存回测配置读取失败");
    } finally {
      setSectionLoading(false);
    }
  }

  useEffect(() => {
    let current = true;
    listSavedConfigs()
      .then((result) => { if (current) setConfigs(result.items); })
      .catch((reason) => { if (current) setSectionError(reason instanceof Error ? reason.message : "已保存回测配置读取失败"); })
      .finally(() => { if (current) setSectionLoading(false); });
    return () => { current = false; };
  }, []);

  async function runRerun(config: SavedBacktestConfig) {
    setBusyId(config.config_id);
    setSectionError("");
    try {
      // 只接收本次一键复测的 outcome；绝不拉取或渲染旧 run 的结果/gate。
      const outcome = await rerunSavedConfig(config.config_id, config.revision);
      setOutcomes((current) => ({ ...current, [config.config_id]: outcome }));
      await refreshConfigs();
    } catch (reason) {
      setSectionError(reason instanceof Error ? reason.message : "一键复测失败");
    } finally {
      setBusyId("");
    }
  }

  return <section className="aq-section" aria-label="已保存候选回测配置">
    <SectionHeader title="已保存候选回测配置" description="命名可复用回放参数。一键复测会重新核对结束日前每个标的的 as-of 覆盖；缺覆盖即明确失败、不产生新任务、不复用旧结果/gate、不自动重跑，配置仍可再次复测。" count={`${configs.length} 份配置`}>
      <button type="button" className="aq-btn aq-btn-icon" aria-label="刷新已保存配置" title="刷新" onClick={() => void refreshConfigs()}><RefreshCw aria-hidden="true" /></button>
    </SectionHeader>
    {sectionError ? <StatePanel tone="warn">{sectionError}</StatePanel> : null}
    {sectionLoading ? <LoadingState label="读取已保存回测配置…" /> : configs.length ? <div className="grid gap-3 lg:grid-cols-2">
      {configs.map((config) => {
        const outcome = outcomes[config.config_id] ?? null;
        const view = buildRerunView(outcome);
        return <article className="aq-card min-w-0" key={config.config_id}>
          <header className="flex flex-wrap items-start justify-between gap-2"><div className="min-w-0"><h3 className="text-sm font-semibold">{config.name}</h3><code className="aq-muted mt-1 block break-all text-xs">{config.config_id} · {configRevisionLabel(config)} · {configSymbolLabel(config)}</code><p className="aq-muted mt-1 text-xs">{configWindowLabel(config)}</p></div><Badge tone="neutral">指纹 {config.params_sha256.slice(0, 12)}</Badge></header>
          <p className="aq-note mt-2">{lastRerunLabel(config)}</p>
          <div className="aq-row-actions mt-3"><button type="button" className="aq-btn aq-btn-primary" disabled={busyId === config.config_id} onClick={() => void runRerun(config)}><RefreshCw aria-hidden="true" />{busyId === config.config_id ? "复测中" : "一键复测"}</button></div>
          {view.kind === "rejected" ? <StatePanel tone="warn">复测前置校验失败（终态，不自动重跑；配置仍可再次复测）<ul className="mt-2 list-disc pl-4 text-xs">{view.hints.map((hint, index) => <li key={`${config.config_id}-hint-${index}`}>{hint}</li>)}</ul></StatePanel> : null}
          {view.kind === "running" || view.kind === "completed" ? <StatePanel tone={rerunStatusTone(outcome)}>已创建全新回放任务 {view.resultRunId}；结果以该任务自身产物为准，不展示任何旧 run 的结论。</StatePanel> : null}
        </article>;
      })}
    </div> : <EmptyState title="还没有保存的候选回测配置" detail="完成一次回测后把其冻结参数存成命名配置，即可一键复测。" />}
  </section>;
}

function CustomStrategyWorkbench() {
  const [definitions, setDefinitions] = useState<StrategyDefinition[]>([]);
  const [options, setOptions] = useState<StrategyDefinitionOptions | null>(null);
  const [runs, setRuns] = useState<StrategyScanRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<StrategyScanRun | null>(null);
  const [draft, setDraft] = useState<StrategyDraft | null>(null);
  const [asOf, setAsOf] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [scanningId, setScanningId] = useState("");
  const [error, setError] = useState("");
  const [scanError, setScanError] = useState("");

  async function refreshRuns() {
    const result = await api.strategyScans();
    setRuns(result.items);
    return result.items;
  }

  useEffect(() => {
    let current = true;
    Promise.allSettled([api.strategyDefinitions(), api.strategyDefinitionOptions(), api.strategyScans()]).then((results) => {
      if (!current) return;
      const [definitionsResult, optionsResult, runsResult] = results;
      if (definitionsResult.status === "fulfilled") setDefinitions(definitionsResult.value.items);
      else setError(definitionsResult.reason instanceof Error ? definitionsResult.reason.message : "自定义策略读取失败");
      if (optionsResult.status === "fulfilled") setOptions(optionsResult.value);
      else setError(optionsResult.reason instanceof Error ? optionsResult.reason.message : "策略字段目录读取失败");
      if (runsResult.status === "fulfilled") setRuns(runsResult.value.items);
      else setScanError(runsResult.reason instanceof Error ? runsResult.reason.message : "扫描任务读取失败");
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, []);

  const hasActiveRuns = runs.some((run) => ["starting", "running", "cancelling"].includes(run.status));
  useEffect(() => {
    if (!hasActiveRuns) return;
    const timer = setInterval(() => {
      void refreshRuns().catch((reason) => setScanError(reason instanceof Error ? reason.message : "扫描任务状态读取失败"));
    }, 1500);
    return () => clearInterval(timer);
  }, [hasActiveRuns]);

  async function saveDefinition(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft) return;
    setSaving(true);
    setError("");
    try {
      const saved = await api.saveStrategyDefinition({
        id: draft.id.trim(),
        name: draft.name.trim(),
        description: draft.description.trim(),
        expression: draft.expression.trim(),
        exit_expression: draft.exit_expression.trim(),
        score_expression: draft.score_expression.trim(),
        enabled: draft.enabled,
        expected_revision: draft.revision,
      });
      setDefinitions((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      setDraft(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "策略保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function deleteDefinition(item: StrategyDefinition) {
    if (!window.confirm(`删除策略“${item.name}”？历史扫描记录会保留。`)) return;
    try {
      await api.deleteStrategyDefinition(item.id, item.revision);
      setDefinitions((current) => current.filter((row) => row.id !== item.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "策略删除失败");
    }
  }

  async function startScan(item: StrategyDefinition) {
    setScanningId(item.id);
    setScanError("");
    try {
      const run = await api.startStrategyScan({ strategy_id: item.id, ...(asOf ? { as_of: asOf } : {}) });
      setRuns((current) => [run, ...current.filter((row) => row.run_id !== run.run_id)]);
      setSelectedRun(run);
    } catch (reason) {
      setScanError(reason instanceof Error ? reason.message : "全市场扫描启动失败");
    } finally {
      setScanningId("");
    }
  }

  async function openRun(runId: string) {
    try {
      setSelectedRun(await api.strategyScan(runId));
    } catch (reason) {
      setScanError(reason instanceof Error ? reason.message : "扫描结果读取失败");
    }
  }

  async function cancelScan(runId: string) {
    try {
      const updated = await api.cancelStrategyScan(runId);
      setRuns((current) => current.map((run) => run.run_id === runId ? updated : run));
      setSelectedRun(updated);
    } catch (reason) {
      setScanError(reason instanceof Error ? reason.message : "扫描取消失败");
    }
  }

  async function deleteScan(runId: string) {
    if (!window.confirm("删除这次扫描及其逐代码覆盖结果？")) return;
    try {
      await api.deleteStrategyScan(runId);
      setRuns((current) => current.filter((run) => run.run_id !== runId));
      setSelectedRun((current) => current?.run_id === runId ? null : current);
    } catch (reason) {
      setScanError(reason instanceof Error ? reason.message : "扫描删除失败");
    }
  }

  async function exportScan(runId: string, view: "hits" | "coverage") {
    try {
      await api.downloadStrategyScan(runId, view);
    } catch (reason) {
      setScanError(reason instanceof Error ? reason.message : "CSV 导出失败");
    }
  }

  return <section className="aq-section" aria-label="自定义策略与全市场扫描">
    <SectionHeader title="自定义策略" description="可编辑日线表达式；扫描结果按策略修订和 PIT 价基留档。" count={`${definitions.length}/${options?.max_strategies ?? 50} 个策略`}>
      <button type="button" className="aq-btn aq-btn-primary" onClick={() => { setError(""); setDraft(emptyStrategyDraft()); }} disabled={definitions.length >= (options?.max_strategies ?? 50)}><Plus aria-hidden="true" />新建策略</button>
    </SectionHeader>
    {error ? <StatePanel tone="warn">{error}</StatePanel> : null}
    {scanError ? <StatePanel tone="warn">{scanError}</StatePanel> : null}
    {draft ? <form className="aq-card grid gap-3" onSubmit={(event) => void saveDefinition(event)}>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs">策略 ID<input className="aq-input aq-input-block" value={draft.id} maxLength={40} disabled={saving || draft.revision !== undefined} onChange={(event) => setDraft({ ...draft, id: event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "") })} required /></label>
        <label className="space-y-1 text-xs">名称<input className="aq-input aq-input-block" value={draft.name} maxLength={120} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} required /></label>
      </div>
      <label className="space-y-1 text-xs">说明<input className="aq-input aq-input-block" value={draft.description} maxLength={1000} disabled={saving} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
      <label className="space-y-1 text-xs">入场表达式<textarea className="aq-input aq-input-block min-h-24 font-mono" value={draft.expression} maxLength={options?.max_expression_chars ?? 512} disabled={saving} onChange={(event) => setDraft({ ...draft, expression: event.target.value })} placeholder="close > ma20 and volume_ratio > 2" required /></label>
      <label className="space-y-1 text-xs">退出表达式（可选）<textarea className="aq-input aq-input-block min-h-20 font-mono" value={draft.exit_expression} maxLength={options?.max_expression_chars ?? 512} disabled={saving} onChange={(event) => setDraft({ ...draft, exit_expression: event.target.value })} placeholder="close < ma20" /></label>
      <label className="space-y-1 text-xs">组合评分表达式（可选）<textarea className="aq-input aq-input-block min-h-20 font-mono" value={draft.score_expression} maxLength={options?.max_expression_chars ?? 512} disabled={saving} onChange={(event) => setDraft({ ...draft, score_expression: event.target.value })} placeholder="rank(volume_ratio) + rank(ret_5)" /></label>
      {options ? <div className="flex flex-wrap gap-1" aria-label="可用字段">{options.fields.map((field) => <code key={field} className="aq-code">{field}</code>)}</div> : null}
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.enabled} disabled={saving} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />启用</label>
      <div className="flex justify-end gap-2"><button type="button" className="aq-btn" onClick={() => setDraft(null)} disabled={saving}><X aria-hidden="true" />取消</button><button type="submit" className="aq-btn aq-btn-primary" disabled={saving}><Save aria-hidden="true" />{saving ? "保存中" : "保存策略"}</button></div>
    </form> : null}
    {loading ? <LoadingState label="读取自定义策略…" /> : definitions.length ? <div className="grid gap-3 lg:grid-cols-2">
      {definitions.map((item) => <article className="aq-card min-w-0" key={item.id}>
        <header className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold">{item.name}</h3><Badge tone={item.enabled ? "ok" : "neutral"}>{item.enabled ? "启用" : "停用"}</Badge></div><code className="aq-muted mt-1 block break-all text-xs">{item.id} · r{item.revision} · {item.requires_pit_price_basis ? "需要 PIT 价基" : "不依赖价格复权"}</code></div><div className="flex shrink-0 gap-1"><button type="button" className="aq-btn aq-btn-icon" aria-label={`编辑${item.name}`} title="编辑" onClick={() => { setError(""); setDraft({ id: item.id, name: item.name, description: item.description, expression: item.expression, exit_expression: item.exit_expression || "", score_expression: item.score_expression || "", enabled: item.enabled, revision: item.revision }); }}><Pencil aria-hidden="true" /></button><button type="button" className="aq-btn aq-btn-icon" aria-label={`删除${item.name}`} title="删除" onClick={() => void deleteDefinition(item)}><Trash2 aria-hidden="true" /></button></div></header>
        {item.description ? <p className="aq-muted mt-2 text-sm">{item.description}</p> : null}<code className="mt-2 block break-words text-xs">入场：{item.expression}</code>{item.exit_expression ? <code className="aq-muted mt-1 block break-words text-xs">退出：{item.exit_expression}</code> : null}{item.score_expression ? <code className="aq-muted mt-1 block break-words text-xs">评分：{item.score_expression}</code> : null}
        <div className="aq-row-actions mt-3"><button type="button" className="aq-btn aq-btn-primary" disabled={!item.enabled || !!scanningId || hasActiveRuns || runs.length >= 20} onClick={() => void startScan(item)}><Play aria-hidden="true" />{scanningId === item.id ? "启动中" : "全市场扫描"}</button></div>
      </article>)}
    </div> : <EmptyState title="还没有自定义策略" detail="以已登记的日线字段创建可复核策略。" />}

    <div className="mt-5 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
      <label className="space-y-1 text-xs">扫描日期<input className="aq-input aq-input-block" type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} /></label>
      <Badge tone="neutral">最多保留 20 次扫描 · 单次最多展示 500 条命中</Badge>
    </div>
    <div className="mt-4 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">扫描记录</h3><button type="button" className="aq-btn aq-btn-icon" aria-label="刷新扫描记录" title="刷新" onClick={() => void refreshRuns().catch((reason) => setScanError(reason instanceof Error ? reason.message : "扫描任务读取失败"))}><RefreshCw aria-hidden="true" /></button></div>
      {!runs.length ? <p className="aq-muted text-sm">还没有全市场扫描记录。</p> : runs.map((run) => <article className="aq-card" key={run.run_id}>
        <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h4 className="text-sm font-semibold">{run.strategy_id} r{run.strategy_revision} · {run.as_of}</h4><Badge tone={run.status === "completed" ? run.coverage_status === "ready" ? "ok" : "warn" : ["failed", "timed_out", "unknown"].includes(run.status) ? "warn" : "neutral"}>{run.coverage_status === "ready" ? "覆盖完整" : run.coverage_status === "partial" ? "覆盖部分" : run.status === "completed" ? "完成" : run.status === "running" ? "扫描中" : run.status}</Badge></div><p className="aq-muted mt-1 text-xs">{run.processed_symbols.toLocaleString()}/{run.universe_size.toLocaleString()} 个代码 · {run.hit_count.toLocaleString()} 条命中 · 数据截至 {run.market_as_of} · {run.message}</p></div>
          <div className="flex shrink-0 gap-1"><button type="button" className="aq-btn" onClick={() => void openRun(run.run_id)}>查看</button>{["starting", "running", "cancelling"].includes(run.status) ? <button type="button" className="aq-btn aq-btn-danger" onClick={() => void cancelScan(run.run_id)}>取消</button> : <button type="button" className="aq-btn aq-btn-icon" aria-label="删除扫描记录" title="删除" onClick={() => void deleteScan(run.run_id)}><Trash2 aria-hidden="true" /></button>}</div>
        </div>
      </article>)}
    </div>
    {selectedRun ? <section className="mt-4 border-t border-border pt-4" aria-label="扫描结果详情">
      <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-sm font-semibold">{selectedRun.strategy_id} · {selectedRun.as_of}</h3><p className="aq-muted text-xs">策略 hash {selectedRun.strategy_hash}</p></div>{selectedRun.result ? <div className="flex flex-wrap gap-2"><button type="button" className="aq-btn" onClick={() => void exportScan(selectedRun.run_id, "hits")}><Download aria-hidden="true" />命中 CSV</button><button type="button" className="aq-btn" onClick={() => void exportScan(selectedRun.run_id, "coverage")}><Download aria-hidden="true" />覆盖 CSV</button></div> : null}</div>
      {!selectedRun.result ? <p className="aq-muted mt-3 text-sm">{selectedRun.message}</p> : <>
        <div className="mt-3 flex flex-wrap gap-2">{Object.entries(selectedRun.result.coverage_counts).map(([status, count]) => <Badge key={status}>{status} · {count.toLocaleString()}</Badge>)}</div>
        {selectedRun.result.hits.length ? <div className="aq-table-wrap mt-3"><table className="aq-table"><thead><tr><th>代码</th><th>名称</th><th className="aq-num">raw 收盘</th><th>表达式字段</th></tr></thead><tbody>{selectedRun.result.hits.map((hit) => <tr key={hit.symbol}><td>{hit.symbol}</td><td>{hit.name}</td><td className="aq-num">{hit.raw_close.toFixed(2)}</td><td>{Object.entries(hit.expression_fields).map(([field, value]) => `${field}=${Number(value.toFixed(4))}`).join(" · ")}</td></tr>)}</tbody></table></div> : <p className="aq-muted mt-3 text-sm">没有命中标的。</p>}
        {selectedRun.result.hits_truncated ? <p className="aq-note mt-2">命中超过展示上限，CSV 导出包含当前保存的前 500 条；覆盖 CSV 保留全部逐代码状态。</p> : null}
      </>}
    </section> : null}
  </section>;
}

export function StrategyWorkbenchPage() {
  const snapshot = useWorkspaceSnapshot();
  const [catalog, setCatalog] = useState<StrategyCatalogPayload | null>(null);
  const [performance, setPerformance] = useState<PerformancePayload | null>(null);
  const [evidence, setEvidence] = useState<StrategyOosEvidence[]>([]);
  const [loading, setLoading] = useState(true);
  const [performanceLoading, setPerformanceLoading] = useState(true);
  const [evidenceLoading, setEvidenceLoading] = useState(true);
  const [error, setError] = useState("");
  const [performanceError, setPerformanceError] = useState("");
  const [evidenceError, setEvidenceError] = useState("");

  useEffect(() => {
    let current = true;
    api.strategyCatalog().then((result) => {
      if (current) setCatalog(result);
    }).catch((reason) => {
      if (current) setError(reason instanceof Error ? reason.message : "策略目录读取失败");
    }).finally(() => {
      if (current) setLoading(false);
    });
    api.performance().then((result) => {
      if (current) setPerformance(result);
    }).catch((reason) => {
      if (current) setPerformanceError(reason instanceof Error ? reason.message : "正式台账表现读取失败");
    }).finally(() => {
      if (current) setPerformanceLoading(false);
    });
    api.strategyEvidence().then((result) => {
      if (current) setEvidence(result.items);
    }).catch((reason) => {
      if (current) setEvidenceError(reason instanceof Error ? reason.message : "策略 OOS 证据读取失败");
    }).finally(() => {
      if (current) setEvidenceLoading(false);
    });
    return () => { current = false; };
  }, []);

  const matches = useMemo(() => {
    const result = new Map<string, Array<{ symbol: string; display_name: string }>>();
    for (const candidate of snapshot.data?.candidates ?? []) {
      for (const strategy of candidate.strategies ?? []) {
        const rows = result.get(strategy) ?? [];
        rows.push({ symbol: candidate.symbol, display_name: candidate.display_name });
        result.set(strategy, rows);
      }
    }
    return result;
  }, [snapshot.data]);

  return <div className="aq-page">
    <header className="aq-page-head"><div><p className="aq-eyebrow"><ListFilter aria-hidden="true" />AQSP · 策略研究</p><div className="aq-title-row"><h1>策略</h1>{snapshot.data ? <strong>{snapshot.data.selected_date}</strong> : null}</div><p className="aq-page-sub">查看策略假设、现有信号族和当日冻结快照命中；策略运行与验证状态按真实产物呈现。{snapshot.data ? ` 快照生成 ${snapshot.data.generated_at} · 行情源 ${snapshot.data.source.effective || "未标注"} · 数据截至 ${snapshot.data.source.latest_trade_date || "未标注"}` : ""}</p></div></header>
    <CustomStrategyWorkbench />
    <SavedConfigRerunSection />
    {snapshot.error ? <StatePanel tone="warn">研究快照读取失败：{snapshot.error}<button type="button" className="aq-btn" onClick={snapshot.refresh}><RefreshCw aria-hidden="true" />重试</button></StatePanel> : null}
    {error ? <StatePanel tone="warn">{error}<button type="button" className="aq-btn" onClick={() => { setLoading(true); setError(""); api.strategyCatalog().then(setCatalog).catch((reason) => setError(reason instanceof Error ? reason.message : "策略目录读取失败")).finally(() => setLoading(false)); }}><RefreshCw aria-hidden="true" />重试</button></StatePanel> : null}

    <section className="aq-section">
      <SectionHeader title="组合策略 OOS 证据" description="逐次 Walk-forward 研究产物，按当前日期重新核验；不代表晋级或运行状态。" count={evidence.length} />
      {evidenceError ? <StatePanel tone="warn">{evidenceError}</StatePanel> : evidenceLoading && !evidence.length ? <LoadingState label="读取 OOS 证据…" /> : evidence.length ? <div className="aq-strategy-catalog-list">
        {evidence.slice(0, 5).map((item) => <article className="aq-strategy-family" key={item.artifact_id}>
          <header><div><h3>组合策略 · {item.strategy_version}</h3><span className="aq-code">{item.artifact_id.slice(0, 16)}</span></div><div className="aq-tag-row"><Tag tone={item.current_assessment.status === "pass" ? "ok" : "warn"}>{item.current_assessment.status === "pass" ? "证据门通过" : "证据受阻"}</Tag></div></header>
          <p>OOS 区间 {item.data_start} 至 {item.data_end} · {item.effective_symbols.toLocaleString()} 个标的 · 请求源 {item.requested_data_source} · 实际源 {item.actual_data_source || "未记录"} · {item.price_mode}</p>
          <p className="aq-note">运行于 {formatEvidenceTime(item.generated_at)} · 证据年龄 {item.current_assessment.age_days ?? "未知"}/{item.current_assessment.max_age_days} 日 · DSR {item.gate.dsr ?? "—"} · PBO {item.gate.pbo == null ? "—" : `${(item.gate.pbo * 100).toFixed(1)}%`} · 窗口 {item.gate.n_periods ?? "—"}</p>
          {item.current_assessment.blockers.length ? <details><summary>阻断原因 {item.current_assessment.blockers.length}</summary><ul>{item.current_assessment.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul></details> : null}
          <p className="aq-note">参数指纹 {item.parameters_sha256.slice(0, 16)} · 需要人工复核</p>
        </article>)}
      </div> : <EmptyState title="尚无逐版本 OOS 研究产物" detail="正式晋级状态不会由全局 gate 或个人变体复核推断。" />}
    </section>

    <section className="aq-section">
      <SectionHeader title="正式台账验证" description="只读 AQSP 已结算正式信号；不把候选快照或研究变体混作策略实绩。冷启动期间隐藏胜率。" count={performance?.strategies.length ?? 0} />
      {performanceError ? <StatePanel tone="warn">{performanceError}<button type="button" className="aq-btn" onClick={() => { setPerformanceLoading(true); setPerformanceError(""); api.performance().then(setPerformance).catch((reason) => setPerformanceError(reason instanceof Error ? reason.message : "正式台账表现读取失败")).finally(() => setPerformanceLoading(false)); }}><RefreshCw aria-hidden="true" />重试</button></StatePanel> : null}
      {performanceLoading && !performance ? <LoadingState label="读取正式策略表现…" /> : !performance?.available ? <EmptyState title="正式策略台账暂不可用" detail={performance?.reason || "当前没有可用的已结算正式信号。"} /> : <>
        {performance.freshness.stale ? <StatePanel tone="warn">正式台账已停滞：最新信号日 {performance.freshness.latest_signal_date || "未记录"}，最近写入 {performance.freshness.ledger_updated_at || "未记录"}。以下数字是历史记录，不代表当前状态。</StatePanel> : null}
        {performance.cold_start.is_cold_start ? <p className="aq-note">冷启动：当前 {performance.cold_start.independent_signal_days} 个独立信号日，达到 {performance.cold_start.min_independent_signal_days} 个后才展示命中率。</p> : null}
        {performance.strategies.length ? <>
          <div className="aq-table-wrap aq-strategy-performance-desktop"><table className="aq-table"><thead><tr><th>正式策略 ID</th><th className="aq-num">独立信号日</th><th className="aq-num">已结算样本</th><th className="aq-num">命中率</th><th>状态</th></tr></thead><tbody>
          {performance.strategies.map((row) => <tr key={row.name}><td>{row.name}</td><td className="aq-num">{row.independent_signal_days}</td><td className="aq-num">{row.total_picks}</td><td className="aq-num">{strategyHitRateLabel(row, performance.freshness.stale)}</td><td><Tag tone={performance.freshness.stale ? "warn" : row.displayable ? "ok" : "neutral"}>{performance.freshness.stale ? "历史台账已停滞" : row.displayable ? "满足样本门槛" : "积累中"}</Tag></td></tr>)}
          </tbody></table></div>
          <div className="aq-strategy-performance-mobile" aria-label="正式策略台账">
            {performance.strategies.map((row) => <article className="aq-strategy-performance-row" key={row.name}>
              <header><strong>{row.name}</strong><Tag tone={performance.freshness.stale ? "warn" : row.displayable ? "ok" : "neutral"}>{performance.freshness.stale ? "历史台账已停滞" : row.displayable ? "满足样本门槛" : "积累中"}</Tag></header>
              <dl>
                <div><dt>独立信号日</dt><dd>{row.independent_signal_days}</dd></div>
                <div><dt>已结算样本</dt><dd>{row.total_picks}</dd></div>
                <div><dt>命中率</dt><dd>{strategyHitRateLabel(row, performance.freshness.stale)}</dd></div>
              </dl>
            </article>)}
          </div>
        </> : <EmptyState title="台账中没有策略级结算样本" detail="已有候选和研究信号不会代替正式结算记录。" />}
        {performance.decay_alerts.length ? <details className="aq-workspace-fold"><summary>策略表现观察 <span>{performance.decay_alerts.length} 条告警 · 只读，不自动调整</span></summary><div className="aq-workspace-fold-body">{performance.decay_alerts.map((alert) => <p key={alert.strategy}>{alert.strategy} · {alert.severity} · 观察 {alert.lookback_days} 日 · 连续衰减 {alert.decay_days} 日 · {alert.recommendation}</p>)}</div></details> : null}
      </>}
    </section>

    <section className="aq-section">
      <SectionHeader title="当前候选命中" description="只读当前冻结快照，不在浏览器内重新扫描或改变候选顺序。" count={`${snapshot.data?.candidates.length ?? 0} 只候选`} />
      {snapshot.loading && !snapshot.data ? <LoadingState label="读取当前候选…" /> : snapshot.data?.candidates.length ? <div className="aq-table-wrap"><table className="aq-table"><thead><tr><th>策略</th><th className="aq-num">候选数</th><th>命中标的</th></tr></thead><tbody>
        {[...(catalog?.families ?? [])].map((family) => {
          const rows = matches.get(family.id) ?? [];
          return <tr key={family.id}><td>{family.name} <span className="aq-code">{family.id}</span></td><td className="aq-num">{rows.length}</td><td>{rows.length ? rows.map((row) => <Link className="aq-inline-stock" key={row.symbol} to={`/stock/${row.symbol}`}>{row.display_name || row.symbol}</Link>) : "—"}</td></tr>;
        })}
      </tbody></table></div> : !snapshot.loading && !snapshot.data ? null : <EmptyState title="当前快照没有候选" detail="不使用历史候选替代当前扫描结果。" />}
      <div className="aq-row-actions"><Link className="aq-btn aq-btn-primary" to="/today#candidates">打开候选证据</Link><Link className="aq-btn" to="/backtest">查看正式验证结果</Link></div>
    </section>

    <section className="aq-section">
      <SectionHeader title="策略假设与验证门槛" description={catalog?.notes || "来源和验证要求取自仓库策略目录；并不代表策略已获实盘晋级。"} count={catalog ? `目录 ${catalog.version} · ${catalog.families.length} 个策略族` : "读取中"} />
      {loading && !catalog ? <LoadingState label="读取策略目录…" /> : catalog?.families.length ? <div className="aq-strategy-catalog-list">
        {catalog.families.map((family) => {
          const rows = matches.get(family.id) ?? [];
          return <article className="aq-strategy-family" key={family.id}>
            <header><div><h3>{family.name}</h3><span className="aq-code">{family.id}</span></div><div className="aq-tag-row"><Tag tone={family.runtime_ready ? "ok" : "neutral"}>{STATUS_LABELS[family.current_status] || family.current_status}</Tag><Badge tone="neutral">{rows.length} 只候选命中</Badge></div></header>
            <p>{family.hypothesis}</p>
            <details><summary>信号与验证要求</summary><div className="aq-strategy-family-details">
              {family.signals.length ? <div><b>信号组成</b><ul>{family.signals.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
              {family.validation_required.length ? <div><b>验证要求</b><ul>{family.validation_required.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
              {family.runtime_gate.length ? <div><b>运行门槛</b><ul>{family.runtime_gate.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
              {family.absorbed_from.length ? <div><b>来源</b><p>{family.absorbed_from.join(" · ")}</p></div> : null}
            </div></details>
          </article>;
        })}
      </div> : !loading ? <EmptyState title="策略目录不可用" detail="目录缺失或格式无效；不会从候选名称推测策略定义。" /> : null}
    </section>
  </div>;
}
