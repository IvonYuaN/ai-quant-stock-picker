# AQSP 前端

A 股量化的只读研究看板。**只做研究与记录，不代下单、不接券商接口。**

## 常用命令

```bash
npm ci                 # 安装
npm run dev            # 开发（/api 代理到 127.0.0.1:8900）
npm test               # 类型检查 + 契约断言（真正的执行，不只是类型）
npm run build          # 产物构建
npm run preview        # 预览产物
```

后端需先启动（`bash ../scripts/start_vibe_research.sh`），否则页面只剩降级态。

## 路由

| 线 | 路由 | 说明 |
|---|---|---|
| 系统线（公共只读） | `/today` | 今日研究：结论 / 门禁 / 候选 / 证据 / 讨论 |
| | `/market` | 市场环境：指数 / 涨跌家数 / 短线情绪 / 成交额榜 / 行业榜 / 隔夜外围 |
| | `/radar` | 资讯雷达：12 赛道公开 RSS |
| | `/lab` | 策略实验室：历史变体与生命周期 |
| | `/archive` | 结论归档：按交易日回看 |
| 我的线（本机私有） | `/my/watchlist` | 自选（浏览器 localStorage） |
| | `/my/holdings` | 持仓台账（后端本地目录） |
| | `/my/notes` | 笔记（浏览器 localStorage，支持 Markdown） |
| | `/my/reports` | 研报（后端本地目录，按访客隔离） |

旧路由（`/system/*`、`/my/lab`）在 `src/lib/ia.ts::LEGACY_ROUTES` 里保留为纯重定向。

## 分层

```
src/
  lib/            纯逻辑层（可单测，不碰 DOM）
    api.ts        后端接口客户端 + 原始负载类型
    safe.ts       外部数据安全归一化（asArray / asNumber / ...）
    daily-view.ts       今日研究展示模型
    market-view.ts      市场环境展示模型
    radar-view.ts       资讯雷达归一化
    portfolio-view.ts   持仓台账归一化
    candidate-view.ts   候选链展示模型（研究链：lib/candidate-chain.ts 的视图层）
    performance-view.ts 绩效只读展示模型（不产生新信号）
    chart-options.ts    图表 option 构建（纯对象，可断言）
    format.ts     格式化与 A 股涨跌配色
    ia.ts         信息架构（导航 + 旧路由映射）
  components/
    layout/       页框（侧栏 / 主题 / 备份导出导入）
    ui/           原语（primitives / EChart / Markdown）
    aqsp/         今日研究相关（工作区 / 分区 / 抽屉 / 数据 hook）
  pages/          页面（只做渲染与交互，不写业务判断）
  types/          快照契约类型
```

**核心约定：页面不自己写业务判断。** 任何派生（门禁定性、候选是否可复核、空态原因）
都来自 `lib/*-view.ts`，否则同一件事在不同页会显示成不同结论。

## 几条必须遵守的约定

1. **外部数据一律先归一化。** 任何 `x.length` / `x.toFixed()` 都可能白屏。
   用 `lib/safe.ts` 的 helper，并在数据入口归一化一次（如 `normalizeSnapshot` 在
   `useAqspSnapshot` 里调用），下游不必各自防御。
2. **`api.ts` 里上游原始负载的类型是宽松的**（可选字段 + 索引签名）。
   后端给过 `change_pct: "-"`、K 线用 `datetime`/`vol` 而不是 `date`/`volume`。
   严格性交给归一化后的视图类型，别照字面写死。
3. **Tailwind 类名禁止模板拼接。** `cn("aq-badge", \`aq-badge-${tone}\`)` 会被 purge 掉，
   样式静默失效。必须写成显式字面量映射（见 `primitives.tsx` 顶部的 `*_TONE` 对象）。
4. **重依赖必须按需加载。** echarts 打进主包曾让产物 360 kB → 1020 kB。
   `EChart`、`MarkdownView`、以及每个路由都走运行时 `import()`。
   接重依赖前后都要看 `vite build` 的 chunk 体积。
5. **区分"接口失败"与"数据源没覆盖"。** 两者都表现为空，但文案完全不同：
   前者提示可重试，后者说明该源未覆盖。混成一句"暂无数据"会误导。
6. **日期未对齐时不渲染快照内容**（`useAqspSnapshot` 的 `switching`）。金融场景禁止张冠李戴。
7. **语气只有 ok / warn / neutral 三档**；涨跌与盈亏用 `aq-tone-up`（红）/ `aq-tone-down`（绿），
   不要用 ok/danger 表达涨跌——那是西方口径。

## 验证

`npm test` = `tsc --noEmit` + `node scripts/run-contracts.mjs`。

`scripts/run-contracts.mjs` 用 esbuild 把 `src/**/*.test.ts` 打成 ESM **真正求值**，
检查导出里的布尔量。约定：导出名含 `fixture` 的视为测试数据、跳过检查。
新增核心模块必须同步写永久契约测试，不要只在临时脚本里验一遍。

> 历史坑：这些 `*.test.ts` 曾经只被 `tsc` 类型检查，布尔表达式从未求值 ——
> 文件看起来像测试，实际不验证任何东西，且会随实现一起漂移。

改动后建议再跑一遍无头渲染校验（jsdom 直接 import `dist/assets/index-*.js`，
stub `/api` 返回夹具）。**务必用"最小夹具"（其余接口返回 `{}`）跑一次** ——
数据驱动的页面最容易死在接口给了意外结构上。
可用技能 `vite-react-frontend-verify` 里的 `scripts/render-check.mjs`。

## 已知约束

- CI 目前只跑 `npm run build`，**不跑 `npm test`**——契约断言在 CI 里不生效。
- `src/data/sectors.json` 目前没有任何引用。
- 部分个股接口依赖 akshare / mootdx，未安装时后端返回 501，UI 会如实提示缺哪个依赖。
