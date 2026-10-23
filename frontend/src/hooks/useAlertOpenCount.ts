// 侧栏常驻角标：该卖提醒（alert_event）未处理数量。
//
// 拉一次、不轮询——后端未部署 #431 的端点时 api 抛 ApiError，静默给 0（侧栏不报错、不闪）。
// 切页（useLocation pathname 变）时再拉一次，让"处理完提醒切走"的旧数能刷新。
import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api } from "@/lib/api";
import { normalizeAlerts } from "@/lib/alert-view";
import { ALERTS_CHANGED_EVENT } from "@/lib/operation";

export function useAlertOpenCount() {
  const [openCount, setOpenCount] = useState(0);
  const { pathname } = useLocation();

  const refetch = useCallback(async () => {
    try {
      const data = normalizeAlerts(await api.alerts());
      setOpenCount(data.open_count);
    } catch {
      // #431 端点未部署 / 后端未起 / 网络异常 ⇒ 侧栏静默隐藏，不报错不轮询。
      setOpenCount(0);
    }
  }, []);

  useEffect(() => {
    void refetch();
    window.addEventListener(ALERTS_CHANGED_EVENT, refetch);
    return () => window.removeEventListener(ALERTS_CHANGED_EVENT, refetch);
  }, [refetch, pathname]);

  return { openCount, refetch };
}
