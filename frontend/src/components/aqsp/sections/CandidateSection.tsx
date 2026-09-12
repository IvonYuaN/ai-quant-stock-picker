// 候选研究区块：候选链一览表 + 每个候选的详细依据。
//
// 数据全部来自 daily-view 的展示模型，本文件不做任何判断逻辑，
// 因此"候选名怎么显示""什么算可复核"在页签和卡片里必然一致。
import { ArrowRight, Check, ExternalLink, ShieldAlert, TrendingUp } from "lucide-react";
import {
  Badge,
  Card,
  EmptyState,
  SectionHeader,
  Tag,
  ToneCallout,
  clickableRowProps,
} from "@/components/ui/primitives";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { cn } from "@/lib/utils";
import { useOwnership } from "../useOwnership";
import type { CandidateRow, DailyView, SectionView } from "@/lib/daily-view";
import { symbolNames } from "@/lib/daily-view";
import {
  selectCandidates,
  strategyOptions,
  type CandidateSortKey,
} from "@/lib/candidate-view";
import { ownedKind, summarizeOwned, type OwnershipSets } from "@/lib/ownership";
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

function CandidateDetailCard({
  row,
  ownership,
  inCompare,
  compareFull,
  onToggleCompare,
  onPick,
}: {
  row: CandidateRow;
  ownership: OwnershipSets;
  inCompare: boolean;
  compareFull: boolean;
  onToggleCompare: (symbol: string) => void;
  onPick?: (symbol: string) => void;
}) {
  return (
    <Card accent={row.ready ? "success" : "warning"}>
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
        <Tag tone="primary">{row.status}</Tag>
        <Tag>{row.evidence}</Tag>
        {row.ready ? <Tag tone="ok">可复核</Tag> : <Tag tone="warn">仅观察</Tag>}
        <OwnerTag ownership={ownership} code={row.symbol} />
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

      {row.metrics.length > 0 ? (
        <div className="aq-metric-grid">
          {row.metrics.map((metric) => (
            <div key={metric.key}>
              <span>{metric.label}</span>
              <b>{metric.value}</b>
            </div>
          ))}
        </div>
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

      {row.provenance ? <p className="aq-provenance">数据源：{row.provenance}</p> : null}
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
  onPick,
}: {
  rows: readonly CandidateRow[];
  variantDate: string;
  ownership: OwnershipSets;
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
                  label={row.ready ? "可复核" : "仅观察"}
                  detail={row.ready ? row.nextStep || "按当前结论复核" : "等待消息与讨论闭环"}
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

/** 选股的操作区：排序 + 只看可复核 + 按策略过滤。 */
function CandidateControls({
  rows,
  sort,
  onlyReady,
  strategy,
  hideOwned,
  onChange,
}: {
  rows: readonly CandidateRow[];
  sort: CandidateSortKey;
  onlyReady: boolean;
  strategy: string | null;
  hideOwned: boolean;
  onChange: (next: {
    sort?: CandidateSortKey;
    onlyReady?: boolean;
    strategy?: string | null;
    hideOwned?: boolean;
  }) => void;
}) {
  const strategies = strategyOptions(rows);
  if (rows.length <= 1) return null;
  return (
    <div className="aq-toolbar aq-toolbar-wrap">
      <label className="aq-field-inline">
        <span>排序</span>
        <select
          className="aq-input"
          value={sort}
          onChange={(event) => onChange({ sort: event.target.value as CandidateSortKey })}
        >
          <option value="score">评分高→低</option>
          <option value="ready">可复核优先</option>
          <option value="evidence">证据多→少</option>
        </select>
      </label>

      {strategies.length > 0 ? (
        <label className="aq-field-inline">
          <span>策略</span>
          <select
            className="aq-input"
            value={strategy ?? ""}
            onChange={(event) => onChange({ strategy: event.target.value || null })}
          >
            <option value="">全部</option>
            {strategies.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      ) : null}

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
        className={cn("aq-btn", hideOwned && "aq-btn-primary")}
        onClick={() => onChange({ hideOwned: !hideOwned })}
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
}: {
  view: DailyView;
  section: SectionView;
  onPick?: (symbol: string) => void;
}) {
  const [sort, setSort] = useState<CandidateSortKey>("score");
  const [onlyReady, setOnlyReady] = useState(false);
  const [strategy, setStrategy] = useState<string | null>(null);
  const [hideOwned, setHideOwned] = useState(false);
  const ownership = useOwnership();
  const selection = selectCandidates(view.candidates, {
    sort,
    onlyReady,
    strategy,
    exclude: hideOwned ? (row) => ownedKind(row.symbol, ownership) !== null : undefined,
  });
  const visible = selection.rows;
  const owned = summarizeOwned(
    view.candidates.map((row) => row.symbol),
    ownership,
  );

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
    <section id={section.id} className="aq-section">
      <SectionHeader
        number={section.number}
        title={section.label}
        description={section.description}
        count={section.count}
      >
        <Badge tone={view.chain.tone}>{view.chain.label}</Badge>
      </SectionHeader>

      <ToneCallout
        tone={view.gate.tone}
        title={view.gate.label}
        detail={view.gate.detail}
        icon={view.gate.tone === "ok" ? Check : ShieldAlert}
      />

      <ResearchChainLane view={view} />

      {view.candidates.length === 0 ? (
        <EmptyState
          title={section.empty?.title ?? "当天没有候选"}
          detail={section.empty?.detail}
          action={
            view.previousReviewDate ? (
              <span className="aq-hint">
                可用「结论归档」回看 {view.previousReviewDate} 的结果，当前页不顶替当天数据。
              </span>
            ) : null
          }
        />
      ) : (
        <>
          <CandidateControls
            rows={view.candidates}
            sort={sort}
            onlyReady={onlyReady}
            strategy={strategy}
            hideOwned={hideOwned}
            onChange={(next) => {
              if (next.sort !== undefined) setSort(next.sort);
              if (next.onlyReady !== undefined) setOnlyReady(next.onlyReady);
              if (next.strategy !== undefined) setStrategy(next.strategy);
              if (next.hideOwned !== undefined) setHideOwned(next.hideOwned);
            }}
          />

          {owned.holding + owned.watchlist > 0 ? (
            <p className="aq-note">
              这 {view.candidates.length} 只候选里，有 {owned.holding} 只你已持仓、{owned.watchlist} 只已自选，
              真正的“新面孔”是 {owned.fresh} 只。
            </p>
          ) : null}

          {selection.hidden > 0 ? (
            <p className="aq-note">
              按当前条件显示 {visible.length} / {selection.total} 只，已隐藏 {selection.hidden} 只。
            </p>
          ) : null}

          {visible.length === 0 ? (
            <EmptyState
              title="当前筛选条件下没有候选"
              detail={`共 ${selection.total} 只候选被筛掉了 ${selection.hidden} 只 —— 放宽条件即可看到它们。`}
            />
          ) : (
            <>
              <CandidateChainTable
                rows={visible}
                variantDate={view.variantLatestDate ?? ""}
                ownership={ownership}
                onPick={onPick}
              />
              {compareSymbols.length >= 2 ? (
                <ComparePanel
                  rows={view.candidates}
                  symbols={compareSymbols}
                  onRemove={(symbol) =>
                    setCompareSymbols(toggleCompareSymbol(compareSymbols, symbol))
                  }
                />
              ) : null}

              <div className="aq-card-grid">
                {visible.map((row) => (
                  <CandidateDetailCard
                    key={row.symbol}
                    row={row}
                    ownership={ownership}
                    inCompare={compareSymbols.includes(row.symbol)}
                    compareFull={compareSymbols.length >= MAX_COMPARE_SYMBOLS}
                    onToggleCompare={(symbol) =>
                      setCompareSymbols(toggleCompareSymbol(compareSymbols, symbol))
                    }
                    onPick={onPick}
                  />
                ))}
              </div>
            </>
          )}
        </>
      )}

      {view.chainDetail ? (
        <p className="aq-note">
          <TrendingUp className="aq-inline-icon" aria-hidden="true" />
          {view.chainDetail}
        </p>
      ) : null}
    </section>
  );
}
