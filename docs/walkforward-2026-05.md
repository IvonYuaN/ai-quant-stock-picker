# Walk-Forward 回测报告

**运行日期**: 2026-05-27
**回测区间**: 2018-01-01 ~ 2024-12-31
**标的数量**: 20 只 (沪深300成分股代表)
**训练窗口**: 120 天
**测试窗口**: 30 天
**Purge Gap**: 5 天

---

## 整体指标

| 指标 | 值 | 说明 |
|------|-----|------|
| 总收益 | 待运行 | 需要真实数据验证 |
| 年化收益 | 待运行 | |
| 最大回撤 | 待运行 | |
| Sharpe Ratio | 待运行 | |
| 胜率 | 待运行 | |
| 盈利因子 | 待运行 | |
| 总交易次数 | 待运行 | |
| 不可成交次数 | 待运行 | |

---

## 过拟合检测

| 指标 | 值 | 说明 |
|------|-----|------|
| Deflated Sharpe Ratio | 待运行 | > 1.0 表示策略可能有效 |
| PBO (过拟合概率) | 待运行 | < 50% 表示低过拟合风险 |
| 稳健性评分 | 待运行 | > 70% 表示稳定 |
| 参数标准差 | 待运行 | 越小越稳定 |

---

## 分阶段表现

| 阶段 | 收益 | Sharpe | 胜率 | 交易次数 | 不可成交 |
|------|------|--------|------|----------|----------|
| 待运行 | - | - | - | - | - |

---

## 分 Regime 胜率

| Regime | 胜率 | 样本数 |
|--------|------|--------|
| stable_bull | 待运行 | - |
| volatile_bull | 待运行 | - |
| stable_bear | 待运行 | - |
| volatile_bear | 待运行 | - |
| stable_sideways | 待运行 | - |
| volatile_sideways | 待运行 | - |

---

## 结论

⏳ **待运行**: 需要执行 `aqsp walkforward` 命令填充真实数据。

### 运行方法

```bash
# 使用 akshare 数据源运行完整回测
PYTHONPATH=src python3 -c "
from aqsp.cli import main
import sys
sys.exit(main([
    'walkforward',
    '--symbols', '600519,000858,601318,600036,000333,002415,600276,601888,300750,002594,600900,601012,000001,600000,601166,002475,300059,600887,000725,002714',
    '--start', '2018-01-01',
    '--end', '2024-12-31',
    '--train-days', '120',
    '--test-days', '30',
    '--purge-days', '5',
    '--report', 'docs/walkforward-2026-05.md'
]))
"
```

### 验证标准

根据 architecture.md §8 的自我校验:

1. **策略来源**: 经济或市场结构假设是什么? → 写进 `hypothesis` 字段
2. **数据消毒**: 幸存者偏差、复权口径、停牌涨跌停、财报时间戳是否都对齐?
3. **验证方式**: 用 Purged + Embargoed Walk-Forward 或 CPCV, **不要用普通 K 折**
4. **统计折扣**: 试了 N 组参数挑出最好的, Sharpe 必须用 Deflated Sharpe Ratio 折扣
5. **运行后学习**: 学习对象是 IC / 命中率分布 / 特征漂移(KS 检验), **不是 PnL**

### DSR 阈值

- DSR > 1.0: ✅ 策略通过验证, 可以考虑实盘
- DSR 0.5 ~ 1.0: ⚠️ 策略表现一般, 建议优化
- DSR < 0.5: ❌ 策略未通过验证, 不建议实盘

### PBO 阈值

- PBO < 50%: ✅ 低过拟合风险
- PBO 50% ~ 80%: ⚠️ 中等过拟合风险
- PBO > 80%: ❌ 高过拟合风险, 策略可能无效

---

## 参考资料

- López de Prado, *Advances in Financial Machine Learning*, ch.7 (Purged CV) 和 ch.14 (DSR)
- Bailey & López de Prado, *The Deflated Sharpe Ratio*
- CPCV 对比: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4686376
