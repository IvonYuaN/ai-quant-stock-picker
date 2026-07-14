import { ArrowRight, FileSearch } from "lucide-react";
import { Link } from "react-router-dom";
import { AqspDailySnapshot } from "@/components/aqsp/AqspPanels";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";

export function DailyReview() {
  return (
    <div>
      <PageHeader
        title="每日复盘"
        subtitle="Vibe-Research · AQSP 当前研究快照 · 先结论，后证据"
        actions={
          <Link
            to="/paper-research"
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <FileSearch className="h-4 w-4" />
            查看纸面研究
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        }
      />

      <div className="mb-4 flex items-center gap-2 rounded-lg border border-primary/25 bg-primary/5 p-3 text-xs text-primary">
        <FileSearch className="h-4 w-4 shrink-0" />
        AQSP 是本入口的唯一研究数据源。页面只读展示当前日快照、证据和后续观察条件。
      </div>

      <AqspDailySnapshot />
      <Disclaimer />
    </div>
  );
}
