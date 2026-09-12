// 策略实验室：变体实验的独立区域。
//
// 与「今日研究」刻意分开：变体是历史 raw 回测，只用于验证策略，不参与当天结论。
import { useMemo } from "react";
import { Badge, LoadingState } from "@/components/ui/primitives";
import { VariantSection } from "@/components/aqsp/sections/VariantSection";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { buildDailyView } from "@/lib/daily-view";

export function LabPage() {
  const { data, loading } = useWorkspaceSnapshot();
  const view = useMemo(() => (data ? buildDailyView(data) : null), [data]);

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">AQSP · 策略实验室</p>
          <div className="aq-title-row">
            <h1>策略实验室</h1>
          </div>
          <p className="aq-page-sub">变体对比与生命周期：晋级 / 淘汰 / 样本积累</p>
        </div>
        <Badge tone="neutral">公共只读 · 不进入正式推荐</Badge>
      </header>

      {loading && !data ? <LoadingState label="正在读取变体实验数据" /> : <VariantSection view={view} />}
    </div>
  );
}
