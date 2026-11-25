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
const market = pageSource("src/pages/MarketPage.tsx");

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

/**
 * M1（buildEmotion fail-closed）在页面层的落点：EmotionBlock 必须在 emotion 为
 * null 时整块不渲染。若把 view.emotion ?? 兜成对象，页面会显示「涨停 0 家 /
 * 最高 0 板」的假数据，M1 白做——这条反向验证过（加 ?? 后转红）。
 *
 * earlyReturn 这条是第二道保险：直接删早退会被 TS 空值检查先拦（所有 e.xxx
 * 报 possibly null），但若有人同时放宽 EmotionView 类型让字段可空，TS 就不拦了。
 * 那时这条文本断言才起作用，故无法用简单的"删早退"反向验证它。
 */
const EMOTION_NULL_FALLBACK = /view\.emotion\s*\?\?|view\.emotion\s*\|\|/;
export const marketEmotionEarlyReturnsOnNull = /if\s*\(!e\)\s*return\s*null/.test(market);
export const marketEmotionHasNoNullFallback = !EMOTION_NULL_FALLBACK.test(market);

/**
 * M10（IndicesPage 分段失败态，2026-11-25）：一段取数失败不得让页面永远停在「正在读取」。
 *
 * 原实现 `if (a.status === "rejected" && b.status === "rejected") setError(...)`：
 * 单段失败时该段 state 停在 null ⇒ 渲染成 LoadingState 转圈到天荒地老，原因还被吞。
 * 现在每段走 `settleSection`，失败段立刻出 StatePanel。
 *
 * 反向验证过：把 `settleSection(a, ...)` 改回 `if (a.status === "fulfilled") setIndices(a.value ?? [])`，
 * 下面两条断言同时转红。行为断言在 safe.test.ts（settleSection 三态），这里只守页面接线。
 */
const BOTH_MUST_FAIL = /status\s*===\s*"rejected"\s*&&\s*b\.status\s*===\s*"rejected"/;
export const indicesSettlesEachSectionIndependently =
  indices.includes("settleSection(a,") && indices.includes("settleSection(b,");
export const indicesHasNoBothMustFailGuard = !BOTH_MUST_FAIL.test(indices);
export const indicesRendersFailedSectionNotLoading =
  indices.includes('indices?.state === "failed"') && indices.includes('global?.state === "failed"');

/**
 * M11（KPI 最高分，2026-11-25）：候选行缺分时不得渲染「最高 0.0 分」。
 *
 * 原实现 `view.candidates.reduce((max, row) => Math.max(max, row.score ?? 0), 0)`：
 * 缺分被兜成 0 后取 max，KPI 写「最高 0.0 分」，而候选表同一行写「—」——同一批数据两处相反。
 * 行为断言在 daily-view.test.ts（topCandidateScore），这里只守组件真的用了它。
 */
const SCORE_NULL_TO_ZERO = /score\s*\?\?\s*0/;
const today = pageSource("src/components/aqsp/TodayWorkspace.tsx");
export const todayUsesTopCandidateScore = today.includes("topCandidateScore(view.candidates)");
export const todayHasNoScoreNullToZero = !SCORE_NULL_TO_ZERO.test(today);
export const todaySaysScoreMissingWhenUnknown = today.includes("最高分未取得");

/**
 * M14（刷新失败静默沿用旧值，2026-11-25）：所有消费工作区快照的模块页都必须挂提示条。
 *
 * Provider 每 60s 轮询且刻意不清空 data（避免整页闪烁），所以刷新失败时页面会继续渲染
 * 上一次成功的快照。页面若不提示，用户看不出那批数字是几分钟前还是几小时前的 ——
 * 这正是「不得沿用旧值」里的后一半。行为断言在 refresh-warning.test.ts，这里守接线。
 *
 * 候选遍历型守卫：先断言候选非空，否则 `every` 在空数组上恒真（假绿）。
 */
const REFRESH_WARNING_PAGES = [
  "CandidatesPage",
  "DataPage",
  "EvidencePage",
  "FactorsPage",
  "BacktestPage",
  "ScreenerPage",
] as const;
const refreshWarningSources = REFRESH_WARNING_PAGES.map((name) => pageSource(`src/pages/${name}.tsx`));
export const refreshWarningPageCandidatesNonEmpty = refreshWarningSources.length === REFRESH_WARNING_PAGES.length && REFRESH_WARNING_PAGES.length >= 6;
export const everyModulePageMountsRefreshWarning = refreshWarningSources.every((source) =>
  source.includes("<SnapshotRefreshWarning"),
);
export const todayWorkspaceAlsoMountsRefreshWarning = today.includes("<SnapshotRefreshWarning");
// 反向：不许页面自己再写一份内联提示（两份文案必然漂移）
export const noPageInlinesRefreshWarning = refreshWarningSources.every(
  (source) => !source.includes("刷新失败，保留上次成功数据"),
);

/**
 * M17（研报页用户动作静默失败，2026-11-25）：下载/删除失败必须给提示。
 *
 * `downloadReport` 在非 2xx 时 `throw`（`api.ts:97`），`api.deleteReport` 同理。
 * 原来写成 `onClick={() => void downloadReport(item.id, item.name)}` 与
 * `void api.deleteReport(item.id).then(...)` —— 两者都没有 `.catch`：
 * 失败时页面上**什么都不发生**（没文件、没提示），用户只会反复点。
 *
 * 现在两个动作各自走带 try/catch 的具名 handler，JSX 里只调 handler。
 * 这条守卫盯的就是「别再把裸 promise 直接挂到 onClick 上」。
 */
const reports = pageSource("src/pages/my/ReportsPage.tsx");
const BARE_DOWNLOAD = /void downloadReport\(/;
const BARE_DELETE = /void api\.deleteReport\([^)]*\)\s*\.then\(/;
export const reportsHasNoBareDownloadCall = !BARE_DOWNLOAD.test(reports);
export const reportsHasNoBareDeleteChain = !BARE_DELETE.test(reports);
export const reportsWrapsActionsInHandlers =
  reports.includes("async function download(") && reports.includes("async function remove(");
// 删除失败时不能只弹一句话：结果未知，必须重新读列表核对（否则用户重复点 → 404）
export const reportsDeleteFailureRereadsList = reports.includes("删除结果未知，已重新读取列表核对");

/**
 * M20（事件面读失败被误诊成「缓存没生成」+ 空态/加载态残留，2026-11-25）。
 *
 * 后端 `GET /api/events` 是 fail-soft 的：任何异常都回 200 + 空结构。原来空结构里
 * 只有 `has_*_data: false`，前端于是渲染「当前事件缓存尚未提供记录」—— 把
 * 「aqsp 未装 / 缓存损坏 / 运行时异常」统统说成「缓存还没生成」，误诊比不诊断更糟。
 * 现在后端带 `available:false` + `reason`，`eventsAvailability()` 翻成显式状态，
 * 页面据此不再说「暂无事件数据」、计数也不再报 0。
 */
const research = pageSource("src/pages/StockResearchPage.tsx");
export const researchUsesEventsAvailability =
  research.includes("eventsAvailability") && research.includes("calendarAvailability");
// 反向：失败态下不许继续渲染「暂无事件数据」空态，也不许把计数报成 0
export const researchGatesEmptyStateOnAvailability = research.includes(
  'calendarAvailability.state !== "failed"',
);
export const researchCountsUnknownWhenFailed = research.includes('"数量未知"');

/**
 * 同一类残留：读取失败后仍显示「暂无…」或「正在读取…」。
 * 两处都在错误提示旁边自相矛盾（上面说读取失败，下面说没有 / 还在读）。
 */
const observations = pageSource("src/components/aqsp/CandidateObservationList.tsx");
export const observationEmptyStateDistinguishesError =
  observations.includes("观察条件读取失败，数量未知");

const alertInbox = pageSource("src/components/aqsp/AlertInbox.tsx");
export const alertHeaderDistinguishesError = alertInbox.includes("提醒数量未知");

/**
 * M21（信号页把「没拿到」渲染成 0，2026-11-25）。
 *
 * `overall: {...} | null` 在 `available:false` 与「零已结算」两种情况下都是 null；
 * 原写法 `data.overall?.observations ?? 0` 会在台账**尚未可用**时把标题写成「0 条」——
 * 而页面下方明明写着「台账尚未可用」。同页其他字段用的是 `?? "—"`，这里跟齐口径。
 */
const signalsPageSource = pageSource("src/pages/SignalsPage.tsx");
export const signalsHasNoObservationZeroFallback = !/overall\?\.observations\s*\?\?\s*0/.test(signalsPageSource);
export const signalsSaysUnknownWhenOverallMissing = signalsPageSource.includes("条数未知");
export const signalsHasNoIndependentDaysZeroFallback =
  !/independent_signal_days\s*\?\?\s*0/.test(signalsPageSource);

/**
 * M22（事件面「缺缓存」与「没事件」在页面上是同一句话，2026-11-25）。
 *
 * 后端 `EventCalendar.has_*_data()` 的 docstring 写明：调用方**只能在该标志为
 * True 时**才允许把「查不到」解读成「没有」。前端此前完全没消费这组字段 ——
 * 于是「哪一面没预加载」和「该股确实没有这类事件」都渲染成同一句
 * 「当前事件缓存尚未提供记录」，既说不清是哪一面，也把预加载问题说成事实。
 */
export const researchUsesEventsFaceGaps =
  research.includes("eventsFaceGaps") && research.includes("calendarFaceGaps");
export const researchUsesComputedEmptyDetail = research.includes("detail={calendarEmptyDetail}");
// 反向：不许再写回那句「一律说缓存没提供」的固定文案
export const researchHasNoBlanketEventsCaveat = !research.includes(
  "当前事件缓存尚未提供记录，缺少数据不代表没有风险事件。",
);

/**
 * M23（影子账户把「超额未知」渲染成「无超额」，2026-11-25）。
 *
 * `PaperArenaSleeve.exc_mean` 是 `number | null`：`research/paper_ledger.py` 在
 * 「有结算笔数、但这些笔没有基准超额」时留 `None`。原判定
 * `(row.exc_mean ?? 0) > 0` 落到最后一个分支渲染成「无超额」—— 同一行的数字列
 * 走的是 `pct(row.exc_mean)`，显示「—」。同一批数据，一处说未知、一处下否定结论。
 *
 * 行为断言在 paper-arena-view.test.ts（sleeveVerdict 六态 + null/0 对照），
 * 这里只守页面真的用了它、且没把 `?? 0` 写回去。
 */
const backtest = pageSource("src/pages/BacktestPage.tsx");
const EXCESS_NULL_TO_ZERO = /exc_mean\s*\?\?\s*0/;
export const backtestUsesSleeveVerdict = backtest.includes("sleeveVerdict(row)");
export const backtestHasNoExcessNullToZero = !EXCESS_NULL_TO_ZERO.test(backtest);
export const backtestDisclosesUnknownExcess =
  backtest.includes("unknownExcessCount(sleeves)") && backtest.includes("个策略超额未知，未计入");

/**
 * M25（「台账有信号但还没结算」被写成「没有信号记录」，2026-11-25）。
 *
 * 后端 `performance_payload()` 的 `settled.empty` 分支返回 `strategies: []`，
 * 但台账里其实有记录（`status_counts` 有数）。页面原来一律写
 * 「还没有策略级信号记录。」并把 count 渲染成 `0` —— 两处都是确定的否定，
 * 而真相是「有信号、缺结算」。行为断言在 performance-view.test.ts，这里守接线。
 */
export const signalsUsesStrategyEmptyNotice = signalsPageSource.includes("strategyEmptyNotice(");
export const signalsHasNoBlanketNoRecordsClaim = !signalsPageSource.includes(
  ">还没有策略级信号记录。<",
);
export const signalsSaysUnsettledWhenRecordsExist = signalsPageSource.includes('"未结算"');

/**
 * M26（监控页失败后仍显示「正在读取」，2026-11-25）。
 *
 * 原写法 `{error ? …}{!followups ? <LoadingState/> : null}`：请求失败时
 * `followups` 仍是 `null`，于是「读取失败」和「正在读取跟进状态…」同时出现，
 * 而且永远转圈 —— 与 M10（IndicesPage）同一类，只是没走 `settleSection`。
 *
 * 行为断言在 safe.test.ts（readState：失败优先于正在读），这里守接线。
 */
const monitor = pageSource("src/pages/MonitorPage.tsx");
export const monitorUsesReadState = monitor.includes("readState(followups, error)");
// 加载态必须由 readState 的三态决定，不能再出现「只看值是否为 null」的裸判断
export const monitorHasNoUngatedLoading = !/\{!followups \? <LoadingState/.test(monitor);
export const monitorSaysFailureIsNotNoFollowups = monitor.includes("这不代表没有需要跟进的方案");

/**
 * M28（收评 6 段把「读失败」写成「还没生成」，2026-11-25）。
 *
 * 后端 6 个 builder 在「文件不存在 / 读失败 / 表头残缺 / 没可用行」四种情况下一律
 * `return ""`，端点折成 `available: false`，面板只能猜一句
 * 「该段当前无可用数据（盘后预载未落盘或数据源缺缓存）」—— 把故障说成等待。
 *
 * 行为断言在 closing-view.test.ts（sectionGapCopy / allSectionsGapNotice），这里守接线：
 * 面板必须按状态码取文案，不能再内联那句合并猜测。
 */
const closingPanel = pageSource("src/components/aqsp/ClosingReviewPanel.tsx");
export const closingPanelUsesSectionGapCopy = closingPanel.includes("sectionGapCopy(section.state");
export const closingPanelHasNoBlanketEmptyHint = !closingPanel.includes(
  "该段当前无可用数据（盘后预载未落盘或数据源缺缓存）",
);
export const closingPanelUsesAllSectionsGapNotice = closingPanel.includes(
  "allSectionsGapNotice(ordered)",
);
// 徽标文案也不许写死成「暂无数据」——那一句正是四态合流的地方。
export const closingPanelHasNoHardcodedBadge = !closingPanel.includes(">暂无数据</Badge>");

/**
 * M30（板块资金流把「没取到」渲染成「持平 0.00%」，2026-11-25）。
 *
 * 后端 `_sectors()` 原来每列都写 `round(float(row.get(col, 0) or 0), 2)`：
 * - `float(nan or 0)` **兜不住 NaN**（NaN 是真值）⇒ payload 里出现 NaN ⇒
 *   starlette 的 `allow_nan=False` 直接抛错 ⇒ 整个 `/api/market/overview` 500；
 * - 整列缺失时把「这一列没了」渲染成「全板块 0.00%」。
 *
 * 现在后端把不可用的单元格落成 `null`，前端必须原样传给 `formatSignedPct`
 * （它把 null 渲染成「—」）。**页面里补 `?? 0` 会把修复抵消掉**，这里守住。
 * 行为断言在 market-view.test.ts 的 `sectorPctNullContract`。
 */
const marketPage = pageSource("src/pages/MarketPage.tsx");
export const marketPageDoesNotFoldPctToZero = !/\bpct\s*\?\?\s*0/.test(marketPage);
export const marketPagePctIsNullable = marketPage.includes("pct: number | null");
