/** Stable read-only response shape for the AQSP -> Vibe-Research bridge. */

export interface AqspCandidate {
  symbol: string;
  display_name: string;
  score: number;
  research_status: string;
  next_step: string;
  context: string;
  deterministic_reasons: readonly string[];
  strategies: readonly string[];
  evidence_status: string;
}

export interface AqspMessage {
  title: string;
  summary: string;
  impact: string;
  category: string;
  source: string;
  published_at: string;
}

export interface AqspAgentDiscussion {
  symbol: string;
  display_name: string;
  conclusion: string;
  primary_risk_gate: string;
  next_trigger: string;
  active_roles: readonly string[];
}

export interface AqspSnapshot {
  schema_version: string;
  generated_at: string;
  selected_date: string;
  available_dates: readonly string[];
  candidates: readonly AqspCandidate[];
  debates: readonly AqspAgentDiscussion[];
  summaries: readonly string[];
  source: {
    effective: string;
    latest_trade_date: string;
    lag_days: number;
    status: string;
  };
  coldstart: { status: string; detail: string };
  stale_after: string;
  message_status: string;
  messages: readonly AqspMessage[];
}

export interface AqspSnapshotEnvelope {
  data: AqspSnapshot;
  meta: { historical: boolean; stale: boolean };
}
