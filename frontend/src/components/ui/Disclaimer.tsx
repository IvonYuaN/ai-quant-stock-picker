import { Info } from "lucide-react";

// 中立说明：只呈现 AQSP 研究数据与来源状态，不把研究记录变成操作指令。
export function Disclaimer({ compact = false }: { compact?: boolean }) {
  if (compact) {
    return (
      <p className="text-[11px] leading-relaxed text-muted-foreground/70">
        Vibe-Research 只呈现 AQSP 只读研究数据、来源状态与历史记录。
      </p>
    );
  }
  return (
    <div className="mt-8 flex items-start gap-2 rounded-lg border border-border/60 bg-muted/20 p-3 text-xs leading-relaxed text-muted-foreground">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>
        Vibe-Research 是 AQSP 的研究展示入口。页面内容均为<b className="text-foreground">只读研究数据、证据和来源状态</b>；候选仅用于纸面研究与历史复核，<b className="text-foreground">不生成操作指令</b>。请在使用前核对数据日期与来源状态。
      </span>
    </div>
  );
}
