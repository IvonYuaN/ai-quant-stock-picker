#!/usr/bin/env python3
"""测试多Agent辩论系统"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402

from aqsp.briefing.debate import AShareDebateCoordinator, format_debate_result  # noqa: E402
from aqsp.core.time import today_shanghai  # noqa: E402
from aqsp.core.types import PickResult  # noqa: E402


def create_mock_pick(symbol: str, name: str, score: float, rating: str) -> PickResult:
    """创建模拟标的"""
    strategies = ["momentum", "volume_breakout"] if score > 50 else ["value"]
    reasons = []
    if score > 60:
        reasons.append("价格突破关键阻力位")
        reasons.append("成交量明显放大")
    elif score > 40:
        reasons.append("价格位于均线之上")
        reasons.append("技术指标中性偏多")
    else:
        reasons.append("估值相对合理")

    risks = []
    if score < 50:
        risks.append("近期市场波动较大")
        risks.append("板块整体表现不佳")
    else:
        risks.append("需要警惕回调风险")

    from aqsp.core.time import now_shanghai

    today_date = now_shanghai().strftime("%Y-%m-%d")

    return PickResult(
        symbol=symbol,
        name=name,
        date=today_date,
        close=10.0 + (score / 10),
        score=score,
        rating=rating,
        strategies=tuple(strategies),
        reasons=tuple(reasons),
        risks=tuple(risks),
        position="10%-30%",
        ideal_buy=10.0 + (score / 10),
        stop_loss=9.5,
        take_profit=12.0,
        entry_type="next_open",
    )


def create_mock_ohlcv(days: int = 30) -> pd.DataFrame:
    """创建模拟OHLCV数据"""
    dates = pd.date_range(end=today_shanghai(), periods=days)
    base_price = 10.0

    rng = np.random.default_rng(42)
    returns = rng.normal(0, 0.02, days)
    prices = base_price * (1 + returns).cumprod()

    data = {
        "date": dates,
        "open": prices * (1 - rng.uniform(0, 0.01, days)),
        "high": prices * (1 + rng.uniform(0, 0.02, days)),
        "low": prices * (1 - rng.uniform(0, 0.02, days)),
        "close": prices,
        "volume": rng.integers(1000000, 10000000, days),
        "amount": rng.integers(10000000, 100000000, days),
    }

    return pd.DataFrame(data)


def main() -> int:
    print("=" * 70)
    print("测试多Agent辩论系统")
    print("=" * 70)
    print()

    # 创建测试标的
    print("1. 创建测试标的...")
    picks = [
        create_mock_pick("000001", "平安银行", 72.5, "strong_buy"),
        create_mock_pick("000002", "万科A", 55.0, "buy"),
        create_mock_pick("000003", "PT金田", 35.0, "avoid"),
    ]
    print("   已创建 3 只测试标的")
    print()

    # 生成模拟数据
    print("2. 生成模拟OHLCV数据...")
    df = create_mock_ohlcv()
    print(f"   已生成 {len(df)} 天数据")
    print()

    # 运行辩论系统
    print("3. 运行A股多Agent辩论系统...")
    coordinator = AShareDebateCoordinator(enable_llm=False, max_rounds=2)

    for i, pick in enumerate(picks):
        print(f"\n   辩论中: {pick.symbol} {pick.name} (评分: {pick.score})")

        result = coordinator.run_debate(pick, df)

        print(f"   最终共识: {result.final_consensus}")
        print(f"   建议调整: {result.recommended_adjustment}")
        print(f"   辩论轮次: {len(result.rounds)}")

        print("\n   完整辩论结果:")
        print("   " + "=" * 60)
        result_text = format_debate_result(result)
        for line in result_text.split("\n"):
            print(f"   {line}")
        print()

    print("=" * 70)
    print("测试完成！")
    print("\n多Agent辩论系统已就绪，包含以下角色：")
    print("  🐂 Bull (多头): 强调正面因素")
    print("  🐻 Bear (空头): 强调风险和负面因素")
    print("  ⚖️ Neutral (中性): 保持平衡观点")
    print("  🛡️ Risk Control (风控): 专注于风险控制")
    print("  📊 Technical (技术分析): 专注于技术面")
    print("  📈 Fundamental (基本面分析): 专注于基本面")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
