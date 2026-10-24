// 自选继续研究：浏览器私有列表，与所选日期的研究快照关联。
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Plus, RefreshCw, Star, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  addCodes, loadWatch, saveWatch, WATCHLIST_CHANGED_EVENT, WATCHLIST_STORAGE_KEY,
} from "@/lib/watchlist";
import { api, type Quote } from "@/lib/api";
import { Badge, EmptyState, StatePanel, Tag } from "@/components/ui/primitives";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { buildDailyView } from "@/lib/daily-view";
import { statusLabel } from "@/lib/display-labels";
import { changeClass, formatSignedPct } from "@/lib/format";
import { cn } from "@/lib/utils";

function quoteNumber(value: number | undefined, digits = 2, suffix = ""): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : "—";
}

export function WatchlistPage() {
  const [codes, setCodes] = useState<string[]>(() => loadWatch());
  const [raw, setRaw] = useState("");
  const [quoteState, setQuoteState] = useState<{ key: string; data: Record<string, Quote> }>({ key: "", data: {} });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const { data: snapshot, loading: snapshotLoading, error: snapshotError, switching, selectedDate } = useWorkspaceSnapshot();
  const view = useMemo(() => snapshot && !switching ? buildDailyView(snapshot) : null, [snapshot, switching]);
  const codesKey = codes.join(",");

  useEffect(() => {
    const sync = () => {
      const next = loadWatch();
      setCodes((previous) => previous.join(",") === next.join(",") ? previous : next);
    };
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
    let alive = true;
    setQuoteState({ key: codesKey, data: {} });
    setError("");
    if (!codesKey) {
      setLoading(false);
      return;
    }
    setLoading(true);
    api.quote(codesKey)
      .then((data) => {
        if (alive) setQuoteState({ key: codesKey, data });
      })
      .catch((reason: unknown) => {
        if (alive) setError(reason instanceof Error ? reason.message : "行情读取失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, [codesKey, reload]);

  const candidateByCode = useMemo(() => new Map(view?.candidates.map((row) => [row.symbol, row]) ?? []), [view]);
  const matchedCount = codes.filter((code) => candidateByCode.has(code)).length;

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
      toast.error("自选保存失败，请检查浏览器存储后重试；原列表仍保留。");
      return false;
    }
  }

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const { next, added } = addCodes(codes, raw);
    if (!added) {
      toast("没有新的有效代码，请输入 6 位 A 股代码。");
      return;
    }
    if (persist(next)) {
      setRaw("");
      toast(`已关注 ${added} 只股票`);
    }
  }

  const researchDate = view?.date || selectedDate;
  const researchLink = (code: string) => `/stock/${code}${researchDate ? `?date=${encodeURIComponent(researchDate)}` : ""}`;

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><Star aria-hidden="true" /> AQSP · 个人跟踪</p>
          <div className="aq-title-row"><h1>我的自选</h1></div>
          <p className="aq-page-sub">继续核对关注标的的研究依据与风险。</p>
        </div>
        <Badge tone="neutral">只存此浏览器</Badge>
      </header>

      <div className="aq-watch-summary" aria-label="自选摘要">
        <span><b>{codes.length}</b> 只关注</span>
        {view ? (
          <>
            <span><b>{matchedCount}</b> 只进入所选日候选</span>
            <span>研究对照 <b>{researchDate}</b> · {view.isHistorical ? "历史记录" : "当前快照"}</span>
          </>
        ) : <span>{snapshotLoading || switching ? "正在读取研究对照…" : "研究快照暂不可用"}</span>}
      </div>

      <form className="aq-watch-entry" onSubmit={submit}>
        <label htmlFor="watchlist-codes">添加关注代码</label>
        <div className="aq-row-actions">
          <input
            id="watchlist-codes"
            className="aq-input"
            value={raw}
            onChange={(event) => setRaw(event.target.value)}
            placeholder="600519, 000858"
            aria-describedby="watchlist-input-help"
            autoComplete="off"
          />
          <button type="submit" className="aq-btn aq-btn-primary"><Plus aria-hidden="true" /> 添加</button>
          <button
            type="button"
            className="aq-btn"
            onClick={() => setReload((value) => value + 1)}
            disabled={loading || !codes.length}
          >
            <RefreshCw className={cn(loading && "aq-spin")} aria-hidden="true" />
            刷新行情
          </button>
        </div>
        <p id="watchlist-input-help" className="aq-note">支持逗号、空格或换行分隔。自选列表保留在此浏览器；移出动作会留下当前身份的审计记录。</p>
      </form>

      {error ? (
        <StatePanel tone="warn">
          行情读取失败：{error}。关注记录仍保留。
          <button type="button" className="aq-btn" onClick={() => setReload((value) => value + 1)} disabled={loading}>
            <RefreshCw aria-hidden="true" /> 重试行情
          </button>
        </StatePanel>
      ) : null}
      {snapshotError ? <StatePanel tone="warn">研究对照更新失败：{snapshotError}。{view ? "保留上次读取的研究记录。" : "仍可查看自选和行情。"}</StatePanel> : null}

      {codes.length === 0 ? (
        <EmptyState
          title="还没有关注标的"
          detail="从候选研究中加入自选，或在上方输入股票代码。"
          action={<Link className="aq-btn aq-btn-primary" to="/today">查看研究候选 <ArrowRight aria-hidden="true" /></Link>}
        />
      ) : (
        <>
          <p className="aq-note">行情为当前接口快照，与 {researchDate || "所选日期"} 的研究记录分开展示，不作为历史行情证据。{loading ? "行情读取中…" : ""}</p>
          <ul className="aq-watch-list" aria-label="已关注股票">
            {codes.map((code) => {
              const quote = quoteState.key === codesKey ? quoteState.data[code] : undefined;
              const candidate = candidateByCode.get(code);
              const validChange = typeof quote?.change_pct === "number" && Number.isFinite(quote.change_pct);
              return (
                <li className="aq-watch-row" key={code}>
                  <div className="aq-watch-identity">
                    <Link to={researchLink(code)}><strong>{candidate?.name || quote?.name || code}</strong></Link>
                    <span className="aq-code">{code}</span>
                  </div>
                  <dl className="aq-watch-metrics">
                    <div><dt>现价</dt><dd className="aq-num">{quoteNumber(quote?.price)}</dd></div>
                    <div><dt>涨跌幅</dt><dd className={cn("aq-num", validChange && changeClass(quote!.change_pct))}>{validChange ? formatSignedPct(quote!.change_pct) : "—"}</dd></div>
                    <div><dt>换手率</dt><dd className="aq-num">{quoteNumber(quote?.turnover_pct, 2, "%")}</dd></div>
                  </dl>
                  <div className="aq-watch-research">
                    {candidate ? (
                      <>
                        <div className="aq-tag-row"><Tag tone="primary">所选日候选</Tag><Tag>{statusLabel(candidate.status)}</Tag></div>
                        <p>确定性评分 <b>{candidate.scoreText}</b> · {candidate.ready ? "研究材料齐备" : "研究材料待补"}</p>
                        {candidate.nextStep ? <p className="aq-note">下一观察：{candidate.nextStep}</p> : null}
                      </>
                    ) : <p className="aq-note">{view ? `未进入 ${researchDate} 候选；可继续查看该日研究记录。` : "研究对照尚未读取，不推断候选状态。"}</p>}
                  </div>
                  <div className="aq-watch-actions">
                    <Link className="aq-btn" to={researchLink(code)}>继续研究 <ArrowRight aria-hidden="true" /></Link>
                    <button
                      type="button"
                      className="aq-btn aq-btn-icon"
                      title={`移除 ${candidate?.name || quote?.name || code}`}
                      aria-label={`移除 ${candidate?.name || quote?.name || code}`}
                      onClick={() => {
                        if (persist(codes.filter((item) => item !== code))) toast(`已移除 ${code}`);
                      }}
                    ><Trash2 aria-hidden="true" /></button>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}
