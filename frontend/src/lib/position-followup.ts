import { api, type ActionWorksheet, type FollowupMode, type FollowupObservation, type Holding } from "./api";
import { worksheetGuard } from "./position-workspace";
import type { PositionAlertGroup } from "./alert-view";

export const FOLLOWUP_MODES: Record<FollowupMode, string> = {
  off: "只记录在站内，不向外发送",
  changes: "状态变化时提醒，同一计划同一状态最多一次",
  daily: "需要关注时提醒，同一状态每天最多一次",
};

/** Never present another position's or a superseded plan's observation as current. */
export function followupView(worksheet: ActionWorksheet): { observation: FollowupObservation | null; stale: boolean; issue: string } {
  const snapshot = worksheet.followup?.last_check;
  const matching = !!snapshot && !!worksheet.position_id && snapshot.position_id === worksheet.position_id && snapshot.position_snapshot?.position_id === worksheet.position_id;
  return {
    observation: matching ? snapshot : null,
    stale: !!worksheet.followup?.requires_refresh || (!!snapshot && (!matching || snapshot.revision !== worksheet.revision)),
    issue: worksheet.followup?.issue || (snapshot && !matching ? "后台记录的持仓身份待核对" : ""),
  };
}

/** The home endpoint excludes quiet states; additionally match the currently owned root. */
export function visibleFollowups(items: readonly FollowupObservation[], holdings: readonly Holding[]): FollowupObservation[] {
  const roots = new Map(holdings.filter((row) => row.shares > 0 && row.position_id).map((row) => [row.position_id, row.code]));
  const seen = new Set<string>();
  return items.filter((row) => {
    if (!row?.position_id || seen.has(row.position_id) || roots.get(row.position_id) !== row.symbol || row.position_snapshot?.position_id !== row.position_id || !["price_watch", "below_watch", "paused", "needs_review", "evidence_unavailable"].includes(row.state)) return false;
    seen.add(row.position_id); return true;
  });
}

export function followupForRiskGroup(items: readonly FollowupObservation[], group: PositionAlertGroup): FollowupObservation | undefined {
  return items.find((row) => row.position_id === group.holding?.position_id && row.position_id === group.latest.position_id);
}

export function followupsWithoutRiskCards(items: readonly FollowupObservation[], groups: readonly PositionAlertGroup[]): FollowupObservation[] {
  const roots = new Set(groups.map((group) => followupForRiskGroup(items, group)?.position_id).filter(Boolean));
  return items.filter((row) => !roots.has(row.position_id));
}

export function followupResponseIsCurrent(basis: ActionWorksheet, current: ActionWorksheet, next: ActionWorksheet): boolean {
  return current.position_id === basis.position_id && current.revision === basis.revision && current.followup?.mode === basis.followup?.mode && next.position_id === basis.position_id && next.position_snapshot.position_id === basis.position_id && next.revision === basis.revision && !!next.followup;
}

export async function saveFollowupMode(worksheet: ActionWorksheet, mode: FollowupMode): Promise<ActionWorksheet> {
  const guard = worksheetGuard(worksheet);
  if (worksheet.position_id !== guard.expected_position.position_id) throw new Error("当前持仓身份不一致，请返回持仓列表重新进入。");
  if (!worksheet.followup) throw new Error("通知设置尚未读取，请更新后重试。");
  return api.savePositionFollowup(guard.expected_position.position_id!, mode, worksheet.followup.mode, guard.expected_position, guard.expected_revision);
}
