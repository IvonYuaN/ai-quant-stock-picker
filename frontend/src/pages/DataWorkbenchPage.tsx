import { useCallback, useEffect, useState } from "react";
import { Database, RefreshCw } from "lucide-react";
import { Badge, LoadingState, SectionHeader, StatePanel, Tag } from "@/components/ui/primitives";
import { api, type DailyDataRefreshPayload, type DataCoveragePayload, type DataSourceCapability, type HealthPayload } from "@/lib/api";
import { dataCoverageVerdict } from "@/lib/data-coverage-view";
import { formatEvidenceTime } from "@/lib/format";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { productionGateLabel } from "@/lib/research-workbench-view";
import { snapshotFreshness } from "@/lib/snapshot-freshness";

const WORKLOAD_LABELS = { live_short: "盘中候选", walkforward: "历史验证", pit: "PIT" } as const;
const DAILY_STATUS_LABELS: Readonly<Record<DailyDataRefreshPayload["availability"], string>> = {
  ready: "刷新范围覆盖完整",
  partial: "部分覆盖",
  stale: "数据已陈旧",
  blocked: "覆盖不可核验",
  missing: "本地日线库缺失",
};
const SOURCE_TIER_LABELS: Readonly<Record<string, string>> = {
  free_online: "免费在线",
  local_offline: "本地离线",
  free_package: "免费组件",
  free_login: "免费登录源",
  local_cache: "本地缓存",
  token_api: "Token 接口",
  local_terminal: "本地终端",
};
const SOURCE_ROLE_LABELS: Readonly<Record<string, string>> = {
  primary_online_fallback: "在线主源与降级",
  tdx_server_fallback: "通达信服务器备源",
  light_realtime_fallback: "轻量实时备源",
  eastmoney_field_runtime: "东财补充字段",
  broad_research_data: "广域研究数据",
  primary_history_and_paper: "本地主历史与纸面验证",
  historical_and_financial_candidate: "历史与财务候选源",
  local_structured_cache: "本地结构化缓存",
  pit_calendar_fundamental_future: "PIT 日历与基本面候选源",
  local_warehouse_candidate: "本地数据仓候选源",
  research_api_candidate: "研究接口候选源",
  future_realtime_execution_state: "未来只读实时状态源",
};
const SOURCE_FRESHNESS_LABELS: Readonly<Record<string, string>> = {
  realtime: "实时",
  delayed_realtime: "延迟实时",
  end_of_day: "盘后",
  historical_batch: "历史批量",
  cached: "缓存",
  point_in_time: "PIT",
  terminal_realtime: "终端实时",
};
const SOURCE_COVERAGE_LABELS: Readonly<Record<string, string>> = {
  multi_dimensional: "多维行情",
  quotes_plus: "行情与报价",
  broad_research: "广域研究",
  history_core: "核心历史",
  history_plus: "扩展历史",
  pit_fundamental: "PIT 基本面",
  warehouse: "数据仓库",
  execution_state: "交易状态只读",
};
const AUTH_STATUS_LABELS: Readonly<Record<string, string>> = {
  not_required: "无需鉴权",
  not_checked: "未探测",
  configured: "已配置，未校验",
  missing_env: "缺少配置",
  missing_package: "缺少依赖",
  ok: "校验通过",
  login_failed: "登录失败",
  auth_failed: "鉴权失败",
  unknown: "未知",
};
const FIT_LABELS: Readonly<Record<string, string>> = {
  primary: "主链路",
  fallback: "兜底",
  fallback_only: "仅历史兜底",
  candidate: "候选源",
  supplement: "补充源",
  future_primary: "未来主链路",
  avoid: "不适配",
  unknown: "未知",
};

function localStatusLabel(value: string): string {
  return value === "present" ? "本地数据存在" : value === "missing" ? "缺少本地数据" : value === "not_required" ? "无需本地数据" : "状态未知";
}

function DataSourceCard({ source }: { source: DataSourceCapability }) {
  return <article className="aq-card">
    <header className="aq-card-head"><div><h3>{source.id}</h3><p className="aq-note">{SOURCE_ROLE_LABELS[source.role] || source.role} · {SOURCE_TIER_LABELS[source.tier] || source.tier}</p></div><Badge tone={source.runtime_ready ? "ok" : "neutral"}>{source.runtime_ready ? "已接入" : "未接入"}</Badge></header>
    <p className="aq-note">覆盖 {SOURCE_COVERAGE_LABELS[source.coverage_tier] || source.coverage_tier} · 时效 {SOURCE_FRESHNESS_LABELS[source.freshness_tier] || source.freshness_tier}</p>
    <div className="aq-tag-row">
      <Tag tone={source.supports_daily ? "ok" : "neutral"}>日线 {source.supports_daily ? "支持" : "不支持"}</Tag>
      <Tag tone={source.supports_intraday ? "ok" : "neutral"}>盘中 {source.supports_intraday ? "支持" : "不支持"}</Tag>
      <Tag tone={source.supports_realtime ? "ok" : "neutral"}>实时 {source.supports_realtime ? "支持" : "不支持"}</Tag>
    </div>
    <dl className="aq-research-kv">
      {(Object.entries(source.workload_fit) as Array<[keyof typeof WORKLOAD_LABELS, string]>).map(([workload, fit]) => <div key={workload}><dt>{WORKLOAD_LABELS[workload]}</dt><dd>{FIT_LABELS[fit] || fit}</dd></div>)}
    </dl>
    <p>{source.requires_local_data ? localStatusLabel(source.local_status) : "无需本地数据"} · {AUTH_STATUS_LABELS[source.auth.status] || source.auth.status}：{source.auth.message}</p>
    <p className="aq-note">近期请求：成功 {source.health.successes} / 失败 {source.health.failures} · 最近成功 {source.health.last_success ? formatEvidenceTime(source.health.last_success) : "—"}</p>
    <details><summary>失败与配置</summary><p>{source.failure_mode}</p><p>{source.setup}</p></details>
  </article>;
}

export function DataWorkbenchPage() {
  const snapshot = useWorkspaceSnapshot();
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [sources, setSources] = useState<DataSourceCapability[]>([]);
  const [dailyData, setDailyData] = useState<DailyDataRefreshPayload | null>(null);
  const [coverage, setCoverage] = useState<DataCoveragePayload | null>(null);
  const [coverageError, setCoverageError] = useState("");
  const [loading, setLoading] = useState(true);
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [dailyLoading, setDailyLoading] = useState(true);
  const [dailyStarting, setDailyStarting] = useState(false);
  const [error, setError] = useState("");
  const [sourcesError, setSourcesError] = useState("");
  const [dailyError, setDailyError] = useState("");
  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api.health().then(setHealth)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "数据源健康读取失败"))
      .finally(() => setLoading(false));
  }, []);
  const loadSources = useCallback(() => {
    setSourcesLoading(true);
    setSourcesError("");
    api.dataSources().then((result) => setSources(result.sources))
      .catch((reason) => setSourcesError(reason instanceof Error ? reason.message : "数据源能力读取失败"))
      .finally(() => setSourcesLoading(false));
  }, []);
  const loadDailyData = useCallback(() => {
    api.dailyDataRefresh().then(setDailyData)
      .catch((reason) => setDailyError(reason instanceof Error ? reason.message : "日线覆盖状态读取失败"))
      .finally(() => setDailyLoading(false));
  }, []);
  const loadCoverage = useCallback(() => {
    api.dataCoverage().then((result) => { setCoverage(result); setCoverageError(""); })
      .catch((reason) => setCoverageError(reason instanceof Error ? reason.message : "日线完整性核验失败"));
  }, []);
  const startDailyRefresh = useCallback(async () => {
    setDailyStarting(true);
    setDailyError("");
    try {
      await api.startDailyDataRefresh();
      loadDailyData();
    } catch (reason) {
      setDailyError(reason instanceof Error ? reason.message : "日线刷新未能启动");
    } finally {
      setDailyStarting(false);
    }
  }, [loadDailyData]);
  useEffect(() => { load(); loadSources(); loadDailyData(); loadCoverage(); }, [load, loadSources, loadDailyData, loadCoverage]);
  const refreshRunning = ["starting", "running"].includes(dailyData?.job.status || "");
  useEffect(() => {
    if (!refreshRunning) return undefined;
    const timer = window.setInterval(loadDailyData, 2000);
    return () => window.clearInterval(timer);
  }, [loadDailyData, refreshRunning]);
  const source = snapshot.data?.source;
  const freshness = snapshot.data ? snapshotFreshness(snapshot.data) : "unknown";
  const freshnessLabel = freshness === "fresh" ? "研究快照在时效内" : freshness === "stale" ? "研究快照已过期或为历史记录" : "研究快照时效未知";
  const coverageVerdict = dataCoverageVerdict(coverage, coverageError);
  return <div className="aq-page">
    <header className="aq-page-head"><div><p className="aq-eyebrow"><Database aria-hidden="true" />AQSP · 数据与能力</p><div className="aq-title-row"><h1>数据</h1><Badge tone={freshness === "fresh" ? "ok" : "warn"}>{freshnessLabel}</Badge></div><p className="aq-page-sub">展示 AQSP 实际研究快照、数据源能力、就绪状态和来源时点。</p></div><button type="button" className="aq-btn" disabled={loading || sourcesLoading} onClick={() => { load(); loadSources(); loadCoverage(); }}><RefreshCw aria-hidden="true" />刷新</button></header>
    <section className="aq-section">
      <SectionHeader title="日线完整性" description="库内最大日期不等于完整市场日。只有最新交易日覆盖足够比例的标的，才允许被依赖市场日线的功能使用。" />
      <StatePanel tone={coverageVerdict.tone}><b>{coverageVerdict.title}</b><p className="aq-note">{coverageVerdict.detail}</p>{coverage?.available ? <div className="aq-total-grid"><div className="aq-total-card"><span>库内最大日期</span><b>{coverage.market_max_date ?? "—"}</b></div><div className="aq-total-card"><span>该日覆盖标的</span><b>{coverage.symbols_on_max_date ?? 0} / {coverage.total_symbols ?? 0}</b></div><div className="aq-total-card"><span>覆盖率</span><b>{((coverage.coverage_pct ?? 0) * 100).toFixed(2)}%</b></div><div className="aq-total-card"><span>可作市场日</span><b>{coverage.verified_market_date ?? "否（已拦截）"}</b></div></div> : null}</StatePanel>
    </section>
    <section className="aq-section"><SectionHeader title="研究快照" description="今日确定性选股流水线的实际输出，不由本页重新计算。" />{snapshot.error ? <StatePanel tone="warn">研究快照暂不可用；当前不以估算数据替代。</StatePanel> : snapshot.loading && !snapshot.data ? <LoadingState label="读取研究快照…" /> : snapshot.data ? <div className="aq-total-grid"><div className="aq-total-card"><span>运行状态</span><b>{source?.status || "未知"}</b></div><div className="aq-total-card"><span>最近交易日</span><b>{source?.latest_trade_date || "—"}</b></div><div className="aq-total-card"><span>数据延迟</span><b>{source?.lag_days ?? "—"} 日</b></div><div className="aq-total-card"><span>快照更新时间</span><b>{formatEvidenceTime(snapshot.data.generated_at)}</b></div></div> : null}</section>
    <section className="aq-section">
      <SectionHeader title="可刷新标的日线覆盖" description="按当前刷新器支持的主板、科创板和创业板活跃标的逐只核验；北交所暂未接入。数据库最大日期不代表该范围已覆盖。" />
      <div className="aq-row-actions">
        <Badge tone={dailyData?.availability === "ready" ? "ok" : "warn"}>
          {dailyData ? DAILY_STATUS_LABELS[dailyData.availability] : "读取中"}
        </Badge>
        <button
          type="button"
          className="aq-btn aq-btn-primary"
          onClick={() => void startDailyRefresh()}
          disabled={dailyStarting || refreshRunning || dailyData?.availability === "missing"}
        >
          <RefreshCw aria-hidden="true" />
          {refreshRunning ? "刷新运行中…" : dailyStarting ? "启动中…" : "刷新目标日数据"}
        </button>
        <button type="button" className="aq-btn" onClick={loadDailyData} disabled={dailyLoading}>
          <RefreshCw aria-hidden="true" />核验覆盖
        </button>
      </div>
      {dailyError ? <StatePanel tone="warn">{dailyError}</StatePanel> : null}
      {dailyLoading && !dailyData ? <LoadingState label="读取 SQLite 覆盖…" /> : null}
      {dailyData ? <>
        <div className="aq-total-grid">
          <div className="aq-total-card"><span>最新日线日期</span><b>{dailyData.latest_as_of || "—"}</b></div>
          <div className="aq-total-card"><span>目标交易日</span><b>{dailyData.expected_as_of}</b></div>
          <div className="aq-total-card"><span>活跃标的覆盖</span><b>{dailyData.covered_symbols}/{dailyData.eligible_symbols}</b></div>
          <div className="aq-total-card"><span>目标日覆盖率</span><b>{(dailyData.coverage_ratio * 100).toFixed(1)}%</b></div>
        </div>
        {dailyData.job.status ? <p className="aq-note">
          刷新任务 {dailyData.job.status} · 目标日 {dailyData.job.target_day || "—"} · 最近更新 {formatEvidenceTime(dailyData.job.updated_at || dailyData.job.started_at || "")}
          {dailyData.job.eligible_covered_count != null ? ` · 游标已覆盖 ${dailyData.job.eligible_covered_count}/${dailyData.cursor.universe_size || dailyData.eligible_symbols}` : ""}
          {dailyData.job.error ? ` · ${dailyData.job.error}` : ""}
          {dailyData.job.message ? ` · ${dailyData.job.message}` : ""}
        </p> : <p className="aq-note">尚无通过本工作台启动的刷新任务；目标日覆盖按 SQLite 实际数据计算。</p>}
        {dailyData.availability !== "ready" ? <StatePanel tone="warn">
          目标日数据仍未覆盖全部受支持活跃标的。异动、监控和研究工作流必须保留部分/陈旧状态，不能把缺数据解释为“无信号”。
        </StatePanel> : null}
      </> : null}
    </section>
    <section className="aq-section"><SectionHeader title="生产 walk-forward gate" description="只读全局生产回测产物；新鲜度与双门字段经服务端校验。它不对应某条研究变体，也不会自动启用策略。" />{loading && !health ? <LoadingState label="读取生产 gate…" /> : error ? <StatePanel tone="warn">{error}</StatePanel> : <>
      <div className="aq-total-grid"><div className="aq-total-card"><span>最后判定</span><b>{productionGateLabel(health?.gate)}</b></div><div className="aq-total-card"><span>证据新鲜度</span><b>{health?.gate?.freshness === "fresh" ? `新鲜 · ${health.gate.age_days ?? "—"}/${health.gate.max_age_days ?? "—"} 日` : health?.gate?.freshness === "stale" ? `过期 · ${health.gate.age_days ?? "—"} 日` : health?.gate?.freshness === "unverified" ? "日期未核实" : health?.gate?.freshness === "missing" ? "缺失" : "未核实"}</b></div><div className="aq-total-card"><span>回测区间右端</span><b>{health?.gate?.generated_at || "—"}</b></div><div className="aq-total-card"><span>产物更新时间</span><b>{formatEvidenceTime(health?.gate?.updated || "")}</b></div><div className="aq-total-card"><span>DSR / PBO</span><b>{health?.gate?.dsr_pass == null || health?.gate?.pbo_pass == null ? "—" : `${health.gate.dsr_pass ? "通过" : "未通过"} / ${health.gate.pbo_pass ? "通过" : "未通过"}`}</b></div></div>
      {health?.gate?.deflated_sharpe != null || health?.gate?.pbo != null || health?.gate?.n_variants != null ? <p className="aq-note">产物指标：DSR {health.gate.deflated_sharpe ?? "—"} · PBO {health.gate.pbo ?? "—"} · 变体数 {health.gate.n_variants ?? "—"}</p> : null}
      {health?.gate?.artifact_id ? <p className="aq-note">证据指纹：{health.gate.artifact_id.slice(0, 16)} · 只标识全局 gate 文件，不是变体证据。</p> : null}
      {health?.gate?.validation_blockers?.length ? <StatePanel tone="warn">当前 gate 不能作为通过证据：{health.gate.validation_blockers.join("；")}</StatePanel> : null}
    </>}</section>
    <section className="aq-section"><SectionHeader title="数据源健康" description="进程内近期请求观测；健康状态不代替单项数据的来源时间与覆盖验证。" count={health?.sources.length ?? 0} />{loading && !health ? <LoadingState label="读取数据源状态…" /> : error ? <StatePanel tone="warn">{error}<button type="button" className="aq-btn" onClick={load}>重试</button></StatePanel> : health?.sources.length ? <div className="aq-table-wrap"><table className="aq-table"><thead><tr><th>数据源</th><th>状态</th><th className="aq-num">成功</th><th className="aq-num">失败</th><th>最近成功</th><th>最近错误</th><th>错误摘要</th></tr></thead><tbody>{health.sources.map((row) => <tr key={row.source}><td><b>{row.source}</b></td><td><Tag tone={row.healthy ? "ok" : "warn"}>{row.healthy ? "近期可用" : "近期异常"}</Tag></td><td className="aq-num">{row.ok_count}</td><td className="aq-num">{row.error_count}</td><td>{row.last_ok_at ? formatEvidenceTime(row.last_ok_at) : "—"}</td><td>{row.last_error_at ? formatEvidenceTime(row.last_error_at) : "—"}</td><td>{row.last_error || "—"}</td></tr>)}</tbody></table></div> : <StatePanel>当前进程没有可报告的请求历史。源健康记录只说明最近请求情况，不代表行情数据新鲜。</StatePanel>}</section>
    <section className="aq-section"><SectionHeader title="数据源能力与就绪度" description="本机配置与静态工作负载适配；不代表本次请求实际选中了该来源。" count={sources.length ? `${sources.length} 个来源` : undefined} />{sourcesLoading && !sources.length ? <LoadingState label="读取来源目录…" /> : sourcesError ? <StatePanel tone="warn">{sourcesError}<button type="button" className="aq-btn" onClick={loadSources}>重试</button></StatePanel> : sources.length ? <div className="aq-card-grid">{sources.map((source) => <DataSourceCard key={source.id} source={source} />)}</div> : <StatePanel>当前没有可列出的数据源。</StatePanel>}</section>
  </div>;
}
