import { useMemo, useState } from "react";
import type { AlertEvent, MacdChartEvidence } from "@/lib/api";
import { ALERT_LEVEL_NAMES, formatAlertPrice } from "@/lib/alert-view";
import { macdEvidenceOption } from "@/lib/alert-chart";
import { useThemeMode } from "@/lib/theme-mode";
import { EChart } from "@/components/ui/EChart";
import { Tag } from "@/components/ui/primitives";

function MacdSnapshot({ chart, symbol }: { chart: MacdChartEvidence; symbol: string }) {
  const mode = useThemeMode();
  const option = useMemo(() => macdEvidenceOption(chart, mode), [chart, mode]);
  const previous = chart.points[chart.points.length - 2], trigger = chart.points[chart.points.length - 1];
  return <figure className="aq-alert-chart" aria-label={`${symbol} MACD 死叉触发依据`}>
    <figcaption><b>{chart.trigger_date} · MACD 死叉</b><span>{chart.source} · MACD({chart.fast},{chart.slow},{chart.signal})</span></figcaption>
    <p>前一日 DIF {previous.dif.toFixed(6)} ≥ DEA {previous.dea.toFixed(6)}；触发日 DIF {trigger.dif.toFixed(6)} &lt; DEA {trigger.dea.toFixed(6)}。</p>
    <EChart option={option} height={320} fallback={<p>图形暂不可用，死叉数值与日线明细仍可复核。</p>} />
    <p className="aq-alert-chart-note">红线 DIF · 青线 DEA · 虚线与圆点标记触发日；显示当次检查快照，非实时走势。</p>
    <details className="aq-alert-chart-values"><summary>查看原始日线数值（{chart.points.length} 日）</summary>
      <table><caption>不复权收盘价与当次 MACD 数值</caption><thead><tr><th scope="col">日期</th><th scope="col">收盘价</th><th scope="col">DIF</th><th scope="col">DEA</th></tr></thead>
        <tbody>{chart.points.map((point) => <tr key={point.date} data-trigger={point.date === chart.trigger_date || undefined}><th scope="row">{point.date}{point.date === chart.trigger_date ? " 死叉" : ""}</th><td>{formatAlertPrice(point.close)}</td><td>{point.dif.toFixed(6)}</td><td>{point.dea.toFixed(6)}</td></tr>)}</tbody>
      </table>
    </details>
  </figure>;
}

export function AlertEvidence({ events, symbol }: { events: AlertEvent[]; symbol: string }) {
  const [open, setOpen] = useState(false);
  return <details className="aq-alert-evidence" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>触发依据 · {events.length} 项规则（合并展示）</summary>
    {events.map((row) => <div className="aq-position-rule" key={row.id}><Tag>{row.level}</Tag><div className="aq-alert-rule-body">
      <b>{ALERT_LEVEL_NAMES[row.level] || row.level} · 参考线 {formatAlertPrice(row.threshold_at)}</b>
      <p>{row.basis_json.rule}；{row.basis_json.actual}</p><small>{row.basis_json.evidence}</small>
      {open && row.level === "L4" ? row.basis_json.chart
        ? <MacdSnapshot chart={row.basis_json.chart} symbol={symbol} />
        : <p className="aq-alert-chart-note">该次触发未保存可用图形证据，文字依据保留；下次有效 MACD 死叉检查时可保存图形。</p> : null}
    </div></div>)}
  </details>;
}
