// 展示层映射的契约断言（由 npm test 真正求值）。
//
// 两条铁律必须钉住：
//   1. 已知码 → 固定的中文名（界面口径不漂移）；
//   2. 未知码 → 原样透传（界面不撒谎、不吞码，后端加新码时至少可排查）。
import {
  evidenceLabel,
  factorLabel,
  gateReasonLabel,
  ledgerStatusLabel,
  ratingLabel,
  roleLabel,
  statusLabel,
  strategyLabel,
  strategyListLabel,
} from "./display-labels";

export const displayLabelsContract = {
  /* ---- 评级（与 src/aqsp/ratings.py 同源） ---- */
  ratingStrong: ratingLabel("strong_buy_candidate") === "重点跟踪",
  ratingBuy: ratingLabel("buy_candidate") === "继续观察",
  ratingWatch: ratingLabel("watch") === "观察名单",
  ratingAvoid: ratingLabel("avoid") === "回避（仅观察）",
  ratingUnknownPassthrough: ratingLabel("brand_new_rating") === "brand_new_rating",
  ratingEmptyDefault: ratingLabel("") === "仅观察",
  ratingNullDefault: ratingLabel(null) === "仅观察",

  /* ---- 候选状态 / 证据 ---- */
  statusValidated: statusLabel("validated") === "已复核",
  statusPending: statusLabel("pending") === "待复核",
  statusBreaker: statusLabel("blocked_by_circuit_breaker") === "风控拦截",
  statusUnknownPassthrough: statusLabel("some_new_status") === "some_new_status",
  statusEmptyDefault: statusLabel("") === "状态未记录",
  evidenceReady: evidenceLabel("evidence_ready") === "证据已就绪",
  evidenceDefault: evidenceLabel("") === "证据不足",

  /* ---- 门禁原因 ---- */
  gateMissing: gateReasonLabel("recommendation_gate_missing") === "门禁状态未记录",
  gateByteBudget: gateReasonLabel("index_byte_budget") === "当日快照超出容量上限，推荐状态未生成",
  gateUnknownPassthrough: gateReasonLabel("freshness_not_ready") === "freshness_not_ready",

  /* ---- 台账状态分布 ---- */
  ledgerValidated: ledgerStatusLabel("validated") === "已结算",
  ledgerPending: ledgerStatusLabel("pending") === "待结算",
  ledgerUnknownPassthrough: ledgerStatusLabel("x") === "x",

  /* ---- 策略名（目录全集抽样） ---- */
  strategyMomentum: strategyLabel("momentum") === "动量",
  strategyComposite: strategyLabel("composite") === "综合评分",
  strategyRpsMomentum: strategyLabel("rps_momentum") === "相对强弱动量",
  strategyMeanReversion: strategyLabel("mean_reversion") === "均值回归",
  strategyUnknownPassthrough: strategyLabel("custom_mine") === "custom_mine",
  strategyEmptyDefault: strategyLabel("") === "—",
  // 列表口径：空码被过滤，未知码原样透传（界面不藏策略），已知码翻译 —— 未知优先保留可排查性
  strategyListJoins: strategyListLabel(["ma_pullback", "bowl_rebound", "momentum", ""]) ===
    "ma_pullback · bowl_rebound · 动量",
  strategyListEmpty: strategyListLabel([]) === "—",
  strategyListNull: strategyListLabel(null) === "—",

  /* ---- IC 因子（runner 7 因子） ---- */
  factorMomentum: factorLabel("momentum") === "动量",
  factorTripleRise: factorLabel("triple_rise") === "三连涨",
  factorHtf: factorLabel("htf") === "高位窄幅整理",
  factorMr: factorLabel("mr") === "均值回归",
  factorVolume: factorLabel("volume") === "成交量突破",
  factorRps: factorLabel("rps") === "相对强弱",
  factorUnknownPassthrough: factorLabel("new_factor") === "new_factor",

  /* ---- 辩论角色（settings debate_roles 全集） ---- */
  roleBull: roleLabel("bull") === "看多方",
  roleBear: roleLabel("bear") === "看空方",
  roleRisk: roleLabel("risk_control") === "风控",
  roleSector: roleLabel("sector_leader") === "板块龙头",
  rolePolicy: roleLabel("policy_sensitive") === "政策敏感",
  roleNorthbound: roleLabel("northbound") === "北向资金",
  roleUnknownPassthrough: roleLabel("quant_analyst") === "quant_analyst",
};
