// 我的测试：我的线。手动挑几只股票做对照，和系统推荐分开看。
import { useEffect, useState } from "react";
import { Plus, ShieldCheck } from "lucide-react";
import { api, type Quote } from "@/lib/api";
import { addCodes, loadWatch, saveWatch } from "@/lib/watchlist";
import { cn } from "@/lib/utils";

export function MyLabPage() {
  const [raw, setRaw] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

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

  return (
    <div className="aqsp-page">
      <div className="vr-line-head">
        <div>
          <h1>我的测试</h1>
          <p>手动选股对照，不影响系统推荐</p>
        </div>
        <span className="vr-chip">只存此浏览器</span>
      </div>

      <p className="vr-private-note">
        <ShieldCheck className="h-4 w-4 shrink-0" />
        <span>这里的对照列表只存在本地，是给你自己试想法用的，不会进入系统线的正式结论。</span>
      </p>

      <div className="vr-row" style={{ marginBottom: "1rem" }}>
        <input
          className="vr-input"
          value={raw}
          onChange={(event) => setRaw(event.target.value)}
          placeholder="输入 6 位代码做对照，例如 600519 000858 300750"
        />
        <button
          type="button"
          className="vr-button vr-button-primary"
          onClick={() => {
            const parsed = raw.match(/\d{6}/g) ?? [];
            setCodes(Array.from(new Set(parsed)));
            setRaw("");
          }}
        >
          对照
        </button>
      </div>

      {error ? <div className="vr-state-panel vr-state-panel-warning">{error}</div> : null}
      {loading ? <div className="vr-state-panel">正在读取行情</div> : null}

      {!loading && codes.length > 0 && Object.keys(quotes).length > 0 ? (
        <table className="vr-table">
          <thead>
            <tr>
              <th>代码 / 名称</th>
              <th>最新价</th>
              <th>涨跌幅</th>
              <th>换手率</th>
              <th>市值(亿)</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {codes.map((code) => {
              const quote = quotes[code];
              if (!quote) return null;
              return (
                <tr key={code}>
                  <td>
                    <strong>{quote.name || code}</strong>
                    <div className="vr-chip">{code}</div>
                  </td>
                  <td>{quote.price.toFixed(2)}</td>
                  <td className={cn(quote.change_pct >= 0 ? "vr-up" : "vr-down")}>
                    {quote.change_pct >= 0 ? "+" : ""}
                    {quote.change_pct.toFixed(2)}%
                  </td>
                  <td>{quote.turnover_pct.toFixed(2)}%</td>
                  <td>{quote.mcap_yi.toFixed(1)}</td>
                  <td>
                    <button
                      type="button"
                      className="vr-button"
                      onClick={() => {
                        const { next } = addCodes(loadWatch(), code);
                        saveWatch(next);
                      }}
                      title="加入自选"
                    >
                      <Plus className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
