# T+1 工作台最终验收矩阵

该矩阵只记录当前可核验事实，不把隔离夹具或本地构建当成公网验收。

| 要求 | 当前证据 | 状态 |
| --- | --- | --- |
| 首页以持仓风险提醒为中心 | `AlertInbox`、持仓/自选合页、T+1 分组及处置测试 | 本地已证实 |
| 全局“我操作了”回录并自动关联规则 | OperationRecorder、买入/部分卖出/T+1/幂等重试测试 | 本地已证实 |
| 持仓与自选合并 | `HoldingsWatchlist` 与 watchlist removal 租户隔离测试 | 本地已证实 |
| 候选按交易日冻结且持仓/自选优先 | `candidate_freeze`、`home_snapshot`、候选排序测试 | 本地已证实；公网快照待验 |
| 今日/持仓/大盘/消息四项导航 | 前端路由契约、旧路由兼容测试 | 本地已证实；公网桌面/手机待验 |
| 提醒处置、频率和通知持久化 | `live_risk_delivery`、处置、忽略降频、失败恢复测试 | 本地已证实；真实送达待验 |
| 线上运行最新代码 | 本地 `44504a07`、前端制品 SHA 已记录 | 未完成：公网仍为 `03f30986` |
| 至少两只真实持仓并触发提醒 | 线上真实租户目前只有 `002129 TCL中环` 1 只 | 未完成 |
| 真实外发成功回执 | 线上 `live-risk-delivery.json`/`notify-state-live-risk.json` 无回执 | 未完成 |
| 首周噪音率 | 没有完整真实运行周数据 | 未完成 |

## 发布前门禁

- `npm run build`：2500 modules transformed。
- 前端契约：34 个文件、784 项断言通过。
- 实盘工作台、通知和 monitor 定向 Python：111 项通过。
- immutable 部署脚本回归：19 项通过。
- `ruff check .`、`scripts/preflight_upload.py`、`git diff --check` 通过。
- 发布时将预置 `frontend/dist`，传入 `AQSP_FRONTEND_DIST_SOURCE` 和
  `AQSP_FRONTEND_DIST_SHA256`，使用 `--skip-frontend-build`；ECS 不执行 npm/vite。

## 2026-10-24 本地复验

- `uv run --project . pytest -q`：5852 passed、4 skipped、16 deselected。
- 前端契约 34 个文件、784 项断言通过；生产构建成功（2500 modules transformed）。
- 隔离 Chromium + 临时 FastAPI/租户再次通过：两只持仓录入、股票名、持仓风险提醒、持有/减仓/做 T 方案、频率降噪、部分卖出、失败重试、四项导航、旧路由、桌面 1440px 与手机 390px。
- 验收夹具使用上一交易日收盘行情；盘中旧报价在周末被正确判为过期，避免用不可核验数据制造提醒。

## 当前阻塞

本机 GitHub keyring token 无效，SSH agent 也没有 identity；远端无法 fetch 本地
`44504a07`。在 commit 可由服务器从 GitHub 获取前，不执行线上切换、重启或绕过
immutable release 的上传。
