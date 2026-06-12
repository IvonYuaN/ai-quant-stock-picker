# Momentum 策略诊断报告（PR22.5）

## 诊断日期
2026-05-28

## 当前状态

此报告前半段记录的是 PR22.5 当时的历史故障。当前 `src/aqsp/strategies/momentum.py` 已修复 RSI 方向和负收益 `return_score` 下限，并在 `tests/test_strategies.py` 补了防回退测试。本文保留历史根因，是为了解释为什么后续 walk-forward 仍必须复核，而不是说明当前代码仍存在同一 bug。

## 背景

PR22 的 walkforward 诊断跑发现：降低 min_total_score 阈值后 trades 恢复（283/289），但策略严重亏损（Sharpe -2.93，胜率 36%）。这比随机还差，指向系统性 bug。

## 诊断方法

1. 读 `momentum.py` 源码，逐行分析 score 计算逻辑
2. 写 `scripts/diagnose_momentum.py`，对 80 只沪深300 标的跑单日 score 分布
3. 对比 sina vs akshare 数据一致性（akshare 因网络不可用，仅验证 sina）

## 历史根因定位

### 当时确认：RSI 分数是反向信号

当时 `src/aqsp/strategies/momentum.py` 的 RSI 分数逻辑为：

```python
return (overbought - rsi) / (overbought - oversold)
```

逻辑：
- RSI ≥ 70（涨得多，overbought）→ 返回 0.1（**低分**）
- RSI ≤ 30（跌得多，oversold）→ 返回 0.9（**高分**）
- 中间线性插值

**这是一个反向/均值回归信号，不是动量信号。**

### 当时与 momentum 组件的矛盾

当时 momentum 组件为：
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

## 历史结论

**PR22.5 当时三个问题叠加导致策略系统性亏损：**

1. **RSI 反向信号**（最严重）：RSI 作为均值回归信号嵌入 momentum 策略，方向矛盾。涨得多的股票被 RSI 惩罚（overbought → 0.1），跌得多的股票被 RSI 奖励（oversold → 0.9）。

2. **策略实际在做均值回归而非动量**：Top 10 全是超卖股（RSI < 32），策略选的是"跌多了该反弹"的股票，不是"涨势好的"股票。

3. **阈值过高放大问题**：`min_total_score=0.6` 在 RSI 反向拖累下，只有 13.75% 的标的能过线，且全是超卖股。

## 修复建议

### 方案 A：反转 RSI 逻辑（已实施）

```python
# 修复前（反向）：
return (overbought - rsi) / (overbought - oversold)
# RSI=70 → 0.1, RSI=30 → 0.9

# 修复后（正向）：
return (rsi - oversold) / (overbought - oversold)
# RSI=70 → 1.0, RSI=30 → 0.0
```

### 修复后 80 只标的 score 分布

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| score 均值 | 0.3216 | 0.1192 |
| score 最大值 | 0.6701 | 0.5811 |
| 过 0.6 阈值 | 11/80 | 0/80 |
| 过 0.5 阈值 | - | 3/80 |
| 过 0.3 阈值 | 42/80 | 12/80 |

**修复后分数整体下降**——因为超卖股不再被 RSI 奖励。这是正确的行为：策略不再选"跌多了该反弹"的股票。

### 待验证

Walkforward 因新浪 IP 封禁暂无法跑。用户解封后执行：
```bash
aqsp walkforward --source sina --start 2022-03-08 --end 2026-04-30 \
  --train-days 120 --test-days 30 --purge-days 5 --min-score 0.1 \
  --cache-path data/walkforward_cache_sina_rsi_fix \
  --report docs/walkforward-rsi-fix-min01.md
```

### 已补防回退

当前代码已在 `src/aqsp/strategies/momentum.py` 落地两处修复：

1. RSI 分数改为正向：RSI 高（涨势好）→ 高分，RSI 低（跌势差）→ 低分。与 momentum 方向一致。
2. `return_score` 对负收益做下限 clamp，避免 `total_return < 0` 时把 momentum 组件拖成负分。

防回退测试见 `tests/test_strategies.py`：

- `test_momentum_rsi_score_is_directional_when_rsi_is_high`
- `test_momentum_rsi_score_is_not_mean_reversion_when_rsi_is_low`
- `test_momentum_return_score_does_not_go_negative_when_return_is_negative`

### 仍需关注

1. `min_total_score=0.6` 在单因子下仍过高；当前 `config/thresholds.yaml` 已把 composite 主链阈值降到 `0.4`，后续仍需 walk-forward 复核。
2. quality/value 因子仍 disabled，不应在没有验证报告时直接启用。

### 方案 B：移除 RSI 组件

将 RSI 权重设为 0，只保留 momentum + trend。简单但丢失了超买超卖过滤信息。

### 方案 C：RSI 作为过滤器而非分数

RSI ≥ 70 时直接排除（不买过热股），但不参与 score 计算。

## 验证命令

```bash
python3 scripts/diagnose_momentum.py --source sina --symbols 600519,300750,000001,000858,601318,002714,600036,000333,601012,600900
```

## 后续动作

- RSI 逻辑和负收益 clamp 已修复并补防回退测试
- 重跑 walkforward 验证
- 如果 Sharpe 仍为负，再考虑启用 quality/value 因子
