# 监控运行边界（Monitoring Boundary）

> 本文档声明 `aqsp monitor` 的运行边界，防止“CI 绿灯”被误读为“系统健康”。
> 相关代码：`src/aqsp/monitor/checker.py`（`MonitorResult.skipped`）、`src/aqsp/cli.py`（`run_monitor`）。
> 相关配置：`config/monitors.yaml`、` .github/workflows/monitor.yml`。

## 1. 两套运行环境，职责不同

| 环境 | 命令 | data/ 是否持久 | 职责 |
| --- | --- | --- | --- |
| GitHub Actions | `aqsp monitor --allow-skipped` | 否（每次全新 runner） | **冒烟测试**：验证 monitor 命令能跑通、配置合法、不崩溃 |
| 生产服务器 cron | `aqsp monitor --notify` | 是（持久磁盘） | **权威监控**：真正执行业务正确性检查并推送告警 |

二者共用同一份 `config/monitors.yaml`。同一份配置在两种环境下含义不同，是因为
“数据是否存在”这一前提不同，而非配置本身分裂。

## 2. `skipped` ≠ 健康（核心原则）

数据依赖型检查（`data_freshness` / `screening_liveness` / `empty_picks` /
`walkforward_runtime`）在目标文件缺失时，**不会**误报，而是标记 `skipped=True`：

- CI 无持久 data/ → 这些检查被跳过，属预期现象。
- 生产有 data/ → 这些检查真正执行，缺失才会按 `required` 语义处理。

`skipped` 只表示“本次因子据不可用而无法评估”，**绝不等于该项健康**。
因此 `run_monitor` 会显式打印被跳过的数量；当**全部**检查都被跳过（零监控）时，
未加 `--allow-skipped` 会以退出码 2 失败，避免“零监控 = 健康”的假绿。

## 3. `required` 语义

| `required` | 目标文件缺失时的行为 |
| --- | --- |
| `false`（默认） | 标记 `skipped`，不误报（CI/无数据环境预期） |
| `true` | 按真实故障处理（`triggered=True`） |

当前 `config/monitors.yaml` 各项均为 `required: false`，以保证无数据 CI 稳定。
若生产环境希望把“缓存缺失”当作严重故障，可在服务器侧使用覆盖配置并将对应项设为
`required: true`（例如 `stale_data`）。

## 4. 为什么 GitHub Actions 不加 `--notify`

CI 中 `data/` 缺失 → 所有数据依赖型检查被跳过 → 无 `triggered` → 即便加 `--notify`
也不会推送任何内容。因此 CI 加 `--notify` 既无收益又有误推风险（一旦全局配置了
通知渠道）。真实告警推送只在生产服务器 cron（`aqsp monitor --notify`）进行。

## 5. 验收口径

- CI 运行：`skipped` 数量 > 0，打印“N/M 项检查因数据不可用被跳过”，退出码 0（已声明冒烟）。
- 生产运行：检查真正执行；筛选停更 / 0 标的 / 数据滞后 / walk-forward 过期等
  业务故障按 `severity` 触发并推送。
- 反例（必须避免）：CI 上报“✅ 所有监控项正常”却无任何检查真正执行（零监控假绿）。
