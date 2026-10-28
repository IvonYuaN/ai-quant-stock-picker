// 候选研究区块：候选链一览表 + 每个候选的详细依据。
//
// 数据全部来自 daily-view 的展示模型，本文件不做任何判断逻辑，
// 因此"候选名怎么显示""什么算可复核"在页签和卡片里必然一致。
import { ArrowRight, BookOpen, Check, ExternalLink, SlidersHorizontal, Star } from "lucide-react";
import {
  Badge,
  Card,
  EmptyState,
  SectionHeader,
  Tag,
  clickableRowProps,
} from "@/components/ui/primitives";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { loadWatch, saveWatch } from "@/lib/watchlist";
import { stockResearchHref } from "@/lib/research-route";
import { cn } from "@/lib/utils";
import { api, type Holding, type ReviewRecord } from "@/lib/api";
import { evidenceLabel, statusLabel } from "@/lib/display-labels";
import { useOwnership } from "../useOwnership";
import type { CandidateRow, DailyView, SectionView } from "@/lib/daily-view";
import { symbolNames } from "@/lib/daily-view";
import type { AqspCandidateLifecycle } from "@/types/aqsp";
import {
  selectCandidates,
  groupCandidatesByOwnership,
  strategyCounts,
  type CandidateSortKey,
} from "@/lib/candidate-view";
import { ownedKind, summarizeOwned, ownershipSummaryLabel, type OwnershipSets } from "@/lib/ownership";
import {
  MAX_COMPARE_SYMBOLS,
  buildCompareMatrix,
  parseCompareSymbols,
  toggleCompareSymbol,
} from "@/lib/candidate-compare";

/** 并排对比：最后筛剩几只时，摆开看才分得出该先动哪只。 */
function ComparePanel({
  rows,
  symbols,
  onRemove,
}: {
  rows: readonly CandidateRow[];
  symbols: readonly string[];
  onRemove: (symbol: string) => void;
}) {
  const matrix = buildCompareMatrix(rows, symbols);
  if (matrix.columns.length < 2) return null;
  return (
    <div className="aq-table-wrap">
      <table className="aq-table">
        <thead>
          <tr>
            <th>指标</th>
            {matrix.columns.map((column) => (
              <th key={column.symbol} className="aq-num">
                {column.name}
                <button
                  type="button"
                  className="aq-btn aq-btn-icon"
                  onClick={() => onRemove(column.symbol)}
                  title="从对比中移除"
                  aria-label={`移除 ${column.name}`}
                >
                  <span aria-hidden="true">×</span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.metrics.map((metric) => (
            <tr key={metric.key}>
              <td>{metric.label}</td>
              {matrix.columns.map((column) => (
                <td key={column.symbol} className={metric.numeric ? "aq-num" : undefined}>
                  {matrix.values[metric.key][column.symbol]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 已持有 / 已关注的标记 —— 选股时最该先看到的，是哪些票你已经有仓位或盯过了。 */
function OwnerTag({ ownership, code }: { ownership: OwnershipSets; code: string }) {
  const kind = ownedKind(code, ownership);
  if (!kind) return null;
  return (
    <Tag tone={kind === "holding" ? "warn" : "neutral"}>{kind === "holding" ? "已持仓" : "已自选"}</Tag>
  );
}

/**
 * 候选池生命周期标记（issue #317 W1）：一眼看出「新面孔 / 连续在榜 / 中断后回归」。
 * 证据挂在 title 上：首次入选、出现次数、快照面覆盖范围；取不到时静默不显示。
 */
function LifecycleTag({
  lifecycle,
  historyWindow,
}: {
  lifecycle?: AqspCandidateLifecycle;
  historyWindow: number;
}) {
  if (!lifecycle) return null;
  const label = lifecycle.is_new
    ? "新入选"
    : lifecycle.streak > 1
      ? `连续 ${lifecycle.streak} 日在榜`
      : "中断后回归";
  const detail = `首次入选 ${lifecycle.first_seen} · 快照面内出现 ${lifecycle.total_appearances} 次 · 生命周期覆盖最近 ${historyWindow} 个快照日`;
  return (
    <Tag tone={lifecycle.is_new ? "primary" : "neutral"} title={detail}>
      {label}
    </Tag>
  );
}

function CandidateDetailCard({
  row,
  ownership,
  inCompare,
  compareFull,
  reviews,
  lifecycle,
  historyWindow,
  onToggleCompare,
  onPick,
  date,
}: {
  row: CandidateRow;
  ownership: OwnershipSets;
  inCompare: boolean;
  compareFull: boolean;
  reviews: ReviewRecord[];
  lifecycle?: AqspCandidateLifecycle;
  historyWindow: number;
  onToggleCompare: (symbol: string) => void;
  onPick?: (symbol: string) => void;
  date: string;
}) {
  const watched = ownership.watchlist.has(row.symbol);
  const toggleWatch = () => {
    try {
      const current = loadWatch();
      const change = saveWatch(watched ? current.filter((code) => code !== row.symbol) : [...new Set([...current, row.symbol])]);
      if (change.removed.length && change.operation_id) {
        void api.recordWatchlistRemoval([...change.removed], change.operation_id).catch(() => {
          // localStorage removal evidence remains available when the API is offline.
        });
      }
      toast(watched ? "已移出自选" : "已加入自选");
    } catch {
      toast.error("自选保存失败，请检查浏览器存储后重试。");
    }
  };
  return (
    <Card className="aq-selected-candidate" accent={row.ready ? "success" : "warning"}>
      <div className="aq-card-head">
        <div className="aq-card-title">
          <h3>{row.name}</h3>
          <span className="aq-code">{row.symbol || "代码未记录"}</span>
        </div>
        <div className="aq-score">
          <b>{row.scoreText}</b>
          <span>评分</span>
        </div>
      </div>

      <div className="aq-tag-row">
        <Tag tone="primary">{statusLabel(row.status)}</Tag>
        <Tag>{evidenceLabel(row.evidence)}</Tag>
        {row.ready ? <Tag tone="ok">材料齐了</Tag> : <Tag tone="warn">材料待补</Tag>}
        <OwnerTag ownership={ownership} code={row.symbol} />
        <LifecycleTag lifecycle={lifecycle} historyWindow={historyWindow} />
      </div>
      <div className="aq-candidate-actions">
        <Link className="aq-btn aq-btn-primary" to={stockResearchHref(row.symbol, date)}>看理由与 AI 讨论 <ArrowRight aria-hidden="true" /></Link>
        <button type="button" className="aq-btn" onClick={toggleWatch} aria-pressed={watched}><Star aria-hidden="true" fill={watched ? "currentColor" : "none"} />{watched ? "已自选" : "加入自选"}</button>
        {onPick && row.symbol ? (
          <button type="button" className="aq-btn aq-btn-icon" onClick={() => onPick(row.symbol)} title="查看个股详情">
            <ExternalLink aria-hidden="true" />
          </button>
        ) : null}
        <button
          type="button"
          className={cn("aq-btn", inCompare && "aq-btn-primary")}
          onClick={() => onToggleCompare(row.symbol)}
          disabled={compareFull && !inCompare}
          title={
            compareFull && !inCompare
              ? `最多同时对比 ${MAX_COMPARE_SYMBOLS} 只`
              : inCompare
                ? "从对比中移除"
                : "加入并排对比"
          }
          aria-pressed={inCompare}
        >
          {inCompare ? "已加入对比" : "加入对比"}
        </button>
      </div>

      {row.context ? <p className="aq-card-summary">{row.context}</p> : null}

      {reviews.length > 0 ? (
        <p
          className="aq-review-echo"
          title={reviews[0].notes || undefined}
        >
          <BookOpen aria-hidden="true" />
          历史复盘 {reviews.length} 次 · 上次 {reviews[0].rating}★
          {reviews[0].tags.length > 0
            ? ` · ${reviews[0].tags.join("/")}`
            : ""}
        </p>
      ) : null}

      {row.metrics.length > 0 ? (
        <><div className="aq-metric-grid">
          {row.metrics.map((metric) => (
            <div key={metric.key}>
              <span>{metric.label}</span>
              <b>{metric.value}</b>
            </div>
          ))}
        </div>{row.technicalProvenance ? <details className="aq-provenance"><summary>{row.technicalProvenance.summary}</summary><p>{row.technicalProvenance.detail}</p></details> : null}</>
      ) : null}

      {row.breakdown.length > 0 ? (
        <p className="aq-breakdown">
          <b>评分依据</b>
          {row.breakdown.slice(0, 4).join(" · ")}
        </p>
      ) : null}

      {row.reasons.length > 0 ? (
        <ul className="aq-reason-list">
          {row.reasons.slice(0, 3).map((reason) => (
            <li key={reason}>
              <Check className="aq-inline-icon aq-tone-ok" aria-hidden="true" />
              {reason}
            </li>
          ))}
        </ul>
      ) : null}

      {row.nextStep ? (
        <p className="aq-next-step">
          <ArrowRight className="aq-inline-icon" aria-hidden="true" />
          下一观察：{row.nextStep}
        </p>
      ) : null}

      {row.provenance ? <p className="aq-provenance">快照行情：{row.provenance}</p> : null}
      {row.coverageNote ? <p className="aq-note" role="status">{row.coverageNote}</p> : null}
    </Card>
  );
}

// 语气类名写成字面量映射，避免 Tailwind 扫不到拼接出的类名而裁掉样式。
const CELL_TONE: Readonly<Record<"ok" | "warn", string>> = {
  ok: "aq-tone-ok",
  warn: "aq-tone-warn",
};

function ChainCell({ label, tone, detail }: { label: string; tone?: "ok" | "warn"; detail: string }) {
  return (
    <div className={cn("aq-chain-cell", tone && CELL_TONE[tone])}>
      <b>{label}</b>
      <p>{detail}</p>
    </div>
  );
}

function CandidateChainTable({
  rows,
  variantDate,
  ownership,
  lifecycles,
  historyWindow,
  onPick,
}: {
  rows: readonly CandidateRow[];
  variantDate: string;
  ownership: OwnershipSets;
  lifecycles: Record<string, AqspCandidateLifecycle>;
  historyWindow: number;
  onPick?: (symbol: string) => void;
}) {
  return (
    <div className="aq-table-wrap">
      <table className="aq-table aq-chain-table">
        <thead>
          <tr>
            <th>候选</th>
            <th>消息证据</th>
            <th>讨论复核</th>
            <th>历史变体</th>
            <th>当前复核</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.symbol}
              {...(onPick ? clickableRowProps(() => onPick(row.symbol), "查看个股详情") : {})}
            >
              <td>
                <div className="aq-chain-candidate">
                  <strong>{row.name}</strong>
                  <span className="aq-code">{row.symbol}</span>
                  <OwnerTag ownership={ownership} code={row.symbol} />
                  <LifecycleTag
                    lifecycle={lifecycles[row.symbol]}
                    historyWindow={historyWindow}
                  />
                </div>
                <b className="aq-chain-score">{row.scoreText}</b>
                <p>{[row.strategies, row.keyMetric].filter(Boolean).join(" · ") || "策略未记录"}</p>
              </td>
              <td>
                <ChainCell
                  tone={row.messageCount > 0 ? "ok" : "warn"}
                  label={row.messageCount > 0 ? `${row.messageCount} 条个股证据` : "未形成个股证据"}
                  detail={row.firstMessageTitle || "来源已抓取，但没有可引用到该候选的高影响事件"}
                />
              </td>
              <td>
                <ChainCell
                  tone={row.debateRounds !== null ? "ok" : "warn"}
                  label={row.debateRounds !== null ? `已完成 ${row.debateRounds} 轮` : "讨论阻断"}
                  detail={row.debateConclusion || "缺少个股消息支持、反证与可证伪条件"}
                />
              </td>
              <td>
                <ChainCell
                  label={row.variantCount ? `历史持仓覆盖 ${row.variantCount} 组` : "历史持仓未覆盖"}
                  detail={variantDate ? `实验截至 ${variantDate}，不替代当天证据` : "尚无可核验变体日期"}
                />
              </td>
              <td>
                <ChainCell
                  tone={row.ready ? "ok" : "warn"}
                  label={row.ready ? "材料齐了" : "材料待补"}
                  detail={row.ready ? row.nextStep || "按当前结论跟进" : "等材料补齐再跟进"}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResearchChainLane({ view }: { view: DailyView }) {
  const chain = view.researchChain;
  if (!chain || chain.candidate_symbols.length === 0) return null;
  const candidates = view.candidates.map((row) => ({ symbol: row.symbol, display_name: row.name }));
  const reviewed = symbolNames(chain.debated_symbols, candidates) || "尚未进入复核";
  const experiment = symbolNames(chain.variant_candidate_symbols, candidates) || "本轮实验未覆盖";
  return (
    <div className="aq-chain-lane">
      <div className="aq-chain-node">
        <span>当天候选 · {chain.candidate_symbols.length}</span>
        <b>{symbolNames(chain.candidate_symbols, candidates)}</b>
      </div>
      <ArrowRight className="aq-chain-arrow" aria-hidden="true" />
      <div className="aq-chain-node aq-chain-node-info">
        <span>讨论复核 · {chain.debated_symbols.length}</span>
        <b>{reviewed}</b>
      </div>
      <ArrowRight className="aq-chain-arrow" aria-hidden="true" />
      <div className="aq-chain-node aq-chain-node-warn">
        <span>历史实验池关联 · {chain.variant_candidate_symbols.length}</span>
        <b>{experiment}</b>
      </div>
    </div>
  );
}

/**
 * 分策略选股（issue #317）：策略不再藏在下拉里，做成带计数的一等 chip。
 * 计数来自 strategyCounts，与过滤命中共用同一套拆分规则；
 * 再点一次已选中的 chip 即清除过滤。
 */
function StrategyChips({
  rows,
  active,
  onChange,
}: {
  rows: readonly CandidateRow[];
  active: string | null;
  onChange: (next: string | null) => void;
}) {
  const counts = strategyCounts(rows);
  if (counts.length === 0) return null;
  return (
    <div className="aq-tag-row" role="group" aria-label="按策略选股">
      <button
        type="button"
        className={cn("aq-chip", active === null && "aq-tag-primary")}
        onClick={() => onChange(null)}
        aria-pressed={active === null}
        title={`全部 ${rows.length} 只候选`}
      >
        全部 {rows.length}
      </button>
      {counts.map(({ name, count }) => (
        <button
          key={name}
          type="button"
          className={cn("aq-chip", active === name && "aq-tag-primary")}
          onClick={() => onChange(active === name ? null : name)}
          aria-pressed={active === name}
          title={`${name}：${count} 只候选（一只候选可同时挂多个策略）`}
        >
          {name} {count}
        </button>
      ))}
    </div>
  );
}

/** 选股的操作区：排序 + 只看可复核 + 按策略过滤。 */
function CandidateControls({
  rows,
  sort,
  onlyReady,
  strategy,
  hideOwned,
  ownershipKnown,
  onChange,
}: {
  rows: readonly CandidateRow[];
  sort: CandidateSortKey;
  onlyReady: boolean;
  strategy: string | null;
  hideOwned: boolean;
  ownershipKnown: boolean;
  onChange: (next: {
    sort?: CandidateSortKey;
    onlyReady?: boolean;
    strategy?: string | null;
    hideOwned?: boolean;
    onlyOwned?: boolean;
  }) => void;
}) {
  if (rows.length === 0) return null;
  return (
    <div className="aq-toolbar aq-toolbar-wrap">
      <label className="aq-field-inline">
        <span>排序</span>
        <select
          className="aq-input"
          value={sort}
          onChange={(event) => onChange({ sort: event.target.value as CandidateSortKey })}
        >
          <option value="personal">{ownershipKnown ? "我的持仓 / 自选优先" : "自选优先 · 持仓待核"}</option>
          <option value="score">评分高→低</option>
          <option value="ready">可复核优先</option>
          <option value="evidence">证据多→少</option>
        </select>
      </label>

      <StrategyChips rows={rows} active={strategy} onChange={(next) => onChange({ strategy: next })} />

      <button
        type="button"
        className={cn("aq-btn", onlyReady && "aq-btn-primary")}
        onClick={() => onChange({ onlyReady: !onlyReady })}
        title="只看证据链已闭环、可进入纸面复核的候选"
      >
        {onlyReady ? "只看可复核 ✓" : "只看可复核"}
      </button>

      <button
        type="button"
        disabled={!ownershipKnown}
        className={cn("aq-btn", hideOwned && "aq-btn-primary")}
        onClick={() => onChange({ hideOwned: !hideOwned, onlyOwned: false })}
        title="隐藏已经持有或已加入自选的候选，专注找新面孔"
      >
        {hideOwned ? "隐藏已持有 ✓" : "隐藏已持有"}
      </button>
    </div>
  );
}

export function CandidateSection({
  view,
  section,
  onPick,
  ownedHoldings,
}: {
  view: DailyView;
  section: SectionView;
  onPick?: (symbol: string) => void;
  ownedHoldings?: readonly Holding[] | null;
}) {
  const [sort, setSort] = useState<CandidateSortKey>("personal");
  const [onlyReady, setOnlyReady] = useState(false);
  const [strategy, setStrategy] = useState<string | null>(null);
  const [hideOwned, setHideOwned] = useState(false);
  const [onlyOwned, setOnlyOwned] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const ownership = useOwnership(ownedHoldings);
  const ownershipKnown = ownership.holdingsKnown !== false;
  // 历史复盘：看候选时同步看到「自己上次对这只票的判断」——决策时刻最有价值的镜子。
  // 取不到时静默降级（不影响候选研究主流程）。
  const [reviewsBySymbol, setReviewsBySymbol] = useState<Map<string, ReviewRecord[]>>(
    () => new Map(),
  );
  useEffect(() => {
    let cancelled = false;
    api
      .reviews()
      .then((rows) => {
        if (cancelled) return;
        const map = new Map<string, ReviewRecord[]>();
        for (const row of rows) {
          if (!row.symbol) continue;
          const list = map.get(row.symbol) ?? [];
          list.push(row);
          map.set(row.symbol, list);
        }
        setReviewsBySymbol(map);
      })
      .catch(() => {
        /* fail-soft：复盘数据缺失不阻断候选研究 */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  // 候选池生命周期（issue #317 W1）：新入选/连续在榜/回归一眼可见。
  // 快照面缺失或端点不可用时静默降级，不阻断候选研究主流程。
  const [lifecycles, setLifecycles] = useState<Record<string, AqspCandidateLifecycle>>({});
  const [lifecycleDate, setLifecycleDate] = useState("");
  const [historyWindow, setHistoryWindow] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLifecycles({});
    setHistoryWindow(0);
    api
      .aqspLifecycle(view.date)
      .then((index) => {
        if (cancelled) return;
        setLifecycles(index.lifecycles ?? {});
        setLifecycleDate(view.date);
        setHistoryWindow(index.history_window ?? 0);
      })
      .catch(() => {
        /* fail-soft：生命周期数据缺失不阻断候选研究 */
      });
    return () => {
      cancelled = true;
    };
  }, [view.date]);
  const selection = selectCandidates(view.candidates, {
    sort,
    onlyReady,
    strategy,
    ownership,
    onlyOwned: ownershipKnown && onlyOwned,
    exclude: ownershipKnown && hideOwned ? (row) => ownedKind(row.symbol, ownership) !== null : undefined,
  });
  const visible = selection.rows;
  const visibleGroups = groupCandidatesByOwnership(visible);
  const selected = visible.find((row) => row.symbol === selectedSymbol) ?? visible[0];
  const owned = summarizeOwned(
    view.candidates.map((row) => row.symbol),
    ownership,
  );
  const invalidated = new Set(
    (view.candidateChanges ?? []).filter((change) => change.status === "invalidated").map((change) => change.symbol),
  );
  const added = (view.candidateChanges ?? []).filter((change) => change.status === "added");

  // 并排对比的所选标的放在 URL 上（与所选交易日同一套原则），但用 replace ——
  // 这是视图微调，不该在浏览器历史里堆一串记录。
  const [searchParams, setSearchParams] = useSearchParams();
  const compareSymbols = parseCompareSymbols(
    searchParams.get("cmp"),
    view.candidates.map((row) => row.symbol),
  );
  const setCompareSymbols = (next: string[]) => {
    const params = new URLSearchParams(searchParams);
    if (next.length === 0) params.delete("cmp");
    else params.set("cmp", next.join(","));
    setSearchParams(params, { replace: true });
  };
  return (
    <section id={section.id} className="aq-candidate-workspace">
      <SectionHeader
        title="选股与 AI 研究"
        description="先看为什么入选、依据是否齐全，再进入个股讨论和路线比较。"
        count={section.count}
      >
        <Badge tone={view.chain.tone}>{view.chain.label}</Badge>
      </SectionHeader>

      <div className="aq-tag-row">
        <Tag title={view.parameterSnapshot.detail}>{view.parameterSnapshot.label}</Tag>
        {view.frozenAt ? <Tag title={view.frozenAt}>当日候选已冻结 · {new Date(view.frozenAt).toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false })}</Tag> : <Tag>冻结信息尚未生成</Tag>}
        <Tag tone={!ownershipKnown ? "warn" : owned.holding + owned.watchlist > 0 ? "primary" : "neutral"}>{ownershipSummaryLabel(view.candidates.map((row) => row.symbol), ownership)}</Tag>
        <button type="button" disabled={!ownershipKnown} className={cn("aq-btn", ownershipKnown && onlyOwned && "aq-btn-primary")} aria-pressed={ownershipKnown && onlyOwned} onClick={() => { setOnlyOwned(!onlyOwned); setHideOwned(false); }}>只看我的命中</button>
      </div>
      {!ownershipKnown ? <p className="aq-note" role="status">持仓身份待确认，已暂停持仓筛选与计数；不影响查看候选研究。可用上方“更新跟进状态”重试。</p> : null}
      {view.frozenAt ? <p className="aq-hint">评分、策略依据与参数身份沿用当日首次冻结；现价、新鲜度与消息按最新证据更新。</p> : null}
      {added.length > 0 ? (
        <section className="aq-candidate-increment" aria-label="盘中新增候选">
          <div className="aq-candidate-increment-head">
            <strong>盘中新增</strong>
            <span>不改变已冻结名单与排序</span>
          </div>
          {added.map((change) => (
            <div className="aq-candidate-increment-row" key={change.symbol}>
              <div className="aq-candidate-increment-identity">
                <Tag tone="primary">新增</Tag>
                <strong>{change.display_name || "未命名标的"}</strong>
                <span className="aq-code">{change.symbol}</span>
              </div>
              {onPick ? (
                <button type="button" className="aq-btn" onClick={() => onPick(change.symbol)}>
                  查看个股
                </button>
              ) : null}
            </div>
          ))}
        </section>
      ) : null}

      {view.candidates.length === 0 ? (
        <EmptyState
          title={section.empty?.title ?? "当天没有候选"}
          detail={section.empty?.detail}
          action={
            view.previousReviewDate ? (
              <span className="aq-hint">
                可用上方日期切换回看 {view.previousReviewDate} 的结果，当前页不顶替当天数据。
              </span>
            ) : null
          }
        />
      ) : (
        <>
          <details className="aq-candidate-filters">
            <summary><SlidersHorizontal aria-hidden="true" />筛选与排序 <span>{visible.length} / {view.candidates.length} 只 · {owned.watchlist} 已自选{selection.hidden ? ` · 已筛除 ${selection.hidden} 只` : ""}</span></summary>
          <CandidateControls
            rows={view.candidates}
            sort={sort}
            onlyReady={onlyReady}
            strategy={strategy}
            hideOwned={ownershipKnown && hideOwned}
            ownershipKnown={ownershipKnown}
            onChange={(next) => {
              if (next.sort !== undefined) setSort(next.sort);
              if (next.onlyReady !== undefined) setOnlyReady(next.onlyReady);
              if (next.strategy !== undefined) setStrategy(next.strategy);
              if (next.hideOwned !== undefined) setHideOwned(next.hideOwned);
              if (next.onlyOwned !== undefined) setOnlyOwned(next.onlyOwned);
            }}
          />
          </details>

          {visible.length === 0 ? (
            <EmptyState
              title="当前筛选条件下没有候选"
              detail={`共 ${selection.total} 只候选被筛掉了 ${selection.hidden} 只 —— 放宽条件即可看到它们。`}
            />
          ) : (
            <>
              <div className="aq-candidate-split">
                <div className="aq-candidate-roster" role="group" aria-label="选择研究候选">
                  <div className="aq-candidate-roster-head"><span>标的 / 研究状态</span><span>评分</span></div>
                  {visibleGroups.map((group, groupIndex) => {
                    const rankOffset = visibleGroups.slice(0, groupIndex).reduce((total, item) => total + item.rows.length, 0);
                    return group.rows.length ? (
                      <section className="aq-candidate-roster-group" key={group.kind} data-candidate-group={group.kind} aria-label={`${group.label} ${group.rows.length} 只`}>
                        <div className="aq-candidate-roster-group-head"><strong>{group.label}</strong><span>{group.rows.length} 只</span></div>
                        {group.rows.map((row, index) => (
                          <button key={row.symbol} type="button" className={cn("aq-candidate-choice", selected?.symbol === row.symbol && "aq-candidate-choice-active")} aria-pressed={selected?.symbol === row.symbol} onClick={() => setSelectedSymbol(row.symbol)}>
                            <span className="aq-candidate-rank">{String(rankOffset + index + 1).padStart(2, "0")}</span>
                            <span className="aq-candidate-choice-body"><span className="aq-candidate-choice-title"><b>{row.name}</b><span className="aq-code">{row.symbol}</span>{invalidated.has(row.symbol) ? <Tag tone="warn">已失效</Tag> : null}</span><span>{statusLabel(row.status)} · {row.messageCount} 条消息 · {row.debateRounds !== null ? `${row.debateRounds} 轮复核` : "待复核"}</span><OwnerTag ownership={ownership} code={row.symbol} /></span>
                            <strong className="aq-candidate-choice-score">{row.scoreText}</strong>
                          </button>
                        ))}
                      </section>
                    ) : null;
                  })}
                </div>
                {selected ? <CandidateDetailCard
                    key={selected.symbol}
                    row={selected}
                    date={view.date}
                    ownership={ownership}
                    inCompare={compareSymbols.includes(selected.symbol)}
                    compareFull={compareSymbols.length >= MAX_COMPARE_SYMBOLS}
                    reviews={reviewsBySymbol.get(selected.symbol) ?? []}
                    lifecycle={lifecycleDate === view.date ? lifecycles[selected.symbol] : undefined}
                    historyWindow={historyWindow}
                    onToggleCompare={(symbol) => setCompareSymbols(toggleCompareSymbol(compareSymbols, symbol))}
                    onPick={onPick}
                  /> : null}
              </div>
              {compareSymbols.length >= 2 ? (
                <ComparePanel
                  rows={view.candidates}
                  symbols={compareSymbols}
                  onRemove={(symbol) =>
                    setCompareSymbols(toggleCompareSymbol(compareSymbols, symbol))
                  }
                />
              ) : null}

              <details className="aq-workspace-fold">
                <summary>逐票完整证据链 <span>消息 / 讨论 / 历史实验 / 当前复核</span></summary>
                <div className="aq-workspace-fold-body"><ResearchChainLane view={view} /><CandidateChainTable rows={visible} variantDate={view.variantLatestDate ?? ""} ownership={ownership} lifecycles={lifecycleDate === view.date ? lifecycles : {}} historyWindow={historyWindow} onPick={onPick} /></div>
              </details>
            </>
          )}
        </>
      )}

    </section>
  );
}
