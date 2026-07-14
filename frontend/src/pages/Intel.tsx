import { FileSearch } from "lucide-react";
import { Link } from "react-router-dom";
import { AqspIntelSnapshot } from "@/components/aqsp/AqspPanels";
import { PageHeader } from "@/components/ui/PageHeader";
import { Disclaimer } from "@/components/ui/Disclaimer";

export function Intel() {
  return <div>
    <PageHeader title="消息核验" subtitle="Vibe-Research · 来源状态、数据新鲜度与跨市场事实" actions={<Link to="/paper-research" className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted-foreground hover:border-primary/40 hover:text-foreground"><FileSearch className="h-4 w-4" />查看候选</Link>} />
    <AqspIntelSnapshot />
    <Disclaimer compact />
  </div>;
}
