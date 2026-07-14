import { Database, FileSearch } from "lucide-react";
import { Link } from "react-router-dom";
import { AqspIntelSnapshot } from "@/components/aqsp/AqspPanels";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";

export function Intel() {
  return (
    <div>
      <PageHeader
        title="资讯核验"
        subtitle="Vibe-Research · AQSP 来源状态与跨市场事实 · 只读研究记录"
        actions={
          <Link
            to="/paper-research"
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <FileSearch className="h-4 w-4" />
            查看候选观察
          </Link>
        }
      />

      <div className="mb-4 flex items-center gap-2 rounded-lg border border-primary/25 bg-primary/5 p-3 text-xs text-primary">
        <Database className="h-4 w-4 shrink-0" />
        本页只核对 AQSP 快照内的有效来源、数据新鲜度和跨市场验证条件，不追加外部话术。
      </div>

      <AqspIntelSnapshot />
      <Disclaimer />
    </div>
  );
}
