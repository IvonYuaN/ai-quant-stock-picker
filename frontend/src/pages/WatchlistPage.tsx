// 自选：我关注的标的与实时价格。
//
// 2026-11-09 新增模块。自选代码存在本机浏览器（lib/watchlist.ts），
// 这里只做「看一眼现在什么价」的读表，不写任何交易状态。
import { useCallback, useEffect, useState } from "react";
import { Star } from "lucide-react";
import { EmptyState, LoadingState, SectionHeader, Tag } from "@/components/ui/primitives";
import { api, type Quote } from "@/lib/api";
import { loadWatch, saveWatch, WATCHLIST_CHANGED_EVENT } from "@/lib/watchlist";
import { cn } from "@/lib/utils";

export function WatchlistPage() {
  const [codes, setCodes] = useState<string[]>(() => loadWatch());
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const sync = useCallback(() => setCodes(loadWatch()), []);
  useEffect(() => {
    const onChange = () => sync();
    window.addEventListener(WATCHLIST_CHANGED_EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(WATCHLIST_CHANGED_EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, [sync]);

  useEffect(() => {
    if (codes.length === 0) {
      setQuotes({});
      return;
    }
    let alive = true;
    setLoading(true);
    api
      .quote(codes.join(","))
      .then((value) => {
        if (!alive) return;
        setQuotes(value ?? {});
        setError("");
      })
      .catch((reason: unknown) => {
        if (!alive) return;
        setError(reason instanceof Error ? reason.message : "读取行情失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, [codes.join(",")]);

  const remove = (code: string) => {
    const current = loadWatch();
    saveWatch(current.filter((item) => item !== code));
    sync();
  };

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><Star aria-hidden="true" /> AQSP · 决策模块</p>
          <div className="aq-title-row">
            <h1>自选</h1>
            <strong>{codes.length} 只</strong>
          </div>
          <p className="aq-page-sub">我关注的标的与最新价格；自选存在本机浏览器，不随访客身份同步。</p>
        </div>
      </header>

      <section className="aq-section" aria-label="自选列表">
        <SectionHeader number="01 · 自选" title="我的关注" description="最新成交价与涨跌；数据缺失时显示缺材料而不是补 0。" count={codes.length} />
        {error ? <p className="aq-note">行情读取失败：{error}</p> : null}
        {loading && codes.length > 0 ? <LoadingState label="正在读取最新价格…" /> : null}
        {codes.length === 0 ? (
          <EmptyState title="还没有自选" detail="在候选页点「加入自选」，或用搜索框（⌘K）加代码。" />
        ) : (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>标的</th>
                  <th className="aq-num">现价</th>
                  <th className="aq-num">涨跌</th>
                  <th className="aq-num">昨收</th>
                  <th>状态</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {codes.map((code) => {
                  const quote = quotes[code];
                  return (
                    <tr key={code}>
                      <td><strong>{quote?.name || code}</strong> <span className="aq-code">{code}</span></td>
                      <td className="aq-num">{quote ? quote.price.toFixed(2) : "—"}</td>
                      <td className={cn("aq-num", quote && (quote.change_pct >= 0 ? "aq-tone-up" : "aq-tone-down"))}>
                        {quote ? `${quote.change_pct >= 0 ? "+" : ""}${quote.change_pct.toFixed(2)}%` : "—"}
                      </td>
                      <td className="aq-num">{quote ? quote.last_close.toFixed(2) : "—"}</td>
                      <td>{quote ? <Tag tone="ok">有行情</Tag> : <Tag tone="warn">缺材料</Tag>}</td>
                      <td className="aq-num">
                        <button type="button" className="aq-btn aq-btn-danger aq-btn-icon" onClick={() => remove(code)} aria-label={`移出自选 ${code}`} title="移出自选">
                          ×
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
