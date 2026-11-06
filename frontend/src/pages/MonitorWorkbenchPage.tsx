import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { BellRing, Check, CirclePause, CirclePlay, CircleStop, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { AlertInbox } from "@/components/aqsp/AlertInbox";
import { Badge, SectionHeader, StatePanel } from "@/components/ui/primitives";
import { ApiError, api, type AlertEvent, type CandidateObservation, type MarketEventRule, type MarketEventType, type MonitorIndicatorKey, type MonitorIndicatorSpec, type PriceObservationRule, type PriceObservationRuleInput, type SignalDefinition } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";

function errorText(reason: unknown): string {
  if (reason instanceof ApiError && reason.status === 409) return "观察记录已在其他页面变化，已重新读取。";
  return reason instanceof Error ? reason.message : "候选观察读取失败";
}

type PriceConditionDraft = {
  comparator: "at_or_above" | "at_or_below";
  price: string;
};
type RuleType = "price" | "indicator" | "signal" | "market";
const MARKET_EVENT_LABELS: Record<MarketEventType, string> = {
  limit_up: "涨停池",
  blown_up: "炸板池",
  limit_down: "跌停池",
};

const SEVERITY_LABEL: Record<PriceObservationRuleInput["severity"], string> = {
  info: "提示",
  warn: "警示",
  critical: "重点",
};

function indicatorSpec(metric: string | undefined, catalog: MonitorIndicatorSpec[]): MonitorIndicatorSpec | undefined {
  return catalog.find((item) => item.key === metric);
}

function indicatorLabel(metric: string | undefined, catalog: MonitorIndicatorSpec[]): string {
  return indicatorSpec(metric, catalog)?.label ?? (metric ? `技术指标(${metric})` : "技术指标");
}

function indicatorValueText(metric: string | undefined, value: number, catalog: MonitorIndicatorSpec[]): string {
  return value.toFixed(indicatorSpec(metric, catalog)?.price_based ? 2 : 1);
}

// Form starting values only; the user remains free to enter any value inside
// the catalogue domain. These are not strategy thresholds.
function indicatorInitialThreshold(spec: MonitorIndicatorSpec): string {
  if (spec.key === "rsi12") return "70";
  if (spec.price_based) return "0";
  return "1";
}

export function MonitorWorkbenchPage() {
  const [items, setItems] = useState<CandidateObservation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [rules, setRules] = useState<PriceObservationRule[]>([]);
  const [signalDefinitions, setSignalDefinitions] = useState<SignalDefinition[]>([]);
  const [marketRules, setMarketRules] = useState<MarketEventRule[]>([]);
  const [ruleEvents, setRuleEvents] = useState<AlertEvent[]>([]);
  const [ruleType, setRuleType] = useState<RuleType>("price");
  const [symbol, setSymbol] = useState("");
  const [conditions, setConditions] = useState<PriceConditionDraft[]>([{ comparator: "at_or_above", price: "" }]);
  const [indicatorComparator, setIndicatorComparator] = useState<"at_or_above" | "at_or_below">("at_or_above");
  const [indicatorThreshold, setIndicatorThreshold] = useState("70");
  const [indicatorCatalog, setIndicatorCatalog] = useState<MonitorIndicatorSpec[]>([]);
  const [indicatorMetric, setIndicatorMetric] = useState<MonitorIndicatorKey>("rsi12");
  const [signalId, setSignalId] = useState("");
  const [marketEventType, setMarketEventType] = useState<MarketEventType>("limit_up");
  const [marketMinCount, setMarketMinCount] = useState("1");
  const [combination, setCombination] = useState<PriceObservationRuleInput["combination"]>("all");
  const [severity, setSeverity] = useState<PriceObservationRuleInput["severity"]>("info");
  const [cooldownMinutes, setCooldownMinutes] = useState(0);
  const [note, setNote] = useState("");
  const [ruleError, setRuleError] = useState("");
  const load = useCallback(() => {
    setLoading(true);
    Promise.allSettled([api.candidateObservations(), api.priceObservationRules(), api.marketEventRules(), api.alerts(), api.conditionSignals(), api.monitorIndicatorCatalog()]).then(([candidateResult, rulesResult, marketRulesResult, alertsResult, signalsResult, catalogResult]) => {
      if (candidateResult.status === "fulfilled") { setItems(candidateResult.value.items); setError(""); }
      else setError(errorText(candidateResult.reason));
      if (rulesResult.status === "fulfilled") { setRules(rulesResult.value.items); setRuleError(""); }
      else setRuleError(rulesResult.reason instanceof Error ? rulesResult.reason.message : "价格观察规则读取失败");
      if (marketRulesResult.status === "fulfilled") setMarketRules(marketRulesResult.value.items);
      else setRuleError((previous) => previous || (marketRulesResult.reason instanceof Error ? marketRulesResult.reason.message : "全市场异动规则读取失败"));
      if (alertsResult.status === "fulfilled") setRuleEvents(alertsResult.value.events.filter((event) => event.kind === "price_observation" || event.kind === "market_event"));
      else setRuleError((previous) => previous || (alertsResult.reason instanceof Error ? alertsResult.reason.message : "观察提醒读取失败"));
      if (signalsResult.status === "fulfilled") {
        const definitions = signalsResult.value.items;
        const enabled = definitions.filter((definition) => definition.enabled);
        setSignalDefinitions(definitions);
        setSignalId((current) => enabled.some((definition) => definition.id === current) ? current : enabled[0]?.id ?? "");
      } else setRuleError((previous) => previous || (signalsResult.reason instanceof Error ? signalsResult.reason.message : "条件信号读取失败"));
      if (catalogResult.status === "fulfilled") {
        const items = catalogResult.value.items;
        setIndicatorCatalog(items);
        // Keep the user's current selection across refreshes; only repair an
        // unknown key. Do not overwrite a threshold the user already typed.
        setIndicatorMetric((current) => items.some((item) => item.key === current) ? current : items[0]?.key ?? current);
      } else setRuleError((previous) => previous || "指标目录读取失败，请刷新后重试");
    }).finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);
  async function stop(item: CandidateObservation) {
    setBusyId(item.id);
    setError("");
    try {
      const updated = await api.stopCandidateObservation(item.id, item.revision);
      setItems((previous) => previous.map((row) => row.id === updated.id ? updated : row));
    } catch (reason) {
      setError(errorText(reason));
      load();
    } finally { setBusyId(""); }
  }
  async function createRule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusyId("create"); setRuleError("");
    try {
      const common = {
        symbol: symbol.trim(), severity, cooldown_minutes: cooldownMinutes, note: note.trim(),
      } as const;
      if (ruleType === "market") {
        await api.createMarketEventRule({
          event_type: marketEventType,
          min_count: Number(marketMinCount),
          severity,
          cooldown_minutes: cooldownMinutes,
          note: note.trim(),
        });
      } else if (ruleType === "indicator") {
        await api.createPriceObservationRule({
          ...common,
          rule_type: "indicator",
          metric: indicatorMetric,
          comparator: indicatorComparator,
          threshold: Number(indicatorThreshold),
        });
      } else if (ruleType === "signal") {
        await api.createPriceObservationRule({
          ...common,
          rule_type: "signal",
          signal_id: signalId,
        });
      } else {
        await api.createPriceObservationRule({
          ...common,
          rule_type: "price",
          conditions: conditions.map((condition) => ({ comparator: condition.comparator, price: Number(condition.price) })),
          combination,
        });
      }
      setSymbol(""); setConditions([{ comparator: "at_or_above", price: "" }]); setNote(""); load();
    } catch (reason) { setRuleError(reason instanceof Error ? reason.message : "规则未保存，请重试"); }
    finally { setBusyId(""); }
  }
  async function changeRule(rule: PriceObservationRule) {
    setBusyId(rule.id); setRuleError("");
    try {
      await api.updatePriceObservationRule(rule.id, rule.status === "active" ? "paused" : "active", rule.revision);
      load();
    } catch (reason) { setRuleError(errorText(reason)); load(); }
    finally { setBusyId(""); }
  }
  async function changeMarketRule(rule: MarketEventRule) {
    setBusyId(rule.id); setRuleError("");
    try {
      await api.updateMarketEventRule(rule.id, rule.status === "active" ? "paused" : "active", rule.revision);
      load();
    } catch (reason) { setRuleError(errorText(reason)); load(); }
    finally { setBusyId(""); }
  }
  async function handleRuleEvent(event: AlertEvent, action: "handled" | "ignored") {
    setBusyId(event.id); setRuleError("");
    try { await api.alertTransition(event.id, action); load(); }
    catch (reason) { setRuleError(reason instanceof Error ? reason.message : "提醒处置失败"); }
    finally { setBusyId(""); }
  }
  const active = items.filter((item) => item.state === "active");
  const history = items.filter((item) => item.state !== "active");
  const activeRules = rules.filter((rule) => rule.status === "active");
  const activeMarketRules = marketRules.filter((rule) => rule.status === "active");
  const enabledSignals = signalDefinitions.filter((definition) => definition.enabled);
  const selectedSignal = enabledSignals.find((definition) => definition.id === signalId);
  const selectedIndicator = indicatorCatalog.find((item) => item.key === indicatorMetric);
  const openRuleEvents = ruleEvents.filter((event) => event.kind === "price_observation" && event.status === "OPEN");
  const marketRuleEvents = ruleEvents.filter((event) => event.kind === "market_event");
  const openMarketEvents = marketRuleEvents.filter((event) => event.status === "OPEN");
  return <div className="aq-page">
    <header className="aq-page-head"><div><p className="aq-eyebrow"><BellRing aria-hidden="true" />AQSP · 离页跟进</p><div className="aq-title-row"><h1>监控</h1><Badge tone={active.length + activeRules.length + activeMarketRules.length ? "warn" : "neutral"}>{active.length + activeRules.length + activeMarketRules.length} 条观察进行中</Badge></div><p className="aq-page-sub">观察条件满足后请人工核对；规则提醒不构成买卖信号。</p></div><button type="button" className="aq-btn" onClick={load} disabled={loading}><RefreshCw aria-hidden="true" />刷新</button></header>
    <AlertInbox />
    <section className="aq-section"><SectionHeader title="观察规则" description="价格、受限日线指标、固定修订条件信号和全市场异动由现有后台周期检查；依据过期或缺失时不触发。" count={`${activeRules.length + activeMarketRules.length} 条启用`} />
      {ruleError ? <StatePanel tone="warn">{ruleError}</StatePanel> : null}
      <form className="aq-candidate-review-form-grid" onSubmit={(event) => void createRule(event)}>
        <div className="aq-abnormal-subtabs" role="group" aria-label="规则类型">
          <button type="button" aria-pressed={ruleType === "price"} className={ruleType === "price" ? "is-active" : ""} onClick={() => setRuleType("price")}>价格</button>
          <button type="button" aria-pressed={ruleType === "indicator"} className={ruleType === "indicator" ? "is-active" : ""} onClick={() => setRuleType("indicator")}>指标信号</button>
          <button type="button" aria-pressed={ruleType === "signal"} className={ruleType === "signal" ? "is-active" : ""} onClick={() => setRuleType("signal")}>条件信号</button>
          <button type="button" aria-pressed={ruleType === "market"} className={ruleType === "market" ? "is-active" : ""} onClick={() => setRuleType("market")}>全市场异动</button>
        </div>
        {ruleType !== "market" ? <label>股票代码<input className="aq-input" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} required value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="例如 600000" /></label> : null}
        {ruleType === "price" ? <div className="aq-rule-condition-list">
          <div className="aq-rule-condition-heading"><span>价格条件</span><label>条件关系<select className="aq-input" value={combination} onChange={(event) => setCombination(event.target.value as PriceObservationRuleInput["combination"])}><option value="all">全部满足（AND）</option><option value="any">任一满足（OR）</option></select></label></div>
          {conditions.map((condition, index) => <div className="aq-rule-condition" key={index}>
            <label>方向<select className="aq-input" value={condition.comparator} onChange={(event) => setConditions((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, comparator: event.target.value as PriceConditionDraft["comparator"] } : row))}><option value="at_or_above">达到或高于</option><option value="at_or_below">达到或低于</option></select></label>
            <label>观察价格<input className="aq-input" inputMode="decimal" type="number" min="0.0001" step="0.0001" required value={condition.price} onChange={(event) => setConditions((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, price: event.target.value } : row))} placeholder="0.00" /></label>
            <button type="button" className="aq-icon-btn" aria-label={`删除第 ${index + 1} 条条件`} title="删除条件" disabled={conditions.length <= 1} onClick={() => setConditions((rows) => rows.filter((_, rowIndex) => rowIndex !== index))}><Trash2 aria-hidden="true" /></button>
          </div>)}
          <button type="button" className="aq-btn" disabled={conditions.length >= 5} onClick={() => setConditions((rows) => [...rows, { comparator: "at_or_above", price: "" }])}><Plus aria-hidden="true" />添加条件</button>
        </div> : ruleType === "indicator" ? <>
          <label>技术指标<select className="aq-input" aria-label="技术指标" value={indicatorMetric} disabled={!indicatorCatalog.length} onChange={(event) => {
            const key = event.target.value as MonitorIndicatorKey;
            setIndicatorMetric(key);
            const spec = indicatorCatalog.find((item) => item.key === key);
            if (spec) setIndicatorThreshold(indicatorInitialThreshold(spec));
          }}>{indicatorCatalog.length ? indicatorCatalog.map((item) => <option key={item.key} value={item.key}>{item.label}</option>) : <option value={indicatorMetric}>指标目录读取中…</option>}</select></label>
          <label>方向<select className="aq-input" value={indicatorComparator} onChange={(event) => setIndicatorComparator(event.target.value as typeof indicatorComparator)}><option value="at_or_above">达到或高于</option><option value="at_or_below">达到或低于</option></select></label>
          <label>阈值<input className="aq-input" inputMode="decimal" type="number" min={selectedIndicator?.min_threshold ?? undefined} max={selectedIndicator?.max_threshold ?? undefined} step={selectedIndicator?.price_based ? "0.01" : "0.1"} required value={indicatorThreshold} onChange={(event) => setIndicatorThreshold(event.target.value)} /></label>
          <p className="aq-note">{selectedIndicator ? `${selectedIndicator.label}${selectedIndicator.unit ? `（单位：${selectedIndicator.unit}）` : ""}` : "指标"}按 PIT 复权因子统一至 as-of 价基后计算；数据缺失或过期时不触发，结果仅用于人工复核。{selectedIndicator ? `至少需要 ${selectedIndicator.min_bars} 根已完成日线。` : ""}</p>
        </> : ruleType === "signal" ? <>
          <label>条件信号<select className="aq-input" required value={signalId} onChange={(event) => setSignalId(event.target.value)} disabled={!enabledSignals.length}><option value="">{enabledSignals.length ? "选择已启用信号" : "没有已启用信号"}</option>{enabledSignals.map((definition) => <option key={definition.id} value={definition.id}>{definition.name} · r{definition.revision} · {definition.kind}</option>)}</select></label>
          {selectedSignal ? <p className="aq-note">监控固定使用 {selectedSignal.name} r{selectedSignal.revision}；后续编辑不会改写此规则。检查依赖最新 raw 日线及跨日条件所需 PIT 因子。</p> : <p className="aq-note">请先在 <Link to="/signals">信号库</Link> 创建并启用条件信号。</p>}
        </> : <>
          <label>异动池<select className="aq-input" value={marketEventType} onChange={(event) => setMarketEventType(event.target.value as MarketEventType)}><option value="limit_up">涨停池</option><option value="blown_up">炸板池</option><option value="limit_down">跌停池</option></select></label>
          <label>触发数量<input className="aq-input" type="number" inputMode="numeric" min="1" max="10000" step="1" required value={marketMinCount} onChange={(event) => setMarketMinCount(event.target.value)} /></label>
          <p className="aq-note">仅使用当日且缓存不超过 5 分钟的公开异动池；来源或交易日过期时不触发，提醒进入本站收件箱。</p>
        </>}
        <label>提醒级别<select className="aq-input" value={severity} onChange={(event) => setSeverity(event.target.value as PriceObservationRuleInput["severity"])}><option value="info">提示</option><option value="warn">警示</option><option value="critical">重点</option></select></label>
        <label>再次提醒冷却<select className="aq-input" value={cooldownMinutes} onChange={(event) => setCooldownMinutes(Number(event.target.value))}><option value={0}>不额外冷却</option><option value={5}>5 分钟</option><option value={15}>15 分钟</option><option value={30}>30 分钟</option><option value={60}>1 小时</option><option value={240}>4 小时</option><option value={1440}>24 小时</option></select></label>
        <label>本人备注<input className="aq-input" maxLength={300} value={note} onChange={(event) => setNote(event.target.value)} placeholder="可选：为什么等这个位置" /></label>
        <button className="aq-btn aq-btn-primary" type="submit" disabled={busyId === "create" || (ruleType === "signal" && !selectedSignal)}><BellRing aria-hidden="true" />{busyId === "create" ? "保存中…" : ruleType === "market" ? "保存全市场规则" : "保存观察规则"}</button>
      </form>
      {loading && rules.length === 0 ? <StatePanel>正在读取个股观察规则…</StatePanel> : rules.length ? <div className="aq-card-grid">{rules.map((rule) => <article className="aq-card aq-strategy-watch" key={rule.id}>
        <header className="aq-card-head"><div><h3><Link to={`/stock/${rule.symbol}`}>{rule.symbol}</Link></h3><span>{rule.rule_type === "signal"
          ? `${rule.signal_snapshot?.name ?? rule.signal_id ?? "条件信号"} · r${rule.signal_snapshot?.revision ?? "?"}`
          : rule.rule_type === "indicator"
          ? `${indicatorLabel(rule.metric, indicatorCatalog)} ${rule.comparator === "at_or_above" ? "≥" : "≤"} ${indicatorValueText(rule.metric, Number(rule.threshold ?? 0), indicatorCatalog)}`
          : (rule.conditions?.length ? rule.conditions : [{ comparator: rule.comparator, price: rule.price ?? 0 }]).map((condition) => `${condition.comparator === "at_or_above" ? "≥" : "≤"}${condition.price.toFixed(2)} 元`).join(rule.combination === "any" ? " 或 " : " 且 ")}</span><div className="aq-row-actions"><Badge tone={rule.severity === "critical" ? "warn" : "neutral"}>{SEVERITY_LABEL[rule.severity || "info"]}</Badge><Badge tone="neutral">冷却 {rule.cooldown_minutes || 0} 分钟</Badge></div></div><Badge tone={rule.status === "active" ? "ok" : "neutral"}>{rule.status === "active" ? "观察中" : rule.status === "paused" ? "已暂停" : "已转入持仓"}</Badge></header>
        {rule.status === "transferred" && rule.transferred_to_position_id ? <p className="aq-note">该股票已转为持仓，候选观察结束。<Link to={`/my/holdings/${encodeURIComponent(rule.transferred_to_position_id)}/plan`}>打开持仓工作区</Link></p> : null}
        {rule.rule_type === "signal" && rule.signal_snapshot ? <p className="aq-note">固定条件：{rule.signal_snapshot.conditions.map((condition) => `${condition.left}${condition.left_days ? `(前${condition.left_days}日)` : ""} ${condition.op} ${typeof condition.right === "string" && condition.right.startsWith("field:") ? `${condition.right.slice(6)}${condition.right_days ? `(前${condition.right_days}日)` : ""}` : condition.right}`).join(" 且 ")}</p> : null}
        {rule.note ? <p>{rule.note}</p> : null}
        {rule.last_check ? rule.last_check.issue ? <p className="aq-note">最近检查依据不可用：{rule.last_check.issue} · {formatEvidenceTime(rule.last_check.checked_at)}</p> : rule.rule_type === "signal" ? <p className="aq-note">{rule.last_check.triggered ? "信号已命中，待人工复核" : "信号未满足"} · {formatEvidenceTime(rule.last_check.as_of)} · r{rule.last_check.signal_revision ?? rule.signal_snapshot?.revision ?? "?"} · {rule.last_check.source || "来源未标注"} · {rule.last_check.freshness || "时效未知"}</p> : rule.rule_type === "indicator" ? <p className="aq-note">最近值 {rule.last_check.value == null ? "—" : indicatorValueText(rule.metric, rule.last_check.value, indicatorCatalog)} · {rule.last_check.triggered ? "已命中，待人工复核" : "未满足条件"} · {formatEvidenceTime(rule.last_check.as_of)} · {rule.last_check.source || "来源未标注"} · {rule.last_check.freshness || "时效未知"}</p> : <p className="aq-note">最近检查 {rule.last_check.price?.toFixed(2) ?? "—"} 元 · {rule.last_check.triggered ? `命中 ${rule.last_check.matched_conditions?.filter((condition) => condition.triggered).length ?? 1} 条条件` : "尚未满足规则"} · {formatEvidenceTime(rule.last_check.quote_as_of)} · {rule.last_check.source || "来源未标注"}</p> : <p className="aq-note">尚无有效后台检查结果</p>}
        {rule.status !== "transferred" ? <button type="button" className="aq-btn" disabled={busyId === rule.id} onClick={() => void changeRule(rule)}>{rule.status === "active" ? <CirclePause aria-hidden="true" /> : <CirclePlay aria-hidden="true" />}{rule.status === "active" ? "暂停" : "恢复观察"}</button> : null}
      </article>)}</div> : !loading ? <StatePanel>还没有价格观察规则。</StatePanel> : null}
      {openRuleEvents.length ? <section className="aq-risk-group"><h3 className="aq-risk-group-heading">观察规则待复核<span>{openRuleEvents.length} 条</span></h3>{openRuleEvents.map((event) => <article className="aq-position-alert" key={event.id}>
        <header className="aq-position-alert-head"><div className="aq-alert-row-title"><Link to={`/stock/${event.symbol}`}><b>{event.symbol}</b></Link><Badge tone="warn">{event.basis_json.rule_type === "signal" || event.basis_json.rule?.includes("条件信号：") ? "条件信号" : event.basis_json.rule_type === "indicator" || event.basis_json.metric ? "指标观察" : "价格观察"}</Badge><Badge tone={event.severity === "critical" ? "warn" : "neutral"}>{SEVERITY_LABEL[event.severity || "info"]}</Badge></div><span className="aq-note">证据时点 {formatEvidenceTime(event.quote_as_of || event.created_at)}</span></header>
        <p>{event.basis_json.rule} · {event.basis_json.actual}</p>{event.user_note ? <p className="aq-note">本人备注：{event.user_note}</p> : null}<p className="aq-note">来源：{event.quote_source || event.basis_json.evidence}。到价不代表成交或买卖建议。</p>
        <div className="aq-row-actions"><button type="button" className="aq-btn" disabled={busyId === event.id} onClick={() => void handleRuleEvent(event, "handled")}><Check aria-hidden="true" />已复核</button><button type="button" className="aq-btn" disabled={busyId === event.id} onClick={() => void handleRuleEvent(event, "ignored")}><X aria-hidden="true" />忽略本次</button></div>
      </article>)}</section> : null}
      {ruleEvents.some((event) => event.status !== "OPEN") ? <details className="aq-workspace-fold"><summary>观察规则历史 <span>{ruleEvents.filter((event) => event.status !== "OPEN").length} 条</span></summary><div className="aq-workspace-fold-body">{ruleEvents.filter((event) => event.status !== "OPEN").slice(0, 30).map((event) => <p key={event.id}>{event.symbol} · {event.basis_json.actual} · {event.status === "HANDLED" ? "已复核" : event.status === "IGNORED" ? "已忽略" : "条件失效"} · {formatEvidenceTime(event.updated_at)}</p>)}</div></details> : null}
    </section>
    <section className="aq-section">
      <SectionHeader title="全市场异动规则" description="复用当日涨停、炸板、跌停公开池；只产生人工复核提醒，不接外部投递。" count={`${activeMarketRules.length} 条启用`} />
      {loading && marketRules.length === 0 ? <StatePanel>正在读取全市场异动规则…</StatePanel> : marketRules.length ? <div className="aq-card-grid">
        {marketRules.map((rule) => <article className="aq-card aq-strategy-watch" key={rule.id}>
          <header className="aq-card-head"><div><h3>{MARKET_EVENT_LABELS[rule.event_type]} ≥ {rule.min_count} 只</h3><div className="aq-row-actions"><Badge tone={rule.severity === "critical" ? "warn" : "neutral"}>{SEVERITY_LABEL[rule.severity]}</Badge><Badge tone="neutral">冷却 {rule.cooldown_minutes} 分钟</Badge></div></div><Badge tone={rule.status === "active" ? "ok" : "neutral"}>{rule.status === "active" ? "观察中" : "已暂停"}</Badge></header>
          {rule.note ? <p>{rule.note}</p> : null}
          {rule.last_check ? rule.last_check.issue
            ? <p className="aq-note">最近依据不可用：{rule.last_check.issue} · {formatEvidenceTime(rule.last_check.checked_at)}</p>
            : <p className="aq-note">最近检查 {rule.last_check.count ?? 0} 只 · {rule.last_check.triggered ? "已命中，待人工复核" : "未达到数量"} · {rule.last_check.trade_date || "日期缺失"} · {rule.last_check.source}</p>
            : <p className="aq-note">尚无可核验检查结果</p>}
          <button type="button" className="aq-btn" disabled={busyId === rule.id} onClick={() => void changeMarketRule(rule)}>{rule.status === "active" ? <CirclePause aria-hidden="true" /> : <CirclePlay aria-hidden="true" />}{rule.status === "active" ? "暂停" : "恢复观察"}</button>
        </article>)}
      </div> : !loading ? <StatePanel>还没有全市场异动规则。</StatePanel> : null}
      {openMarketEvents.length ? <section className="aq-risk-group"><h3 className="aq-risk-group-heading">全市场异动待复核<span>{openMarketEvents.length} 条</span></h3>{openMarketEvents.map((event) => <article className="aq-position-alert" key={event.id}>
        <header className="aq-position-alert-head"><div className="aq-alert-row-title"><Badge tone="warn">{event.basis_json.rule}</Badge><Badge tone={event.severity === "critical" ? "warn" : "neutral"}>{SEVERITY_LABEL[event.severity || "info"]}</Badge></div><span className="aq-note">事件日期 {event.event_trade_date || event.quote_as_of || "—"} · 缓存 {formatEvidenceTime(event.source_cached_at || "")}</span></header>
        <p>{event.basis_json.actual}</p>
        {event.event_examples?.length ? <p className="aq-note">样例：{event.event_examples.map((item) => item.name || item.code).join("、")}</p> : null}
        {event.user_note ? <p className="aq-note">本人备注：{event.user_note}</p> : null}
        <p className="aq-note">来源 {event.quote_source || event.basis_json.evidence} · 仅供核验，不构成个股信号或买卖建议。</p>
        <div className="aq-row-actions"><button type="button" className="aq-btn" disabled={busyId === event.id} onClick={() => void handleRuleEvent(event, "handled")}><Check aria-hidden="true" />已复核</button><button type="button" className="aq-btn" disabled={busyId === event.id} onClick={() => void handleRuleEvent(event, "ignored")}><X aria-hidden="true" />忽略本次</button></div>
      </article>)}</section> : null}
      {marketRuleEvents.some((event) => event.status !== "OPEN") ? <details className="aq-workspace-fold"><summary>全市场异动历史 <span>{marketRuleEvents.filter((event) => event.status !== "OPEN").length} 条</span></summary><div className="aq-workspace-fold-body">{marketRuleEvents.filter((event) => event.status !== "OPEN").slice(0, 30).map((event) => <p key={event.id}>{event.basis_json.rule} · {event.basis_json.actual} · {event.status === "HANDLED" ? "已复核" : event.status === "IGNORED" ? "已忽略" : "条件解除或规则暂停"} · {formatEvidenceTime(event.updated_at)}</p>)}</div></details> : null}
    </section>
    <section className="aq-section"><SectionHeader title="未持仓候选条件" description="候选观察与持仓风险分别管理；真实持仓出现后，候选观察会收束并引用对应仓位。" count={`${active.length} 项进行中`} />
      {error ? <StatePanel tone="warn">{error}</StatePanel> : null}
      {loading && items.length === 0 ? <StatePanel>正在读取候选观察…</StatePanel> : active.length ? <div className="aq-card-grid">{active.map((item) => <article className="aq-card aq-strategy-watch" key={item.id}>
        <header className="aq-card-head"><div><h3>{item.name || item.symbol}</h3><span className="aq-code">{item.symbol}</span></div><Badge tone="ok">观察中</Badge></header>
        <p><b>{item.comparator === "at_or_above" ? "达到或高于" : "达到或低于"} {item.price.toFixed(2)} 元</b></p><p><span className="aq-label">等什么</span>{item.condition}</p><p><span className="aq-label">何时取消</span>{item.invalidation}</p>
        {item.personal_context ? <details className="aq-candidate-personal"><summary>本人问题与判断</summary><p><b>问题：</b>{item.personal_context.personal_intent || "未记录"}</p><p><b>期限：</b>{item.personal_context.time_horizon || "未记录"}</p>{item.personal_context.personal_hypothesis ? <p><b>本人假设：</b>{item.personal_context.personal_hypothesis}</p> : null}{item.personal_context.counterargument ? <p><b>反方理由：</b>{item.personal_context.counterargument}</p> : null}</details> : null}
        {item.last_check ? <p className="aq-note">最近报价 {item.last_check.price.toFixed(2)} 元 · {item.last_check.triggered ? "已到人工复核线" : "未到观察线"} · 行情时点 {formatEvidenceTime(item.last_check.quote_as_of)}</p> : <p className="aq-note">后台尚无可核验检查结果</p>}
        <div className="aq-row-actions"><Link className="aq-btn" to={`/stock/${item.symbol}`}>查看个股依据</Link><button type="button" className="aq-btn" disabled={busyId === item.id} onClick={() => void stop(item)}><CircleStop aria-hidden="true" />{busyId === item.id ? "停止中…" : "停止观察"}</button></div>
      </article>)}</div> : !loading ? <StatePanel>没有进行中的候选观察。先从策略候选里记录你的判断，再确认采用一条有来源的观察条件。</StatePanel> : null}
    </section>
    {history.length ? <details className="aq-workspace-fold"><summary>已停止或转持仓的候选观察 <span>{history.length} 条历史记录</span></summary><div className="aq-workspace-fold-body">{history.map((item) => <p key={item.id}>{item.name || item.symbol} · {item.state === "transferred" ? "已转持仓" : item.state === "transferred_unlinked" ? "已转持仓 · 仓位身份待核" : "已停止"} · {item.condition} · 更新于 {formatEvidenceTime(item.updated_at)}{item.transferred_to_position_id ? <> · <Link to={`/my/holdings/${encodeURIComponent(item.transferred_to_position_id)}/plan`}>查看对应持仓计划</Link></> : null}</p>)}</div></details> : null}
  </div>;
}
