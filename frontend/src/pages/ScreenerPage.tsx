// 策略：当前候选命中了哪些策略假设、每条策略覆盖多少只。
//
// 2026-11-09 新增模块。做法参考 investment-news：左栏是模块切换器，点进这一屏
// 就要能一次看完这个问题的全部数据，而不是再点一层。
import { useEffect, useState } from "react";
import { ListFilter, ShieldCheck } from "lucide-react";
import { HeroState, LoadingState, SectionHeader } from "@/components/ui/primitives";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { buildDailyView } from "@/lib/daily-view";
import { strategyCounts } from "@/lib/candidate-view";
import { statusLabel } from "@/lib/display-labels";
import { api, type FilterVerdict, type FilterVerdictRow, type MarketRisk } from "@/lib/api";

// 「筛选器判读」快照（固化自 research/pit_factors/筛选器定位_决策.md，2026-11-16 锁定）。
// 定位 = 选股筛选工具：不承诺正超额，只要求「可行/稳健」。
// 动态链路：优先读 /api/aqsp/filter-verdict（runner 重算探针写出的 pit_cache 工件，低频更新）；
// 端点缺失/失败时回退本常量作兜底快照，并在界面上标注 as-of。
const FILTER_VERDICT = {
  viable: true,
  baseline: "双反向 2f · mom_20 + pv_corr_20 均反向 · top10 生产池",
  positioning: "选股筛选工具：不承诺正超额，只要求「不是垃圾策略」",
  criterion: "多数 regime / 多数年度下，相对同截面池等权的超额「不显著为负」（Newey-West t > −1.96，双侧 95%）",
  dynamicSwitching: false,
  dynamicNote: "各 regime 最优方向漂移（h=20：熊→A / 震荡→D / 牛→D）⇒ 不做动态切换（过拟合风险）",
  evidence: [
    { horizon: 10, regime: "熊", excess: "+0.30%", t: "+0.50", ok: true },
    { horizon: 10, regime: "震荡", excess: "−0.76%", t: "−0.92", ok: true },
    { horizon: 10, regime: "牛", excess: "−0.62%", t: "−1.17", ok: true },
    { horizon: 20, regime: "熊", excess: "+0.71%", t: "+0.92", ok: true },
    { horizon: 20, regime: "震荡", excess: "−1.05%", t: "−1.67", ok: true },
    { horizon: 20, regime: "牛", excess: "−0.79%", t: "−1.39", ok: true },
  ],
} as const;

const RISK_LEVEL_LABEL: Record<string, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

export function ScreenerPage() {
  const { data, error, switching } = useWorkspaceSnapshot();
  const view = data && !switching ? buildDailyView(data) : null;

  // 02 节「当前市场面」上下文：复用既有 market-risk 只读端点（risk_warning_only，不用于择时 alpha）。
  // 市场级快照与所选研究日期/候选无关 ⇒ 无条件调用（必须放在所有条件 return 之前，React hooks 规则）。
  // 失败/无数据显示「暂无数据」，不阻塞 02 节主体（筛选器判读是固化研究结论，不依赖此端点）。
  const [marketRisk, setMarketRisk] = useState<MarketRisk | null>(null);
  const [marketRiskError, setMarketRiskError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    api.marketRisk()
      .then((snap) => { if (active) setMarketRisk(snap); })
      .catch(() => { if (active) setMarketRiskError("当前市场面读取失败"); });
    return () => { active = false; };
  }, []);

  // 02 节「筛选器判读」判决工件：读 /api/aqsp/filter-verdict（runner 重算探针才更新的低频研究结论）。
  // 与所选日期无关 ⇒ 无条件调用（hooks 规则：放所有条件 return 前）。端点缺失/失败不阻塞 02 主体，
  // 渲染时按 available 回退硬编码 FILTER_VERDICT 快照（见下方 `verdict` 选择）。
  const [filterVerdict, setFilterVerdict] = useState<FilterVerdict | null>(null);
  useEffect(() => {
    let active = true;
    api.filterVerdict()
      .then((v) => { if (active) setFilterVerdict(v); })
      .catch(() => { /* fail-soft：端点/网络异常视为未取到，渲染时回退快照 */ });
    return () => { active = false; };
  }, []);

  if (error && !data) {
    return <div className="aq-page"><HeroState tone="warn" title="读不到候选快照" detail={error} /></div>;
  }
  if (!view) {
    return <div className="aq-page"><LoadingState label={switching ? "正在切换研究日期…" : "正在读取策略命中…"} /></div>;
  }

  const counts = strategyCounts(view.candidates);
  const hits = counts.map((entry) => ({
    ...entry,
    rows: view.candidates.filter((row) =>
      (row.strategies ?? "").split(/[·、,，]/).some((part) => part.trim() === entry.name),
    ),
  }));

  const riskLevel = marketRisk?.available ? marketRisk.level : undefined;
  const riskContext = marketRisk?.available ? marketRisk : null;

  // 02 节统一渲染模型：端点 available 则用 runner 工件（低频更新），否则回退硬编码快照。
  // evidence 归一为 FilterVerdictRow[]（快照常量元素是 as const 字面量，逐字段重建成普通对象），
  // 保证无论数据来源，`.map` 与 `row.*` 访问类型一致、无 union array 问题。
  const ev: FilterVerdictRow[] =
    filterVerdict?.available && filterVerdict.evidence && filterVerdict.evidence.length > 0
      ? filterVerdict.evidence
      : FILTER_VERDICT.evidence.map((r) => ({
          horizon: r.horizon, regime: r.regime, excess: r.excess, t: r.t, ok: r.ok,
        }));
  const verdict = filterVerdict?.available
    ? {
        viable: filterVerdict.viable ?? FILTER_VERDICT.viable,
        baseline: filterVerdict.baseline ?? FILTER_VERDICT.baseline,
        positioning: filterVerdict.positioning ?? FILTER_VERDICT.positioning,
        criterion: filterVerdict.criterion ?? FILTER_VERDICT.criterion,
        dynamicNote: filterVerdict.dynamicNote ?? FILTER_VERDICT.dynamicNote,
        evidence: ev,
        asOf: filterVerdict.as_of ? `as-of ${filterVerdict.as_of}（runner 工件）` : "（工件未标 as-of）",
        evidenceStrength: filterVerdict.evidence_strength ?? "UNKNOWN",
        sourceLabel: "runner 重算工件",
      }
    : {
        viable: FILTER_VERDICT.viable,
        baseline: FILTER_VERDICT.baseline,
        positioning: FILTER_VERDICT.positioning,
        criterion: FILTER_VERDICT.criterion,
        dynamicNote: FILTER_VERDICT.dynamicNote,
        evidence: ev,
        asOf: "as-of 2026-11-16 固化快照（runner 工件未就绪，回退内嵌值）",
        evidenceStrength: "INCONCLUSIVE_LEGACY_SNAPSHOT",
        sourceLabel: "内嵌快照",
      };

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><ListFilter aria-hidden="true" /> AQSP · 研究模块</p>
          <div className="aq-title-row">
            <h1>策略</h1>
            <strong>{view.date}</strong>
          </div>
          <p className="aq-page-sub">当前候选命中了哪些策略假设。</p>
        </div>
      </header>

      <section className="aq-section" aria-label="策略命中分布">
        <SectionHeader
          number="01 · 命中分布"
          title="策略命中概览"
          description="一只候选可同时命中多条策略，因此各策略命中数之和会大于候选总数。"
          count={`${counts.length} 条策略`}
        />
        {hits.length === 0 ? (
          <p className="aq-note">当天没有候选，策略命中为空。</p>
        ) : (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>策略</th>
                  <th className="aq-num">命中</th>
                  <th>命中候选</th>
                </tr>
              </thead>
              <tbody>
                {hits.map((hit) => (
                  <tr key={hit.name}>
                    <td><strong>{hit.name}</strong></td>
                    <td className="aq-num">{hit.count}</td>
                    <td>
                      {hit.rows.map((row) => (
                        <span key={row.symbol} className="aq-chip" title={`${row.name} ${row.symbol} · ${statusLabel(row.status)}`}>
                          {row.name} {row.scoreText}
                        </span>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="aq-section" aria-label="筛选器判读">
        <SectionHeader
          number="02 · 筛选器判读"
          title="当前筛选器是否可用"
          description={verdict.positioning}
          icon={ShieldCheck}
        />

        {/* 判据（定位已锁定：稳健性，非正收益） */}
        <div className="aq-note">判据：{verdict.criterion}</div>
        {/* 数据来源与时效（动态链路：优先 runner 工件，回退内嵌快照） */}
        <div className="aq-note">来源：{verdict.sourceLabel} · {verdict.asOf}</div>

        {/* 结论 */}
        <table className="aq-table aq-summary-table">
          <tbody>
            <tr>
              <td><strong>生产基线</strong></td>
              <td>{verdict.baseline}</td>
            </tr>
            <tr>
              <td><strong>可行筛选器</strong></td>
              <td>
                {String(verdict.evidenceStrength).startsWith("INCONCLUSIVE")
                  ? "⚠️ 证据不足：统计上无法判定可行，不构成有效性结论"
                  : verdict.viable
                    ? "✅ 跨 3 市场面 + 2025/2026 均不显著为负"
                    : "❌ 未通过判据"}
              </td>
            </tr>
            <tr>
              <td><strong>动态切换</strong></td>
              <td>{verdict.dynamicNote}</td>
            </tr>
            {riskContext ? (
              <tr>
                <td><strong>当前市场面</strong></td>
                <td>
                  <span className="aq-chip">
                    {RISK_LEVEL_LABEL[riskLevel ?? ""] ?? "未知"}
                  </span>
                  {" "}趋势20 {riskContext.trend_20 != null ? `${(riskContext.trend_20 * 100).toFixed(1)}%` : "—"}
                  {" · "}广度 {riskContext.breadth != null ? `${Math.round(riskContext.breadth * 100)}%` : "—"}
                  {" · "}回撤252 {riskContext.drawdown_252 != null ? `${(riskContext.drawdown_252 * 100).toFixed(1)}%` : "—"}
                </td>
              </tr>
            ) : (
              <tr>
                <td><strong>当前市场面</strong></td>
                <td className="aq-note">{marketRiskError ?? "暂无数据"}</td>
              </tr>
            )}
          </tbody>
        </table>

        {/* runner 证据（regime 轴） */}
        <div className="aq-table-wrap">
          <table className="aq-table">
            <thead>
              <tr>
                <th>horizon</th>
                <th>市场面</th>
                <th className="aq-num">超额（vs 池等权）</th>
                <th className="aq-num">NW-t</th>
                <th>判读</th>
              </tr>
            </thead>
            <tbody>
              {verdict.evidence.map((row) => (
                <tr key={`${row.horizon}-${row.regime}`}>
                  <td>{row.horizon}</td>
                  <td>{row.regime}</td>
                  <td className="aq-num">{row.excess}</td>
                  <td className="aq-num">{row.t}</td>
                  <td>{row.ok ? "✅ 不显著为负" : "❌ 显著为负"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="aq-note">
            证据来自 runner 作业面事件研究（双反向 2f，top10 生产池，同截面池等权为基准，
            {verdict.asOf}）。不跨期累乘、只用 ≤t 数据无前视。此为**研究结论**，
            会随行情数据更新而重算，非实盘盈亏。
          </p>
        </div>
      </section>
    </div>
  );
}
