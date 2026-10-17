---
name: aqsp-stock-research
description: Research a single A-share name through AQSP's evidence surfaces — candidates, financials, valuation percentile, fund flow, events, chip distribution — and state what the data does and does not support. Use when the user asks about one specific stock, why it was (or was not) selected, or wants a fundamentals + flows snapshot.
description_zh: "用 AQSP 的证据面（候选/财务/估值分位/资金流/事件/筹码）研究单只票，并明确数据支持什么、不支持什么"
description_en: "Research one A-share name through AQSP evidence surfaces and state the limits of what the data supports"
agent_created: true
---

# AQSP 个股研究

## 何时用

用户问「这只票怎么样」「为什么它进了候选/没进」「帮我看看 600519」。

## 读的顺序

### 1. 先看它是不是候选，以及理由

```bash
aqsp runtime-snapshot --date 2026-10-16            # 只读运行时视图（推荐的入口）
aqsp runtime-snapshot --json                       # 机器可读
```

```
GET /api/aqsp/snapshot                    # 全量候选快照
GET /api/aqsp/candidate/{symbol}          # 单票候选证据
GET /api/aqsp/candidates/{symbol}         # 历史候选记录
```

**候选证据 = 因子分 + 命中理由 + 排除理由。** 如果这只票**没进候选**，先看
`filters_lethal` 的排雷结果（跌停保护/公告关键词/股东户数/解禁）——被排雷剔掉与
「分数不够」是两件事，别混着说。

### 2. 基本面与估值

```
GET /api/financials                # 财务
GET /api/valuation                 # 估值
GET /api/valuation/percentile      # 估值历史分位（比绝对值有用得多）
GET /api/reports                   # 研报
```

🔴 估值分位要**说清区间**（近三年/近五年），不同区间结论可能相反。

### 3. 资金与事件

```
GET /api/fund-flow        # 资金流
GET /api/dragon-tiger     # 龙虎榜
GET /api/lockup           # 解禁
GET /api/announcements    # 公告
GET /api/margin           # 两融
GET /api/block-trade      # 大宗
GET /api/holders          # 股东户数
GET /api/disclosure       # 披露
```

### 4. 板块归属与题材

```
GET /api/blocks           # 所属板块
GET /api/hot-concepts     # 当下被市场归到哪些概念在炒
GET /api/industry         # 行业对比
```

⚠️ 板块归属只给**当前**口径。做历史对比时要用 PIT 行业
（`aqsp.data.industry_pit.industry_as_of(df, code, as_of)`），否则是前视偏差。

### 5. 筹码分布

```bash
aqsp cyq --symbol 600519            # 获利比例 / 成本 / 集中度
aqsp cyq --symbol 600519 --csv data/600519.csv   # 用本地 OHLCV 算
```

### 6. 行情与形态

```
GET /api/kline?code=600519
GET /api/quote?codes=600519
```

## 输出纪律

- **LLM 只做摘要，不做打分。** 不要给出「综合评分 8.5/10」这类自己造的分数——
  AQSP 的分数来自因子，不来自语言模型。
- **不给目标价、不给买卖建议、不预测。**
- 每个结论标注**数据日期**与**数据来源**；缺数就说缺数，不要用「暂无数据」掩饰成
  「没有风险」。
- 用户问「能不能买」时：回答这只票在 AQSP 体系里的**证据状态**（是否候选、命中/
  排除理由、数据是否完整），并明确说明这不是投资建议。

## 相关文档

`docs/AQSP_PROJECT_GUIDE.md`（模块地图与 API 清单）、`docs/architecture.md`（宪法）。
