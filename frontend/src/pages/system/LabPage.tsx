// 策略实验室：系统线。策略变体的独立实验区，不进入正式结论。
import { AqspResearchWorkspace } from "@/components/aqsp/AqspPanels";
import { TEST_VARIANTS_SECTION_ID } from "@/lib/research-layout";

export function LabPage() {
  return (
    <div className="aqsp-page">
      <div className="vr-line-head">
        <div>
          <h1>策略实验室</h1>
          <p>变体对比与生命周期：晋级 / 淘汰 / 样本积累</p>
        </div>
        <span className="vr-chip">公共只读 · 不进入正式推荐</span>
      </div>
      <AqspResearchWorkspace view={TEST_VARIANTS_SECTION_ID} />
    </div>
  );
}
