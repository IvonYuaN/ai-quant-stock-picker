import { ArrowRight, FileSearch } from "lucide-react";
import { Link } from "react-router-dom";
import { AqspDailySnapshot } from "@/components/aqsp/AqspPanels";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";

export function DailyReview() {
  return <div>
    <PageHeader title="今日研究" subtitle="Vibe-Research · 当天结论、候选、消息与委员会结果" actions={<Link to="/paper-research" className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:border-primary/40 hover:text-foreground"><FileSearch className="h-4 w-4" />纸面研究<ArrowRight className="h-3.5 w-3.5" /></Link>} />
    <AqspDailySnapshot />
    <Disclaimer compact />
  </div>;
}
