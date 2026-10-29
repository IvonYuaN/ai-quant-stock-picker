import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import type { StrategyReviewContext } from "./api";
import { originalReviewPrice, reviewEvidenceText, strategyReviewView } from "./strategy-review-context";
import { StrategyReviewContextPanel } from "../components/aqsp/StrategyReviewContextPanel";

const ref = { label: "原有源前收盘价", price: 10, source: "合成行情源", as_of: "2026-10-27T10:00:00+08:00" };
const context: StrategyReviewContext = { kind: "candidate", status: "adopted", original: { choice: "pullback", condition: "回落后核对承接", invalidation: "行业走弱则重核", comparator: "at_or_below", price_ref: ref, saved_at: ref.as_of, evidence_snapshot: {}, snapshot_complete: true }, current: { evidence_snapshot: {}, price_refs: { low: { ...ref, price: 12 } } }, changes: [{ category: "sector", label: "板块背景", before: { status: "rising", summary: "原板块走强", source: "合成源" }, after: { status: "falling", summary: "本次板块走弱", source: "合成源" } }], missing_evidence: [], material_changed: true, quote_updated: true, notice: "不自动改写人工选择" };
const html = renderToStaticMarkup(createElement(StrategyReviewContextPanel, { context }));
const noContext = renderToStaticMarkup(createElement(StrategyReviewContextPanel, {}));
const noAdoption = renderToStaticMarkup(createElement(StrategyReviewContextPanel, { context: { ...context, status: "not_adopted", original: null } }));
const mismatch = renderToStaticMarkup(createElement(StrategyReviewContextPanel, { context: { ...context, status: "identity_mismatch" } }));
const missing = renderToStaticMarkup(createElement(StrategyReviewContextPanel, { context: { ...context, original: { ...context.original!, snapshot_complete: false }, missing_evidence: ["原大盘证据缺失"] } }));
export const strategyReviewContextContracts = {
  unchangedQuoteDoesNotMeanExpired: !strategyReviewView({ ...context, material_changed: false, quote_updated: false })?.state.includes("过期"),
  marketDirectionChangesAreReadable: reviewEvidenceText({ market: { breadth: "偏弱", indices: [{ name: "上证指数", direction: "down", source: "指数源" }] } }).includes("市场宽度：偏弱；指数背景：名称：上证指数；涨跌方向：下跌；来源：指数源"),
  compatibilityWithoutNewField: noContext === "" && noAdoption === "",
  defaultDisclosureCollapsed: html.includes('<details class="aq-strategy-review-context">') && !html.includes(' open=""'),
  stableReviewSummary: html.includes("原方案与当前依据对比"),
  savedRouteAndBothConditionsVisible: html.includes("等待回落") && html.includes("回落后核对承接") && html.includes("行业走弱则重核"),
  beforeAndAfterEvidenceStayDistinct: html.includes("原板块走强") && html.includes("本次板块走弱") && html.includes("采用时") && html.includes("这次"),
  originalReferenceDoesNotDriftToCurrent: html.includes("10.00 元") && !html.includes("12.00 元"),
  idsCannotResolveThroughCurrentPrices: originalReviewPrice({ ...context, original: { ...context.original!, price_ref: "low" } }).includes("未完整保存"),
  sourceAndTimeStayVisible: html.includes("合成行情源") && html.includes(ref.as_of),
  oldMissingEvidenceNeverBackfilled: missing.includes("原采用时的证据没有完整保存") && missing.includes("原大盘证据缺失"),
  identityMismatchCannotRevealForeignOriginal: mismatch.includes("身份不一致") && !mismatch.includes("回落后核对承接") && !mismatch.includes("原板块走强"),
  quoteOnlyIsNotMaterialChange: strategyReviewView({ ...context, material_changed: false })?.state.includes("报价变化本身不代表原方案已失效") === true,
  incompletePremisesCannotClaimStablePlan: strategyReviewView({ ...context, material_changed: false, missing_evidence: ["原方向依据版本缺失"] })?.state.includes("尚不能确认原方案是否仍然成立") === true,
  noForcedDecisionWhenStable: strategyReviewView({ ...context, material_changed: false, quote_updated: false })?.state.includes("未记录到实质依据变化") === true,
  unknownSourceDoesNotBecomeCurrentFact: reviewEvidenceText(null) === "未保存或不可用",
  evidenceTextNeverBecomesRawJson: reviewEvidenceText({ status: "unknown", summary: "板块缺失" }) === "状态：未知；依据：板块缺失",
  actualCandidateSnapshotHasReadableScoreAndReasons: reviewEvidenceText({ symbol: "600000", display_name: "合成候选", score: 75, deterministic_reasons: ["趋势通过"], freshness: "fresh", data_source: "synthetic", data_fetched_at: ref.as_of }).includes("确定性评分：75；评分依据：趋势通过；数据新鲜度：新鲜"),
  positionSnapshotHasReadableCostAndQuantity: reviewEvidenceText({ position_id: "root-a", cost: 12, shares: 1000, sellable_shares: 500 }) === "持仓成本（元）：12；持有股数：1000；可卖股数：500",
  actualDirectionsShowIndustryAndSources: reviewEvidenceText({ market: { status: "weak", source: "行情源", industry: "银行" }, sector: { status: "strong" } }).includes("大盘：状态：偏弱；来源：行情源；行业：银行；板块：状态：偏强"),
  personalConstraintsUseContextualAttentionLabel: reviewEvidenceText({ attention: "unavailable", extra_cash: 0, loss_limit: 1000 }) === "盯盘条件：没有时间盯盘；新增资金上限（元）：0；新增亏损上限（元）：1000",
  actualRiskSnapshotHasReadableRuleAndFacts: reviewEvidenceText({ rules: [{ level: "L1", status: "triggered", threshold: 9 }], current_facts: ["报价跌破原观察线"] }).includes("风险规则：风险类型：成本止损；状态：已触发；阈值：9；本次风险事实：报价跌破原观察线"),
  unknownSavedFieldsAreNotCalledMissingEvidence: reviewEvidenceText({ future_field: 123 }) === "详细字段未在此摘要展开",
};
