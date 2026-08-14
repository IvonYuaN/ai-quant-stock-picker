import type { AqspCandidate, AqspSnapshot } from "@/types/aqsp";
import { allCandidatesResearchReady, candidateName, historicalVariantCount, latestVariantDate, messagesForCandidate, sourceCoverageLines } from "./candidate-chain";

const candidate = { symbol: "300639", display_name: "300639 300639 凯普生物" } satisfies Pick<AqspCandidate, "symbol" | "display_name">;
const snapshot = {
  market_context: { status: "", overview: "", cross_market: [], warnings: [], summary_lines: ["来源覆盖: 国内 9/9 路", "消息结果: 无高影响事件", "实时跨市: partial"] },
  variants: [{ end_date: "2026-08-12", holdings: [{ symbol: "300639" }] }],
} as unknown as AqspSnapshot;

export const candidateChainContract = {
  repeatedSymbolIsRemoved: candidateName(candidate) === "凯普生物",
  coverageDoesNotPretendToBeMessages: sourceCoverageLines(snapshot).length === 2,
  historicalCoverageIsCandidateSpecific: historicalVariantCount(snapshot, "300639") === 1 && historicalVariantCount(snapshot, "000001") === 0,
  variantDateIsExplicit: latestVariantDate(snapshot) === "2026-08-12",
  unsourcedMessageDoesNotCount: messagesForCandidate([{ title: "", summary: "", impact: "", category: "", source: "", published_at: "", affected_symbols: ["300639"] }], "300639").length === 0,
  incompleteChainFailsClosed: !allCandidatesResearchReady({ ...snapshot, candidates: [{ ...candidate, score: 1, research_status: "", next_step: "", context: "", deterministic_reasons: [], strategies: [], evidence_status: "" }], messages: [], debates: [], research_chain: { status: "linked", candidate_symbols: ["300639"], debated_symbols: [], pending_review_symbols: ["300639"], variant_candidate_symbols: [], variant_review_symbols: [], variant_holding_candidate_symbols: [], variant_holding_review_symbols: [], blocker: "" } } as AqspSnapshot),
};
