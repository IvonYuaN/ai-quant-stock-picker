// 影子账户区的文案契约：禁止「后端口径改了、前端还按旧口径讲反话」。
//
// 为什么值得单独写一组断言（不是形式主义）—— 这类错已经犯了两次，而且**都不会让页面崩**：
//
//   1) #574 把打板 B 格改成「检测到信号时已封死也照买」，前端仍然渲染
//      「封死排不进 N」。于是页面对着用户说「这两只没买」，而它们已经躺在持仓表里；
//   2) 加 P6 之后策略从 5 个变成 6 个，前端仍写死「5 个策略」「Q（FDR·5 策略）」。
//
// 两处都是：页面照常渲染、数字照常刷新，只是语义全错。截图看不出来（文字小、颜色正常），
// 只有读文本才发现 —— 所以只能用文本断言守。
//
// 前端没有装 @types/node（不为了几条断言引入新依赖），用到的两个最小接口
// 声明在 `src/types/node-shim.d.ts`（全局 ambient，放本文件里会被当成 module augmentation）。
import { readFileSync } from "node:fs";

/** 依次尝试仓库根与 frontend 根，避免依赖调用方的 cwd。 */
function sourceOf(relative: string): string {
  for (const root of [process.cwd(), `${process.cwd()}/frontend`]) {
    try {
      return readFileSync(`${root}/${relative}`, "utf-8");
    } catch {
      // 换个根再试
    }
  }
  throw new Error(`找不到源码文件：${relative}（cwd=${process.cwd()}）`);
}

const backtest = sourceOf("src/pages/BacktestPage.tsx");
const api = sourceOf("src/lib/api.ts");

/** 🔴 「封死排不进」是 #574 之前的旧口径。现在这类信号照买，再出现就是讲反话。 */
export const backtest_has_no_stale_sealed_wording = !backtest.includes("封死排不进");
export const api_has_no_stale_sealed_wording = !api.includes("封死排不进");

/** 封死必须被描述成「不拦买入」，否则用户会以为这类信号没成交。 */
export const backtest_states_sealed_is_still_bought = backtest.includes("不拦买入");

/** 策略数量必须取 sleeves.length，不能写死（加 P6 时就是这里漏的）。 */
export const backtest_does_not_hardcode_sleeve_count = !/5 个策略|5 个互不相同|FDR·5/.test(
  backtest,
);
export const api_does_not_hardcode_sleeve_count = !/5 个策略/.test(api);
