// 展示层「黑话 → 人话」单一映射层。
//
// 后端契约（英文码）是稳定接口，界面是给人看的：本层只负责「显示时翻译」，
// 不动任何数据字段 / 代码 / 注释。两个硬规则：
//   1. **未知码原样透传**（绝不吞、不猜）——后端新增码时界面至少不撒谎；
//   2. 同一码全站只在这里映射一次，页面/组件/图表图例共用，天然口径一致。
//
// 各码表的取值来源（2026-09-29 盘点）：
//   评级       = src/aqsp/ratings.py RATING_LABELS + TRADABLE_RATINGS
//   台账状态   = 台账 status 值域（validated/pending/watch_only/…）
//   策略名     = src/aqsp/strategies/* 的 name 字段（catalog 全集）
//   IC 因子    = scripts/analysis/ic_diagnosis.py _BASE_FACTORS + _EXTRA_CHOICES
//   辩论角色   = config/settings.*.yaml debate_roles
//
// 前端契约测试见 display-labels.test.ts（npm test 真正求值）。

type LabelMap = Readonly<Record<string, string>>;

function mapOf<M extends LabelMap>(value: string, map: M, fallback?: string): string {
  const key = value.trim();
  if (!key) return (fallback ?? "—").trim();
  return map[key] ?? key;
}

/* ---------------------------------------------------------------- 评级（信号台账） */

const RATING_LABELS: LabelMap = {
  strong_buy_candidate: "重点跟踪",
  buy_candidate: "继续观察",
  watch: "观察名单",
  avoid: "回避（仅观察）",
};

/** 信号评级码 → 中文（与后端 src/aqsp/ratings.py 同源）。未知码原样透传。 */
export function ratingLabel(code: string | null | undefined): string {
  return mapOf(code ?? "", RATING_LABELS, "仅观察");
}

/* ------------------------------------------------------- 候选状态 / 证据 */

const STATUS_LABELS: LabelMap = {
  validated: "已复核",
  pending: "待复核",
  watch_only: "仅观察",
  not_executable: "无法复核",
  blocked_by_circuit_breaker: "风控拦截",
  run_completed_no_picks: "无候选",
};

/** 候选研究状态码 → 中文（与绩效页 STATUS_LABELS 同源）。未知码原样透传。 */
export function statusLabel(code: string | null | undefined): string {
  return mapOf(code ?? "", STATUS_LABELS, "状态未记录");
}

const EVIDENCE_LABELS: LabelMap = {
  evidence_not_ready: "证据未就绪",
  evidence_ready: "证据已就绪",
  evidence_insufficient: "证据不足",
  evidence_missing: "证据未记录",
};

/** 证据状态码 → 中文（缺省「证据不足」与后端默认一致）。未知码原样透传。 */
export function evidenceLabel(code: string | null | undefined): string {
  return mapOf(code ?? "", EVIDENCE_LABELS, "证据不足");
}

/* ------------------------------------------------------- 门禁原因码 */

const GATE_REASON_LABELS: LabelMap = {
  recommendation_gate_missing: "门禁状态未记录",
  index_byte_budget: "当日快照超出容量上限，推荐状态未生成",
};

/** 门禁未放行原因码 → 人话。未知码原样透传（保留可排查性）。 */
export function gateReasonLabel(code: string | null | undefined): string {
  return mapOf(code ?? "", GATE_REASON_LABELS, "未记录");
}

/* ------------------------------------------------------- 台账状态分布 */

const LEDGER_STATUS_LABELS: LabelMap = {
  validated: "已结算",
  pending: "待结算",
  watch_only: "仅观察",
  not_executable: "无法复核",
  blocked_by_circuit_breaker: "风控拦截",
  run_completed_no_picks: "无候选",
};

/** 台账状态码 → 中文（绩效页台账分布用；与 STATUS_LABELS 语义一致但默认文案不同）。 */
export function ledgerStatusLabel(code: string | null | undefined): string {
  return mapOf(code ?? "", LEDGER_STATUS_LABELS, "未记录");
}

/* ------------------------------------------------------- 策略名 */

const STRATEGY_LABELS: LabelMap = {
  momentum: "动量",
  triple_rise: "三连涨",
  composite: "综合评分",
  mean_reversion: "均值回归",
  volume_breakout: "放量突破",
  ma_breakout: "均线突破",
  morning_breakout: "早盘突破",
  closing_premium: "尾盘溢价",
  n_rebound: "N 字反弹",
  limit_up_ladder: "连板梯队",
  sector_rotation: "板块轮动",
  multi_factor_rotation: "多因子轮动",
  rotation_sideways: "横盘轮动",
  event_driven: "事件驱动",
  intraday_trade: "盘中交易",
  national_team_filter: "国家队过滤",
  quality: "质量",
  value: "价值",
  rps_momentum: "相对强弱动量",
};

/** 策略码 → 中文（策略目录全集；台账里可能出现的混用名如 rps_momentum 已一并覆盖）。未知原样透传。 */
export function strategyLabel(code: string | null | undefined): string {
  return mapOf(code ?? "", STRATEGY_LABELS, "—");
}

/** 一组策略码 → 「中文 · 中文」展示串（空列表给 "—"）。 */
export function strategyListLabel(codes: readonly string[] | null | undefined): string {
  const labels = (codes ?? []).map((code) => strategyLabel(code)).filter((label) => label !== "—");
  return labels.length ? labels.join(" · ") : "—";
}

/* ------------------------------------------------------- IC 因子 */

const FACTOR_LABELS: LabelMap = {
  momentum: "动量",
  triple_rise: "三连涨",
  composite: "综合",
  htf: "高位窄幅整理",
  mr: "均值回归",
  volume: "成交量突破",
  rps: "相对强弱",
};

/** 因子码 → 中文（IC 趋势图例 / 指标卡用，与 runner 7 因子对齐）。未知原样透传。 */
export function factorLabel(code: string | null | undefined): string {
  return mapOf(code ?? "", FACTOR_LABELS, "—");
}

/* ------------------------------------------------------- 辩论角色 */

const ROLE_LABELS: LabelMap = {
  bull: "看多方",
  bear: "看空方",
  risk_control: "风控",
  sector_leader: "板块龙头",
  policy_sensitive: "政策敏感",
  northbound: "北向资金",
};

/** 辩论角色码 → 中文（settings debate_roles 全集）。未知原样透传。 */
export function roleLabel(code: string | null | undefined): string {
  return mapOf(code ?? "", ROLE_LABELS, "—");
}
