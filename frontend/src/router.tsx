import { createBrowserRouter, Navigate } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { DEFAULT_ROUTE } from "@/lib/ia";
import { ReviewPage } from "@/pages/system/ReviewPage";
import { RecommendPage } from "@/pages/system/RecommendPage";
import { LabPage } from "@/pages/system/LabPage";
import { ArchivePage } from "@/pages/system/ArchivePage";
import { WatchlistPage } from "@/pages/my/WatchlistPage";
import { MyLabPage } from "@/pages/my/MyLabPage";
import { NotesPage } from "@/pages/my/NotesPage";
import { ReportsPage } from "@/pages/my/ReportsPage";

// 两条主线各有独立路由：系统线（公共只读）与我的线（本机私有）。
export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Navigate to={DEFAULT_ROUTE} replace /> },
      { path: "/system/review", element: <ReviewPage /> },
      { path: "/system/recommend", element: <RecommendPage /> },
      { path: "/system/lab", element: <LabPage /> },
      { path: "/system/archive", element: <ArchivePage /> },
      { path: "/my/watchlist", element: <WatchlistPage /> },
      { path: "/my/lab", element: <MyLabPage /> },
      { path: "/my/notes", element: <NotesPage /> },
      { path: "/my/reports", element: <ReportsPage /> },
      { path: "*", element: <Navigate to={DEFAULT_ROUTE} replace /> },
    ],
  },
]);
