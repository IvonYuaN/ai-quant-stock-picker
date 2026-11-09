import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import { MemoryRouter } from "react-router-dom";
import { StrategyAttentionItem, StrategyRecordDetails } from "../components/aqsp/TodayStrategyBoard";
import type { StrategyAttention } from "./today-strategy";

const item: StrategyAttention = { key: "position:root-a", kind: "position", symbol: "600000", name: "合成持仓", title: "风险记录待复核", reason: "原检查时风险线触发", next: "用当前依据核对继续持有、减仓和做 T", href: "/my/holdings/root-a/plan?from=%2Ftoday", observedAt: "2026-10-26T10:00:00+08:00", quoteAsOf: null, priority: 0 };
const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(StrategyAttentionItem, { item, index: 0 })));
const details = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(StrategyRecordDetails)));
export const strategyAttentionDisplayContracts = {
  onePrimaryEntryPerStock: (html.match(/<a /g) ?? []).length === 1 && html.includes("复核当前方案"),
  stockHasNameCodeReasonAndNext: ["合成持仓", "600000", "原检查时风险线触发", "下一步", "用当前依据"].every((text) => html.includes(text)),
  previousCheckNeverClaimsCurrentTrigger: html.includes("记录时点") && html.includes("当前是否仍成立需重核") && !html.includes("现在触发"),
  stableStockIdentityForAcceptance: html.includes('data-strategy-symbol="600000"'),
  // 2026-11-09：完整风险检查与观察记录已拆成 /alerts、/observations 两个模块页，
  // 这里不再用折叠容器承载，改成两个直接入口；断言入口可发现且不再重复挂载读取器。
  fullRecordControlsRemainDiscoverable:
    details.includes("风险检查与观察记录") && details.includes('href="/monitor"'),
  collapsedRecordsDoNotMountDuplicateReaders:
    !details.includes("检查持仓并通知") && !details.includes("我的候选观察") && !details.includes("<details"),
};
