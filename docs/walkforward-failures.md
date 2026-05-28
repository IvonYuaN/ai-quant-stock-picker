# Walk-forward 失败立此存照

## 2026-05-28 第一次真跑

**配置**：
- 数据源: sina（push2his.eastmoney.com 在本地网络环境 SNI 阻断，akshare 路不通）
- 窗口: 2022-03-08 ~ 2026-04-30（受 sina API 1023 根上限限制，原 2018-2024 计划无法执行）
- 训练/测试: 120/30 day，purge 5 day
- 标的池: 沪深 300 默认池（实际有效 90 只）

**结果**：
- DSR: 0.0000
- PBO: 0.00%
- 总交易次数: 0
- 是否通过双门: **否**

**根因初判**：
- CompositeStrategy 在 `min_total_score=0.6` 阈值下，quality/value 策略 disabled，仅 momentum 一个因子加权后几乎不可能 ≥0.6，导致 0 交易
- 90 只标的在 2022-2026 窗口内，策略未触发任何选股信号

**后续动作**：
- 不升 thresholds.yaml version，保持 1.0.0
- 不进入实盘
- 累积 30 天冷启动 ledger 后重跑（CONSTITUTION §1.3 #14）
- 网络条件改善（换网络/VPN 节点）后用 akshare 跑 2018-2024 完整窗口
- 考虑降低 `min_total_score` 到 0.3 或添加 `--min-score 0` 参数重跑

**完整报告**：见 `docs/walkforward-2026-05-27-real.md`

## 2026-05-28 诊断重跑（PR22 T1）

### --min-score 0.3 重跑结果
- 总交易次数: 283
- DSR: 0.0000
- PBO: 75.00%
- 是否过双门: 否

### --min-score 0.1 重跑结果
- 总交易次数: 289
- DSR: 0.0000
- PBO: 75.00%
- 是否过双门: 否

### 诊断结论

**实际命中情景：A**

验证根因正确：min_total_score=0.6 阈值过高 + 单 momentum 因子凑不够分。降低阈值后 trades 恢复（283 / 289），但策略本身严重亏损：

| 阈值 | 交易次数 | 总收益 | 年化收益 | 胜率 | Sharpe | DSR | PBO |
|------|----------|--------|----------|------|--------|-----|-----|
| 0.3  | 283      | -77.68%| -73.69%  | 36.40%| -2.93  | 0.0000| 75.00%|
| 0.1  | 289      | -73.77%| -68.87%  | 36.33%| -2.34  | 0.0000| 75.00%|

**深层结论**：问题不仅仅是阈值。单 momentum 因子在 2022-2026 窗口上产出的信号质量极低（胜率 < 37%，Sharpe < -2），即使放行交易也无法通过 DSR/PBO 双门。

**后续动作**：
- PR23 启用 quality/value 因子让 composite 加权后能稳定产出有意义的信号（不在本 PR 内做）
- PR23 考虑写 `aqsp diagnose-scores` 子命令查看 momentum 在各标的的实际 score 分布（不在本 PR 内做）
- 不升 thresholds.yaml version，保持 1.0.0

## 2026-05-28 Momentum 根因诊断（PR22.5）

### 诊断方法

1. 逐行读 `src/aqsp/strategies/momentum.py` 源码
2. 写 `scripts/diagnose_momentum.py`，对 80 只沪深300 标的跑单日 score 分布
3. 分析 RSI/momentum/trend 三个组件的方向一致性

### 根因：RSI 分数是反向信号

`momentum.py` 第 99 行：
```python
return (overbought - rsi) / (overbought - oversold)
```

- RSI ≥ 70（涨得多）→ 0.1（低分）
- RSI ≤ 30（跌得多）→ 0.9（高分）

**这是均值回归信号，不是动量信号。与 momentum 组件方向矛盾。**

### 80 只标的 score 分布

| 指标 | 值 |
|------|-----|
| 有效标的 | 80/80 |
| score 均值 | 0.3216 |
| score 最大值 | 0.6701 |
| 过 0.6 阈值 | 11/80 (13.75%) |

Top 10 全是 RSI < 32 的超卖股。策略实际在做"跌多了该反弹"的均值回归，不是动量。

### 修复建议

**方案 A（推荐）**：反转 RSI 逻辑
```python
# 当前（反向）：
return (overbought - rsi) / (overbought - oversold)
# 修复（正向）：
return (rsi - oversold) / (overbought - oversold)
```

**方案 B**：移除 RSI 组件（权重设为 0）
**方案 C**：RSI 仅作过滤器（≥70 排除），不参与 score

### 详细报告

见 `docs/momentum-diagnosis.md`
