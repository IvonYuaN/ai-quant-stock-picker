# Momentum 策略诊断报告（PR22.5）

## 诊断日期
2026-05-28

## 背景

PR22 的 walkforward 诊断跑发现：降低 min_total_score 阈值后 trades 恢复（283/289），但策略严重亏损（Sharpe -2.93，胜率 36%）。这比随机还差，指向系统性 bug。

## 诊断方法

1. 读 `momentum.py` 源码，逐行分析 score 计算逻辑
2. 写 `scripts/diagnose_momentum.py`，对 80 只沪深300 标的跑单日 score 分布
3. 对比 sina vs akshare 数据一致性（akshare 因网络不可用，仅验证 sina）

## 根因定位

### 确认：RSI 分数是反向信号

`src/aqsp/strategies/momentum.py` 第 99 行：

```python
return (overbought - rsi) / (overbought - oversold)
```

逻辑：
- RSI ≥ 70（涨得多，overbought）→ 返回 0.1（**低分**）
- RSI ≤ 30（跌得多，oversold）→ 返回 0.9（**高分**）
- 中间线性插值

**这是一个反向/均值回归信号，不是动量信号。**

### 与 momentum 组件的矛盾

momentum 组件（第 64 行）：
```python
return_score = min(total_return / min_returns, 1.0)
```

- 涨得多 → return_score 高（趋势跟随）
- 跌得多 → return_score 低甚至负

**两个组件方向相反**：
- momentum 说"买涨的"
- RSI 说"买跌的（超卖反弹）"

### 权重分配

```yaml
weights:
  momentum: 0.4
  trend: 0.3
  rsi: 0.3
```

RSI 占 30% 权重，足以把涨得多的股票从 0.7+ 拉到 0.4 以下。

## 80 只标的 score 分布

```
有效标的: 80/80
score 分布: min=0.0000 max=0.6701 mean=0.3216 std=0.2198
过 0.6 阈值: 11/80 (13.75%)
过 0.3 阈值: 42/80 (52.5%)
```

### Top 10（全部是低 RSI 超卖股）

| 标的 | 收益率 | RSI | momentum_score | rsi_score | final |
|------|--------|-----|----------------|-----------|-------|
| 600019 | 4.62% | 28.6 | 0.6252 | 0.9000 | 0.6701 |
| 600025 | 3.84% | 28.2 | 0.6158 | 0.9000 | 0.6663 |
| 600011 | 5.42% | 29.4 | 0.5935 | 0.9000 | 0.6574 |
| 601225 | 11.29% | 29.6 | 0.5806 | 0.9000 | 0.6522 |
| 601888 | 14.06% | 32.5 | 0.5000 | 0.9378 | 0.6314 |

Top 10 的 RSI 全部在 17-32 区间（超卖区）。策略选的是"跌得多但近期微涨"的股票。

### Bottom 10（全部是负 momentum）

| 标的 | 收益率 | RSI | momentum_score | rsi_score | final |
|------|--------|-----|----------------|-----------|-------|
| 600048 | -15.08% | 23.2 | -1.2841 | 0.9000 | 0.0000 |
| 600104 | -20.33% | 25.8 | -1.9136 | 0.9000 | 0.0000 |
| 600570 | -21.79% | 46.2 | -2.1792 | 0.5953 | 0.0000 |

## 结论

**三个问题叠加导致策略系统性亏损：**

1. **RSI 反向信号**（最严重）：RSI 作为均值回归信号嵌入 momentum 策略，方向矛盾。涨得多的股票被 RSI 惩罚（overbought → 0.1），跌得多的股票被 RSI 奖励（oversold → 0.9）。

2. **策略实际在做均值回归而非动量**：Top 10 全是超卖股（RSI < 32），策略选的是"跌多了该反弹"的股票，不是"涨势好的"股票。

3. **阈值过高放大问题**：`min_total_score=0.6` 在 RSI 反向拖累下，只有 13.75% 的标的能过线，且全是超卖股。

## 修复建议

### 方案 A：反转 RSI 逻辑（推荐）

```python
# 当前（反向）：
return (overbought - rsi) / (overbought - oversold)

# 修复（正向）：
return (rsi - oversold) / (overbought - oversold)
```

这样 RSI 高（涨势好）→ 高分，RSI 低（跌势差）→ 低分。与 momentum 方向一致。

### 方案 B：移除 RSI 组件

将 RSI 权重设为 0，只保留 momentum + trend。简单但丢失了超买超卖过滤信息。

### 方案 C：RSI 作为过滤器而非分数

RSI ≥ 70 时直接排除（不买过热股），但不参与 score 计算。

## 验证命令

```bash
python3 scripts/diagnose_momentum.py --source sina --symbols 600519,300750,000001,000858,601318,002714,600036,000333,601012,600900
```

## 后续动作

- PR23 修复 RSI 逻辑（方案 A）
- 修复后重跑 walkforward 验证
- 如果 Sharpe 仍为负，再考虑启用 quality/value 因子
