import { createBrowserRouter, Navigate } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { DailyReview } from "@/pages/DailyReview";
import { Intel } from "@/pages/Intel";
import { PaperResearch } from "@/pages/PaperResearch";

export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Navigate to="/daily-review" replace /> },
      { path: "/daily-review", element: <DailyReview /> },
      { path: "/paper-research", element: <PaperResearch /> },
      { path: "/intel", element: <Intel /> },
    ],
  },
]);
