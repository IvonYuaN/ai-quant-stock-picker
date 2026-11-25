// 「刷新失败，保留上次成功数据」提示条。
//
// `AqspWorkspaceProvider` 每 60s 轮询一次，失败时刻意不清空 data（清空会让整页闪烁、
// 刷新入口一起消失）。降级本身合理，但用户必须能看出来页面上那批数字是旧的。
// 判定与文案在 `@/lib/refresh-warning`（纯函数，有契约断言）；这里只负责渲染。
import { formatAqspTime } from "./useAqspSnapshot";
import { refreshWarningText, shouldShowRefreshWarning } from "@/lib/refresh-warning";

export function SnapshotRefreshWarning({
  error,
  hasData,
  switching,
  generatedAt,
}: {
  error: string | null;
  hasData: boolean;
  switching: boolean;
  generatedAt: string;
}) {
  if (!shouldShowRefreshWarning({ error, hasData, switching })) return null;
  const text = refreshWarningText(error, formatAqspTime(generatedAt));
  return (
    <div className="aq-refresh-warning" role="alert">
      <strong>{text.title}</strong>
      <span>{text.detail}</span>
    </div>
  );
}
