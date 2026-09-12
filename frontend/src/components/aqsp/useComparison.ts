// 多日对比的数据获取。
//
// 串行拉取：一次拉一堆日期既没必要（用户一次最多选 5 个），也会把本机的快照 IO 打满。
// 任一日期取不到时**单独记为失败**而不是整块报错 —— 少一天也能对比，但要说清少了哪天。
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { buildDailyView, normalizeSnapshot, type DailyView } from "@/lib/daily-view";
import { buildCompareSummary, type CompareSummary } from "@/lib/compare-view";

export interface ComparisonState {
  summary: CompareSummary | null;
  loading: boolean;
  /** 取不到数据的日期（后端可能已清理当天快照） */
  failed: readonly string[];
}

export function useComparison(dates: readonly string[]): ComparisonState {
  const [views, setViews] = useState<readonly DailyView[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState<readonly string[]>([]);
  // 依赖用字符串：dates 每次渲染都是新数组，直接用会无限重拉
  const key = dates.join(",");

  useEffect(() => {
    if (!key) {
      setViews([]);
      setFailed([]);
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    const collected: DailyView[] = [];
    const failures: string[] = [];

    (async () => {
      for (const date of key.split(",")) {
        if (!alive) return;
        try {
          const snapshot = await api.aqspSnapshot(date);
          if (!alive) return;
          collected.push(buildDailyView(normalizeSnapshot(snapshot)));
        } catch {
          if (alive) failures.push(date);
        }
      }
      if (!alive) return;
      setViews(collected);
      setFailed(failures);
      setLoading(false);
    })();

    return () => {
      alive = false;
    };
  }, [key]);

  return useMemo(
    () => ({
      summary: views.length > 0 ? buildCompareSummary(views) : null,
      loading,
      failed,
    }),
    [views, loading, failed],
  );
}
