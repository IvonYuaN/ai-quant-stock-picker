# Docs Index

当前目录只保留**长期有效、需要持续维护**的文档；试验记录和过程文档统一下沉到 `docs/archive/`。

> 🔎 **先看 `CURRENT_STATE.md`** —— 项目现状的单一事实来源（多 agent 并行开发，用它对齐"现在是什么"）。

当前正式公网入口是 **AQSP React + FastAPI**：React/Vite 监听 `127.0.0.1:5899`，FastAPI 监听 `127.0.0.1:8900`，由 Nginx/宝塔统一代理到域名。旧 Streamlit/8501 只属于历史回滚路径，不是当前部署入口。

## Active

- `CURRENT_STATE.md`: **项目现状（单一事实来源）** —— 定位红线 / 拓扑 / 数据面 / LLM 能力 / 前端 IA / 进行中 / 过期登记
- `architecture.md`: 项目架构、边界、模块契约、PR 顺序
- `CONSTITUTION.md`: 项目宪法（最高准则）
- `agent-operating-boundaries.md`: 本地开发 / GitHub / 服务器 / 公网入口四层边界
- `two-node-handoff.md`: 两机（本地 + 服务器/runner）交接
- `checklist_before_live.md`: 半实盘/人工参考前的实盘前清单
- `daily-operation.md`: 日常操作说明
- `email-setup.md`: 邮件通知配置
- `monitoring-boundary.md`: `aqsp monitor` 运行边界（防"CI 绿灯"被误读为系统健康）
- `momentum-diagnosis.md`: 动量诊断说明
- `open_source_research.md`: 开源研究整理
- `secret-and-upload-policy.md`: 上传与密钥策略
- `server-dashboard-deployment.md`: React + FastAPI 服务器前端部署与历史回滚
- `short-term-realtime-roadmap.md`: 短线实时目标、边界、并行主线和控偏入口
- `simple-server-mode.md`: GitHub -> 云服务器自动更新运行模式
- `TROUBLESHOOTING.md`: 常见故障排查手册
- `vibe-research-migration.md`: AQSP 对外（研究前台）最小只读桥接契约
- `DASHBOARD_GUIDE.md`: 研究工作台（React 入口 + 历史 Streamlit 回滚）使用指南
- `STRATEGY_HEALTH_INTEGRATION.md`: 策略健康度监控（`aqsp.monitor.strategy_health`）集成指南
- `walkforward-2026-05.md`: walk-forward 报告落点（**亦是 `aqsp walkforward --report` 默认路径，勿删**）

## Archive

阶段性调试报告、试验记录、PR 过程文档已移动到：

- `archive/experiments/`（walk-forward 试验、数据源验证、参数诊断）
- `archive/process/`（PR 拆分、审查记录、排障过程、历史任务清单、已被取代的接手/模块指南）
- `archive/README.md`

2026-09-22 归档：`model-handoff.md`（2026-06-03 接手快照，已被 `CURRENT_STATE.md` 取代）、`FETCHER_USAGE.md`（`MultiSourceFetcher` 非当前数据面）。

这些归档文件保留追溯价值，但不再作为当前运行文档入口。

---

_本次整理（2026-09-22）：新增 `CURRENT_STATE.md`；归档根目录 4 份历史任务清单与 `BEGINNER_DASHBOARD_INTEGRATION.md`；判定 Pending Review 文档去留并归档 `model-handoff.md` / `FETCHER_USAGE.md`；修复断链引用（详见 `CURRENT_STATE.md` §8）。_
