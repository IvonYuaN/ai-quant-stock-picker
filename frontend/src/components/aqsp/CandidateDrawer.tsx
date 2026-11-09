// 候选抽屉：点击候选行时弹出个股证据。
//
// 今日总览与候选池两个页面都要用它，抽成一处避免两页各写一遍取数逻辑。
import { StockDetailDrawer, type StockIntro } from "./StockDetailDrawer";
import type { DailyView } from "@/lib/daily-view";

export function CandidateDrawer({
  view,
  symbol,
  onClose,
}: {
  view: DailyView | null;
  symbol: string | null;
  onClose: () => void;
}) {
  const row = symbol ? view?.candidates.find((item) => item.symbol === symbol) : undefined;
  const intro: StockIntro | undefined = row
    ? {
        scoreText: row.scoreText,
        status: row.status,
        evidence: row.evidence,
        ready: row.ready,
        strategies: row.strategies ? [row.strategies] : [],
        reasons: row.reasons,
        nextStep: row.nextStep,
        context: row.context,
      }
    : undefined;
  return <StockDetailDrawer symbol={symbol} onClose={onClose} intro={intro} />;
}
