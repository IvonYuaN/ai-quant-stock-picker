import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Compass, RefreshCw, Ruler, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { EmptyState, LoadingState, SectionHeader, StatePanel } from "@/components/ui/primitives";
import { api, type AbnormalDeviationPayload, type AbnormalDeviationStatus, type AbnormalPoolRow, type AuctionBenchmarkPayload, type HistoricalAbnormalSignalKey, type HistoricalAbnormalSignalRow, type HistoricalAbnormalSignals, type ShortTermEmotion } from "@/lib/api";
import { changeClass, formatEvidenceTime, formatRate, formatSignedPct } from "@/lib/format";

type AbnormalTab = "auction" | "intraday" | "deviation";
type PoolKind = "limit_up" | "blown_up" | "limit_down";
type IntradayMode = "public_pools" | "local_history";

const TABS: Array<{ key: AbnormalTab; label: string; icon: typeof Activity }> = [
  { key: "auction", label: "竞价异动", icon: Compass },
  { key: "intraday", label: "盘中异动", icon: Activity },
  { key: "deviation", label: "偏移异动", icon: Ruler },
];

const POOLS: Array<{ key: PoolKind; label: string }> = [
  { key: "limit_up", label: "涨停" },
  { key: "blown_up", label: "炸板" },
  { key: "limit_down", label: "跌停" },
];

export function AbnormalMovesPage() {
  const [tab, setTab] = useState<AbnormalTab>("intraday");
  const [poolKind, setPoolKind] = useState<PoolKind>("limit_up");
  const [intradayMode, setIntradayMode] = useState<IntradayMode>("public_pools");
  const [query, setQuery] = useState("");
  const [visibleLimit, setVisibleLimit] = useState(20);
  const [emotion, setEmotion] = useState<ShortTermEmotion | null>(null);
  const [auction, setAuction] = useState<AuctionBenchmarkPayload | null>(null);
  const [auctionLoading, setAuctionLoading] = useState(false);
  const [auctionError, setAuctionError] = useState("");
  const [auctionQuery, setAuctionQuery] = useState("");
  const [auctionVisibleLimit, setAuctionVisibleLimit] = useState(20);
  const [deviation, setDeviation] = useState<AbnormalDeviationPayload | null>(null);
  const [deviationLoading, setDeviationLoading] = useState(false);
  const [deviationError, setDeviationError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    setRefreshing(true);
    setError("");
    api.emotion()
      .then(setEmotion)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "当日异动池读取失败"))
      .finally(() => {
        setLoading(false);
        setRefreshing(false);
      });
  }, []);

  const reloadAuction = useCallback(() => {
    setAuctionLoading(true);
    setAuctionError("");
    api.auctionBenchmark()
      .then(setAuction)
      .catch((reason: unknown) => setAuctionError(reason instanceof Error ? reason.message : "竞价名单读取失败"))
      .finally(() => setAuctionLoading(false));
  }, []);

  const reloadDeviation = useCallback(() => {
    setDeviationLoading(true);
    setDeviationError("");
    api.abnormalDeviation()
      .then(setDeviation)
      .catch((reason: unknown) => setDeviationError(reason instanceof Error ? reason.message : "多日偏移数据读取失败"))
      .finally(() => setDeviationLoading(false));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const pools = emotion?.abnormal_pools;
  const pool = pools?.[poolKind];
  const rows = useMemo(() => {
    const search = query.trim().toLowerCase();
    return (pool?.items ?? []).filter((row) =>
      !search || `${row.code} ${row.name}`.toLowerCase().includes(search),
    );
  }, [pool, query]);
  const visibleRows = rows.slice(0, visibleLimit);

  return <div className="aq-page aq-abnormal-page">
    <header className="aq-page-head">
      <div>
        <p className="aq-eyebrow"><Activity aria-hidden="true" />AQSP · 市场异动</p>
        <h1>异动监控</h1>
        <p className="aq-page-sub">按竞价、盘中和多日偏移查看异动；只展示有来源的行情事实。</p>
      </div>
      {tab === "intraday" ? <button type="button" className="aq-btn" onClick={reload} disabled={refreshing} title="刷新当日异动池"><RefreshCw aria-hidden="true" />刷新</button> : null}
      {tab === "auction" ? <button type="button" className="aq-btn" onClick={reloadAuction} disabled={auctionLoading} title="刷新竞价名单"><RefreshCw aria-hidden="true" />刷新</button> : null}
      {tab === "deviation" ? <button type="button" className="aq-btn" onClick={reloadDeviation} disabled={deviationLoading} title="重新计算多日偏移"><RefreshCw aria-hidden="true" />刷新</button> : null}
    </header>

    <div className="aq-abnormal-tabs" role="tablist" aria-label="异动时间范围">
      {TABS.map(({ key, label, icon: Icon }) => <button
        key={key}
        type="button"
        role="tab"
        aria-selected={tab === key}
        className={tab === key ? "aq-abnormal-tab is-active" : "aq-abnormal-tab"}
        onClick={() => {
          setTab(key);
          if (key === "auction" && !auction && !auctionLoading) reloadAuction();
          if (key === "deviation" && !deviation && !deviationLoading) reloadDeviation();
        }}
      ><Icon aria-hidden="true" />{label}</button>)}
    </div>

    {tab === "auction" ? <AuctionView
      payload={auction}
      loading={auctionLoading}
      error={auctionError}
      query={auctionQuery}
      visibleLimit={auctionVisibleLimit}
      onQueryChange={(value) => { setAuctionQuery(value); setAuctionVisibleLimit(20); }}
      onRetry={reloadAuction}
      onShowMore={() => setAuctionVisibleLimit((count) => count + 20)}
    /> : null}
    {tab === "deviation" ? <DeviationView
      payload={deviation}
      loading={deviationLoading}
      error={deviationError}
      onRetry={reloadDeviation}
    /> : null}
      {tab === "intraday" ? <>
      <div className="aq-abnormal-subtabs" role="group" aria-label="盘中数据视图">
        <button type="button" aria-pressed={intradayMode === "public_pools"} className={intradayMode === "public_pools" ? "is-active" : ""} onClick={() => setIntradayMode("public_pools")}>当日公开池</button>
        <button type="button" aria-pressed={intradayMode === "local_history"} className={intradayMode === "local_history" ? "is-active" : ""} onClick={() => setIntradayMode("local_history")}>本地日线历史信号</button>
      </div>
      {intradayMode === "public_pools" ? <section className="aq-section">
      <SectionHeader title="当日公开异动池" description="东财涨停、炸板、跌停池；不把榜单转换成推荐或交易信号。" />
      {loading && !emotion ? <LoadingState label="读取当日异动池…" /> : error ? <StatePanel tone="warn">当日异动池不可用：{error}<button type="button" className="aq-btn" onClick={reload} disabled={refreshing}>重试</button></StatePanel> : !pools ? <EmptyState title="异动池未返回" detail="市场情绪接口没有提供可核验的当日池数据；不能据此判断为零记录。" /> : <>
        <div className="aq-abnormal-toolbar">
          <div className="aq-abnormal-pool-tabs" role="group" aria-label="异动池类型">
            {POOLS.map(({ key, label }) => <button key={key} type="button" aria-pressed={poolKind === key} className={poolKind === key ? "aq-abnormal-pool-tab is-active" : "aq-abnormal-pool-tab"} onClick={() => { setPoolKind(key); setQuery(""); setVisibleLimit(20); }}>{label}<span>{pools[key].count}</span></button>)}
          </div>
          <label className="aq-abnormal-search"><Search aria-hidden="true" /><input value={query} onChange={(event) => { setQuery(event.target.value); setVisibleLimit(20); }} placeholder="搜索代码或名称" aria-label="搜索代码或名称" /></label>
        </div>
        <p className="aq-note">数据交易日 {emotion?.date || "未标注"} · {query ? `匹配 ${rows.length} / ${pool?.count ?? 0} 只` : `${pool?.count ?? 0} 只`}{pool?.truncated ? ` · 数据源最多展示 ${pool.displayed_count} 条` : ""}{refreshing ? " · 更新中" : ""}</p>
        {pool?.items.length ? <div className="aq-abnormal-list">
          {rows.length ? visibleRows.map((row) => <AbnormalStockRow key={row.code} row={row} poolKind={poolKind} />) : <EmptyState title="没有匹配的标的" detail="更换代码或名称后重试。" />}
        </div> : <EmptyState title={`当日${POOLS.find((item) => item.key === poolKind)?.label}池为空`} detail="数据源本次返回成功，但没有该类记录。" />}
        {rows.length > visibleRows.length ? <button type="button" className="aq-btn" onClick={() => setVisibleLimit((count) => count + 20)}>显示更多 · {visibleRows.length}/{rows.length}</button> : null}
      </>}
    </section> : <HistoricalDailySignals />}
      </> : null}
  </div>;
}

const HISTORICAL_SIGNAL_LABELS: Record<HistoricalAbnormalSignalKey, string> = {
  new_high: "60 日收盘新高",
  new_low: "60 日收盘新低",
  volume_surge: "量比达到观察阈值",
};

function HistoricalDailySignals() {
  const [data, setData] = useState<HistoricalAbnormalSignals | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [signalFilter, setSignalFilter] = useState<HistoricalAbnormalSignalKey | "all">("all");
  const [query, setQuery] = useState("");
  const [visibleLimit, setVisibleLimit] = useState(20);

  async function load() {
    setLoading(true);
    setError("");
    try {
      setData(await api.abnormalHistoricalSignals());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "本地日线历史信号读取失败");
    } finally {
      setLoading(false);
    }
  }

  const rows = useMemo(() => {
    const search = query.trim().toLowerCase();
    return (data?.rows ?? []).filter((row) =>
      (signalFilter === "all" || row.signals.includes(signalFilter))
      && (!search || `${row.symbol} ${row.name}`.toLowerCase().includes(search)),
    );
  }, [data, query, signalFilter]);
  const visibleRows = rows.slice(0, visibleLimit);

  return <section className="aq-section">
    <SectionHeader title="本地日线历史信号" description="读取 raw OHLCV；新高/新低仅在 PIT 因子覆盖完整时按同一价基比较。结果与实时公开池分开，不进入评分或成交台账。" />
    {!data && !loading && !error ? <StatePanel tone="neutral">按需读取本机日线。结果会标注实际数据日；数据滞后时仅作为历史复核，不代表当前盘中状态。<button type="button" className="aq-btn" onClick={() => void load()}>读取本地历史信号</button></StatePanel> : null}
    {loading ? <LoadingState label="扫描本地 raw 日线…" /> : null}
    {error ? <StatePanel tone="warn">{error}<button type="button" className="aq-btn" onClick={() => void load()}>重试</button></StatePanel> : null}
    {data ? <>
      <StatePanel
        tone={data.freshness === "fresh" ? "neutral" : "warn"}
        action={<button type="button" className="aq-btn" onClick={() => void load()} disabled={loading}>刷新扫描</button>}
      >
        <span className="aq-abnormal-freshness">
          {data.freshness === "fresh" ? "本地数据截至最近已完成交易日" : `历史数据已滞后：数据库最新交易日 ${data.as_of}，预期最近完成日 ${data.expected_as_of}，相差 ${data.lag_calendar_days} 个自然日`}
        </span>
        <span className="aq-abnormal-source">来源 {data.source} · raw OHLCV + PIT 因子统一至数据日 · 生成于 {formatEvidenceTime(data.generated_at)}</span>
      </StatePanel>
      <div className="aq-total-grid aq-abnormal-historical-stats">
        <div className="aq-total-card"><span>数据截至</span><b>{data.as_of}</b></div>
        <div className="aq-total-card"><span>符合源日期</span><b>{data.eligible_symbols}/{data.requested_symbols}</b></div>
        <div className="aq-total-card"><span>PIT 因子覆盖</span><b>{data.factor_covered_symbols}/{data.eligible_symbols}</b></div>
        <div className="aq-total-card"><span>60 日新高 / 新低</span><b>{data.counts.new_high} / {data.counts.new_low}</b></div>
        <div className="aq-total-card"><span>放量观察</span><b>{data.counts.volume_surge} · ≥{data.volume_threshold_ratio.toFixed(2)}×</b></div>
      </div>
      {data.skipped_stale_symbols ? <p className="aq-note">另有 {data.skipped_stale_symbols} 个标的最新日线早于数据库最新日期，已跳过。</p> : null}
      {data.skipped_missing_factor_symbols ? <p className="aq-note">另有 {data.skipped_missing_factor_symbols} 个标的缺少完整 PIT 因子；这些标的不计新高/新低，只保留独立可算的量能观察。</p> : null}
      <div className="aq-abnormal-toolbar">
        <div className="aq-abnormal-pool-tabs" role="group" aria-label="历史信号类型">
          {(["all", "new_high", "new_low", "volume_surge"] as const).map((key) => <button key={key} type="button" aria-pressed={signalFilter === key} className={signalFilter === key ? "aq-abnormal-pool-tab is-active" : "aq-abnormal-pool-tab"} onClick={() => { setSignalFilter(key); setVisibleLimit(20); }}>{key === "all" ? "全部" : HISTORICAL_SIGNAL_LABELS[key]}<span>{key === "all" ? data.rows.length : data.counts[key]}</span></button>)}
        </div>
        <label className="aq-abnormal-search"><Search aria-hidden="true" /><input value={query} onChange={(event) => { setQuery(event.target.value); setVisibleLimit(20); }} placeholder="搜索代码或名称" aria-label="搜索历史信号代码或名称" /></label>
      </div>
      <p className="aq-note">历史信号截至 {data.as_of} · 当前筛选 {rows.length} 项</p>
      {visibleRows.length ? <div className="aq-abnormal-list">{visibleRows.map((row) => <HistoricalSignalRow key={row.symbol} row={row} />)}</div> : <EmptyState title="当前数据日没有匹配信号" detail="此结果是历史扫描，不代表当前盘中状态。" />}
      {rows.length > visibleRows.length ? <button type="button" className="aq-btn" onClick={() => setVisibleLimit((count) => count + 20)}>显示更多 · {visibleRows.length}/{rows.length}</button> : null}
    </> : null}
  </section>;
}

function HistoricalSignalRow({ row }: { row: HistoricalAbnormalSignalRow }) {
  return <article className="aq-abnormal-row aq-abnormal-historical-row">
    <Link className="aq-abnormal-stock" to={`/stock/${encodeURIComponent(row.symbol)}?date=${encodeURIComponent(row.as_of)}`} title={`按 ${row.as_of} 查看 ${row.name} 研究页`}>
      <strong>{row.name}</strong><code>{row.symbol} · {row.as_of}</code>
    </Link>
    <div className="aq-abnormal-signal-labels">{row.signals.map((signal) => <span key={signal}>{HISTORICAL_SIGNAL_LABELS[signal]}</span>)}</div>
    <span className={`aq-abnormal-change${row.change_pct == null ? "" : ` ${changeClass(row.change_pct)}`}`}>{row.change_pct == null ? "—" : formatSignedPct(row.change_pct)}</span>
    <span className="aq-abnormal-value">量比 {row.volume_ratio_5d == null ? "—" : `${row.volume_ratio_5d.toFixed(2)}×`}</span>
  </article>;
}

function AbnormalStockRow({ row, poolKind }: { row: AbnormalPoolRow; poolKind: PoolKind }) {
  return <article className="aq-abnormal-row">
    <Link className="aq-abnormal-stock" to={`/stock/${encodeURIComponent(row.code)}`} title={`查看${row.name}研究页`}>
      <strong>{row.name}</strong><code>{row.code}</code>
    </Link>
    <span className={`aq-abnormal-change${row.change_pct == null ? "" : ` ${changeClass(row.change_pct)}`}`}>{row.change_pct == null ? "—" : formatSignedPct(row.change_pct)}</span>
    <span className="aq-abnormal-value">{row.price == null ? "—" : row.price.toFixed(2)}</span>
    <span className="aq-abnormal-extra">{poolKind === "limit_up" ? row.boards == null ? "—" : `${row.boards} 板` : poolKind === "blown_up" ? row.blow_count ?? "—" : row.industry || "—"}</span>
  </article>;
}

function AuctionView({
  payload,
  loading,
  error,
  query,
  visibleLimit,
  onQueryChange,
  onRetry,
  onShowMore,
}: {
  payload: AuctionBenchmarkPayload | null;
  loading: boolean;
  error: string;
  query: string;
  visibleLimit: number;
  onQueryChange: (value: string) => void;
  onRetry: () => void;
  onShowMore: () => void;
}) {
  const rows = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (payload?.items ?? []).filter((row) =>
      !normalized || `${row.symbol} ${row.name} ${row.tags.join(" ")}`.toLowerCase().includes(normalized),
    );
  }, [payload, query]);

  return <section className="aq-section">
    <SectionHeader title="盘前风向标" description="同花顺竞价筛选名单；只作当日观察，不改量化评分。" />
    {loading ? <LoadingState label="读取竞价名单…" /> : null}
    {error ? <StatePanel tone="warn">竞价名单读取失败：{error}<button type="button" className="aq-btn" onClick={onRetry}>重试</button></StatePanel> : null}
    {payload?.state === "source_unavailable" ? <StatePanel tone="neutral">
      {payload.message}。配置后可读取名单；未连接数据源时不生成替代榜单。
      <Link className="aq-btn" to="/data">查看数据源状态</Link>
    </StatePanel> : null}
    {payload?.state === "no_data" ? <StatePanel tone="warn">{payload.message || "竞价名单暂不可用"}<button type="button" className="aq-btn" onClick={onRetry}>重试</button></StatePanel> : null}
    {payload?.state === "ok" ? <>
      <div className="aq-abnormal-toolbar aq-auction-toolbar">
        <p className="aq-note">{payload.trade_date || "交易日未标注"} · {payload.count} 只 · 来源 {payload.source}</p>
        <label className="aq-abnormal-search"><Search aria-hidden="true" /><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索代码、名称或标签" aria-label="搜索竞价代码、名称或标签" /></label>
      </div>
      <div className="aq-auction-table" aria-label="盘前风向标名单">
        <div className="aq-auction-head" aria-hidden="true"><span>股票</span><span>竞价幅度</span><span>当日</span><span>次日</span><span>标签</span></div>
        {rows.length ? rows.slice(0, visibleLimit).map((row) => <article className="aq-auction-row" key={`${payload.trade_date}-${row.symbol}`}>
          <Link className="aq-abnormal-stock" to={`/stock/${encodeURIComponent(row.symbol)}`} title={`查看 ${row.name} 研究页`}>
            <strong>{row.name}</strong><code>{row.symbol}</code>
          </Link>
          <span className={`aq-auction-change${row.auction_pct == null ? "" : ` ${changeClass(row.auction_pct)}`}`}>
            <span className="aq-auction-mobile-label">竞价</span>{row.auction_pct == null ? "—" : formatSignedPct(row.auction_pct)}
          </span>
          <span className="aq-auction-return aq-auction-day0" title="当日开盘至收盘，raw OHLCV 同日比值">
            <span className="aq-auction-mobile-label">当日</span>{formatRate(row.day0_oc)}
          </span>
          <span className="aq-auction-return aq-auction-d1" title="次日收盘相对竞价日收盘；需两日 PIT 因子齐全">
            <span className="aq-auction-mobile-label">次日</span>{formatRate(row.d1_pct)}
          </span>
          <span className="aq-auction-tags">{row.tags.length ? row.tags.slice(0, 3).join(" · ") : "—"}</span>
        </article>) : <EmptyState title="本期没有匹配名单" detail={query ? "更换代码、名称或标签后重试。" : "数据源返回成功，但名单为空。"} />}
      </div>
      {rows.length > visibleLimit ? <button type="button" className="aq-btn" onClick={onShowMore}>显示更多 · {visibleLimit}/{rows.length}</button> : null}
      <p className="aq-note">名单来源 Fuyao / 同花顺。收益来源 {payload.returns_source || "未匹配本机日线"} · {payload.returns_price_mode || "raw OHLCV 不可用"}{payload.returns_as_of ? ` · 数据截至 ${payload.returns_as_of}` : ""}。次日收益需两日 PIT 因子齐全；该名单仅作当日观察，不构成买卖信号。</p>
    </> : null}
  </section>;
}

const DEVIATION_STATUS_LABELS: Record<AbnormalDeviationStatus, string> = {
  triggered: "已触发",
  edge: "异动边缘",
  watch: "观察",
};

const DEVIATION_BOARD_LABELS: Record<AbnormalDeviationPayload["rows"][number]["board"], string> = {
  SH: "沪主板",
  STAR: "科创板",
  SZ: "深主板",
  GEM: "创业板",
  BJ: "北交所",
};

function DeviationView({
  payload,
  loading,
  error,
  onRetry,
}: {
  payload: AbnormalDeviationPayload | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
}) {
  const [statusFilter, setStatusFilter] = useState<AbnormalDeviationStatus | "all">("all");
  const [query, setQuery] = useState("");
  const rows = useMemo(() => {
    const search = query.trim().toLowerCase();
    return (payload?.rows ?? []).filter((row) =>
      (statusFilter === "all" || row.status === statusFilter)
      && (!search || `${row.symbol} ${row.name} ${row.benchmark_symbol}`.toLowerCase().includes(search)),
    );
  }, [payload, query, statusFilter]);

  return <section className="aq-section">
    <SectionHeader title="多日偏移" description="个股相对板块指数的 3、10、30 日累计涨跌幅偏离；仅展示有完整 raw/PIT 依据的窗口。" />
    {loading ? <LoadingState label="读取 raw 日线、指数和 PIT 因子…" /> : null}
    {error ? <StatePanel tone="warn">多日偏移读取失败：{error}<button type="button" className="aq-btn" onClick={onRetry}>重试</button></StatePanel> : null}
    {payload ? <>
      <StatePanel tone={payload.freshness === "fresh" && payload.availability === "ready" ? "neutral" : "warn"}>
        <span className="aq-abnormal-freshness">
          {payload.freshness === "fresh" ? "本地日线截至最近已完成交易日" : `历史数据已滞后：${payload.as_of || "无数据日"}，预期最近完成日 ${payload.expected_as_of}`}
        </span>
        <span className="aq-abnormal-source">来源 sqlite_db · raw 收盘 + PIT 因子 · 截面基准按优先级选择并标记回退 · 生成于 {formatEvidenceTime(payload.generated_at)}</span>
      </StatePanel>
      {payload.availability === "blocked" ? <StatePanel tone="warn">
        偏移计算已阻断：{payload.blocking_reasons.map(deviationReasonLabel).join("；") || "必要数据覆盖不足"}。覆盖不足不代表当前没有异动。
      </StatePanel> : null}
      {payload.availability === "partial" ? <StatePanel tone="warn">
        当前只有部分市场数据可计算（最新日线 {payload.current_bar_symbols}/{payload.requested_symbols} 只）。下方样本可供核查，不能据此判断全市场异动情况。
      </StatePanel> : null}
      <div className="aq-total-grid aq-deviation-stats">
        <div className="aq-total-card"><span>数据截至</span><b>{payload.as_of || "—"}</b></div>
        <div className="aq-total-card"><span>最新日线标的</span><b>{payload.current_bar_symbols}/{payload.requested_symbols}</b></div>
        <div className="aq-total-card"><span>3 日可计算</span><b>{payload.window_coverage["3d"].computed}</b></div>
        <div className="aq-total-card"><span>10 日可计算</span><b>{payload.window_coverage["10d"].computed}</b></div>
        <div className="aq-total-card"><span>30 日可计算</span><b>{payload.window_coverage["30d"].computed}</b></div>
      </div>
      <div className="aq-deviation-benchmarks" aria-label="指数基准覆盖">
        {Object.entries(DEVIATION_BOARD_LABELS).map(([board, label]) => {
          const selected = payload.benchmark_selection[board as keyof typeof DEVIATION_BOARD_LABELS];
          return <span key={board}>{label} · {selected ? `${selected.symbol}${selected.fallback ? "（回退）" : ""}` : "指数缺失"}</span>;
        })}
      </div>
      <div className="aq-abnormal-toolbar">
        <div className="aq-abnormal-pool-tabs aq-deviation-pool-tabs" role="group" aria-label="偏移状态">
          {(["all", "triggered", "edge", "watch"] as const).map((status) => <button
            key={status}
            type="button"
            aria-pressed={statusFilter === status}
            className={statusFilter === status ? "aq-abnormal-pool-tab is-active" : "aq-abnormal-pool-tab"}
            onClick={() => setStatusFilter(status)}
          >{status === "all" ? "全部" : DEVIATION_STATUS_LABELS[status]}<span>{status === "all" ? payload.candidate_count : payload.counts[status]}</span></button>)}
        </div>
        <label className="aq-abnormal-search"><Search aria-hidden="true" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索代码、名称或基准" aria-label="搜索偏移异动代码、名称或基准" /></label>
      </div>
      <p className="aq-note">窗口覆盖（raw 配对 / PIT 配对 / 指数配对 / 成功计算）：3 日 {coverageLabel(payload.window_coverage["3d"])} · 10 日 {coverageLabel(payload.window_coverage["10d"])} · 30 日 {coverageLabel(payload.window_coverage["30d"])}。阈值来源 TickFlow 规则集；接近度 ≥100% 已触及阈值，≥70% 边缘，≥50% 观察，不构成监管认定或交易信号。</p>
      {rows.length ? <div className="aq-deviation-table">
        <div className="aq-deviation-head" aria-hidden="true"><span>股票 / 基准</span><span>3 日</span><span>10 日</span><span>30 日</span><span>最高接近度</span><span>状态</span></div>
        {rows.map((row) => <DeviationRow key={row.symbol} row={row} />)}
      </div> : payload.availability === "ready" ? <EmptyState title="当前数据日没有达到观察门槛的标的" detail={`有效计算 ${payload.window_coverage["3d"].computed + payload.window_coverage["10d"].computed + payload.window_coverage["30d"].computed} 个窗口；历史数据日期 ${payload.as_of || "—"}。`} /> : null}
      {payload.truncated ? <p className="aq-note">仅返回接近度最高的 {payload.rows.length} 项，共 {payload.candidate_count} 项。</p> : null}
    </> : null}
  </section>;
}

function coverageLabel(value: AbnormalDeviationPayload["window_coverage"]["3d"]) {
  return `${value.raw_pairs} / ${value.pit_pairs} / ${value.index_pairs} / ${value.computed}`;
}

function deviationReasonLabel(reason: string) {
  const labels: Record<string, string> = {
    insufficient_market_sessions: "本地交易日不足 31 个",
    no_index_benchmarks: "没有可用的指数基准日线",
    no_current_stock_bars: "没有标的日线与数据日对齐",
    missing_pit_factors: "股票 PIT 因子缺失",
    missing_benchmark_dates: "指数缺少对应交易日",
    no_comparable_windows: "没有完整可比窗口",
    partial_market_coverage: "市场日线、PIT 因子或指数配对覆盖不完整",
  };
  return labels[reason] || reason;
}

function DeviationRow({ row }: { row: AbnormalDeviationPayload["rows"][number] }) {
  return <article className="aq-deviation-row">
    <Link className="aq-abnormal-stock" to={`/stock/${encodeURIComponent(row.symbol)}?date=${encodeURIComponent(row.as_of)}`} title={`按 ${row.as_of} 查看 ${row.name} 研究页`}>
      <strong>{row.name}</strong>
      <code>{row.symbol} · {DEVIATION_BOARD_LABELS[row.board]} · {row.benchmark_symbol}{row.benchmark_fallback ? " 回退" : ""}</code>
    </Link>
    {(["3d", "10d", "30d"] as const).map((window) => {
      const value = row.windows[window];
      return <span className="aq-deviation-window" key={window}>
        <small>{window}</small>
        <b className={value ? changeClass(value.deviation * 100) : ""}>{value ? formatSignedPct(value.deviation * 100) : "—"}</b>
        <em>{value ? `${formatRate(value.closeness)} / ${formatRate(value.threshold)}` : "偏移 / 阈值"}</em>
      </span>;
    })}
    <span className="aq-deviation-maximum">{formatRate(row.max_closeness)}</span>
    <span className={`aq-deviation-status is-${row.status}`}>{DEVIATION_STATUS_LABELS[row.status]}</span>
  </article>;
}

