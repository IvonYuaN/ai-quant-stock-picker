---
name: aqsp-market-overview
description: Read AQSP's market state without guessing — runtime health, sentiment phase, turnover leaders and theme strength, in the order that prevents "data outage" being misread as "market crash". Use when the user asks how the market is doing today, what the sentiment phase is, which themes are strongest, or whether the data pipeline is healthy.
description_zh: "按正确顺序读 AQSP 的市场状态（运行时健康 → 情绪 phase → 成交额榜 → 题材强弱），避免把数据故障误读成行情"
description_en: "Read AQSP market state (health → sentiment phase → turnover leaders → theme strength) without misreading outages as moves"
agent_created: true
---

# AQSP 市场总览

## 何时用

用户问「今天市场怎么样」「情绪周期到哪一步了」「哪个题材最强」「数据源正常吗」。
**也用于**：任何要基于市场状态下结论之前的第一道检查。

## 读的顺序（顺序错了会得出相反结论）

### 1. 先看数据健康，再看行情

```bash
aqsp doctor                  # 运行时就绪度（不主动探活）
aqsp sources --ready-only    # 只列就绪的数据源
aqsp sources --json          # 机器可读，便于程序化判断
```

🔴 **数据源故障 ≠ 市场下跌。** 如果 `sources` 显示某源 `stale`/`error`，那么
基于它的一切数字（成交额、涨停家数、板块涨幅）都**不可解读**，只能报「该维度缺数」。
先把这一条说清楚，再谈行情。

### 2. 情绪周期（规则判定，不是预测）

```
GET /api/market/emotion
```

关注字段：

| 字段 | 含义 | 注意 |
|---|---|---|
| `emotion_model.phase` | `freezing` / `repairing` / `fermenting` / `diverging` / `ebbing` / `climax` / `unknown` | **规则 first-match 判定**，不是概率、不是预测 |
| `emotion_model.composite` | 0–100 综合分（四维加权，只在有值维度上重新归一化） | 缺失维度会让分数漂移，看 `dimensions` 判断可信度 |
| `emotion_model.reasons` | 命中该 phase 的具体条件 | **引用它**，不要自己另编解释 |
| `emotion_model` **整体缺失** | 情绪模型降级（数据不足或计算异常） | 核心行情字段照常返回；**不要因此推断市场状态** |

🔴 `phase=unknown` = 数据不足（`zt_count + dt_count + zb_count == 0`）。
**绝不**把 unknown 说成「分歧」或「震荡」——那是编的。

配置在 `config/emotion_model.yaml`（独立于 `thresholds.yaml`）。改阈值要另走流程，
不要在对话里改。

### 3. 市场总览与成交额榜

```
GET /api/market/overview
GET /api/market/turnover-top
```

### 4. 题材强弱

```
GET /api/info            # 概念板块截面 + 叙事归并（噪声标签已过滤）
```

叙事归并规则在 `src/aqsp/features/narrative.py`：`融资融券`/`深股通`/`昨日涨停` 这类
**交易机制标签已被判为噪声丢弃**，`AI智能体`/`AIGC`/`DeepSeek` 已并入同一叙事。
若某题材名看起来「不该出现在主线里」，先查 `label_role()` 的判定，别急着当信号。

**题材历史 K（日/周/月）**：

```python
from aqsp.data.theme_history import load_board_history, board_change_series
from aqsp.features.theme_index import build_vendor_series

history = load_board_history()                      # pit_cache/theme_history.csv
pairs = board_change_series(history, "AI应用")
series = build_vendor_series(pairs, board_name="AI应用", freq="week")
```

🔴 `kind=vendor` 是**东财市值加权口径、不可交易**，不是等权指数。`kind=reference`
（成分股等权）目前**算不出来**（无成员源）。展示时必须带上 `kind_label`。
历史从首次积累日开始，**不可回补**；`degraded=True` 表示中间有缺数据日（不是平盘）。

## 输出纪律

- **不给买卖建议、不给目标价、不预测点位。** AQSP 是候选池生成器，不是投顾。
- 引用数字时**带上 as-of 日期**——这是除数据源故障之外最容易出错的地方。
- 分不清「缺数」和「真值」时，说「缺数」，不要给 0。

## 相关文档

`docs/AQSP_PROJECT_GUIDE.md`（模块地图）、`docs/architecture.md`（宪法）。
