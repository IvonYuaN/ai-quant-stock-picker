# AQSP Agent Skills（草稿）

三个给 AI 助手用的操作手册（WorkBuddy / Claude 风格的 `SKILL.md`）：
**市场总览**、**盘后复盘**、**个股研究**。

## 它们是什么

不是代码，也不是新的数据链路。每个 `SKILL.md` 只做一件事：把「读 AQSP 的正确顺序
和结论纪律」写下来，让助手不要靠猜。

它们刻意**不复制** `docs/AQSP_PROJECT_GUIDE.md` 的内容——那份文档是模块地图与
实现细节的单一来源。这里只写**操作顺序 + 易错点 + 不能下的结论**，细节一律指回去。
这样两处不会各自漂移。

## 怎么装

```bash
# 装到用户级 skills（跨项目可用）
cp -r docs/skills/aqsp-market-overview  ~/.workbuddy-ai/skills/
cp -r docs/skills/aqsp-closing-review   ~/.workbuddy-ai/skills/
cp -r docs/skills/aqsp-stock-research   ~/.workbuddy-ai/skills/
```

装完在对话里直接说「今天市场怎么样」「跑一下复盘」即可触发。

## 为什么放在仓库里

`~/.workbuddy-ai/skills/` 是**本机个人目录、不进版本库**。放进 `docs/skills/` 的好处是：

- 命令与 API 路径的改动可以跟着 PR 一起 review（写错的 skill 会教出错误操作）；
- 换机器 / 新同事不用从零再写一遍；
- 与 `docs/` 下其它文档同源，改动有历史。

## 三个 skill 的共同纪律

1. **缺数 ≠ 0，缺数 ≠ 无风险。** 数据源 `stale`/`error` 时，基于它的数字不可解读。
2. **不给目标价、不给买卖建议、不预测点位。** AQSP 是候选池生成器，不是投顾。
3. **LLM 只摘要，不评分。** 任何「综合评分」都必须是因子算出来的，不能是模型编的。
4. **看亏损就调阈值 = 红线。** 阈值只能在 walk-forward 之后改，且必须附报告。
