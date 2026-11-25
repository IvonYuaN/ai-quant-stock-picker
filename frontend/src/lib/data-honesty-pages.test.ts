// 页面层「缺数当有效」防复发契约（2026-11-25）。
//
// 为什么必须用源码断言（和 backtest-copy.test.ts 同构）：
//   数据诚实性 M2/M3/M4 的修复都在页面 JSX 层——把 `(change_pct ?? 0) >= 0`
//   换成 `changeClass(change_pct)`。但契约测试只求值 lib 里的 helper 断言，
//   **页面是否真的用了 helper 没有任何测试覆盖**：将来有人把某行改回 `?? 0`，
//   helper 测试照样绿，页面又会把「缺数」染成「涨/盈利」。这类 bug 不会让页面崩、
//   截图看不出来，只能靠文本断言守。
//
// 🔴 刻意保持窄口径：
//   - 只扫这 3 个已修复的页面/组件，不做全仓扫描（会误伤 chart-options.ts 里
//     `.filter(changePct != null)` 之后合法的 TS 收窄 ?? 0）。
//   - 只针对已确认可空的字段（api.ts 里 number | null），不泛化到别的字段。
//   - 断言「不含危险模式」+「含正确 helper」两条，缺一不可——只断言不含会把
//     整行删掉也算通过，只断言含 helper 会挡不住某人同时留一个手写的旧分支。
import { readFileSync } from "node:fs";

function pageSource(relative: string): string {
  const roots = [process.cwd(), `${process.cwd()}/frontend`];
  for (const root of roots) {
    try {
      return readFileSync(`${root}/${relative}`, "utf-8");
    } catch {
      // 换根再试
    }
  }
  throw new Error(`找不到页面源码：${relative}`);
}

const indices = pageSource("src/pages/IndicesPage.tsx");
const signals = pageSource("src/pages/SignalsPage.tsx");
const risk = pageSource("src/components/aqsp/MarketRiskBanner.tsx");

/**
 * 可空字段被 `?? 0` 兜底 = 把「没数据」渲染成「有方向/零风险」。
 *
 * 🔴 不要求左括号或特定变量名（2026-11-25 加固）：初版正则写成
 * `\( (row|pick|risk|emotion)\.field \?\? 0`，反向验证发现可被轻易绕过——
 * 保留原 helper 行不动、在别处加一行无括号的 `pick.return_pct ?? 0 >= 0`，
 * 两条断言全绿（假安全感）。现在只认「字段名 + ?? 0」本身；这三个页面里
 * 可空字段的合法用法只有传给 helper，没有别的 `field ?? 0`，不会误伤。
 */
const NULL_TO_ZERO = /\.(?:change_pct|return_pct|excess_return_pct|trend_20|trend_60|breadth|drawdown_252)\s*\?\?\s*0/;

export const indicesHasNoNullToZeroTone = !NULL_TO_ZERO.test(indices);
export const signalsHasNoNullToZeroTone = !NULL_TO_ZERO.test(signals);
export const riskBannerHasNoNullToZeroValue = !NULL_TO_ZERO.test(risk);

/** 正向：页面必须真的调用对应 helper（防止简单删掉表达式蒙混过关）。 */
export const indicesUsesChangeClass = indices.includes("changeClass(row.change_pct)");
export const signalsUsesChangeClassForReturn = signals.includes("changeClass(pick.return_pct)");
export const signalsUsesChangeClassForExcess = signals.includes("changeClass(pick.excess_return_pct)");
export const riskUsesFormatSignedRatio = risk.includes("formatSignedRatio(risk.trend_20)");
export const riskUsesFormatRate = risk.includes("formatRate(risk.breadth)");
