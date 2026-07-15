import type { AqspSnapshotEnvelope } from "./aqsp-contract";

// Compile-time fixtures keep the bridge contract checked without adding a test runner.
export const aqspContractFixture = {
  data: {
    schema_version: "v1",
    generated_at: "2026-07-14T09:30:00+08:00",
    selected_date: "2026-07-14",
    available_dates: ["2026-07-14"],
    candidates: [],
    debates: [],
    summaries: [],
    source: {
      effective: "fixture",
      latest_trade_date: "2026-07-14",
      lag_days: 0,
      status: "fresh",
    },
    coldstart: { status: "完成", detail: "" },
    stale_after: "2026-07-15T09:30:00+08:00",
    message_status: "未产出",
    messages: [],
    market_context: null,
  },
  meta: { historical: false, stale: false },
} satisfies AqspSnapshotEnvelope;

export const aqspEmptyStateIsRepresentable =
  aqspContractFixture.data.messages.length === 0 &&
  aqspContractFixture.data.debates.length === 0;
