import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { ArrowRight, Plus, RefreshCw, Star, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, type AlertEvent, type AqspCandidate, type PortfolioData, type Quote } from "@/lib/api";
import { buildHoldingGroups, normalizeHoldingSectors } from "@/lib/holding-view";
import { holdingT1Status, shanghaiDate } from "@/lib/portfolio-view";
import { addCodes, loadWatch, saveWatch, WATCHLIST_CHANGED_EVENT, WATCHLIST_STORAGE_KEY } from "@/lib/watchlist";
import { changeClass, formatMoney, formatSignedPct } from "@/lib/format";
import { WATCHLIST_ANCHOR_ID } from "@/lib/hash-navigation";
import { positionPlanHref } from "@/lib/position-workspace";
import { cn } from "@/lib/utils";
import { Badge, EmptyState, SectionHeader, StatePanel } from "@/components/ui/primitives";

interface Props {
  portfolio: PortfolioData | null;
  alerts: readonly AlertEvent[];
  candidates: readonly AqspCandidate[];
  candidateDate: string;
  candidateStatus: string;
  renderRiskSettings?: (code: string, expanded?: boolean) => ReactNode;
}

export function openOperation(direction: "buy" | "sell", code = ""): void {
  window.dispatchEvent(new CustomEvent("aqsp:record-operation", { detail: { direction, code } }));
}

function numeric(value: number | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";
}

/** 只合并视图，持仓继续由后端负责，自选继续使用原浏览器存储。 */
export function HoldingsWatchlist({ portfolio, alerts, candidates, candidateDate, candidateStatus, renderRiskSettings }: Props) {
  const location = useLocation();
  const [codes, setCodes] = useState(loadWatch);
  const [raw, setRaw] = useState("");
  const [openRules, setOpenRules] = useState<ReadonlyMap<string, boolean>>(() => new Map());
  const [sector, setSector] = useState("");
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [sectors, setSectors] = useState<Record<string, readonly string[]>>({});
  const [quoteTime, setQuoteTime] = useState("");
  const [quoteError, setQuoteError] = useState("");
  const [sectorError, setSectorError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const allCodes = useMemo(() => [...new Set([...(portfolio?.holdings.map((row) => row.code) ?? []), ...codes])], [portfolio?.holdings, codes]);
  const codesKey = allCodes.join(",");

  useEffect(() => {
    const sync = () => setCodes(loadWatch());
    const onStorage = (event: StorageEvent) => {
      if (event.key === WATCHLIST_STORAGE_KEY || event.key === null) sync();
    };
    window.addEventListener(WATCHLIST_CHANGED_EVENT, sync);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(WATCHLIST_CHANGED_EVENT, sync);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  useEffect(() => {
    let active = true;
    const pendingCodes = codesKey.split(",").filter(Boolean);
    setQuoteError("");
    setSectorError(false);
    if (!pendingCodes.length) {
      setLoading(false);
      return;
    }
    setLoading(true);
    api.quote(codesKey).then((data) => {
      if (!active) return;
      setQuotes(data);
      setQuoteTime(new Date().toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }));
    }).catch((reason: unknown) => {
      if (active) setQuoteError(reason instanceof Error ? reason.message : "行情读取失败");
    }).finally(() => { if (active) setLoading(false); });

    // 板块是已有只读 API；限制并发，避免长自选表一次打满数据源。
    const blocks = (api as typeof api & { blocks?: (code: string) => Promise<unknown> }).blocks;
    if (blocks) {
      let next = 0;
      const read = async () => {
        while (active && next < pendingCodes.length) {
          const code = pendingCodes[next++];
          try {
            const result = normalizeHoldingSectors(await blocks(code));
            if (active) setSectors((previous) => ({ ...previous, [code]: result }));
          } catch {
            if (active) setSectorError(true);
          }
        }
      };
      void Promise.all(Array.from({ length: Math.min(3, pendingCodes.length) }, read));
    }
    return () => { active = false; };
  }, [codesKey, reload]);

  const groups = useMemo(() => buildHoldingGroups({
    holdings: portfolio?.holdings ?? [], watchlist: codes, alerts, candidates, quotes, sectors, sector,
  }), [portfolio?.holdings, codes, alerts, candidates, quotes, sectors, sector]);

  function persist(next: string[]): boolean {
    try {
      const change = saveWatch(next);
      setCodes(next);
      if (change.removed.length && change.operation_id) {
        void api.recordWatchlistRemoval([...change.removed], change.operation_id).catch(() => {
          // localStorage removal evidence remains available when the API is offline.
        });
      }
      return true;
    } catch {
      toast.error("自选保存失败，原列表仍保留；请检查浏览器存储后重试。");
      return false;
    }
  }

  function addWatch(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const result = addCodes(codes, raw);
    if (!result.added) {
      toast("请输入新的 6 位 A 股代码。");
      return;
    }
    if (persist(result.next)) {
      setRaw("");
      toast(`已添加 ${result.added} 只自选`);
    }
  }

  const renderGroup = (group: (typeof groups)[number]) => (
    <section id={`holdings-${group.kind}`} className={group.kind === "triggered" || group.kind === "holding" ? "aq-section" : "aq-watch-group"} key={group.kind} data-holding-group={group.kind}>
      <SectionHeader title={group.label} count={`${group.rows.length} 只`} description={group.kind === "triggered" ? "按已记录提醒优先展示；当日新仓仍受 T+1 限制。" : undefined} />
      <ul className="aq-watch-list" aria-label={group.label}>
        {group.rows.map((row) => {
          const holding = row.holding;
          const availability = holding as (typeof holding & { sellable_shares?: number; t1_locked_shares?: number });
          const researchLink = `/stock/${row.code}${candidateDate ? `?date=${encodeURIComponent(candidateDate)}` : ""}`;
          const planHref = positionPlanHref(holding?.position_id, location.pathname + location.search + location.hash);
          const updated = holding ? portfolio?.last_refresh || portfolio?.updated : quoteTime;
          return (
            <li className="aq-watch-row" key={row.code} data-symbol={row.code}>
              <div className="aq-watch-identity">
                <Link to={researchLink}><strong>{row.name}</strong></Link>
                <span className="aq-code">{row.code}</span>
                <div className="aq-tag-row">{holding ? <Badge>持仓</Badge> : <Badge>自选</Badge>}{holding?.risk_rules?.some((rule) => rule.enabled) ? <Badge>强化盯盘 · 已关联规则</Badge> : null}{holding && row.watched ? <Badge>已关注</Badge> : null}{row.candidate ? <Badge tone="ok">今日候选</Badge> : null}</div>
                {holding ? <Badge tone={holdingT1Status(holding.opened_at, shanghaiDate()).tone}>{holdingT1Status(holding.opened_at, shanghaiDate()).label}</Badge> : null}
                {availability && typeof availability.sellable_shares === "number" ? <span className="aq-note">可卖 {availability.sellable_shares} 股{availability.t1_locked_shares ? ` · 今日锁定 ${availability.t1_locked_shares} 股` : ""}</span> : null}
              </div>
              <dl className="aq-watch-metrics">
                <div><dt>现价</dt><dd className="aq-num">{numeric(holding?.price ?? row.quote?.price)}</dd></div>
                {holding ? <>
                  <div><dt>持仓 / 股</dt><dd className="aq-num">{holding.shares}</dd></div>
                  <div><dt>成本价</dt><dd className="aq-num">{numeric(holding.cost)}</dd></div>
                  <div><dt>市值</dt><dd className="aq-num">{formatMoney(holding.market_value)}</dd></div>
                  <div><dt>浮动盈亏</dt><dd className={cn("aq-num", changeClass(holding.pnl))}>{formatMoney(holding.pnl)}</dd></div>
                  <div><dt>收益率</dt><dd className={cn("aq-num", changeClass(holding.pnl_pct))}>{formatSignedPct(holding.pnl_pct)}</dd></div>
                </> : <>
                  <div><dt>涨跌幅</dt><dd className={cn("aq-num", changeClass(row.quote?.change_pct))}>{row.quote ? formatSignedPct(row.quote.change_pct) : "—"}</dd></div>
                  <div><dt>换手率</dt><dd className="aq-num">{row.quote ? `${numeric(row.quote.turnover_pct)}%` : "—"}</dd></div>
                </>}
              </dl>
              <div className="aq-watch-research" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
                {row.alerts.length ? <div className="aq-watch-risk-facts">
                  <p className="aq-watch-risk-summary">{row.alerts.length} 项风险触发 · 需要复核持有依据</p>
                  {row.alerts.map((alert) => <div key={alert.id}><span className="aq-note">{alert.level}</span><p>{alert.basis_json.rule || alert.t1_note || "查看今日提醒中的触发依据"}</p></div>)}
                  <p className="aq-note">这是规则触发事实；持有、减仓或做 T 的适用性，在处理计划中结合大盘与板块分析。</p>
                </div> : null}
                {row.sectors.length ? <details><summary className="aq-note">所属板块 · {row.sectors.length} 个</summary><div className="aq-tag-row">{row.sectors.map((name) => <button key={name} className="aq-tag aq-tag-neutral" type="button" onClick={() => setSector(name)} title={`只看${name}`}>{name}</button>)}</div></details> : <span>板块待补</span>}
                {row.sectors[0] ? <button className="aq-tag aq-tag-neutral" style={{ justifySelf: "start" }} type="button" onClick={() => setSector(row.sectors[0])}>{row.sectors[0]}</button> : null}
                <div className="aq-watch-quote-meta"><p className="aq-note">{holding ? "行情更新" : "行情读取"}：{updated || "尚未取得"}</p>
                {holding?.opened_at ? <p className="aq-note">首次开仓：{holding.opened_at}</p> : null}</div>
              </div>
              <div className="aq-watch-actions">
                {holding && planHref ? <Link className="aq-btn aq-btn-primary" to={planHref}>看处理计划 <ArrowRight aria-hidden="true" /></Link> : holding ? <span className="aq-note">持仓身份待核对，请刷新持仓</span> : null}
                <Link className="aq-btn" to={researchLink}>查看依据 <ArrowRight aria-hidden="true" /></Link>
                {holding ? <button className="aq-btn" type="button" onClick={() => openOperation("sell", row.code)}>录入卖出</button> : <button className="aq-btn" type="button" onClick={() => openOperation("buy", row.code)}>录入买入</button>}
                {row.watched ? <button className="aq-btn aq-btn-icon" type="button" title={`移除 ${row.code} 自选`} aria-label={`移除 ${row.code} 自选`} onClick={() => { if (persist(codes.filter((code) => code !== row.code))) toast(`已移除 ${row.code} 自选`); }}><Trash2 aria-hidden="true" /></button> : <button className="aq-btn aq-btn-icon" type="button" title={`关注 ${row.code}`} aria-label={`关注 ${row.code}`} onClick={() => { if (persist([...codes, row.code])) toast(`已关注 ${row.code}`); }}><Star aria-hidden="true" /></button>}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );

  return (
    <div className="aq-watchlist-area">
      {quoteError ? <StatePanel tone="warn">行情读取失败：{quoteError}。关注记录仍保留，价格可能为上次读取结果。<button className="aq-btn" type="button" disabled={loading} onClick={() => setReload((value) => value + 1)}>重试行情</button></StatePanel> : null}
      {sectorError ? <StatePanel tone="warn">部分板块读取失败，未取得资料的标的显示“板块待补”。<button className="aq-btn" type="button" onClick={() => setReload((value) => value + 1)}>重试板块</button></StatePanel> : null}
      {sector ? <div className="aq-row-actions"><Badge tone="neutral">板块：{sector}</Badge><button type="button" className="aq-btn" onClick={() => setSector("")}>显示全部</button></div> : null}
      {sector && groups.every((group) => !group.rows.length) ? <EmptyState title="该板块没有匹配记录" detail="可以清除板块筛选查看全部持仓和自选。" /> : null}
      {portfolio?.holdings.length ? <div id="holding-positions" className="aq-secondary-modules">
        {groups.filter((group) => group.rows.length && (group.kind === "triggered" || group.kind === "holding")).map(renderGroup)}
        {!groups.some((group) => group.rows.length && (group.kind === "triggered" || group.kind === "holding")) ? <EmptyState title="当前板块没有匹配持仓" detail="清除板块筛选可查看全部持仓与处理计划。" /> : null}
      </div> : null}
      {portfolio && !portfolio.holdings.length ? <section className="aq-section"><SectionHeader title="持仓事实" /><EmptyState title="尚未录入已有持仓" detail="可以先研究候选、加入自选；已有真实成交可点“我操作了”补录。" /></section> : null}
      <section id={WATCHLIST_ANCHOR_ID} className="aq-section" aria-label="我的自选">
        <SectionHeader title="我的自选" description="关注标的与当前持仓分开查看；加入自选不会生成持仓。" count={`${codes.length} 只`} />
        <form className="aq-watch-entry" onSubmit={addWatch}>
          <label htmlFor="holding-watch-codes">添加自选</label>
          <div className="aq-row-actions">
            <input id="holding-watch-codes" className="aq-input" value={raw} onChange={(event) => setRaw(event.target.value)} placeholder="股票代码，支持一次添加多只" autoComplete="off" />
            <button type="submit" className="aq-btn"><Plus aria-hidden="true" /> 添加自选</button>
            <button type="button" className="aq-btn aq-btn-icon" title="刷新自选行情与板块" aria-label="刷新自选行情与板块" disabled={loading || !allCodes.length} onClick={() => setReload((value) => value + 1)}><RefreshCw className={cn(loading && "aq-spin")} aria-hidden="true" /></button>
          </div>
          <p className="aq-note">自选列表保留在此浏览器；移出动作会留下当前身份的审计记录。{candidateStatus}</p>
        </form>
        {groups.filter((group) => group.rows.length && group.kind !== "triggered" && group.kind !== "holding").map(renderGroup)}
        {!groups.some((group) => group.rows.length && group.kind !== "triggered" && group.kind !== "holding") ? <p className="aq-note">暂无独立自选标的；已关注的持仓在上方显示。</p> : null}
      </section>
      {renderRiskSettings && portfolio?.holdings.length ? <section id="holding-rules" className="aq-section aq-risk-rules-section" aria-label="风险规则">
        <SectionHeader
          title="风险规则"
          description="规则只负责提醒和复核，不直接替代持有、减仓或做 T 分析。按股票展开后才读取详细设置。"
          count={`${portfolio.holdings.filter((row) => row.risk_rules?.some((rule) => rule.enabled)).length} 只已启用`}
        />
        <div className="aq-risk-stock-list">
          {portfolio.holdings.map((holding) => {
            const enabled = holding.risk_rules?.filter((rule) => rule.enabled).length ?? 0;
            return <details className="aq-risk-stock" key={holding.code} onToggle={(event) => {
              const open = event.currentTarget.open;
              setOpenRules((prior) => { const next = new Map(prior); next.set(holding.code, open); return next; });
            }}>
              <summary><span><strong>{holding.name || holding.code}</strong><span className="aq-code">{holding.code}</span></span><span className="aq-note">{enabled ? `${enabled} 条启用` : "未启用规则"}</span></summary>
              <div className="aq-risk-stock-body">{openRules.has(holding.code) ? renderRiskSettings(holding.code, openRules.get(holding.code)) : null}</div>
            </details>;
          })}
        </div>
      </section> : null}
    </div>
  );
}
