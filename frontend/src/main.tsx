import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { ErrorBoundary } from "./components/common/ErrorBoundary";
import { router } from "./router";
import "./index.css";

// 提示条（Toaster）放在 Layout 内，才能跟随应用主题切换，见 components/layout/Layout.tsx。
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <RouterProvider router={router} />
    </ErrorBoundary>
  </StrictMode>
);
