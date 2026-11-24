# 历史研究资料归档（2026-11-24 收敛时入库）

本目录收录两块此前只存在于本机收敛备份、从未入库的研究资料。入库目的：**消除唯一副本风险**，让判读结论和原始数据可复现、可追溯。

## ⚠️ 使用边界（先读）

- 这些是**历史研究快照**，结论多为**负面 / 证伪**，不是可上线的策略依据。
- 数据文件（CSV）是当时时点的采集快照，**有截止日期、不更新**，不得当作实时数据或 PIT 数据源使用。
- 脚本仅用于复现当时的分析过程；如需在当前代码库复用，须先按现行 PIT / 复权 / 新鲜度契约复核。
- 任何结论要进生产打分或 `config/thresholds.yaml`，须另走 walk-forward 验证与 AGENTS §5/§6.1 门禁。**本归档不等于验证通过**。

## factor_sweep/ — 全因子 IC 扫描（2026-11-12）

- `verdict.md`：判读结论。37 个日线因子 × 3 域 × 2 horizon = 222 次检验，**负面结论**：全市场看似 28/37 显著，实为「反转 + 低波」共线投影，进作业面塌成 7 个弱候选，无一可直接用；**日线因子池基本穷尽，靠补因子填 alpha 赤字这条路证伪**。
- `factor_sweep.py`：扫描脚本（仅依赖 numpy/pandas/标准库）。
- `result_v1.csv` / `result_v2.csv`：两次扫描原始输出。
- `run_v2.log`：v2 运行日志。

## theme_factor_probe/ — 题材因子数据快照（2026-11-02 ~ 11-10）

- `theme_factor_probe.py`：探针脚本（仅依赖 numpy/pandas/标准库）。
- 13 份 CSV：公告、概念板块、龙虎榜、解禁、持仓户数、业绩预告、分红、停复牌、财联社快讯、开盘啦题材轮动/龙头、题材历史等采集快照。

## 来源

2026-11-24 四条并行开发线收敛时，经 `git grep` 逐项确认 main 无对应文档/数据（`git grep "222 次检验" origin/main` = 0、`git grep theme_factor_probe origin/main` = 0），由 issue #614 登记后入库。原始文件来自 `.workbuddy-ai/converge-backup-20261124/untracked-research.tar.gz`。入库时做了两处清理：`theme_factor_probe.py` 修了 ruff 的 E401/E712，并把硬编码的本机绝对路径改为 `AQSP_DB` 环境变量（默认 runner 路径）+ `Path(__file__)`，以符合 main 既有 research 脚本惯例；数据 CSV 与文档逐字节一致。
