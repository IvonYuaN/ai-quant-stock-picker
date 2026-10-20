# AQSP 模块价值密度盘点（数析）

盘点对象：`frontend/src/router.tsx`（10 条路由）、`frontend/src/lib/ia.ts`（NAV_LINES 7 个入口）、`backend/app.py`（63 个 HTTP 端点）。
盘点方法：不猜，逐个核对「路由 → 页面 → 它实际调的 api.ts 方法 → 后端端点」四层链路。

---

## TL;DR（3 行）

1. **导航 7 个入口里只有 3 个服务实盘**：今日选股 / 小道信息 / 行情环境。「笔记」「资料」是纯自存储，与实盘决策零关系。
2. **63 个后端端点里，前端只调 21 个**；31 个单票数据端点（kline / financials / margin / fund-flow / dragon-tiger / lockup / blocks / hot-concepts / investor-qa …）**前端一个都没接**——纯研究员验证用的数据底座，却挂在用户能看见的 `/stock/:code` 上。
3. **ConceptsPage 与 StockResearchPage 是「为自己做」的页**：前者不在导航里（进不去），后者是唯一会把「每日推荐」变成「另一批候选」的地方——正好是用户明确不想要的。

---

## 导航现状 vs 建议

### 现状（`ia.ts` NAV_LINES = 2 组 7 项）

| 组 | 入口 | 路由 | 实际数据源 |
|---|---|---|---|
| 每日选股 | 今日选股 | `/today` | `/aqsp/snapshot` + `/aqsp/performance` + `/aqsp/closing-review` |
| 每日选股 | 小道信息 | `/stream` | `/aqsp/info-stream` |
| 每日选股 | 行情环境 | `/market` | `/indices` `/global/indices` `/market/overview` `/market/emotion` `/market/turnover-top` `/industry` `/health`（7 个端点一次拉） |
| 我的 | 自选 | `/my/watchlist` | localStorage + `/quote` |
| 我的 | 持仓 | `/my/holdings` | `/portfolio` + `/quote` |
| 我的 | 笔记 | `/my/notes` | localStorage（`lib/notes.ts`），**零后端** |
| 我的 | 资料 | `/my/reports` | `/myreports` + `/myreports/file/{id}` |

**路由存在但导航里没有的 2 个页面**（用户只能手敲 URL 进去）：

| 路由 | 页面 | 数据源 | 判定 |
|---|---|---|---|
| `/concepts` | ConceptsPage (102 行) | `/catalyst` 按 `affected_sectors` 聚合 | **孤儿路由**，与 `/stream` 的概念异动榜同源同内容 |
| `/stock/:code` | StockResearchPage (320 行) | `/quote` + `/events` | **第二套个股详情**，且只有 `/catalyst` 事件面；`TodayWorkspace` 里已有 `StockDetailDrawer` 做同样的事 |

### 建议：7 个入口 → 3 个入口

| 动作 | 对象 | 理由 |
|---|---|---|
| **合并** | 持仓 + 自选 → 单页「**我的持仓**」 | 用户的核心诉求是「围绕持仓/自选给买卖建议」，一个决策要跨两页 = 两次跳转 = 不会做 |
| **砍掉** | 我的 · 笔记 | localStorage 手写笔记，与「实盘输入 → 该卖提醒」无关；`lib/notes.ts` 全文 48 行 4 个函数 |
| **砍掉** | 我的 · 资料 | 上传自己的研报 PDF 再从本站 `/myreports/file/{id}` 下载——绕一圈回到本地文件管理器 |
| **降级** | `/concepts` | 不删代码，降为「小道信息」页内的一个分区（概念异动 ↔ 快讯，同一份 `/catalyst`+`/aqsp/info-stream` 数据） |
| **降级** | `/stock/:code` | 改为 TodayWorkspace 内 StockDetailDrawer 的「在新页打开」深链；不再占一个导航位 |

砍完后侧栏：

```
每日决策
  今日选股   /today      ← 结论 + 候选 + 证据
  小道信息   /stream     ← 快讯 + 概念异动（含原 /concepts 分区）
  行情环境   /market     ← 大盘/情绪/涨跌家数
我的
  持仓与自选 /my/holdings ← 合并页
```

**5 → 3 个「每日必看」级入口**（今日选股 / 小道信息 / 行情环境保留不动，这三个正好对上用户说的前两件事）。

---

## 模块分层表

### P0（每天必看，对应用户实盘决策）

| 模块 | 路由 | 用户频率 | 对实盘决策价值 | 建议 |
|---|---|---|---|---|
| 今日选股 | `/today` | 每日 1 次以上 | **最高**：唯一给出「今天能不能动」的页面 | 保留；候选已有持仓/自选**标记**，但排序仍按策略分组 → 需补排序权重（见附注） |
| 小道信息 | `/stream` | 每日多次（盘中刷） | **最高**：用户的「小道消息」诉求，且已带 sources 健康标注（非 108 源堆量） | 保留 + 吸收 `/concepts` |
| 行情环境 | `/market` | 每日 1–2 次 | **高**：大盘/情绪/涨跌家数是择时前提 | 保留，但一次拉 7 个端点（`useMarketData.ts:44-49`）偏重，需按需加载 |
| 持仓 | `/my/holdings` | 每日多次（盯盈亏） | **高**：`/api/portfolio` 是全站唯一把「实盘输入」写进系统的地方 | 保留，与自选合并 |

### P1（偶尔看）

| 模块 | 路由 | 用户频率 | 对实盘决策价值 | 建议 |
|---|---|---|---|---|
| 自选 | `/my/watchlist` | 每日 1–2 次 | 中：本质是 localStorage 代码列表 + `/quote` | 合并进持仓页 |
| 个股详情 | `/stock/:code` | 偶尔（下钻） | 中：`/quote`+`/events` 有用，但与 Drawer 重复 | 降级为深链，不占导航 |

### P2（很少）

| 模块 | 路由 | 用户频率 | 对实盘决策价值 | 建议 |
|---|---|---|---|---|
| 资料 | `/my/reports` | 极少 | **低**：上传→下载本地文件，绕路 | 砍（或降为持仓页一个折叠区） |
| 板块概念 | `/concepts` | 极少（不在导航） | 低：与 `/stream` 概念异动同源 | 降级为 `/stream` 内分区 |

### 砍掉 / 降级（**「为自己做」的模块**）

| 模块 | 位置 | 服务谁 | 判定依据 |
|---|---|---|---|
| 31 个单票数据端点 | `backend/app.py` 行情/财务/资金面/事件/互动易 tag | **研究员验证** | 逐个 grep 前端 `api.ts` + 页面：`/kline` `/financials` `/valuation` `/margin` `/block-trade` `/holders` `/fund-flow` `/dragon-tiger` `/lockup` `/dividend` `/blocks` `/hot-concepts` `/investor-qa` `/announcements` `/reports` `/news` `/disclosure` `/info` `/global/stock` **全部 0 次调用**。这些是 `/stock/:code` 的「弹药库」，弹药没上膛 |
| IC 监控面 | `/api/aqsp/ic-history` `/ic-dual-verdict` | 研究员（换族决策） | 端点自带注释：「用途：换族决策的监控面」「proposal-only」。前端 0 调用，**正确地不该给用户看** |
| 复盘笔记写入面 | `/api/reviews` POST/PUT/DELETE + `/tags` `/insights` | 研究员（agent 复盘） | 前端只用了 GET `/reviews`（`CandidateSection.tsx:487`），写入面 0 调用 |
| 仪表盘 | `/api/dashboard/metrics` | 运维/研究员 | `/dashboard` 路由已在 W4 删掉，**端点还留着**（纯死代码） |
| 信号列表 | `/api/aqsp/signals` | 复盘页数据源 | 复盘页 `/reviews` 已删 ⇒ 数据源无消费者 |
| 12 赛道雷达 | `/api/radar` `/api/radar/refresh` | 已被 `/stream` 取代 | `/radar` 路由已重定向到 `/stream`，**端点仍留着 20-40s 全量重抓** |
| AI 对话 | `/api/chat` | 研究员 | 前端 0 调用（用户要的是「有筛选有判断」，不是自由对话） |
| DSR/PBO 门禁 / CSCV | `src/aqsp/backtest/` | 研究员 | CI 在跑，用户不看。**这块不用动**，只是别把它算进「用户价值」 |

### 后端端点利用率

| 分类 | 数量 | 说明 |
|---|---|---|
| 总端点 | 63 | `app.py` |
| 前端实际调用 | **21** | 持仓 6 / 行情 6 / 今日 4 / 我的资料 4 / 个股 2 / 系统 1 |
| 端点已定义但前端 0 调用 | **31** | 见上表 |
| 路由已删但端点还活着 | 4 | `/dashboard/metrics` `/radar` `/api/aqsp/signals` `/api/reviews` 写入面 |

---

## 用户每天最短路径（≤3 步）

```
1. 打开 /（自动跳 /today）
   └─ 顶部 MarketStrip：上证/深证/创业板 + 涨跌家数（判断今天能不能动）
   └─ DecisionPanel：门禁 pass/fail + 策略表现
   └─ 候选区：先看「命中我的持仓/自选」，再看其余候选

2. 点 /stream（盘中随时）
   └─ 财联社快讯（有 sources 健康标注，非 108 源堆量）
   └─ 概念异动（含原 /concepts 的板块传导）

3. 点「持仓与自选」
   └─ 输入实盘持仓 → 盈亏 + 强化跟进 + 该卖提醒
   └─ 自选同页，不再跳第二个入口
```

3 步、2 页、1 个持仓页 —— 每次实盘判断不需要在 4 个入口之间来回跳。

---

## 一句话建议

**砍掉导航里的「笔记」「资料」和两个孤儿路由，把「自选」并进「持仓」，把「7 入口 2 组」压成「3 入口 1 组」；后端 63 个端点里 31 个前端从未调用的单票数据面 + 4 个已删路由的残留端点，移出用户可感知范围（移到研究员只读接口区或标注 internal），用户面只留「今日选股 / 小道信息 / 行情环境 / 持仓与自选」4 个入口、21 个端点。**

---

## 附：证据索引（可复核）

| 结论 | 证据位置 |
|---|---|
| 7 个导航入口 | `frontend/src/lib/ia.ts:43-68` |
| 2 个孤儿路由 | `frontend/src/router.tsx:37-38` vs `ia.ts` NAV_LINES 无对应项 |
| `/concepts` 与 `/stream` 同源 | `pages/ConceptsPage.tsx:3` 与 `pages/InfoStreamPage.tsx:3` 都读催化/概念 |
| `/stock/:code` 与 Drawer 重复 | `pages/StockResearchPage.tsx:3` 注释自承「复用 StockDetailDrawer 的行情渲染」 |
| `/market` 一次拉 7 端点 | `components/aqsp/useMarketData.ts:44-49` |
| 31 端点 0 调用 | 逐个 grep `frontend/src/`（排除 `.test.`）结果见「砍掉/降级」表 |
| 残留端点 | `app.py:1782`（dashboard/metrics）、`app.py:648`（radar）、`app.py:2054`（signals） |
| Researcher-only 面 | `app.py:2089`（ic-history）、`app.py:2203`（ic-dual-verdict，注释「proposal-only」）自述用途 |

### 附注：候选区已有「持仓/自选标记」，缺的是排序权重

候选区**并非**没有持仓/自选联动——这条联动已完整存在：

| 位置 | 作用 |
|---|---|
| `frontend/src/lib/ownership.ts`（52 行） | `ownedKind`（**持仓优先于自选**，第 19-24 行）/ `summarizeOwned` / `alreadyHeldCount` |
| `frontend/src/components/aqsp/useOwnership.ts`（40 行） | 同时读 `api.portfolio()` 与 `loadWatch()`，持仓不可用时降级为仅自选 |
| `frontend/src/components/aqsp/sections/CandidateSection.tsx:19,28,88,159,292` | 已在候选行渲染 `<OwnerTag>` |

**真正缺的是排序**：`ownership.ts` 内 `sort` / `priority` / `order` / `orderBy` **0 命中**，
候选的排序口径仍是 `candidate-view.ts:31-71` 的 `strategyOptions` / `strategyCounts`（按策略分组）。

⇒ 因此「合并页与排序同批」这条建议成立，但理由是**标记已存在、排序未接**（合并页若不把排序权重
接上，用户仍会看到自己的票埋在策略分组里），**不是**「从零新建一个维度」。
