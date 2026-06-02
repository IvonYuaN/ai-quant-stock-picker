"""Agent性能展示Dashboard生成器"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from aqsp.briefing.agent_roles import agent_role_emoji, agent_role_label
from aqsp.briefing.debate_tracker import DebatePerformanceTracker
from aqsp.core.time import now_shanghai


def _load_debates(debate_file: Path) -> list[dict]:
    """加载辩论历史"""
    debates: list[dict] = []
    if not debate_file.exists():
        return debates
    for line in debate_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            debates.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return debates


def render_agent_dashboard(
    performance_path: str = "data/debate_performance.jsonl",
    debate_path: str = "data/debate_results.jsonl",
    output_path: str = "dist/dashboard/agents.html",
) -> None:
    """生成Agent性能展示页面"""
    tracker = DebatePerformanceTracker(performance_path)
    leaderboard = tracker.get_leaderboard()
    debates = _load_debates(Path(debate_path))

    # 统计辩论历史
    total_debates = len(debates)
    recent_debates = 0
    cutoff = (now_shanghai() - timedelta(days=30)).date().isoformat()
    for d in debates:
        if d.get("related_signal_date", "") >= cutoff:
            recent_debates += 1

    leaderboard_html = ""
    for idx, entry in enumerate(leaderboard, 1):
        accuracy_pct = entry["accuracy"] * 100
        weight = entry["weight"]
        predictions = entry["total_predictions"]
        role_name = agent_role_label(entry["role"], language="zh-CN")
        role_emoji = agent_role_emoji(entry["role"])

        # 权重颜色
        if weight > 0.15:
            weight_class = "weight-high"
        elif weight > 0:
            weight_class = "weight-normal"
        else:
            weight_class = "weight-low"

        # 准确率颜色
        if accuracy_pct >= 60:
            acc_class = "acc-high"
        elif accuracy_pct >= 50:
            acc_class = "acc-medium"
        else:
            acc_class = "acc-low"

        leaderboard_html += f"""
        <div class="leader-card">
            <div class="leader-rank">#{idx}</div>
            <div class="leader-info">
                <h4>{role_name}</h4>
                <p class="role-id">{role_emoji} {entry["role"]}</p>
            </div>
            <div class="leader-metrics">
                <div class="metric">
                    <span class="label">准确率</span>
                    <span class="value {acc_class}">{accuracy_pct:.1f}%</span>
                </div>
                <div class="metric">
                    <span class="label">预测次数</span>
                    <span class="value">{predictions}</span>
                </div>
                <div class="metric">
                    <span class="label">当前权重</span>
                    <span class="value {weight_class}">{weight:.2f}</span>
                </div>
            </div>
        </div>
        """

    # 最近辩论记录
    recent_debates_html = ""
    for d in reversed(debates[-10:]):
        symbol = d.get("symbol", "")
        name = d.get("name", "")
        original = d.get("original_score", 0)
        adjusted = d.get("adjusted_score", original)
        adj = d.get("recommended_adjustment", "keep")
        adj_text = {"raise": "建议上调", "lower": "建议下调", "keep": "维持原评级"}.get(
            adj, adj
        )
        adj_class = {"raise": "bull", "lower": "bear", "keep": "neutral"}.get(
            adj, "neutral"
        )
        date = d.get("related_signal_date", "")

        recent_debates_html += f"""
        <div class="recent-debate">
            <div class="debate-header">
                <span class="symbol">{symbol}</span>
                <span class="name">{name}</span>
                <span class="date">{date}</span>
            </div>
            <div class="debate-score">
                <span>原始 {original:.1f}</span>
                <span class="arrow">→</span>
                <span>调整后 {adjusted:.1f}</span>
            </div>
            <span class="adj-badge {adj_class}">{adj_text}</span>
        </div>
        """

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent性能分析</title>
  <style>
    :root {{
      --ink: #162018;
      --muted: #687568;
      --paper: #f7f4ec;
      --card: rgba(255, 255, 252, .90);
      --green: #1f7a4d;
      --amber: #b86b1d;
      --line: rgba(22, 32, 24, .14);
      --red: #b44836;
      --shadow: rgba(28, 45, 31, .12);
      --bg-gradient: linear-gradient(135deg, #fbf6ea 0%, #e2ead9 48%, #f5ead4 100%);
    }}
    
    @media (prefers-color-scheme: dark) {{
      :root {{
        --ink: #e8e6e1;
        --muted: #9a978f;
        --paper: #1a1a18;
        --card: rgba(30, 30, 28, .95);
        --green: #4caf7d;
        --amber: #d4873a;
        --line: rgba(232, 230, 225, .12);
        --red: #d46b5f;
        --shadow: rgba(0, 0, 0, .3);
        --bg-gradient: linear-gradient(135deg, #1a1a18 0%, #252523 48%, #1f1f1d 100%);
      }}
    }}
    
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      background: var(--bg-gradient);
      padding: 24px;
      line-height: 1.6;
    }}
    
    .container {{
      max-width: 1000px;
      margin: 0 auto;
    }}
    
    header {{
      margin-bottom: 32px;
    }}
    
    h1 {{
      margin: 0 0 8px 0;
      font-size: 32px;
      font-weight: 700;
    }}
    
    .subtitle {{
      color: var(--muted);
      font-size: 14px;
      margin: 0;
    }}
    
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 16px;
      margin-bottom: 28px;
    }}
    
    .stat-card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 20px;
      box-shadow: 0 2px 12px var(--shadow);
    }}
    
    .stat-label {{
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .5px;
    }}
    
    .stat-value {{
      font-size: 28px;
      font-weight: 700;
      margin-top: 8px;
    }}
    
    .section {{
      margin-bottom: 32px;
    }}
    
    .section h2 {{
      font-size: 20px;
      margin: 0 0 16px 0;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--line);
    }}
    
    .leader-card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 20px;
      margin-bottom: 12px;
      display: flex;
      gap: 20px;
      align-items: center;
      box-shadow: 0 2px 12px var(--shadow);
    }}
    
    .leader-rank {{
      font-size: 20px;
      font-weight: 700;
      color: var(--muted);
      min-width: 40px;
    }}
    
    .leader-info {{
      flex: 1;
    }}
    
    .leader-info h4 {{
      margin: 0;
      font-size: 16px;
    }}
    
    .role-id {{
      font-size: 12px;
      color: var(--muted);
      margin: 4px 0 0 0;
    }}
    
    .leader-metrics {{
      display: flex;
      gap: 32px;
    }}
    
    .metric {{
      text-align: center;
    }}
    
    .metric .label {{
      display: block;
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
    }}
    
    .metric .value {{
      display: block;
      font-size: 18px;
      font-weight: 600;
      margin-top: 4px;
    }}
    
    .acc-high {{ color: var(--green); }}
    .acc-medium {{ color: var(--amber); }}
    .acc-low {{ color: var(--red); }}
    
    .weight-high {{ color: var(--green); }}
    .weight-normal {{ color: var(--ink); }}
    .weight-low {{ color: var(--red); }}
    
    .recent-debate {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      gap: 16px;
    }}
    
    .debate-header {{
      flex: 1;
    }}
    
    .symbol {{
      font-weight: 700;
      margin-right: 8px;
    }}
    
    .name {{
      color: var(--muted);
      margin-right: 12px;
    }}
    
    .date {{
      font-size: 12px;
      color: var(--muted);
    }}
    
    .debate-score {{
      font-size: 14px;
    }}
    
    .arrow {{
      margin: 0 8px;
      color: var(--muted);
    }}
    
    .adj-badge {{
      padding: 6px 12px;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 600;
    }}
    
    .adj-badge.bull {{ background: rgba(31,122,77,.15); color: var(--green); }}
    .adj-badge.bear {{ background: rgba(180,72,54,.15); color: var(--red); }}
    .adj-badge.neutral {{ background: rgba(104,117,104,.12); color: var(--muted); }}
    
    .info-box {{
      background: rgba(104,117,104,.10);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px 18px;
      margin-bottom: 20px;
    }}
    
    .info-box p {{
      margin: 0;
      font-size: 14px;
      color: var(--muted);
    }}
    
    .back-link {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--green);
      text-decoration: none;
      font-weight: 600;
      margin-bottom: 16px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <a href="index.html" class="back-link">← 返回主Dashboard</a>
      <h1>🤖 Agent性能分析</h1>
      <p class="subtitle">分析辩论Agent的历史表现与权重变化</p>
    </header>
    
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">活跃Agent数量</div>
        <div class="stat-value">{len(leaderboard)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">30天辩论数</div>
        <div class="stat-value">{recent_debates}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">总辩论历史</div>
        <div class="stat-value">{total_debates}</div>
      </div>
    </div>
    
    <div class="info-box">
      <p><strong>说明：</strong> Agent表现统计仅包含最近30天数据。准确率越高，该Agent在辩论中的权重越大。权重范围：-0.10 ~ 0.25。</p>
    </div>
    
    <div class="section">
      <h2>🏆 Agent表现排行榜</h2>
      {leaderboard_html if leaderboard else "<p style='color: var(--muted);'>暂无数据。运行 'scripts/feedback_debate.py' 来收集反馈。</p>"}
    </div>
    
    <div class="section">
      <h2>📋 最近辩论记录</h2>
      {recent_debates_html if recent_debates_html else "<p style='color: var(--muted);'>暂无辩论记录。运行带 --enable-debate 的选股命令生成辩论。</p>"}
    </div>
    
  </div>
</body>
</html>
"""

    # 保存文件
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"✅ Agent性能Dashboard已保存至 {output_path}")


if __name__ == "__main__":
    render_agent_dashboard()
