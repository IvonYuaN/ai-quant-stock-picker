// 图表容器：按需加载 echarts，不带入首屏包。
//
// 为什么动态导入：echarts 打进主包会让产物从 ~360 kB 涨到 ~1020 kB（gzip 107→328 kB），
// 而图表只出现在「市场环境」和「个股详情抽屉」—— 默认路由（今日研究）不该为此付费。
// 改成 `await import(...)` 后，echarts 落到独立 chunk，首次渲染图表时才拉。
//
// 另外两处兜底：
//   1. 无 canvas 环境（无头校验、禁用 canvas 的浏览器）→ 渲染调用方给的 fallback。
//   2. 动态导入失败（离线 / CDN 被拦）→ 同样走 fallback，不留空白。
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { EChartsOption } from "echarts";
import { useThemeMode } from "@/lib/theme-mode";

type EchartsModule = Awaited<typeof import("echarts/core")>;

function canvasAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext && canvas.getContext("2d"));
  } catch {
    return false;
  }
}

let echartsPromise: Promise<EchartsModule | null> | null = null;

/** 只装配用到的图表与组件，避免把整包拉下来。 */
function loadEcharts(): Promise<EchartsModule | null> {
  if (!echartsPromise) {
    echartsPromise = (async () => {
      const [core, charts, components, renderers] = await Promise.all([
        import("echarts/core"),
        import("echarts/charts"),
        import("echarts/components"),
        import("echarts/renderers"),
      ]);
      core.use([
        charts.BarChart,
        charts.LineChart,
        components.GridComponent,
        components.TooltipComponent,
        renderers.CanvasRenderer,
      ]);
      return core;
    })().catch(() => null);
  }
  return echartsPromise;
}

export interface EChartProps {
  /** 必须由调用方 useMemo，否则每次渲染都会销毁重建图表。 */
  option: EChartsOption;
  height?: number;
  fallback?: ReactNode;
}

type Status = "loading" | "ready" | "fallback";

export function EChart({ option, height = 200, fallback }: EChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mode = useThemeMode();
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;

    let disposed = false;
    let chart: ReturnType<EchartsModule["init"]> | null = null;
    let observer: ResizeObserver | null = null;

    (async () => {
      if (!canvasAvailable()) {
        if (!disposed) setStatus("fallback");
        return;
      }
      const echarts = await loadEcharts();
      if (!echarts) {
        if (!disposed) setStatus("fallback");
        return;
      }
      if (disposed || !containerRef.current) return;
      try {
        chart = echarts.init(containerRef.current, undefined, { renderer: "canvas" });
        chart.setOption(option);
      } catch {
        chart?.dispose();
        if (!disposed) setStatus("fallback");
        return;
      }
      if (!disposed) setStatus("ready");
      observer = new ResizeObserver(() => chart?.resize());
      observer.observe(containerRef.current);
    })();

    return () => {
      disposed = true;
      observer?.disconnect();
      chart?.dispose();
    };
  }, [option, mode]);

  if (status === "fallback") {
    return (
      <div className="aq-chart-fallback">
        {fallback ?? <p className="aq-detail-muted">当前环境不支持图表渲染，已用列表代替。</p>}
      </div>
    );
  }

  return (
    <div className="aq-chart-wrap" style={{ height }}>
      <div ref={containerRef} className="aq-chart" style={{ height }} />
      {status === "loading" ? <p className="aq-chart-loading">图表加载中…</p> : null}
    </div>
  );
}
