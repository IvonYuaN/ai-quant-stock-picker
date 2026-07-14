import { Link } from "react-router-dom";
import { ArrowLeft, FileSearch } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { AqspPaperResearch } from "@/components/aqsp/AqspPanels";

export function PaperResearch() {
  return (
    <div>
      <PageHeader
        title="纸面研究"
        subtitle="Vibe-Research · AQSP 当前日快照 · 候选证据与复核条件"
        actions={<Link to="/daily-review" className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="h-4 w-4" />返回复盘</Link>}
      />
      <div className="mb-4 flex items-center gap-2 rounded-lg border border-warning/25 bg-warning/5 p-3 text-xs text-warning"><FileSearch className="h-4 w-4 shrink-0" />只读纸面研究。候选仅保留证据、风险卡点和下一观察条件。</div>
      <AqspPaperResearch />
      <Disclaimer compact />
    </div>
  );
}
