"""辩论结果反馈脚本 - 根据实际收益更新Agent表现"""

from __future__ import annotations

import json
from pathlib import Path

from aqsp.briefing.debate_tracker import DebatePerformanceTracker
from aqsp.ledger.base import read_ledger


def _load_debates(debate_file: Path) -> dict[str, dict]:
    """加载辩论历史，按symbol_date索引"""
    debates_by_key: dict[str, dict] = {}
    if not debate_file.exists():
        return debates_by_key
    for line in debate_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            symbol = data.get("symbol", "")
            date = data.get("related_signal_date", "")
            if symbol and date:
                key = f"{symbol}_{date}"
                debates_by_key[key] = data
        except json.JSONDecodeError:
            continue
    return debates_by_key


def feedback_debate_results(
    ledger_path: str = "data/ledger.jsonl",
    debate_path: str = "data/debate_results.jsonl",
    performance_path: str = "data/debate_performance.jsonl",
):
    """
    根据实际收益反馈辩论结果，更新Agent表现
    
    逻辑：
    1. 读取ledger中的validated记录
    2. 查找对应的辩论结果
    3. 对比Agent观点与实际收益，更新Agent准确性
    """
    print("🔄 开始反馈辩论结果...")
    
    debates = _load_debates(Path(debate_path))
    rows = read_ledger(ledger_path)
    tracker = DebatePerformanceTracker(performance_path)
    
    updated_count = 0
    skipped_no_debate = 0
    
    for row in rows:
        if row.get("status") != "validated":
            continue
        
        symbol = str(row.get("symbol", ""))
        signal_date = str(row.get("signal_date", ""))
        key = f"{symbol}_{signal_date}"
        
        debate = debates.get(key)
        if not debate:
            skipped_no_debate += 1
            continue
        
        # 获取实际收益
        actual_return = float(row.get("return_pct", 0))
        was_positive = actual_return > 0
        
        # 确定"正确"观点（根据实际收益）
        # 如果涨了，看多看空为对；如果跌了，看空为对
        target_stance = "bullish" if was_positive else "bearish"
        
        for round_data in debate.get("rounds", []):
            for opinion in round_data.get("opinions", []):
                role = opinion.get("role", "")
                agent_id = opinion.get("agent_id", f"{role}_{signal_date}")
                stance = opinion.get("stance", "neutral")
                
                # 判断该Agent是否正确
                was_correct = stance == target_stance
                
                # 记录预测
                tracker.record_prediction(
                    role=role,
                    agent_id=agent_id,
                    predicted_stance=stance,
                    was_correct=was_correct
                )
        
        print(f"   ✅ {symbol} ({signal_date}): 实际 {actual_return:+.1f}% → {'上涨' if was_positive else '下跌'}")
        updated_count += 1
    
    print("\n📊 反馈完成:")
    print(f"   更新了 {updated_count} 个辩论的反馈")
    print(f"   跳过了 {skipped_no_debate} 个无辩论记录的验证")
    
    leaderboard = tracker.get_leaderboard()
    if leaderboard:
        print("\n🏆 Agent表现排行榜:")
        for entry in leaderboard:
            print(f"   {entry['role_name']}: 准确率 {entry['accuracy']*100:.1f}% ({entry['total_predictions']}次), 权重 {entry['weight']:.2f}")


if __name__ == "__main__":
    feedback_debate_results()
