import { lazy } from "react";
import { createBrowserRouter, Navigate, useLocation } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { DEFAULT_ROUTE, LEGACY_ROUTES } from "@/lib/ia";
import { RiskRuleEditor } from "@/components/aqsp/RiskRuleEditor";

// 路由级代码分割：多数人只打开「今日研究」，没必要为市场 / 雷达 / 持仓页付首屏流量。
// 每个页面单独成 chunk，进入该路由时才拉；Layout 里用 Suspense 兜住加载态。
const TodayPage = lazy(() => import("@/pages/TodayPage").then((m) => ({ default: m.TodayPage })));
const MarketPage = lazy(() => import("@/pages/MarketPage").then((m) => ({ default: m.MarketPage })));
const HoldingsPage = lazy(() => import("@/pages/my/HoldingsPage").then((m) => ({ default: m.HoldingsPage })));
const PositionPlanPage = lazy(() => import("@/pages/my/PositionPlanPage").then((m) => ({ default: m.PositionPlanPage })));
const NotesPage = lazy(() => import("@/pages/my/NotesPage").then((m) => ({ default: m.NotesPage })));
const ReportsPage = lazy(() => import("@/pages/my/ReportsPage").then((m) => ({ default: m.ReportsPage })));
const StockResearchPage = lazy(() => import("@/pages/StockResearchPage").then((m) => ({ default: m.StockResearchPage })));
const InfoStreamPage = lazy(() => import("@/pages/InfoStreamPage").then((m) => ({ default: m.InfoStreamPage })));

// 旧路由保留为纯重定向，书签与旧文档里的链接不会失效。
function LegacyRedirect({ to }: { to: string }) {
  const { search } = useLocation();
  const [path, hash] = to.split("#");
  return <Navigate to={`${path}${search}${hash ? `#${hash}` : ""}`} replace />;
}
const legacyRoutes = Object.entries(LEGACY_ROUTES).map(([from, to]) => ({
  path: from,
  element: <LegacyRedirect to={to} />,
}));

// 两条主线各有独立路由：系统线（公共只读）与我的线（本机私有）。
export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Navigate to={DEFAULT_ROUTE} replace /> },
      { path: "/today", element: <TodayPage /> },
      { path: "/stream", element: <InfoStreamPage /> },
      { path: "/market", element: <MarketPage /> },
      { path: "/my/holdings", element: <HoldingsPage renderRiskSettings={(code, expanded) => <RiskRuleEditor code={code} expanded={expanded} />} /> },
      { path: "/my/holdings/:positionId/plan", element: <PositionPlanPage /> },
      { path: "/my/notes", element: <NotesPage /> },
      { path: "/my/reports", element: <ReportsPage /> },
      { path: "/stock/:code", element: <StockResearchPage /> },
      ...legacyRoutes,
      { path: "*", element: <Navigate to={DEFAULT_ROUTE} replace /> },
    ],
  },
]);
