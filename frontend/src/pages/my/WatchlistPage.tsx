// 我的自选：我的线。数据只存当前浏览器（localStorage），不上传服务端。
import { useEffect, useMemo, useState } from "react";
import { ShieldCheck, Trash2 } from "lucide-react";
import { addCodes, loadWatch, saveWatch } from "@/lib/watchlist";
import { api, type Quote } from "@/lib/api";
import { cn } from "@/lib/utils";

export function WatchlistPage() {
  const [codes, setCodes] = useState<string[]>(() => loadWatch());
  const [raw, setRaw] = useState("");
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [hint, setHint] = useState("");

  useEffect(() => {
    if (codes.length === 0) {
      setQuotes({});
      return;
    }
    let alive = true;
    api
      .quote(codes.join(","))
      .then((data) => {
        if (alive) setQuotes(data);
      })
      .catch(() => {
        if (alive) setHint("行情读取失败，自选列表仍可使用");
      });
    return () => {
      alive = false;
    };
  }, [codes]);

  const rows = useMemo(
    () => codes.map((code) => ({ code, quote: quotes[code] })),
    [codes, quotes],
  );

  function persist(next: string[]) {
    setCodes(next);
    saveWatch(next);
  }

  return (
    <div className="aqsp-page">
      <div className="vr-line-head">
        <div>
          <h1>我的自选</h1>
          <p>共 {codes.length} 只</p>
        </div>
        <span className="vr-chip">只存此浏览器</span>
      </div>

      <p className="vr-private-note">
        <ShieldCheck className="h-4 w-4 shrink-0" />
        <span>
          自选股保存在你当前这台设备的浏览器里，不会上传服务器，因此别人访问看不到、也改不了你的列表。
          代价是换设备或清缓存会丢——用侧栏底部的「导出备份」可以搬走。
        </span>
      </p>

      <div className="vr-row" style={{ marginBottom: "1rem" }}>
        <input
          className="vr-input"
          value={raw}
          onChange={(event) => setRaw(event.target.value)}
          placeholder="粘贴 6 位代码，逗号 / 空格 / 换行都行，例如 600519,000858"
        />
        <button
          type="button"
          className="vr-button vr-button-primary"
          onClick={() => {
            const { next, added } = addCodes(codes, raw);
            persist(next);
            setRaw("");
            setHint(added > 0 ? `已添加 ${added} 只` : "没有新的代码");
          }}
        >
          添加
        </button>
      </div>

      {hint ? <p className="vr-private-note">{hint}</p> : null}

      {rows.length === 0 ? (
        <div className="vr-state-panel">还没有自选股。</div>
      ) : (
        <table className="vr-table">
          <thead>
            <tr>
              <th>代码 / 名称</th>
              <th>最新价</th>
              <th>涨跌幅</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map(({ code, quote }) => (
              <tr key={code}>
                <td>
                  <strong>{quote?.name || code}</strong>
                  <div className="vr-chip">{code}</div>
                </td>
                <td>{quote ? quote.price.toFixed(2) : "—"}</td>
                <td className={cn(quote && quote.change_pct >= 0 ? "vr-up" : "vr-down")}>
                  {quote ? `${quote.change_pct >= 0 ? "+" : ""}${quote.change_pct.toFixed(2)}%` : "—"}
                </td>
                <td>
                  <button
                    type="button"
                    className="vr-button vr-button-danger"
                    onClick={() => persist(codes.filter((item) => item !== code))}
                    title="移除"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
