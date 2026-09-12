// 候选并排对比的契约断言（由 npm test 真正执行）。
import {
  CANDIDATE_METRICS,
  MAX_COMPARE_SYMBOLS,
  buildCompareMatrix,
  parseCompareSymbols,
  toggleCompareSymbol,
} from "./candidate-compare";
import type { CandidateRow } from "./daily-view";

const row = (over: Partial<CandidateRow>): CandidateRow =>
  ({
    symbol: "000000",
    name: "X",
    score: 50,
    scoreText: "50",
    status: "通过筛选",
    evidence: "已闭环",
    ready: false,
    strategies: "",
    keyMetric: "",
    messageCount: 0,
    firstMessageTitle: "",
    debateRounds: null,
    debateConclusion: "",
    variantCount: 0,
    nextStep: "",
    context: "",
    reasons: [],
    metrics: [],
    breakdown: [],
    provenance: "",
    ...over,
  }) as CandidateRow;

const rows: CandidateRow[] = [
  row({ symbol: "000001", name: "A", score: 60, scoreText: "60", ready: false, messageCount: 3, debateRounds: 2, variantCount: 1, keyMetric: "20日涨幅 +6.0%", nextStep: "等回踩" }),
  row({ symbol: "000002", name: "B", score: 90, scoreText: "90", ready: true, messageCount: 1, debateRounds: null, variantCount: 4, keyMetric: "20日涨幅 +18.4%" }),
];

const available = ["000001", "000002", "000003"];
const matrix = buildCompareMatrix(rows, ["000002", "000001"]);

export const candidateCompareContract = {
  /* ---- 解析与开关 ---- */
  parsesKnownSymbols: parseCompareSymbols("000001,000002", available).join("|") === "000001|000002",
  dropsUnknownSymbols: parseCompareSymbols("000001,999999", available).join("|") === "000001",
  dedupesRepeated: parseCompareSymbols("000001,000001", available).length === 1,
  nullYieldsEmpty: parseCompareSymbols(null, available).length === 0,
  respectsCap: parseCompareSymbols(available.join(","), available).length <= MAX_COMPARE_SYMBOLS,
  toggleAdds: toggleCompareSymbol(["000001"], "000002").join("|") === "000001|000002",
  toggleRemoves: toggleCompareSymbol(["000001", "000002"], "000002").join("|") === "000001",
  // 满了不动，不静默丢弃
  toggleRespectsMax: toggleCompareSymbol(["000001", "000002"], "000003", 2).length === 2,
  toggleAllowsBelowMax: toggleCompareSymbol(["000001"], "000002", 2).length === 2,

  /* ---- 矩阵 ---- */
  // 列顺序跟随传入顺序（用户点选顺序），而不是候选列表顺序
  columnOrderFollowsInput: matrix.columns.map((c) => c.symbol).join("|") === "000002|000001",
  columnsCarryNames: matrix.columns[0].name === "B",
  metricCountMatchesDefs: matrix.metrics.length === CANDIDATE_METRICS.length,
  valuesIndexedByMetricThenSymbol: matrix.values.score["000002"] === "90",
  readyIsHumanReadable: matrix.values.ready["000002"].includes("可进入纸面复核"),
  notReadyIsHonest: matrix.values.ready["000001"].includes("仅观察"),
  missingDebateSaysSo: matrix.values.debate["000002"] === "未讨论",
  messageCountFormatted: matrix.values.messages["000001"] === "3 条",
  emptyFieldFallsBack: matrix.values.nextStep["000002"] === "—",

  /* ---- 边界 ---- */
  unknownSymbolDropped: buildCompareMatrix(rows, ["000001", "999999"]).columns.length === 1,
  emptySelectionNoColumns: buildCompareMatrix(rows, []).columns.length === 0,
  emptyRowsNoColumns: buildCompareMatrix([], ["000001"]).columns.length === 0,
  missingValueIsDash: buildCompareMatrix([], []).values.score !== undefined,
};
