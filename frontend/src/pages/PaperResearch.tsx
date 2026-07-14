import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { AqspPaperResearch } from "@/components/aqsp/AqspPanels";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";

export function PaperResearch() {
  return <div>
    <PageHeader title="纸面研究" subtitle="Vibe-Research · 候选证据、风险卡点与复核条件" actions={<Link to="/daily-review" className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:border-primary/40 hover:text-foreground"><ArrowLeft className="h-4 w-4" />回到今日</Link>} />
    <AqspPaperResearch />
    <Disclaimer compact />
  </div>;
}
