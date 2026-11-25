// 回测：研究变体台账、研究链覆盖，以及短线影子账户的前向验证记录。
//
// 2026-11-09 新增模块。此前变体数据只作为候选表里的一列出现，看不到收益、
// 成交与阻断原因；这里把台账摊平成一张可排序对照的表。
// 2026-11-19 增补「短线影子账户」：与研究变体并列，但口径不同（见组件注释）。
import { useEffect, useMemo, useState } from "react";
import { BarChart3 } from "lucide-react";
import { EmptyState, HeroState, LoadingState, SectionHeader, StatePanel, Tag, ToneCallout } from "@/components/ui/primitives";
import { EChart } from "@/components/ui/EChart";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { SnapshotRefreshWarning } from "@/components/aqsp/SnapshotRefreshWarning";
import { api, type PaperArenaPayload } from "@/lib/api";
import { paperNavOption } from "@/lib/chart-options";
import { buildDailyView } from "@/lib/daily-view";
import { sleeveVerdict, unknownExcessCount } from "@/lib/paper-arena-view";
import { useThemeMode } from "@/lib/theme-mode";
import { cn } from "@/lib/utils";

/** 短线影子账户：多个互不相同的策略各一个 ¥1 万纸面账户，纯前向验证。
 *
 * 与上方「研究变体」的区别（页面必须讲清楚，否则两个纸面账户会混读）：
 * - 研究变体 = 历史区间上的样本外验证；
 * - 影子账户 = 从起跑日开始**只往后加**，不回填、不复用任何回测结果。
 *
 * 判读只看超额列：下跌市里绝对收益也能为正，那是 β 不是 α。
 */
function ShortlineArena() {
  const mode = useThemeMode();
  const [arena, setArena] = useState<PaperArenaPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  // 独立拉取：产物缺失时后端返回 available=false（不是错误），
  // 所以只把真正的请求失败当 error，避免"还没开始跑"被渲染成"读取失败"。
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    api.paperArena()
      .then((payload) => { if (current) setArena(payload); })
      .catch((reason) => { if (current) setError(reason instanceof Error ? reason.message : "影子账户读取失败"); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [reloadKey]);

  const navOption = useMemo(
    () => paperNavOption(arena?.nav ?? { dates: [], port: [], bench: [] }, mode),
    [arena, mode],
  );

  const title = "短线影子账户";
  const blurb = "每个策略一个独立纸面账户的前向验证记录。";
  const reload = () => setReloadKey((value) => value + 1);

  if (loading && !arena) {
    return <section className="aq-section"><SectionHeader title={title} description={blurb} /><LoadingState label="读取影子账户…" /></section>;
  }
  if (error) {
    return <section className="aq-section"><SectionHeader title={title} description={blurb} count="读取失败" /><StatePanel tone="warn">影子账户状态未知：{error}<button className="aq-btn" type="button" onClick={reload}>重试</button></StatePanel></section>;
  }
  if (!arena || !arena.available) {
    return <section className="aq-section"><SectionHeader title={title} description={blurb} count="未就绪" /><EmptyState title="尚未收到影子账户产物" detail={arena?.reason || "runner 还没有推送前向验证结果；此时不显示任何数字，也不以回测结果代替。"} /></section>;
  }

  const sleeves = arena.sleeves;
  // 已结算笔数合计：为 0 时任何"超额"都不是证据（首日组合恒为 0%，超额恰好等于基准的反数，
  // 直接展示会让人以为策略抗跌赚钱 —— 这正是这类记录最容易自欺的地方）。
  const settled = sleeves.reduce((sum, row) => sum + row.n, 0);
  // 「扛住多重检验」是**肯定**结论，超额未知不能计入；未知个数单独披露（见下方卡片）。
  const survived = sleeves.filter((row) => sleeveVerdict(row).kind === "survived");
  const unknownExcess = unknownExcessCount(sleeves);
  const navPoints = arena.nav.dates.length;
  const benchLast = arena.nav.bench.length ? arena.nav.bench[arena.nav.bench.length - 1] : null;
  const retPct = arena.total_ret_pct;
  const edge = retPct != null && benchLast != null ? retPct - benchLast : null;
  const money = (value: number | null | undefined) => (value == null || !Number.isFinite(value) ? "—" : `¥${Math.round(value).toLocaleString("zh-CN")}`);
  const pct = (value: number | null | undefined, digits = 2) => (value == null || !Number.isFinite(value) ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`);
  // 0 不给颜色：+0.00% 染成红字等于把"没有变化"说成"上涨"。
  const tone = (value: number | null | undefined) => (value == null || !Number.isFinite(value) || value === 0 ? "" : value > 0 ? "aq-tone-up" : "aq-tone-down");

  return <section className="aq-section" aria-label="短线影子账户">
    <SectionHeader
      title={title}
      description={`${sleeves.length} 个互不相同的短线策略各开一个 ¥1 万纸面账户，同一天起跑、只用真实报价成交、不回填。判据全部是相对全市场等权基准的超额；t 值按交易日聚类（同一天多个信号先平均），q 值是 ${sleeves.length} 个策略一起做的 BH-FDR 校正。与上方研究变体的区别：研究变体验证的是历史区间，这里只从起跑日往后累加。`}
      count={`${sleeves.length} 个策略 · ${arena.trading_days ?? 0} 个交易日 · ${settled} 笔已结算`}
    />
    {arena.stale ? <StatePanel tone="warn">runner 最近一次推送是 {(arena.generated_at || "").slice(0, 10) || "多日之前"}，下面的数字停在最后一次收盘；周末与长假属正常。</StatePanel> : null}
    <div className="aq-total-grid">
      <div className="aq-total-card"><span>组合虚拟净值</span><b className={tone(retPct)}>{pct(retPct)}</b>{settled === 0 ? <small className="aq-note">{arena.positions.length ? `已有 ${arena.positions.length} 个持仓，还没到结算日（T+1 收盘才出结果）` : "尚未建仓，0% 只是起点"}</small> : benchLast == null ? null : <small className="aq-note">全市场等权 {pct(benchLast)}</small>}</div>
      <div className="aq-total-card"><span>超额（组合 − 基准）</span>{settled === 0 ? <><b>尚无成交</b><small className="aq-note">一笔都没结算时，组合与基准的差值不构成证据</small></> : <><b className={tone(edge)}>{pct(edge)}</b>{benchLast == null ? null : <small className="aq-note">基准同期 {pct(benchLast)}</small>}</>}</div>
      <div className="aq-total-card"><span>当前总净值</span><b>{money(arena.total_nav)}</b><small className="aq-note">{sleeves.length} 个账户各 {money(arena.initial_capital)} 起</small></div>
      <div className="aq-total-card"><span>扛住多重检验的策略</span>{settled === 0 ? <><b>—</b><small className="aq-note">还没有结算样本，不能说 0 个通过（也没到能判的时候）</small></> : <><b>{survived.length} / {sleeves.length}</b><small className="aq-note">q &lt; 0.10 且超额为正{unknownExcess ? `；另有 ${unknownExcess} 个策略超额未知，未计入` : ""}</small></>}</div>
    </div>
    {navPoints >= 2 ? <EChart option={navOption} height={260} fallback={<p className="aq-detail-muted">当前环境不支持图表；下表的超额与组合收益仍可复核。</p>} /> : <p className="aq-note">净值曲线要等第二个收盘日才有形状；现在只有一个起点，画出来会是一条毫无信息的横线。</p>}
    {sleeves.length ? <div className="aq-table-wrap">
      <table className="aq-table aq-summary-table">
        <thead><tr><th>策略</th><th className="aq-num">超额均收益</th><th className="aq-num">超额胜率</th><th className="aq-num">T（按日聚类）</th><th className="aq-num">Q（FDR·{sleeves.length} 策略）</th><th className="aq-num">止损影响</th><th className="aq-num">统计进度</th><th className="aq-num">组合收益</th></tr></thead>
        <tbody>{sleeves.map((row) => {
          const notes = [row.sealed_n ? `检测时已封死 ${row.sealed_n}（照买）` : "", ...Object.entries(row.unfunded).slice(0, 2).map(([key, value]) => `${key} ${value}`)].filter(Boolean).join(" · ");
          const verdict = sleeveVerdict(row);
          return <tr key={row.id}>
            <td><strong>{row.name}</strong> <Tag tone={verdict.tone} title={verdict.kind === "unknown" ? verdict.detail : undefined}>{verdict.label}</Tag><p className="aq-note">{row.hypothesis}{notes ? ` · ${notes}` : ""}</p></td>
            <td className={cn("aq-num", tone(row.exc_mean))}>{row.n === 0 ? "—" : pct(row.exc_mean)}</td>
            <td className="aq-num">{row.exc_win == null || row.n === 0 ? "—" : `${(row.exc_win * 100).toFixed(1)}%`}</td>
            <td className="aq-num">{row.t_exc == null || row.n === 0 ? "—" : row.t_exc.toFixed(2)}</td>
            <td className="aq-num">{row.q_exc == null || row.n === 0 ? "—" : row.q_exc.toFixed(3)}</td><td className="aq-num">{row.stop_impact == null ? "—" : <span className={row.stop_impact < 0 ? "down" : "up"}>{row.stop_impact.toFixed(2)}pp{row.stop_n ? <span className="aq-note"> · {row.stop_n}笔</span> : null}</span>}</td>
            <td className="aq-num">{row.days} 日{row.n_need_days ? ` / 需 ${row.n_need_days}` : ""}</td>
            <td className={cn("aq-num", tone(row.funded_ret))}>{row.funded_n === 0 ? "—" : pct(row.funded_ret)}</td>
          </tr>;
        })}</tbody>
      </table>
    </div> : null}
    {arena.positions.length ? <>
      <p className="aq-label">当前持仓（{arena.positions.length}）</p>
      <div className="aq-table-wrap"><table className="aq-table aq-summary-table"><thead><tr><th>账户</th><th>标的</th><th>买入日</th><th className="aq-num">成本价</th><th className="aq-num">股数</th><th>备注</th></tr></thead><tbody>{arena.positions.map((row) => <tr key={`${row.sleeve}-${row.code}`}><td><span className="aq-code">{row.sleeve}</span></td><td>{row.name} <span className="aq-code">{row.code}</span></td><td>{row.buy_date || "—"}</td><td className="aq-num">{row.buy_price == null ? "—" : row.buy_price.toFixed(2)}</td><td className="aq-num">{row.shares}</td><td>{row.note || "—"}</td></tr>)}</tbody></table></div>
    </> : null}
    {arena.recent_trades.length ? <>
      <p className="aq-label">最近成交（{arena.recent_trades.length}）</p>
      <div className="aq-table-wrap"><table className="aq-table aq-summary-table"><thead><tr><th>账户</th><th>标的</th><th>卖出日</th><th className="aq-num">单笔收益</th><th className="aq-num">单笔超额</th><th className="aq-num">若不止损</th><th>卖出原因</th></tr></thead><tbody>{arena.recent_trades.map((row, index) => <tr key={`${row.sleeve}-${row.code}-${row.sell_date}-${index}`}><td><span className="aq-code">{row.sleeve}</span></td><td>{row.name} <span className="aq-code">{row.code}</span></td><td>{row.sell_date || "—"}</td><td className={cn("aq-num", tone(row.ret_pct))}>{pct(row.ret_pct)}</td><td className={cn("aq-num", tone(row.excess_pct))}>{pct(row.excess_pct)}</td><td className={cn("aq-num", tone(row.no_stop_ret_pct))}>{pct(row.no_stop_ret_pct)}</td><td>{row.reason || "—"}{row.funded ? "" : "（未占仓）"}</td></tr>)}</tbody></table></div>
    </> : null}
    {/* 用 p 而不是 ul：`.aq-note` 是 flex 容器，套在 ul 上会把 li 压成横向乱文。 */}
    <p className="aq-note">· 所有判据都是<strong>超额</strong>：每笔先减掉「买入日 → 卖出日」的全市场等权收益，剩下的才算策略。组合收益那一列是绝对收益，只能对照着看。</p>
    <p className="aq-note">· 打板只买 B 格（集合竞价没顶死 + 盘中摸板）。A 格一字排不进，只进观察不进样本；检测到信号的那一刻通常已经封死，但回测过门的那批本来就是「收停板」，所以封死只作标记、单独计数，<strong>不拦买入</strong> —— 拦了等于把作业集整个抹掉，账本会连着几个月 0 成交。</p>
    <p className="aq-note">· 一字跌停卖不掉：顺延持有（最多 10 个交易日），不按当日收盘假装成交。</p>
    <p className="aq-note">· 多策略一起测就要付多重检验的代价（m = 上面的策略数），单看某一个的 t=2 不算；「扛住」那一栏按 q &lt; 0.10 判定。</p>
    <p className="aq-note">· ¥1 万硬约束：单笔 20% 只有 ¥2000，高价股连 1 手都买不起，这类信号照记但标「买不起 1 手」。</p>
  </section>;
}

export function BacktestPage() {
  const { data, error, switching } = useWorkspaceSnapshot();
  const view = data && !switching ? buildDailyView(data) : null;

  if (error && !data) {
    return <div className="aq-page"><HeroState tone="warn" title="读不到研究快照" detail={error} /><ShortlineArena /></div>;
  }
  if (!view || !data) {
    return <div className="aq-page"><LoadingState label={switching ? "正在切换研究日期…" : "正在读取回测台账…"} /><ShortlineArena /></div>;
  }

  const variants = [...(data.variants ?? [])].sort(
    (a, b) => (a.rank ?? Number.MAX_SAFE_INTEGER) - (b.rank ?? Number.MAX_SAFE_INTEGER),
  );
  const suite = data.variant_suite;
  const chain = data.research_chain;
  const gate = data.recommendation_gate;

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><BarChart3 aria-hidden="true" /> AQSP · 研究模块</p>
          <div className="aq-title-row">
            <h1>回测</h1>
            <strong>{view.date}</strong>
          </div>
          <p className="aq-page-sub">研究变体的收益、成交与阻断原因。</p>
        </div>
      </header>

      <SnapshotRefreshWarning
        error={error}
        hasData={Boolean(data)}
        switching={switching}
        generatedAt={data?.generated_at ?? ""}
      />

      {gate ? (
        <ToneCallout
          tone={gate.recommendation_allowed ? "ok" : "warn"}
          title={gate.recommendation_allowed ? "门禁放行" : "门禁未放行"}
          detail={gate.reasons.length ? gate.reasons.join(" · ") : "未记录阻断原因"}
        />
      ) : null}

      <section className="aq-section" aria-label="回测台账">
        <SectionHeader
          number="01 · 变体台账"
          title="研究变体对照"
          description={suite ? `生成于 ${suite.generated_at} · 数据模式 ${suite.data_mode} · 变体 ${suite.variant_count} 个` : "变体台账"}
          count={variants.length}
        />
        {variants.length === 0 ? (
          <p className="aq-note">当前快照没有变体记录。</p>
        ) : (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th className="aq-num">#</th>
                  <th>变体</th>
                  <th className="aq-num">收益</th>
                  <th className="aq-num">期末权益</th>
                  <th className="aq-num">成交/拒单</th>
                  <th>区间</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {variants.map((variant) => (
                  <tr key={variant.variant_id}>
                    <td className="aq-num">{variant.rank ?? "—"}</td>
                    <td><strong>{variant.label}</strong></td>
                    <td className={cn("aq-num", variant.return_pct >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                      {variant.return_pct >= 0 ? "+" : ""}{variant.return_pct.toFixed(2)}%
                    </td>
                    <td className="aq-num">{variant.final_equity.toFixed(0)}</td>
                    <td className="aq-num">{variant.filled_orders} / {variant.rejected_orders}</td>
                    <td>{variant.start_date} → {variant.end_date}</td>
                    <td>{variant.lifecycle_status ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="aq-section" aria-label="研究链覆盖">
        <SectionHeader
          number="02 · 研究链"
          title="候选到复核的链路覆盖"
          description="当天候选有多少只走到了讨论复核、多少只被历史实验覆盖。"
          count={chain ? chain.status : "未记录"}
        />
        {chain ? (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>环节</th>
                  <th className="aq-num">只数</th>
                  <th>标的</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["当天候选", chain.candidate_symbols],
                  ["讨论复核", chain.debated_symbols],
                  ["待复核", chain.pending_review_symbols],
                  ["历史实验覆盖", chain.variant_candidate_symbols],
                  ["实验持仓复核", chain.variant_holding_candidate_symbols],
                ].map(([label, symbols]) => (
                  <tr key={String(label)}>
                    <td><strong>{label}</strong></td>
                    <td className="aq-num">{(symbols as readonly string[]).length}</td>
                    <td>{(symbols as readonly string[]).join(" · ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="aq-note">当前快照没有研究链记录。</p>
        )}
        {chain?.blocker ? <p className="aq-note">阻断原因：{chain.blocker}</p> : null}
      </section>

      <ShortlineArena />
    </div>
  );
}
