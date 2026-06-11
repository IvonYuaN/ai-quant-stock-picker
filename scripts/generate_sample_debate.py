#!/usr/bin/env python3
"""生成示例辩论数据用于测试 dashboard。"""

from __future__ import annotations

import json
import random
from pathlib import Path

from aqsp.core.time import now_shanghai


def generate_sample_debate_data(output_path: Path | None = None) -> Path:
    """生成模拟的辩论结果数据。"""
    agent_roles = [
        "技术多头",
        "基本面空头",
        "风险控制",
        "板块轮动",
        "政策分析",
        "融资融券",
        "北向资金",
        "散户情绪",
    ]
    agent_role_keys = [
        "bull",
        "bear",
        "risk_control",
        "sector_leader",
        "policy_sensitive",
        "margin_trading",
        "northbound",
        "retail_mood",
    ]

    symbols = [
        {"symbol": "600519", "name": "贵州茅台", "score": 7.8, "rating": "A+"},
        {"symbol": "000858", "name": "五粮液", "score": 6.5, "rating": "A"},
        {"symbol": "600036", "name": "招商银行", "score": 7.2, "rating": "A"},
    ]

    debate_file = output_path or Path("data/debate_results.jsonl")
    debate_file.parent.mkdir(parents=True, exist_ok=True)

    results = []
    for symbol_info in symbols:
        current_ts = now_shanghai()
        symbol = symbol_info["symbol"]
        name = symbol_info["name"]

        rounds = []
        for round_num in range(1, 3):
            opinions = []
            for i, (role, role_key) in enumerate(zip(agent_roles, agent_role_keys)):
                stance = random.choice(["bullish", "bearish", "neutral"])
                confidence = round(random.uniform(0.5, 0.95), 2)
                arguments = [
                    f"{role}认为当前价格位置{random.choice(['合理', '偏高', '偏低'])}",
                    f"关注{random.choice(['成交量放大', 'MACD金叉', '布林带上轨'])}信号",
                    f"建议{random.choice(['继续跟踪', '纸面观察', '暂缓跟踪'])}",
                ]
                counterarguments = [
                    f"需要注意{random.choice(['大盘回调风险', '技术背离', '政策不确定性'])}"
                ]
                risk_factors = [
                    f"{random.choice(['业绩不及预期', '监管收紧', '流动性枯竭'])}风险"
                ]
                opportunity_factors = [
                    f"关注{random.choice(['行业景气度提升', '政策利好', '外资持续流入'])}"
                ]

                opinions.append(
                    {
                        "agent_id": f"{role_key}_{random.randint(1000, 9999)}",
                        "role": role_key,
                        "stance": stance,
                        "confidence": confidence,
                        "arguments": arguments,
                        "counterarguments": counterarguments,
                        "risk_factors": risk_factors,
                        "opportunity_factors": opportunity_factors,
                        "final_position": stance,
                    }
                )

            rounds.append(
                {
                    "round_num": round_num,
                    "summary": f"第{round_num}轮辩论完成，各Agent从不同角度梳理了{name}的研究线索",
                    "opinions": opinions,
                    "cross_opinions": {},
                }
            )

        final_vote = {}
        bull_count = 0
        bear_count = 0
        neutral_count = 0
        for i, role_key in enumerate(agent_role_keys):
            vote = random.choice(["bullish", "bearish", "neutral"])
            final_vote[role_key] = vote
            if vote == "bullish":
                bull_count += 1
            elif vote == "bearish":
                bear_count += 1
            else:
                neutral_count += 1

        if bull_count > bear_count + 1:
            final_consensus = (
                f"多Agent辩论后，{bull_count}个看多，{bear_count}个看空，"
                f"{neutral_count}个中性，整体偏多，列入重点观察"
            )
        elif bear_count > bull_count + 1:
            final_consensus = (
                f"多Agent辩论后，{bear_count}个看空，{bull_count}个看多，"
                f"{neutral_count}个中性，整体偏谨慎，暂列观察"
            )
        else:
            final_consensus = (
                f"多Agent辩论后，{bull_count}个看多，{bear_count}个看空，"
                f"{neutral_count}个中性，观点分化，维持研究观察"
            )

        adjustment_weight = round(random.uniform(-0.2, 0.2), 3)
        adjusted_score = round(symbol_info["score"] * (1 + adjustment_weight), 1)
        disagreement_score = round(random.uniform(0.2, 0.8), 2)

        if adjustment_weight > 0.1:
            recommended = "raise"
        elif adjustment_weight < -0.1:
            recommended = "lower"
        else:
            recommended = "keep"

        adjustment_label = {
            "raise": "辩论倾向上调",
            "lower": "辩论倾向下调",
            "keep": "辩论倾向维持",
        }[recommended]
        results.append(
            {
                "debate_id": f"debate_{symbol}_{current_ts.strftime('%Y%m%d%H%M%S')}",
                "symbol": symbol,
                "name": name,
                "original_score": symbol_info["score"],
                "rating": symbol_info["rating"],
                "debate_date": current_ts.date().isoformat(),
                "created_at": current_ts.isoformat(timespec="seconds"),
                "disagreement_score": disagreement_score,
                "adjustment_weight": adjustment_weight,
                "adjusted_score": adjusted_score,
                "recommended_adjustment": recommended,
                "thresholds_version": "v1.0.0",
                "regime": "震荡偏多",
                "data_source": "multi",
                "related_signal_date": current_ts.date().isoformat(),
                "rounds": rounds,
                "final_consensus": final_consensus,
                "final_vote": final_vote,
                "adjustment_reason": (
                    f"多头{bull_count}票 vs 空头{bear_count}票，"
                    f"{adjustment_label}，附件参考分 {adjusted_score}"
                ),
                "risk_warnings": [
                    "需关注大盘系统性风险",
                    "业绩波动风险",
                    "政策调整风险",
                ],
                "opportunity_highlights": [
                    "行业景气度提升",
                    "估值具有吸引力",
                    "外资持续流入",
                ],
            }
        )

    with open(debate_file, "w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    print(f"✅ 已生成 {len(results)} 条示例辩论数据，保存至 {debate_file}")
    return debate_file


if __name__ == "__main__":
    generate_sample_debate_data()
