import { RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { snapshotMatchesSelectedDate } from "@/lib/research-view";

interface AqspDateBoundaryProps {
  children: ReactNode;
}

/** Prevent a previous date's snapshot from remaining visible during a date request. */
export function AqspDateBoundary({ children }: AqspDateBoundaryProps) {
  const { data, error, loading, selectedDate, refresh } = useAqspSnapshot();
  const hasMismatchedSnapshot = Boolean(data && !snapshotMatchesSelectedDate(data, selectedDate));

  if (!hasMismatchedSnapshot) return <>{children}</>;

  return (
    <div className="aqsp-state aqsp-state-warn">
      <span className="min-w-0 flex-1">
        {loading
          ? `正在读取 ${selectedDate} 的研究数据…`
          : error
            ? `无法读取 ${selectedDate} 的研究数据：${error}`
            : `正在读取 ${selectedDate} 的研究数据…`}
      </span>
      {!loading && error && (
        <button type="button" onClick={refresh} className="aqsp-icon-button" title="重新读取" aria-label="重新读取">
          <RefreshCw className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
