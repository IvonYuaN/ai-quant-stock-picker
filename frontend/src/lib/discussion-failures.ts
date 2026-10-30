import type { CandidateDiscussion, PositionDiscussion } from "./api";

const contractReasons: Record<string, string> = {
  action_numbers: "把股数、费用或缺乏依据的数字写进了行动条件",
  action_evidence: "把历史统计或不对应的价位用于当前行动",
  embedded_reference: "行动说明的引用方式不正确",
  evidence_reference: "引用了本次没有提供的证据",
  price_reference: "买卖观察缺少有效来源价位",
  condition_reference: "观察前提存在重复、冲突或缺少来源",
  peer_reference: "没有回应本次实际提供的同行观点",
  hard_constraint: "提出的动作与当前交易或风险限制冲突",
  route_contract: "没有完整比较三种处理路线",
};
const failureReasons: Record<string, string> = {
  timeout: "模型接口响应超时", transport: "模型连接未完成",
  upstream_status: "模型服务未接受请求，原因未明确；稍后重试，持续失败时核对服务状态",
  quota_exhausted: "服务商报告额度不足；需核对模型账户额度",
  rate_limited: "服务商报告请求限流；稍后手动重试",
  provider_busy: "模型服务暂时不可用；稍后手动重试",
  access_denied: "模型服务拒绝访问；需核对服务端授权配置",
  model_unavailable: "服务商报告模型不可用；需核对当前模型目录", truncated_output: "模型返回的内容不完整",
  empty_output: "模型没有返回分析内容",
};
const roleNames: Record<string, string> = { trend: "趋势分析", t_review: "做 T 评估", entry: "回落评估", risk: "风险复核" };
type Failure = { failure_kind?: string; contract_issue_code?: string; retry_after_seconds?: number | null };

function ownValue(values: Record<string, string>, key: string | undefined): string | undefined {
  return key && Object.prototype.hasOwnProperty.call(values, key) ? values[key] : undefined;
}

function reason(failure: Failure): string {
  const detail = ownValue(contractReasons, failure.contract_issue_code) ?? ownValue(failureReasons, failure.failure_kind) ?? "输出未通过证据和条件核验";
  const seconds = failure.retry_after_seconds;
  return typeof seconds === "number" && Number.isInteger(seconds) && seconds >= 0 && seconds <= 86400
    ? `${detail}。服务商建议至少等待 ${seconds} 秒；本次未自动重试` : detail;
}

/** Explain only the latest attempt per role; never echo rejected model text. */
export function discussionFailureReasons(result: PositionDiscussion | CandidateDiscussion): Array<{ label: string; detail: string }> {
  const latest = new Map<string, { status: string } & Failure>();
  for (const round of result.rounds) for (const row of round.opinions) latest.set(row.role, row);
  const failures = [...latest].filter(([, row]) => row.status === "failed")
    .map(([role, row]) => ({ label: ownValue(roleNames, role) ?? "其他角色", detail: reason(row) }));
  if (!result.summary && result.summary_failure_kind && result.summary_failure_kind !== "insufficient_roles") {
    failures.push({ label: "汇总", detail: reason({ failure_kind: result.summary_failure_kind, contract_issue_code: result.summary_contract_issue_code, retry_after_seconds: result.summary_retry_after_seconds }) });
  }
  const grouped = new Map<string, { label: string; detail: string }>();
  for (const failure of failures) {
    const existing = grouped.get(failure.detail);
    if (existing) existing.label += `、${failure.label}`;
    else grouped.set(failure.detail, { ...failure });
  }
  return [...grouped.values()];
}
