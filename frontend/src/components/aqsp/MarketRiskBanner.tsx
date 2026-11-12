// 市场级风险提示条（`GET /api/market-risk`）。
//
// 🔴 **用途锁定：只做风险提示**。实测依据（2026-11-12）：把"趋势曝光"叠加到
// walk-forward 真实逐期收益后，均值 Sharpe 由 +0.66 降到 +0.53（更差），
// 仅最大回撤改善（−10.8% → −3.9%）。故本组件**不得**接入打分/选股权重，
// 也不得在文案里暗示"能提高收益"。
//
// 降级策略：接口失败或 available=false 时**整块不渲染**（fail-soft），
// 不给市场页增加噪音——市场页其他分区各有自己的降级提示。
import { useEffect, useState } from "react";

import { ToneCallout } from "@/components/ui/primitives";
import { api, type MarketRisk } from "@/lib/api";
import { formatSignedPct } from "@/lib/format";

const LEVEL_LABEL: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
};

export function MarketRiskBanner() {
  const [risk, setRisk] = useState<MarketRisk | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .marketRisk()
      .then((data) => {
        if (alive) setRisk(data);
      })
      .catch(() => {
        if (alive) setRisk(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!risk || !risk.available || !risk.level) return null;

  const level = risk.level;
  const tone = level === "low" ? "ok" : "warn";
  const detail = [
    `20 日 ${formatSignedPct((risk.trend_20 ?? 0) * 100)}`,
    `60 日 ${formatSignedPct((risk.trend_60 ?? 0) * 100)}`,
    `广度 ${Math.round((risk.breadth ?? 0) * 100)}%`,
    `距一年高点 ${formatSignedPct((risk.drawdown_252 ?? 0) * 100)}`,
  ].join("｜");

  return (
    <ToneCallout
      tone={tone}
      title={`市场风险：${LEVEL_LABEL[level] ?? level}（${risk.as_of ?? "-"}）`}
      detail={
        <>
          {detail}
          {risk.reasons && risk.reasons.length > 0 ? (
            <div style={{ marginTop: 4 }}>{risk.reasons.join("；")}</div>
          ) : null}
          <div style={{ marginTop: 4, opacity: 0.75 }}>
            仅作风险提示，不参与选股打分。
          </div>
        </>
      }
    />
  );
}
