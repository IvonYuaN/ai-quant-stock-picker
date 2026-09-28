#!/bin/bash
# 策略参数优化和A/B测试平台 - 快速开始示例

echo "==================================================================="
echo "策略参数优化和A/B测试平台 - 示例脚本"
echo "==================================================================="
echo ""

# 示例 1: 参数网格搜索
echo "示例 1: 运行参数网格搜索"
echo "-------------------------------------------------------------------"
echo "命令示例："
echo ""
cat << 'EOF'
aqsp experiment grid \
  --strategy volume_breakout \
  --param volume_ratio_min:1.2,1.35,1.5 \
  --param breakout_proximity:0.99,0.995,1.0 \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --train-days 120 \
  --test-days 30 \
  --purge-days 5 \
  --top-n 10 \
  --output-dir experiments
EOF
echo ""
echo "这将测试 3 × 3 = 9 个参数组合"
echo ""

# 示例 2: A/B 测试
echo "示例 2: 运行 A/B 测试"
echo "-------------------------------------------------------------------"
echo "命令示例："
echo ""
cat << 'EOF'
aqsp experiment ab \
  --variant-a config/thresholds_variant_a.yaml \
  --variant-b config/thresholds_variant_b.yaml \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --name conservative_vs_aggressive \
  --train-days 120 \
  --test-days 30 \
  --purge-days 5 \
  --top-n 10 \
  --output-dir experiments
EOF
echo ""
echo "对比两个配置变体的性能"
echo ""

# 示例 3: 生成报告
echo "示例 3: 生成实验报告"
echo "-------------------------------------------------------------------"
echo "网格搜索报告："
echo ""
cat << 'EOF'
python scripts/experiment_report.py <experiment_id> \
  --type grid \
  --output-dir reports/experiments
EOF
echo ""
echo "A/B 测试报告："
echo ""
cat << 'EOF'
python scripts/experiment_report.py <experiment_id> \
  --type ab \
  --output-dir reports/experiments
EOF
echo ""

# 示例 4: 更多参数优化示例
echo "示例 4: 优化动量策略参数"
echo "-------------------------------------------------------------------"
echo "命令示例："
echo ""
cat << 'EOF'
aqsp experiment grid \
  --strategy momentum \
  --param momentum.min_returns:0.03,0.05,0.07,0.09 \
  --param momentum.lookback_days:30,60,90 \
  --param momentum.max_volatility:0.25,0.30,0.35 \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --output-dir experiments
EOF
echo ""
echo "这将测试 4 × 3 × 3 = 36 个参数组合"
echo ""

# 输出目录结构说明
echo "输出文件结构"
echo "-------------------------------------------------------------------"
cat << 'EOF'
experiments/
├── <experiment_id>_config.json          # 实验配置
├── <experiment_id>_results.jsonl        # 网格搜索结果
├── <experiment_id>_variant_a.json       # A/B 测试变体 A
├── <experiment_id>_variant_b.json       # A/B 测试变体 B
└── <experiment_id>_comparison.json      # A/B 对比结果

reports/experiments/
├── <experiment_id>_report.md            # Markdown 报告
└── <experiment_id>_charts.png           # 可视化图表
EOF
echo ""

# 评估标准
echo "结果评估标准"
echo "-------------------------------------------------------------------"
cat << 'EOF'
优先级（从高到低）：

1. DSR (Deflated Sharpe Ratio) > 1.0
   - 必要条件，低于此值说明存在过拟合风险

2. PBO (Probability of Backtest Overfitting) < 0.5
   - 过拟合概率低（需要多变体 CSCV）

3. Max Drawdown < 20%
   - 风险可接受

4. Sharpe Ratio > 1.0
   - 风险调整后收益

5. Robustness Score > 0.6
   - 跨期稳定性

6. Win Rate > 50%
   - 交易胜率
EOF
echo ""

echo "==================================================================="
echo "详细文档: docs/experiment_framework.md"
echo "实现总结: docs/experiment_implementation_summary.md"
echo "==================================================================="
