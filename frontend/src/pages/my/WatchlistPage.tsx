// 我的自选：我的线。自选列表只存当前浏览器（localStorage），不上传服务端。
//
// 对照用的额外列（换手率 / 市值 / PE）与「查看个股详情」入口都在这里 ——
// 原「我的测试」页的用途已并入本页，不再单独占一个导航项。
import { useEffect, useMemo, useState } from "react";
import { ExternalLink, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { addCodes, loadWatch, saveWatch } from "@/lib/watchlist";
import { api, type Quote } from "@/lib/api";
import { Badge, StatePanel } from "@/components/ui/primitives";
import { StockDetailDrawer } from "@/components/aqsp/StockDetailDrawer";
import { changeClass, formatSignedPct } from "@/lib/format";
import { cn } from "@/lib/utils";

export function WatchlistPage() {
  const [codes, setCodes] = useState<string[]>(() => loadWatch());
  const [raw, setRaw] = useState("");
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    if (codes.length === 0) {
      setQuotes({});
      return;
    }
    let alive = true;
    setLoading(true);
    api
      .quote(codes.join(","))
      .then((data) => {
        if (alive) {
          setQuotes(data);
          setError("");
        }
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "行情读取失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [codes]);

  const rows = useMemo(() => codes.map((code) => ({ code, quote: quotes[code] })), [codes, quotes]);

  function persist(next: string[]) {
    setCodes(next);
    saveWatch(next);
  }

  function submit() {
    const { next, added } = addCodes(codes, raw);
    persist(next);
    setRaw("");
    toast(added > 0 ? `已添加 ${added} 只` : "没有新的代码");
  }

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">AQSP · 我的线</p>
          <div className="aq-title-row">
            <h1>我的自选</h1>
          </div>
          <p className="aq-page-sub">共 {codes.length} 只 · 含换手率 / 市值 / PE，可直接查看个股详情</p>
        </div>
        <Badge tone="neutral">只存此浏览器</Badge>
      </header>

      <p className="aq-privacy-note">
        <ShieldCheck aria-hidden="true" />
        <span>
          自选股保存在你当前这台设备的浏览器里，不会上传服务器，因此别人访问看不到、也改不了你的列表。
          代价是换设备或清缓存会丢 —— 用侧栏底部的「导出备份」可以搬走。
        </span>
      </p>

      <div className="aq-toolbar">
        <input
          className="aq-input"
          value={raw}
          onChange={(event) => setRaw(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") submit();
          }}
          placeholder="粘贴 6 位代码，逗号 / 空格 / 换行都行，例如 600519,000858"
        />
        <button type="button" className="aq-btn aq-btn-primary" onClick={submit}>
          添加
        </button>
      </div>

      {error ? <StatePanel tone="warn">{error}，自选列表仍可使用。</StatePanel> : null}
      {loading ? <StatePanel>正在读取行情…</StatePanel> : null}

      {rows.length === 0 ? (
        <StatePanel>还没有自选股，在上方粘贴代码即可加入。</StatePanel>
      ) : (
        <div className="aq-table-wrap">
          <table className="aq-table">
            <thead>
              <tr>
                <th>代码 / 名称</th>
                <th className="aq-num">最新价</th>
                <th className="aq-num">涨跌幅</th>
                <th className="aq-num">换手率</th>
                <th className="aq-num">市值(亿)</th>
                <th className="aq-num">PE(TTM)</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map(({ code, quote }) => (
                <tr key={code}>
                  <td>
                    <strong>{quote?.name || code}</strong>
                    <div className="aq-code">{code}</div>
                  </td>
                  <td className="aq-num">{quote ? quote.price.toFixed(2) : "—"}</td>
                  <td className={cn("aq-num", quote && changeClass(quote.change_pct))}>
                    {quote ? formatSignedPct(quote.change_pct) : "—"}
                  </td>
                  <td className="aq-num">{quote ? `${quote.turnover_pct.toFixed(2)}%` : "—"}</td>
                  <td className="aq-num">{quote ? quote.mcap_yi.toFixed(1) : "—"}</td>
                  <td className="aq-num">{quote && quote.pe_ttm ? quote.pe_ttm.toFixed(1) : "—"}</td>
                  <td className="aq-num">
                    <div className="aq-row-actions">
                      <button
                        type="button"
                        className="aq-btn aq-btn-icon"
                        onClick={() => setPicked(code)}
                        title="查看个股详情"
                      >
                        <ExternalLink aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="aq-btn aq-btn-danger aq-btn-icon"
                        onClick={() => {
                          persist(codes.filter((item) => item !== code));
                          toast(`已移除 ${code}`);
                        }}
                        title="移除"
                      >
                        <Trash2 aria-hidden="true" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <StockDetailDrawer symbol={picked} onClose={() => setPicked(null)} />
    </div>
  );
}
