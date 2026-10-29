import type { ActionWorksheet, DecisionEvidence, DiscussionSynthesisBasis, PositionDiscussion, ReviewChoice, StrategyWatchInput, WatchComparator } from "./api";
import { positionChecksView } from "./position-conditions";
import { worksheetExpired } from "./position-plan";

export const WATCH_COMPARATORS: Record<WatchComparator, string> = { at_or_above: "达到或高于", at_or_below: "达到或低于" };

/** Same premise fields as backend strategy_direction; quote ticks are excluded. */
export function strategyDirectionKey(evidence?: Partial<Record<"market" | "sector" | "stock", Partial<DecisionEvidence>>>): string {
  return JSON.stringify((["market", "sector", "stock"] as const).map((key) => {
    const fact = evidence?.[key];
    const base = [fact?.status || "unknown", fact?.source ?? null, fact?.industry ?? null];
    return key === "market" ? [...base, fact?.breadth ?? null, (fact?.indices ?? []).map((row) => [row.name, row.source ?? null, Number.isFinite(row.change_pct) ? row.change_pct > 0 ? "up" : row.change_pct < 0 ? "down" : "flat" : "unknown"]).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))) ] : base;
  }));
}

/** A confirmation belongs to the exact model result the user reviewed. */
export function discussionSourceKey(result: PositionDiscussion | null): string {
  return result ? JSON.stringify([result.generated_at, result.analysis_basis_id, result.review_revision, result.choice, result.summary, result.synthesis_basis, result.condition_catalog]) : "";
}

/** Explain who contributed without presenting incomplete review as consensus. */
export function discussionSynthesisNotice(basis: DiscussionSynthesisBasis | undefined, kind: "candidate" | "position"): string {
  if (!basis) return "";
  const labels: Record<string, string> = kind === "candidate"
    ? { trend: "趋势与确认", entry: "回落与代价", risk: "风险与暂缓" }
    : { trend: "趋势分析", t_review: "做 T 评估", risk: "风险复核" };
  const names = (roles: string[]) => [...new Set(roles)].map((role) => labels[role] ?? role).join("、");
  const parts: string[] = [];
  if (basis.mode === "partial_initial") parts.push("部分汇总仅供阅读，不能采用新观察");
  else if (kind === "candidate" && (basis.missing_roles.length || basis.unreviewed_roles.length)) parts.push("部分汇总仅供阅读，不能采用新观察");
  if (basis.missing_roles.length) parts.push(`缺席：${names(basis.missing_roles)}`);
  if (basis.unreviewed_roles.length) parts.push(`未完成回应：${names(basis.unreviewed_roles)}`);
  return parts.length ? `${parts.join("；")}。` : "";
}

function constraintsKey(value: ActionWorksheet["constraints"]): string {
  if (!value) return "missing";
  const baseline = value.risk_baseline;
  return JSON.stringify([value.attention, value.extra_cash, value.loss_limit, baseline && [baseline.price, baseline.shares, baseline.market_value, baseline.as_of, baseline.confirmed_at]]);
}

/** Same semantic evidence permits normal quote updates during model latency. */
export function discussionStrategyCurrent(result: PositionDiscussion | null, worksheet: ActionWorksheet, now = Date.now()): boolean {
  if (!result || !result.analysis_basis_id || result.review_revision !== worksheet.revision) return false;
  if (["position_id", "cost", "shares", "sellable_shares"].some((key) => result.position_snapshot[key as keyof typeof result.position_snapshot] !== worksheet.position_snapshot[key as keyof typeof result.position_snapshot])) return false;
  const age = now - Date.parse(result.generated_at);
  if (!Number.isFinite(age) || age < 0 || age > 900_000 || worksheet.archived) return false;
  const current = worksheet.route_review?.evidence;
  if (!result.evidence_snapshot || !current) return false;
  return strategyDirectionKey(result.evidence_snapshot) === strategyDirectionKey(current);
}

/** Only a validated summary can be explicitly adopted; role drafts remain evidence. */
export function strategyWatchIssue(result: PositionDiscussion | null, worksheet: ActionWorksheet, index: number, choice: ReviewChoice | "", comparator: WatchComparator | "", now = Date.now()): string {
  if (worksheet.archived || !worksheet.position_id || !worksheet.revision || !worksheet.analysis_basis_id) return "请先更新当前持仓与策略依据";
  if (!result?.summary || result.status === "failed") return "尚未形成可采用的汇总条件";
  if (result.synthesis_basis?.mode === "partial_initial") return "部分汇总仅供阅读，不能采用新观察";
  if (!discussionStrategyCurrent(result, worksheet, now)) return "仓位、风险或环境判断已变化，或分析已旧，请重新分析";
  const action = result.summary.actions[index];
  const reference = action && result.price_refs[action.price_ref ?? ""];
  if (!action || action.action === "pause" || !reference || !Number.isFinite(reference.price) || reference.price <= 0 || !reference.source || !reference.as_of) return "这项条件没有可跟踪的来源价位";
  if (!choice) return "请选择要采用的处理路线";
  if (!comparator) return "请确认报价达到或高于、还是达到或低于观察价时提醒";
  if (action.action === "buy_review" && choice !== "t_watch") return "回补复核仅能用于做 T 观察路线";
  if (choice === "t_watch" && (worksheet.basis.joint_weakness || worksheet.basis.personal_pause_t || !worksheet.basis.sellable_shares)) return "当前做 T 条件不满足，可继续分析暂停原因";
  if (worksheet.basis.quote_issue) return "当前行情不可用，先更新策略依据";
  return "";
}

export function strategyWatchInput(result: PositionDiscussion, worksheet: ActionWorksheet, index: number, choice: ReviewChoice, comparator: WatchComparator, now = Date.now()): StrategyWatchInput {
  const issue = strategyWatchIssue(result, worksheet, index, choice, comparator, now);
  if (issue) throw new Error(issue);
  return { choice, source_discussion: result.choice === null ? "comparison" : "plan", generated_at: result.generated_at, action_index: index, comparator,
    expected_position: worksheet.position_snapshot, expected_revision: worksheet.revision!, expected_analysis_basis_id: result.analysis_basis_id! };
}

export function strategyWatchState(worksheet: ActionWorksheet, now = Date.now()): { title: string; detail: string } | null {
  const watch = worksheet.review_plan.watch;
  if (!watch && worksheet.review_plan.watch_stopped) return { title: "这项策略跟踪已停止", detail: "保留处理路线与费用试算；后台不再检查原观察价。需要继续时，重新分析并采用新的条件。" };
  if (!watch) return null;
  const expected = watch.position_snapshot;
  if (worksheet.sale_requires_review || !expected || ["position_id", "cost", "shares", "sellable_shares"].some((key) => expected[key as keyof typeof expected] !== worksheet.position_snapshot[key as keyof typeof expected])) return { title: "持仓已变化，先重新核对策略", detail: "以下保留已保存条件；需要按当前仓位重新分析后确认。" };
  if (worksheet.review_plan.choice === "t_watch" && watch.action === "buy_review" && !worksheet.recorded_sale) return { title: "先核对做 T 的卖出事实", detail: "做 T 按先卖后买顺序复核；尚无已记录卖出事实，到价也不代表可以回补。" };
  if (watch.action === "sell_review" && worksheet.basis.sellable_shares === 0) return { title: "当前没有可卖底仓", detail: "当前可卖数量为 0，先核对 T+1 与股份状态；到价也不能沿用卖出条件。" };
  if (worksheet.basis.quote_issue || worksheetExpired(worksheet.basis, now) || worksheet.basis.price == null) return { title: "等待有效行情", detail: worksheet.basis.quote_issue || "当前报价已旧，暂不判断是否到价。" };
  if (worksheet.review_plan.choice === "t_watch" && (worksheet.basis.joint_weakness || worksheet.basis.personal_pause_t)) return { title: "当前做 T 观察先暂停", detail: worksheet.constraints_issue || "环境或个人条件已变化，先重新分析，不沿用原回补条件。" };
  if (worksheet.review_plan.choice === "hold" && worksheet.current_risk?.some((row) => row.status === "triggered" && (row.level === "L1" || row.level === "L5"))) return { title: "当前风险线仍触发，重新核对持有依据", detail: "继续持有仍承担后续下跌风险；请结合三维证据与可承受损失复核，触发风险线本身不代替卖出分析。" };
  const previous = worksheet.followup?.last_check;
  const sameIntent = !!worksheet.followup?.current_plan_key && previous?.plan_key === worksheet.followup.current_plan_key;
  const pausePending = previous?.joint_pause_pending || previous?.reason === "joint_weakness" || previous?.reason === "joint_evidence_unknown";
  if (worksheet.review_plan.choice === "t_watch" && sameIntent && pausePending && (["market", "sector", "stock"] as const).some((key) => (worksheet.route_review?.evidence[key]?.status ?? "unknown") === "unknown")) return { title: "三维暂停依据尚未核清", detail: "部分大盘、板块或个股证据缺失，不能视为原暂停已解除；先核对来源，不沿用原回补条件。" };
  if (constraintsKey(watch.constraints) !== constraintsKey(worksheet.constraints) || watch.trade_revision !== worksheet.trade_revision) return { title: "原策略对应的持仓或条件已变化", detail: "重新保存试算不会更新原 AI 观察条件；请按最新持仓和个人条件重新分析后采用。" };
  if (!Number.isFinite(watch.price) || watch.price <= 0 || (watch.comparator !== "at_or_above" && watch.comparator !== "at_or_below")) return { title: "已保存观察条件不可核验", detail: "观察价或比较方向不完整，请重新分析并采用。" };
  if (watch.direction_schema !== "direction-v1") return { title: "原方向依据需要重新核对", detail: "旧方案未保存完整方向身份，请核对当前证据后重新分析并采用。" };
  if (strategyDirectionKey(watch.evidence_snapshot) !== strategyDirectionKey(worksheet.route_review?.evidence)) return { title: "原策略依据发生变化，重新分析", detail: "大盘、板块或个股的方向、来源或行业归属与采用时不同，先核对变化再决定是否保留原条件。" };
  const conditionView = positionChecksView(worksheet, now);
  if (conditionView && (watch.condition_contract?.schema_version === 2 || worksheet.strategy_condition_issue)) return { title: "下一步先核对什么", detail: conditionView.nextStep };
  const reached = watch.comparator === "at_or_above" ? worksheet.basis.price >= watch.price : worksheet.basis.price <= watch.price;
  return { title: reached ? "已到观察价，核对下一步条件" : "正在等待策略条件", detail: `当前 ${worksheet.basis.price.toFixed(2)} 元；报价${WATCH_COMPARATORS[watch.comparator]} ${watch.price.toFixed(2)} 元时提示复核。${reached ? "到价尚不能确认下方的转弱、止跌等条件。" : ""}` };
}
