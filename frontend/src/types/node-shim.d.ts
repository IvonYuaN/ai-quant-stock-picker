// 最小 Node 类型垫片。
//
// 前端产物跑在浏览器里，构建/契约脚本跑在 Node 里，但本仓**没有装 @types/node**
// （为几条文本断言引入一个全局类型包不划算）。这里只声明契约测试真正用到的两个接口，
// 且刻意保持最小 —— 不要往这里加东西，加了就等于偷偷把前端代码写成依赖 Node。
//
// 注意：本文件必须是**非模块**（无 import/export），否则 `declare module` 会被当成
// module augmentation，TS 报 TS2664「module 'node:fs' cannot be found」。

declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf-8"): string;
}

declare const process: { cwd(): string };
