// 主题模式读取。
//
// useDarkMode 把主题写在 <html> 的 class 上（light / dark），这里是那面旗子的只读镜像：
// 图表等命令式、不走 React 样式的组件需要知道当前是亮色还是暗色，
// 才能给出正确的网格线/文字颜色。
import { useEffect, useState } from "react";

export type ThemeMode = "light" | "dark";

const LIGHT_CLASS = "light";

export function currentThemeMode(): ThemeMode {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.classList.contains(LIGHT_CLASS) ? "light" : "dark";
}

/** 订阅 <html> 的 class 变化得到当前主题。 */
export function useThemeMode(): ThemeMode {
  const [mode, setMode] = useState<ThemeMode>(currentThemeMode);

  useEffect(() => {
    const observer = new MutationObserver(() => setMode(currentThemeMode()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return mode;
}
