import { AqspDailySnapshot } from "@/components/aqsp/AqspPanels";
import { AqspDateBoundary } from "./AqspDateBoundary";

export function DailyReview() {
  return <AqspDateBoundary><AqspDailySnapshot /></AqspDateBoundary>;
}
