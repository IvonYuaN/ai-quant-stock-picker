// 信号：策略信号台账 —— 每条信号何时发生、命中与否、各策略跑到多少独立信号日。
//
// 2026-11-10 新增模块。左栏此前缺「信号」这一层（TickFlow 有、主线没有），
// 用的都是后端已有的绩效台账数据（/api/performance），没有新造端点。
import { useEffect, useState } from "react";
import { Radio } from "lucide-react";
import { LoadingState, SectionHeader, Tag } from "@/components/ui/primitives";
import { api, type PerformancePayload } from "@/lib/api";
import { cn } from "@/lib/utils";

const EXIT_REASON: Readonly<Record<string, string>> = {
  horizon_close: "到期了结",
  take_profit: "止盈了结",
  stop_loss: "止损了结",
};

export function SignalsPage() {
  const [data, setData] = useState<PerformancePayload | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .performance()
      .then((value) => {
        if (!alive) return;
        setData(value);
        setError("");
      })
      .catch((reason: unknown) => {
        if (alive) setError(reason instanceof Error ? reason.message : "读取失败");
      });
    return () => { alive = false; };
  }, []);

  if (error) {
    return (
      <div className="aq-page">
        <p className="aq-note">信号台账读取失败：{error}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="aq-page">
        <LoadingState label="正在读取信号台账…" />
      </div>
    );
  }

  const freshness = data.freshness;
  const cold = data.cold_start;
  const picks = data.recent_picks ?? [];

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><Radio aria-hidden="true" /> AQSP · 研究模块</p>
          <div className="aq-title-row">
            <h1>信号</h1>
            <strong>{data.overall?.observations ?? 0} 条</strong>
          </div>
          <p className="aq-page-sub">策略信号的台账：何时发生、命中与否、各策略跑到多少独立信号日。</p>
        </div>
      </header>

      {!data.available ? (
        <section className="aq-section" aria-label="信号台账状态">
          <SectionHeader number="01 · 状态" title="台账尚未可用" description={data.reason || "未记录原因"} />
          {data.notes?.length ? (
            <ul className="aq-note-list">
              {data.notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          ) : null}
        </section>
      ) : null}

      <section className="aq-section" aria-label="信号新鲜度">
        <SectionHeader
          number="01 · 新鲜度"
          title="信号是否还在更新"
          description="最新信号日之后的交易日数；停滞过久时命中率不再代表当前策略。"
          count={freshness?.latest_signal_date || "未记录"}
        />
        <div className="aq-fact-grid">
          <p><b>最新信号日</b><span>{freshness?.latest_signal_date || "未记录"}</span></p>
          <p><b>停滞交易日</b><span>{freshness?.trading_days_since_latest ?? "—"} 天{freshness?.stale ? "（已超阈值）" : ""}</span></p>
          <p><b>独立信号日</b><span>{cold?.independent_signal_days ?? 0} / 门槛 {cold?.min_independent_signal_days ?? "—"}</span></p>
          <p><b>台账生成</b><span>{data.generated_at || "未记录"}</span></p>
        </div>
        {freshness?.stale ? (
          <p role="status" className="aq-note aq-tone-warn">
            台账已停滞 {freshness.trading_days_since_latest} 个交易日，下面的命中率不能当作当前策略水平。
          </p>
        ) : null}
      </section>

      <section className="aq-section" aria-label="策略信号台账">
        <SectionHeader
          number="02 · 策略"
          title="各策略的信号表现"
          description="独立信号日不足门槛时不展示命中率（冷启动期）；平均收益只作观测，不作主指标。"
          count={data.strategies?.length}
        />
        {data.strategies?.length ? (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>策略</th>
                  <th className="aq-num">独立信号日</th>
                  <th className="aq-num">命中数</th>
                  <th className="aq-num">命中率</th>
                  <th className="aq-num">平均收益</th>
                  <th className="aq-num">权重</th>
                </tr>
              </thead>
              <tbody>
                {data.strategies.map((row) => (
                  <tr key={row.name}>
                    <td><strong>{row.name}</strong></td>
                    <td className="aq-num">{row.independent_signal_days}</td>
                    <td className="aq-num">{row.win_count} / {row.total_picks}</td>
                    <td className="aq-num">
                      {row.displayable ? `${(row.hit_rate * 100).toFixed(1)}%` : "积累中"}
                    </td>
                    <td className={cn("aq-num", row.avg_return_pct >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                      {row.avg_return_pct >= 0 ? "+" : ""}{row.avg_return_pct.toFixed(2)}%
                    </td>
                    <td className="aq-num">{(row.weight_base * row.weight_confidence).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="aq-note">还没有策略级信号记录。</p>}
        {data.decay_alerts?.length ? (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>策略</th>
                  <th>严重度</th>
                  <th className="aq-num">近段胜率</th>
                  <th className="aq-num">近段平均收益</th>
                  <th>建议</th>
                </tr>
              </thead>
              <tbody>
                {data.decay_alerts.map((alert) => (
                  <tr key={alert.strategy}>
                    <td><strong>{alert.strategy}</strong></td>
                    <td><Tag tone={alert.severity === "high" ? "warn" : "neutral"}>{alert.severity}</Tag></td>
                    <td className="aq-num">{(alert.recent_win_rate * 100).toFixed(1)}%</td>
                    <td className={cn("aq-num", alert.recent_avg_return_pct >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                      {alert.recent_avg_return_pct >= 0 ? "+" : ""}{alert.recent_avg_return_pct.toFixed(2)}%
                    </td>
                    <td>{alert.recommendation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="aq-section" aria-label="最近信号">
        <SectionHeader
          number="03 · 明细"
          title="最近信号明细"
          description="按信号日从新到旧；收益与超额为该笔已了结信号的结果。"
          count={picks.length}
        />
        {picks.length ? (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>信号日</th>
                  <th>标的</th>
                  <th className="aq-num">收益</th>
                  <th className="aq-num">超额</th>
                  <th>结果</th>
                  <th>了结原因</th>
                  <th>策略</th>
                </tr>
              </thead>
              <tbody>
                {picks.map((pick) => (
                  <tr key={`${pick.signal_date}-${pick.symbol}`}>
                    <td>{pick.signal_date}</td>
                    <td><strong>{pick.name}</strong> <span className="aq-code">{pick.symbol}</span></td>
                    <td className={cn("aq-num", (pick.return_pct ?? 0) >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                      {pick.return_pct === null ? "—" : `${pick.return_pct >= 0 ? "+" : ""}${pick.return_pct.toFixed(2)}%`}
                    </td>
                    <td className={cn("aq-num", (pick.excess_return_pct ?? 0) >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                      {pick.excess_return_pct === null ? "—" : `${pick.excess_return_pct >= 0 ? "+" : ""}${pick.excess_return_pct.toFixed(2)}%`}
                    </td>
                    <td><Tag tone={pick.win ? "ok" : "warn"}>{pick.win ? "命中" : "未命中"}</Tag></td>
                    <td>{EXIT_REASON[pick.exit_reason] || pick.exit_reason || "未了结"}</td>
                    <td>{pick.strategies?.join(" · ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="aq-note">还没有可展示的已了结信号。</p>}
      </section>
    </div>
  );
}
