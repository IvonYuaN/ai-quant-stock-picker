#!/usr/bin/env python3
"""实验报告生成器

生成参数优化和A/B测试的可视化报告。
包含：
- 参数敏感性热图
- 最优参数组合
- 收益曲线对比
- 风险指标对比（夏普率、最大回撤、DSR）
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Dict, Any

import matplotlib
matplotlib.use('Agg')  # 无GUI后端
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import pandas as pd

from aqsp.experiment import ExperimentResult, load_experiment_results


def generate_grid_search_report(
    experiment_id: str,
    output_dir: str = "reports/experiments",
) -> None:
    """生成网格搜索报告"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 加载实验结果
    results = load_experiment_results(experiment_id, "experiments")

    if not results:
        print(f"No results found for experiment {experiment_id}")
        return

    # 提取数据
    params_list = [r.params for r in results]
    metrics_list = [r.metrics for r in results]

    # 生成Markdown报告
    report_lines = [
        f"# 网格搜索实验报告",
        f"",
        f"**实验ID:** {experiment_id}",
        f"**变体数量:** {len(results)}",
        f"",
        f"## 最优配置 (Top 5 by DSR)",
        f"",
    ]

    # 按DSR排序
    sorted_results = sorted(
        results,
        key=lambda r: r.metrics.get("deflated_sharpe", -999),
        reverse=True,
    )

    for i, result in enumerate(sorted_results[:5], 1):
        report_lines.extend([
            f"### {i}. {result.variant_id}",
            f"",
            f"**参数:**",
            f"```json",
            json.dumps(result.params, indent=2, ensure_ascii=False),
            f"```",
            f"",
            f"**指标:**",
            f"",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| Sharpe Ratio | {result.metrics.get('sharpe_ratio', 0):.4f} |",
            f"| Deflated Sharpe | {result.metrics.get('deflated_sharpe', 0):.4f} |",
            f"| Total Return | {result.metrics.get('total_return', 0):.2%} |",
            f"| Annual Return | {result.metrics.get('annual_return', 0):.2%} |",
            f"| Max Drawdown | {result.metrics.get('max_drawdown', 0):.2%} |",
            f"| Win Rate | {result.metrics.get('win_rate', 0):.2%} |",
            f"| Profit Factor | {result.metrics.get('profit_factor', 0):.4f} |",
            f"| Trades | {int(result.metrics.get('trades', 0))} |",
            f"| Robustness Score | {result.metrics.get('robustness_score', 0):.4f} |",
            f"",
        ])

    # 参数敏感性分析
    report_lines.extend([
        f"## 参数敏感性分析",
        f"",
    ])

    # 提取参数名称
    if params_list:
        param_names = list(params_list[0].keys())

        for param_name in param_names:
            # 按该参数分组，计算平均指标
            param_values = {}
            for result in results:
                val = result.params.get(param_name)
                if val not in param_values:
                    param_values[val] = []
                param_values[val].append(result.metrics)

            # 计算每个值的平均DSR
            avg_metrics = {}
            for val, metrics_list_for_val in param_values.items():
                avg_dsr = np.mean([m.get("deflated_sharpe", 0) for m in metrics_list_for_val])
                avg_return = np.mean([m.get("total_return", 0) for m in metrics_list_for_val])
                avg_sharpe = np.mean([m.get("sharpe_ratio", 0) for m in metrics_list_for_val])
                avg_metrics[val] = {
                    "dsr": avg_dsr,
                    "return": avg_return,
                    "sharpe": avg_sharpe,
                }

            report_lines.extend([
                f"### {param_name}",
                f"",
                f"| 值 | Avg DSR | Avg Return | Avg Sharpe |",
                f"|-----|---------|------------|------------|",
            ])

            for val in sorted(avg_metrics.keys()):
                metrics = avg_metrics[val]
                report_lines.append(
                    f"| {val} | {metrics['dsr']:.4f} | {metrics['return']:.2%} | {metrics['sharpe']:.4f} |"
                )

            report_lines.append("")

    # 生成可视化图表
    report_lines.extend([
        f"## 可视化图表",
        f"",
    ])

    # 创建图表
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(f"实验结果分析: {experiment_id}", fontsize=16)

    # 1. DSR vs 各参数
    if param_names:
        ax = axes[0, 0]
        for param_name in param_names:
            param_vals = [r.params.get(param_name) for r in results]
            dsr_vals = [r.metrics.get("deflated_sharpe", 0) for r in results]
            ax.scatter(param_vals, dsr_vals, label=param_name, alpha=0.6)
        ax.set_xlabel("Parameter Values")
        ax.set_ylabel("Deflated Sharpe Ratio")
        ax.set_title("Parameter Sensitivity: DSR")
        ax.legend()
        ax.grid(True, alpha=0.3)

    # 2. Return vs Drawdown
    ax = axes[0, 1]
    returns = [r.metrics.get("total_return", 0) for r in results]
    drawdowns = [r.metrics.get("max_drawdown", 0) for r in results]
    dsrs = [r.metrics.get("deflated_sharpe", 0) for r in results]
    scatter = ax.scatter(drawdowns, returns, c=dsrs, cmap="RdYlGn", alpha=0.6)
    ax.set_xlabel("Max Drawdown")
    ax.set_ylabel("Total Return")
    ax.set_title("Return vs Drawdown (colored by DSR)")
    plt.colorbar(scatter, ax=ax, label="DSR")
    ax.grid(True, alpha=0.3)

    # 3. Sharpe vs DSR
    ax = axes[1, 0]
    sharpes = [r.metrics.get("sharpe_ratio", 0) for r in results]
    ax.scatter(sharpes, dsrs, alpha=0.6)
    ax.set_xlabel("Sharpe Ratio")
    ax.set_ylabel("Deflated Sharpe Ratio")
    ax.set_title("Sharpe vs Deflated Sharpe")
    ax.grid(True, alpha=0.3)

    # 4. 指标分布
    ax = axes[1, 1]
    metrics_to_plot = ["sharpe_ratio", "deflated_sharpe", "win_rate", "robustness_score"]
    data_to_plot = []
    labels = []
    for metric in metrics_to_plot:
        values = [r.metrics.get(metric, 0) for r in results]
        if values:
            data_to_plot.append(values)
            labels.append(metric.replace("_", " ").title())

    if data_to_plot:
        ax.boxplot(data_to_plot, labels=labels)
        ax.set_ylabel("Value")
        ax.set_title("Metrics Distribution")
        ax.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()

    # 保存图表
    chart_path = output_path / f"{experiment_id}_charts.png"
    plt.savefig(chart_path, dpi=150, bbox_inches='tight')
    plt.close()

    report_lines.append(f"![Charts]({chart_path.name})")
    report_lines.append("")

    # 保存报告
    report_path = output_path / f"{experiment_id}_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print(f"Report generated: {report_path}")
    print(f"Charts saved: {chart_path}")


def generate_ab_test_report(
    experiment_id: str,
    output_dir: str = "reports/experiments",
) -> None:
    """生成A/B测试报告"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 加载对比结果
    comparison_path = Path("experiments") / f"{experiment_id}_comparison.json"

    if not comparison_path.exists():
        print(f"Comparison file not found: {comparison_path}")
        return

    with open(comparison_path, "r", encoding="utf-8") as f:
        comparison_data = json.load(f)

    result_a = comparison_data["variant_a"]
    result_b = comparison_data["variant_b"]
    comparison = comparison_data["comparison"]

    # 生成Markdown报告
    report_lines = [
        f"# A/B 测试报告",
        f"",
        f"**实验ID:** {experiment_id}",
        f"**完成时间:** {comparison_data.get('completed_at', 'N/A')}",
        f"",
        f"## 变体对比",
        f"",
        f"| 指标 | Variant A | Variant B | 差异 | 变化率 | 胜者 |",
        f"|------|-----------|-----------|------|--------|------|",
    ]

    for metric, comp in comparison.items():
        val_a = comp["variant_a"]
        val_b = comp["variant_b"]
        diff = comp["diff"]
        pct_change = comp["pct_change"]
        winner = comp["winner"]

        report_lines.append(
            f"| {metric.replace('_', ' ').title()} | {val_a:.4f} | {val_b:.4f} | "
            f"{diff:+.4f} | {pct_change:+.2f}% | {winner} |"
        )

    report_lines.extend([
        f"",
        f"## Variant A 配置",
        f"",
        f"```json",
        json.dumps(result_a.get("params", {}), indent=2, ensure_ascii=False),
        f"```",
        f"",
        f"## Variant B 配置",
        f"",
        f"```json",
        json.dumps(result_b.get("params", {}), indent=2, ensure_ascii=False),
        f"```",
        f"",
    ])

    # 生成对比图表
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(f"A/B 测试对比: {experiment_id}", fontsize=16)

    metrics_to_compare = [
        ("sharpe_ratio", "Sharpe Ratio"),
        ("deflated_sharpe", "Deflated Sharpe"),
        ("total_return", "Total Return"),
        ("max_drawdown", "Max Drawdown"),
        ("win_rate", "Win Rate"),
        ("robustness_score", "Robustness Score"),
    ]

    # 1. 指标对比柱状图
    ax = axes[0, 0]
    metrics_names = []
    a_values = []
    b_values = []

    for metric, label in metrics_to_compare:
        if metric in comparison:
            metrics_names.append(label)
            a_values.append(comparison[metric]["variant_a"])
            b_values.append(comparison[metric]["variant_b"])

    x = np.arange(len(metrics_names))
    width = 0.35

    ax.bar(x - width/2, a_values, width, label='Variant A', alpha=0.8)
    ax.bar(x + width/2, b_values, width, label='Variant B', alpha=0.8)
    ax.set_ylabel('Value')
    ax.set_title('Metrics Comparison')
    ax.set_xticks(x)
    ax.set_xticklabels(metrics_names, rotation=45, ha='right')
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')

    # 2. 差异百分比
    ax = axes[0, 1]
    pct_changes = [comparison[m]["pct_change"] for m, _ in metrics_to_compare if m in comparison]
    colors = ['green' if p > 0 else 'red' for p in pct_changes]
    ax.barh(metrics_names, pct_changes, color=colors, alpha=0.6)
    ax.set_xlabel('% Change (B vs A)')
    ax.set_title('Percentage Differences')
    ax.axvline(0, color='black', linewidth=0.8)
    ax.grid(True, alpha=0.3, axis='x')

    # 3. 胜负统计
    ax = axes[1, 0]
    winners = [comparison[m]["winner"] for m, _ in metrics_to_compare if m in comparison]
    winner_counts = {
        "variant_a": winners.count("variant_a"),
        "variant_b": winners.count("variant_b"),
        "tie": winners.count("tie"),
    }
    ax.pie(
        winner_counts.values(),
        labels=winner_counts.keys(),
        autopct='%1.1f%%',
        startangle=90,
    )
    ax.set_title('Winner Distribution')

    # 4. 关键指标雷达图
    ax = axes[1, 1]
    ax.remove()
    ax = fig.add_subplot(2, 2, 4, projection='polar')

    categories = [label for _, label in metrics_to_compare[:5] if metrics_to_compare[0][0] in comparison]
    a_vals = [comparison[m]["variant_a"] for m, _ in metrics_to_compare[:5] if m in comparison]
    b_vals = [comparison[m]["variant_b"] for m, _ in metrics_to_compare[:5] if m in comparison]

    # 归一化到 0-1
    max_vals = [max(abs(a), abs(b)) for a, b in zip(a_vals, b_vals)]
    a_norm = [a / (m if m > 0 else 1) for a, m in zip(a_vals, max_vals)]
    b_norm = [b / (m if m > 0 else 1) for b, m in zip(b_vals, max_vals)]

    angles = np.linspace(0, 2 * np.pi, len(categories), endpoint=False).tolist()
    a_norm += a_norm[:1]
    b_norm += b_norm[:1]
    angles += angles[:1]

    ax.plot(angles, a_norm, 'o-', linewidth=2, label='Variant A')
    ax.fill(angles, a_norm, alpha=0.25)
    ax.plot(angles, b_norm, 'o-', linewidth=2, label='Variant B')
    ax.fill(angles, b_norm, alpha=0.25)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories)
    ax.set_ylim(0, 1)
    ax.set_title('Normalized Metrics Radar')
    ax.legend(loc='upper right')
    ax.grid(True)

    plt.tight_layout()

    # 保存图表
    chart_path = output_path / f"{experiment_id}_comparison.png"
    plt.savefig(chart_path, dpi=150, bbox_inches='tight')
    plt.close()

    report_lines.append(f"## 可视化图表")
    report_lines.append(f"")
    report_lines.append(f"![Comparison Charts]({chart_path.name})")
    report_lines.append("")

    # 保存报告
    report_path = output_path / f"{experiment_id}_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print(f"Report generated: {report_path}")
    print(f"Charts saved: {chart_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate experiment reports")
    parser.add_argument("experiment_id", help="Experiment ID")
    parser.add_argument(
        "--type",
        choices=["grid", "ab"],
        required=True,
        help="Experiment type",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/experiments",
        help="Output directory for reports",
    )

    args = parser.parse_args()

    if args.type == "grid":
        generate_grid_search_report(args.experiment_id, args.output_dir)
    elif args.type == "ab":
        generate_ab_test_report(args.experiment_id, args.output_dir)


if __name__ == "__main__":
    main()
