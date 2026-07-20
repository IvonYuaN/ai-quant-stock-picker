/** Stable read-only response shape for the AQSP research API. */

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
  score_breakdown?: readonly string[];
  technical_metrics?: readonly { key: string; label: string; value: string }[];
  data_source?: string;
  data_fetched_at?: string;
  data_timestamp_source?: string;
  freshness?: string;
}

export interface AqspMessage {
  title: string;
  summary: string;
  impact: string;
  category: string;
  source: string;
  published_at: string;
  url?: string;
  source_region?: string;
  source_quality?: string;
  event_type?: string;
  affected_sectors?: readonly string[];
  affected_symbols?: readonly string[];
  transmission_hypothesis?: string;
  supporting_evidence?: readonly string[];
  source_url?: string;
  transmission_path?: readonly string[];
  validation_signals?: readonly string[];
  verification?: string;
  invalidation_signals?: readonly string[];
}

export interface AqspCrossMarket {
  rule_id: string;
  theme: string;
  strength: string;
  action: string;
  source_title: string;
  source_region: string;
  source_published_at: string;
  affected_sectors: readonly string[];
  transmission_path: readonly string[];
  validation_signals: readonly string[];
  invalidation_signals: readonly string[];
  summary: string;
}

export interface AqspMarketContext {
  status: string;
  overview: string;
  summary_lines: readonly string[];
  cross_market: readonly AqspCrossMarket[];
  warnings: readonly string[];
}

export interface AqspRecommendationGate {
  recommendation_allowed: boolean;
  status: string;
  reasons: readonly string[];
}

export interface AqspPhase {
  task_id: string; label: string; status: string;
  candidate_count: number; unique_symbols: number; overlap_symbols: number;
  updated_at?: string;
}

export interface AqspUniverse {
  total: number; resolved: number; screened: number; final: number;
  max_universe: number; source?: string; batch_active?: boolean; batch_id?: string;
  batch_size?: number; cycle_id?: number; coverage_pct?: number; last_error?: string;
}

export interface AqspAgentResult {
  symbol: string;
  display_name: string;
  conclusion: string;
  primary_risk_gate: string;
  next_trigger: string;
  active_roles: readonly string[];
  round_count: number;
  bull_count: number;
  bear_count: number;
  neutral_count: number;
  process_summary: string;
  round_summaries?: readonly string[];
  viewpoint_buckets?: Readonly<Record<string, readonly string[]>>;
  disagreement_points?: readonly string[];
  uncertainty_points?: readonly string[];
}

export type AqspAgentDiscussion = AqspAgentResult;

export interface AqspSourceHealth {
  effective: string;
  latest_trade_date: string;
  lag_days: number;
  status: string;
}

export interface AqspSnapshot {
  schema_version: string;
  generated_at: string;
  selected_date: string;
  available_dates: readonly string[];
  candidates: readonly AqspCandidate[];
  debates: readonly AqspAgentResult[];
  summaries: readonly string[];
  source: AqspSourceHealth;
  coldstart: { status: string; detail: string };
  stale_after: string;
  message_status: string;
  messages: readonly AqspMessage[];
  market_context: AqspMarketContext | null;
  recommendation_gate?: AqspRecommendationGate;
  phases?: readonly AqspPhase[];
  universe?: AqspUniverse;
  /** Present after the HTTP envelope is normalized; absent in the raw data payload. */
  meta?: AqspSnapshotMeta;
}

export interface AqspSnapshotMeta {
  historical: boolean;
  stale: boolean;
  freshness?: {
    candidates: string;
    messages: string;
    cross_market: string;
  };
}

export interface AqspSnapshotEnvelope {
  data: AqspSnapshot;
  meta: AqspSnapshotMeta;
}

export interface AqspDateIndex {
  selected_date: string;
  available_dates: readonly string[];
}

/** Snapshot data after the HTTP envelope is normalized for the existing view layer. */
export interface AqspSnapshotView extends AqspSnapshot {
  meta: AqspSnapshotMeta;
}
