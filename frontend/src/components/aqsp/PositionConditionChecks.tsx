import type { ActionWorksheet, DiscussionAction, PositionDiscussion } from "@/lib/api";
import { positionActionChecks, positionChecksView } from "@/lib/position-conditions";
import { ActionChecks, ConditionChecksPanel } from "./CandidateConditionChecks";

export function PositionActionChecks({ action, result }: { action: DiscussionAction; result: PositionDiscussion }) {
  return <ActionChecks view={positionActionChecks(action, result)} />;
}

export function PositionConditionChecks({ worksheet, now }: { worksheet: ActionWorksheet; now?: number }) {
  const view = positionChecksView(worksheet, now);
  return view ? <><ConditionChecksPanel view={view} label="持仓条件逐项检查" showNextStep={view.mode === "legacy" && !worksheet.strategy_condition_issue} />{worksheet.strategy_condition_issue ? <details><summary>无法逐项检查的原因</summary><p>{worksheet.strategy_condition_issue}</p></details> : null}</> : null;
}
