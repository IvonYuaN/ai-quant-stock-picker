---
name: aqsp-closing-review
description: Produce and read an AQSP post-close review correctly — which command to run, which sections may legitimately be empty, and which conclusions are off-limits. Use when the user asks for a closing review, a weekly recap, "复盘", "收评", or wants to know how today's candidates performed.
description_zh: "跑 AQSP 盘后复盘/周度复盘，并正确解读各段（哪些段为空是正常的、哪些结论不能下）"
description_en: "Run and correctly interpret AQSP closing/weekly review reports"
agent_created: true
---

# AQSP 盘后复盘

## 何时用

用户要「复盘」「收评」「今天的候选表现」「这周怎么样」。

## 怎么跑

```bash
# 最近一个已收盘交易日（不带 --date 的默认行为）
aqsp closing-review --report reports/closing.md

# 指定日期（补跑历史）
aqsp closing-review --date 2026-10-16 --report reports/closing-20261016.md

# 周度复盘
aqsp closing-review --weekly --report reports/closing-weekly.md

# 需要推送时才加（默认不发通知）
aqsp closing-review --notify
```

🔴 **`--notify` 是显式开关。** 不要为了「顺便」加上——线上通知走的是
`notifier.py` 的真实渠道，误发会打扰到人。

## 各段读法（哪些「空」是正常的）

| 段 | 数据来源 | 空/缺时的含义 |
|---|---|---|
| 板块资金面 | `pit_cache/concept_board.csv`（盘后预载，prod 经 runner 中继） | 文件陈旧 = 预载链路断了，**不是**「今天没有资金流入」 |
| 因子 IC 健康 | `pit_cache/factor_ic/factor_ic_latest.json`（runner 滚动诊断回流） | 缺失 ⇒ 降级为空串；**不要**当作「因子全部失效」 |
| 候选与验证 | 账本 `data/predictions.jsonl` | 无信号 ⇒ `_empty_review` 路径，属正常 |
| 情绪周期 | 同 `aqsp-market-overview` 的 `/api/market/emotion` | `emotion_model` 缺失 ⇒ 该维度降级，其余段照常 |

🔴 **IC 口径易误读**：`factor_ic_latest.json` 里的 per-factor IC 是**套用 live config
的 `invert` 之后**的值。报出 `+0.016` 时，原始 IC 是 `-0.016`。引用前先确认有没有
`invert_signal`。

## 结论纪律（AGENTS.md §5 红线）

- **`not_executable` 的预测不计入胜率。** 涨跌停一字板买不进、停牌无成交，这些样本
  必须剔除；把不可成交样本算成盈利是自欺。
- **看亏损就调参数 = 红线。** 阈值只能在 walk-forward 之后改，且必须附报告。
  对话里「要不把 min_total_score 降一点」这类提议，一律回答「需要走 walk-forward」。
- **不给目标价、不给买卖建议。** 复盘是回顾，不是荐股。
- 报告里没有的结论不要补。缺数就说缺数。

## 想核账本时

```bash
aqsp run --notify            # 收盘选股 + 落账本（会写 data/predictions.jsonl）
aqsp briefing --output reports/briefing.md   # 次日早报
```

两者都会**写文件**；只在明确需要时跑。

## 相关文档

`docs/daily-operation.md`（日常操作）、`docs/AQSP_PROJECT_GUIDE.md` §5（主链数据流）。
