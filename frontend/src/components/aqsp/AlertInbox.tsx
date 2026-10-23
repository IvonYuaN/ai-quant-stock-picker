// P1-2 该卖提醒收件箱（首屏置顶模块）。
//
// 数据来自 `GET /api/alerts`（后端 alert_event 簿，#431），展示层只做三件事：
//   ① 归一化（alert-view，异常负载不白屏）
//   ② 两区派生（actionable = 今日可卖；watch = T+1 当日买入只能看着）
//   ③ 回写（已处理 / 忽略 → PATCH，刷新收件箱）
// 本文件不做任何风控判断——L1/L5 该不该卖是后端 live_risk 算好的，
// 这里只是把"该卖提醒"呈现给用户（副驾驶：给建议，不下单）。
import { useEffect, useState } from "react";
import { BellRing, CircleCheck, Eye, RefreshCw, X } from "lucide-react";
import {
  Badge,
  Card,
  ErrorState,
  LoadingState,
  SectionHeader,
  Tag,
} from "@/components/ui/primitives";
import { api, type AlertData, type AlertEvent } from "@/lib/api";
import { buildAlertInbox, normalizeAlerts, type AlertInboxView } from "@/lib/alert-view";
import { cn } from "@/lib/utils";

function AlertRow({
  event,
  actionable,
  onHandled,
  onIgnored,
}: {
  event: AlertEvent;
  actionable: boolean;
  onHandled: (id: string) => void;
  onIgnored: (id: string) => void;
}) {
  const basis = event.basis_json;
  return (
    <div className="aq-alert-row">
      <div className="aq-alert-row-main">
        <div className="aq-alert-row-title">
          <Tag tone={actionable ? "warn" : "neutral"}>{event.level}</Tag>
          <b>{event.symbol}</b>
          <Badge tone={actionable ? "warn" : "neutral"}>
            {actionable ? "该卖" : event.action_hint || "观察"}
          </Badge>
          {event.t1_note ? <Tag tone="neutral">{event.t1_note}</Tag> : null}
        </div>
        <p className="aq-alert-basis">
          {basis.rule ? <span>{basis.rule}</span> : null}
          {basis.actual ? <span> · {basis.actual}</span> : null}
        </p>
        <p className="aq-alert-basis aq-muted">
          现价 {event.trigger_at} ｜ 止损位 {event.threshold_at}
          {event.pnl_pct ? <> ｜ 盈亏 {(event.pnl_pct * 100).toFixed(1)}%</> : null}
        </p>
      </div>
      <div className="aq-alert-row-actions">
        <button
          type="button"
          className="aq-btn aq-btn-icon"
          onClick={() => onHandled(event.id)}
          title="标记已处理"
          aria-label="标记已处理"
        >
          <CircleCheck aria-hidden="true" />
        </button>
        <button
          type="button"
          className="aq-btn aq-btn-icon"
          onClick={() => onIgnored(event.id)}
          title="忽略这条"
          aria-label="忽略这条"
        >
          <X aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

export function AlertInbox() {
  const [data, setData] = useState<AlertData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const payload = await api.alerts();
      setData(normalizeAlerts(payload));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取该卖提醒失败");
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const transition = async (id: string, action: "handled" | "ignored") => {
    setBusy(true);
    try {
      await api.alertTransition(id, action);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const refreshRisk = async () => {
    setBusy(true);
    setError("");
    try {
      await api.runLiveRiskCheck(undefined, true);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "检查失败");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="aq-alert-inbox"><LoadingState label="正在读取该卖提醒…" /></div>;
  if (error && !data) {
    return (
      <div className="aq-alert-inbox">
        <ErrorState error={error} onRefresh={() => void load()} />
      </div>
    );
  }
  if (!data) return null;

  const view: AlertInboxView = buildAlertInbox(data);
  if (view.openCount === 0) {
    return (
      <div className="aq-alert-quiet">
        <BellRing aria-hidden="true" />
        <span>暂无未处理的持仓风控提醒</span>
        {error ? <span className="aq-tone-warn" role="alert">检查失败：{error}</span> : null}
        <button type="button" className="aq-btn" onClick={() => void refreshRisk()} disabled={busy} title="重新检查持仓 LIVE 风控">
          <RefreshCw aria-hidden="true" /> 检查持仓
        </button>
      </div>
    );
  }

  return (
    <Card accent="warning" className="aq-alert-inbox-card">
      {error ? <ErrorState error={error} onRefresh={() => void load()} /> : null}
      <SectionHeader
        title="今日该卖提醒"
        description="来自你实盘持仓的风控触发。先看「可卖」区，再决定动哪只；「明日生效」区是 T+1 当日买入、今天卖不掉的，先记着。"
        count={
          <span className="aq-alert-count">
            <b>{view.actionable.length}</b> 可卖 · <b>{view.watch.length}</b> 明日
          </span>
        }
        icon={BellRing}
      >
        <button type="button" className="aq-btn" onClick={() => void refreshRisk()} disabled={busy}>
          <RefreshCw aria-hidden="true" /> 重新检查
        </button>
      </SectionHeader>

      {view.actionable.length > 0 ? (
        <div className="aq-alert-group">
          <p className="aq-alert-group-label"><Eye aria-hidden="true" /> 可卖（今日 T+1 可执行）</p>
          {view.actionable.map((event) => (
            <AlertRow
              key={event.id}
              event={event}
              actionable
              onHandled={(id) => void transition(id, "handled")}
              onIgnored={(id) => void transition(id, "ignored")}
            />
          ))}
        </div>
      ) : null}

      {view.watch.length > 0 ? (
        <div className={cn("aq-alert-group", view.actionable.length === 0 && "aq-alert-group-first")}>
          <p className="aq-alert-group-label aq-muted">明日生效 / 观察（T+1 当日买入，今日不可卖出）</p>
          {view.watch.map((event) => (
            <AlertRow
              key={event.id}
              event={event}
              actionable={false}
              onHandled={(id) => void transition(id, "handled")}
              onIgnored={(id) => void transition(id, "ignored")}
            />
          ))}
        </div>
      ) : null}
    </Card>
  );
}
