import { dataCoverageVerdict } from "./data-coverage-view";
import type { DataCoveragePayload } from "./api";

const incomplete: DataCoveragePayload = {
  available: true,
  db_path: "A股量化分析数据/astocks_raw.db",
  market_max_date: "20261102",
  total_symbols: 5728,
  symbols_on_max_date: 1,
  coverage_pct: 0.000175,
  sentinel_symbols: ["000001.SZ"],
  min_coverage_ratio: 0.8,
  market_date_usable: false,
  verified_market_date: null,
};
const complete: DataCoveragePayload = {
  ...incomplete,
  symbols_on_max_date: 5728,
  coverage_pct: 1,
  sentinel_symbols: [],
  market_date_usable: true,
  verified_market_date: "20261102",
};

const partial = dataCoverageVerdict(incomplete);
const full = dataCoverageVerdict(complete);
const missing = dataCoverageVerdict({ available: false, reason: "库缺失" });
const unreadable = dataCoverageVerdict(null, "覆盖接口 503");
const pending = dataCoverageVerdict(null);

export const dataCoverageContracts = {
  singleSentinelIsNotUsable: partial.usable === false && partial.tone === "warn",
  singleSentinelShowsRealCoverage: partial.detail.includes("1/5728")
    && partial.detail.includes("000001.SZ")
    && partial.detail.includes("fail-closed"),
  singleSentinelNeverShowsVerifiedDate: incomplete.verified_market_date === null
    && !partial.detail.includes("覆盖通过"),
  completeCoverageIsUsable: full.usable === true && full.tone === "ok"
    && full.detail.includes("20261102"),
  missingDatabaseIsNotUsable: missing.usable === false && missing.detail.includes("库缺失"),
  readFailureIsNotUsable: unreadable.usable === false && unreadable.detail.includes("503"),
  pendingStateIsNotUsable: pending.usable === false && pending.tone === "neutral",
};
