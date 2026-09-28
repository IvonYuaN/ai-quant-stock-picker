# 策略参数优化和A/B测试平台

本平台提供策略参数网格搜索和A/B测试功能，用于系统化地优化策略参数并对比不同配置的性能。

## 功能特性

- **参数网格搜索**: 遍历参数空间，找到最优参数组合
- **A/B测试**: 对比两个配置变体的性能差异
- **Walk-Forward验证**: 复用现有的walk-forward测试框架，遵循防前视偏差规则
- **DSR和PBO评估**: 使用Deflated Sharpe Ratio和Probability of Backtest Overfitting评估策略稳健性
- **可视化报告**: 自动生成包含图表和分析的Markdown报告

## 快速开始

### 1. 参数网格搜索

```bash
# 示例：优化volume_breakout策略的参数
aqsp experiment grid \
  --strategy volume_breakout \
  --param volume_ratio_min:1.2,1.35,1.5 \
  --param breakout_proximity:0.99,0.995,1.0 \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --train-days 120 \
  --test-days 30 \
  --output-dir experiments
```

这将测试 3 × 3 = 9 个参数组合，每个组合运行完整的walk-forward回测。

### 2. A/B测试

```bash
# 示例：对比两个thresholds配置
aqsp experiment ab \
  --variant-a config/thresholds_v1.yaml \
  --variant-b config/thresholds_v2.yaml \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --name thresholds_comparison \
  --output-dir experiments
```

### 3. 生成报告

```bash
# 生成网格搜索报告
python scripts/experiment_report.py <experiment_id> --type grid

# 生成A/B测试报告
python scripts/experiment_report.py <experiment_id> --type ab
```

报告将保存到 `reports/experiments/` 目录。

## 参数格式

### 网格搜索参数

使用 `--param` 指定参数及其候选值：

```bash
--param param_name:value1,value2,value3
```

支持嵌套参数路径（使用点号分隔）：

```bash
--param momentum.min_returns:0.03,0.05,0.07
--param momentum.lookback_days:30,60,90
```

### A/B测试配置

创建两个YAML配置文件，每个文件包含完整的thresholds配置：

**config/thresholds_v1.yaml:**
```yaml
version: "1.0.0"
momentum:
  min_returns: 0.05
  lookback_days: 60
risk:
  single_stock_stop_pct: 0.08
# ... 其他配置
```

**config/thresholds_v2.yaml:**
```yaml
version: "1.0.1"
momentum:
  min_returns: 0.07
  lookback_days: 90
risk:
  single_stock_stop_pct: 0.10
# ... 其他配置
```

## 实验结果

### 输出目录结构

```
experiments/
├── <experiment_id>_config.json          # 实验配置
├── <experiment_id>_results.jsonl        # 网格搜索结果（每行一个变体）
├── <experiment_id>_variant_a.json       # A/B测试变体A结果
├── <experiment_id>_variant_b.json       # A/B测试变体B结果
└── <experiment_id>_comparison.json      # A/B测试对比结果

reports/experiments/
├── <experiment_id>_report.md            # Markdown报告
└── <experiment_id>_charts.png           # 可视化图表
```

### 结果指标

每个实验结果包含以下指标：

- **Sharpe Ratio**: 夏普比率（年化）
- **Deflated Sharpe Ratio (DSR)**: 校正后的夏普比率，考虑多次试验的过拟合风险
- **PBO**: 过拟合概率（仅多变体CSCV可计算）
- **Total Return**: 总收益率
- **Annual Return**: 年化收益率
- **Max Drawdown**: 最大回撤
- **Win Rate**: 胜率
- **Profit Factor**: 盈利因子
- **Robustness Score**: 稳健性评分
- **Trades**: 交易次数

## 报告内容

### 网格搜索报告

1. **最优配置 (Top 5)**: 按DSR排序的最佳参数组合
2. **参数敏感性分析**: 每个参数对性能的影响
3. **可视化图表**:
   - 参数敏感性散点图
   - 收益-回撤散点图（按DSR着色）
   - Sharpe vs DSR对比
   - 指标分布箱线图

### A/B测试报告

1. **变体对比表**: 所有指标的并排对比
2. **配置详情**: 两个变体的完整配置
3. **可视化图表**:
   - 指标对比柱状图
   - 差异百分比条形图
   - 胜负统计饼图
   - 关键指标雷达图

## 最佳实践

### 1. 参数网格设计

- **从粗到细**: 先用稀疏网格探索大范围，再在有希望的区域细化
- **控制规模**: 避免组合爆炸，建议每次测试不超过50个组合
- **独立变量**: 优先测试相互独立的参数

示例（分阶段优化）：

```bash
# 阶段1：粗网格探索
aqsp experiment grid \
  --strategy momentum \
  --param momentum.min_returns:0.03,0.07,0.11 \
  --param momentum.lookback_days:30,60,90 \
  --start 2024-01-01 --end 2024-12-31

# 阶段2：在最优区域细化（假设0.07和60天最优）
aqsp experiment grid \
  --strategy momentum \
  --param momentum.min_returns:0.05,0.06,0.07,0.08,0.09 \
  --param momentum.lookback_days:50,55,60,65,70 \
  --start 2024-01-01 --end 2024-12-31
```

### 2. A/B测试原则

- **单一变量**: 每次只改变一个关键参数或子系统
- **有意义的差异**: 确保两个变体有实质性差异，避免微调
- **充分的数据**: 使用至少1年的数据进行对比

### 3. 结果评估

优先考虑以下指标（按重要性排序）：

1. **DSR > 1.0**: 必要条件，低于此值说明存在过拟合风险
2. **PBO < 0.5**: 过拟合概率低（需要多变体CSCV）
3. **Max Drawdown**: 风险可接受（通常 < 20%）
4. **Sharpe Ratio**: 风险调整后收益（通常 > 1.0）
5. **Robustness Score**: 跨期稳定性（越高越好）

### 4. 防止过拟合

- **使用DSR而非Sharpe**: DSR考虑了多次试验的影响
- **保留测试集**: 最优参数确定后，在新的时间段验证
- **限制优化次数**: 避免反复优化同一策略
- **记录所有实验**: 包括失败的尝试，防止选择性报告偏差

## 编程接口

### Python API

```python
from aqsp.experiment import GridSearchRunner, ABTestRunner
from aqsp.strategies.composite import CompositeStrategy
from aqsp.strategies.thresholds import Thresholds

# 定义策略工厂
def strategy_factory(params):
    thresholds = Thresholds()
    thresholds_dict = thresholds.to_dict()
    thresholds_dict["momentum"].update(params)
    modified = Thresholds.from_dict(thresholds_dict)
    return CompositeStrategy(modified)

# 运行网格搜索
runner = GridSearchRunner(
    strategy_factory=strategy_factory,
    base_config={},
    output_dir="experiments",
)

results = runner.run(
    param_grid={
        "min_returns": [0.03, 0.05, 0.07],
        "lookback_days": [30, 60, 90],
    },
    data=data,  # Dict[str, pd.DataFrame]
    start_date="2024-01-01",
    end_date="2024-12-31",
    train_period_days=120,
    test_period_days=30,
    purge_days=5,
    top_n=10,
)

# 查看最优结果
best = max(results, key=lambda r: r.metrics["deflated_sharpe"])
print(f"最优参数: {best.params}")
print(f"DSR: {best.metrics['deflated_sharpe']:.2f}")
```

## 常见问题

### Q: 网格搜索需要多长时间？

A: 取决于参数组合数量、数据量和回测周期。经验值：
- 小网格（<10组合，500股票，1年）: 5-15分钟
- 中等网格（20-50组合）: 30分钟-2小时
- 大网格（>100组合）: 数小时

建议使用 `--streaming` 模式减少内存占用。

### Q: 如何选择train/test/purge天数？

A: 推荐配置：
- `--train-days 120`: 4个月训练期
- `--test-days 30`: 1个月测试期
- `--purge-days 5`: 5天缓冲期（防止前视偏差）

可根据策略频率调整（高频策略用更短周期）。

### Q: DSR为负数是否正常？

A: 正常。DSR是z-score，可以为负：
- DSR > 1.0: 策略显著优于随机
- DSR 0-1.0: 策略可能有效，但不够稳健
- DSR < 0: 策略表现不如基准，或存在严重过拟合

### Q: 为什么PBO显示为N/A？

A: 单策略walk-forward无法计算PBO（需要N≥2个变体做CSCV）。使用网格搜索或 `--grid-cscv` 生成多变体才能计算PBO。

## 技术架构

### 组件

- **aqsp/experiment/__init__.py**: 核心实验框架
  - `GridSearchRunner`: 网格搜索引擎
  - `ABTestRunner`: A/B测试引擎
  - `ExperimentConfig`, `ExperimentResult`: 数据模型

- **scripts/experiment_report.py**: 报告生成器
  - 参数敏感性分析
  - 可视化图表生成
  - Markdown报告输出

- **src/aqsp/cli.py**: CLI命令接口
  - `aqsp experiment grid`: 网格搜索命令
  - `aqsp experiment ab`: A/B测试命令

### 依赖关系

```
experiment
├── backtest.walk_forward (复用walk-forward测试)
├── strategies.composite (策略组合)
├── strategies.thresholds (配置管理)
└── data.sqlite_db_source (数据源)
```

### 数据流

```
参数网格 → 策略工厂 → Walk-Forward测试 → 提取指标 → 保存结果
                                             ↓
                                         生成报告
                                             ↓
                                    可视化 + Markdown
```

## 扩展

### 添加新策略

在 `strategy_factory` 中添加参数映射逻辑：

```python
def strategy_factory(params):
    thresholds_dict = base_thresholds.to_dict()
    
    if strategy_name == "my_strategy":
        if "my_strategy" not in thresholds_dict:
            thresholds_dict["my_strategy"] = {}
        thresholds_dict["my_strategy"].update(params)
    
    return CompositeStrategy(Thresholds.from_dict(thresholds_dict))
```

### 自定义指标

修改 `GridSearchRunner._extract_metrics` 添加新指标：

```python
def _extract_metrics(self, wf_result: WalkForwardResult) -> Dict[str, float]:
    metrics = {
        # ... 现有指标
        "custom_metric": self._calculate_custom(wf_result),
    }
    return metrics
```

## 参考文献

- Bailey, D. H., & López de Prado, M. (2014). The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality.
- Bailey, D. H., et al. (2014). The Probability of Backtest Overfitting.

## 更新日志

- **2026-09-28**: 初始版本
  - 实现网格搜索和A/B测试
  - 集成walk-forward验证
  - 支持DSR和PBO评估
  - 自动生成可视化报告
