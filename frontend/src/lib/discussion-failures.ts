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
  upstream_status: "模型服务暂未接受请求", truncated_output: "模型返回的内容不完整",
  empty_output: "模型没有返回分析内容",
};
const roleNames: Record<string, string> = { trend: "趋势分析", t_review: "做 T 评估", entry: "回落评估", risk: "风险复核" };
type Failure = { failure_kind?: string; contract_issue_code?: string };

function ownValue(values: Record<string, string>, key: string | undefined): string | undefined {
  return key && Object.prototype.hasOwnProperty.call(values, key) ? values[key] : undefined;
}

function reason(failure: Failure): string {
  return ownValue(contractReasons, failure.contract_issue_code) ?? ownValue(failureReasons, failure.failure_kind) ?? "输出未通过证据和条件核验";
}

/** Explain only the latest attempt per role; never echo rejected model text. */
export function discussionFailureReasons(result: PositionDiscussion | CandidateDiscussion): Array<{ label: string; detail: string }> {
  const latest = new Map<string, { status: string } & Failure>();
  for (const round of result.rounds) for (const row of round.opinions) latest.set(row.role, row);
  const failures = [...latest].filter(([, row]) => row.status === "failed")
    .map(([role, row]) => ({ label: ownValue(roleNames, role) ?? "其他角色", detail: reason(row) }));
  if (!result.summary && result.summary_failure_kind && result.summary_failure_kind !== "insufficient_roles") {
    failures.push({ label: "汇总", detail: reason({ failure_kind: result.summary_failure_kind, contract_issue_code: result.summary_contract_issue_code }) });
  }
  return failures;
}
