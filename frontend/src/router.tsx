import { lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { DEFAULT_ROUTE, LEGACY_ROUTES } from "@/lib/ia";

// 路由级代码分割：多数人只打开「今日研究」，没必要为市场 / 雷达 / 持仓页付首屏流量。
// 每个页面单独成 chunk，进入该路由时才拉；Layout 里用 Suspense 兜住加载态。
const TodayPage = lazy(() => import("@/pages/TodayPage").then((m) => ({ default: m.TodayPage })));
const MarketPage = lazy(() => import("@/pages/MarketPage").then((m) => ({ default: m.MarketPage })));
const RadarPage = lazy(() => import("@/pages/RadarPage").then((m) => ({ default: m.RadarPage })));
const LabPage = lazy(() => import("@/pages/LabPage").then((m) => ({ default: m.LabPage })));
const ArchivePage = lazy(() => import("@/pages/ArchivePage").then((m) => ({ default: m.ArchivePage })));
const PerformancePage = lazy(() => import("@/pages/PerformancePage").then((m) => ({ default: m.PerformancePage })));
const WatchlistPage = lazy(() => import("@/pages/my/WatchlistPage").then((m) => ({ default: m.WatchlistPage })));
const HoldingsPage = lazy(() => import("@/pages/my/HoldingsPage").then((m) => ({ default: m.HoldingsPage })));
const NotesPage = lazy(() => import("@/pages/my/NotesPage").then((m) => ({ default: m.NotesPage })));
const ReportsPage = lazy(() => import("@/pages/my/ReportsPage").then((m) => ({ default: m.ReportsPage })));

// 旧路由保留为纯重定向，书签与旧文档里的链接不会失效。
const legacyRoutes = Object.entries(LEGACY_ROUTES).map(([from, to]) => ({
  path: from,
  element: <Navigate to={to} replace />,
}));

// 两条主线各有独立路由：系统线（公共只读）与我的线（本机私有）。
export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Navigate to={DEFAULT_ROUTE} replace /> },
      { path: "/today", element: <TodayPage /> },
      { path: "/market", element: <MarketPage /> },
      { path: "/radar", element: <RadarPage /> },
      { path: "/lab", element: <LabPage /> },
      { path: "/archive", element: <ArchivePage /> },
      { path: "/performance", element: <PerformancePage /> },
      { path: "/my/watchlist", element: <WatchlistPage /> },
      { path: "/my/holdings", element: <HoldingsPage /> },
      { path: "/my/notes", element: <NotesPage /> },
      { path: "/my/reports", element: <ReportsPage /> },
      ...legacyRoutes,
      { path: "*", element: <Navigate to={DEFAULT_ROUTE} replace /> },
    ],
  },
]);
