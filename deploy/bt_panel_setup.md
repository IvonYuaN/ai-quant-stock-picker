# 宝塔面板配置指南

AQSP 在服务器上的生产定时统一放在 **宝塔面板 -> 计划任务**。不要再用本地 `launchd` 或手写 `daily_pipeline.sh` 作为生产入口。

统一命令：

```bash
/bin/bash /opt/aqsp/scripts/bt_task.sh <动作>
```

可用动作：`intraday`、`midday`、`news`、`daily`、`coldstart`、`monitor`、`status`。

## 前置条件

- 代码目录：`/opt/aqsp`
- 虚拟环境：`/opt/aqsp/.venv/bin/python3`
- 私有配置：`/opt/aqsp/.env`
- Dashboard：`aqsp-dashboard.service` 监听 `127.0.0.1:8501`
- 公网入口：Nginx/宝塔反代到 `127.0.0.1:8501`

## 计划任务

在宝塔里新增 Shell 脚本任务。建议配置 **6 条自动任务 + 1 条手动自检**：

| 任务名 | 周期/时间 | 脚本内容 |
|---|---|---|
| `AQSP-盘中刷新` | 工作日 `09:40-11:30`、`13:10-14:55` 每 10 分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh intraday` |
| `AQSP-午盘分析` | 工作日 `12:05` | `/bin/bash /opt/aqsp/scripts/bt_task.sh midday` |
| `AQSP-消息面雷达` | 工作日 `08:45` | `/bin/bash /opt/aqsp/scripts/bt_task.sh news` |
| `AQSP-周末消息雷达` | 周六、周日 `10:00` | `/bin/bash /opt/aqsp/scripts/bt_task.sh news` |
| `AQSP-收盘主链路` | 工作日 `18:00` | `/bin/bash /opt/aqsp/scripts/bt_task.sh daily` |
| `AQSP-冷启动补样本` | 工作日 `19:40` | `/bin/bash /opt/aqsp/scripts/bt_task.sh coldstart` |
| `AQSP-服务器监控` | 工作日每 15 分钟 | `/bin/bash /opt/aqsp/scripts/bt_task.sh monitor` |

`status` 不建议定时跑，需要排查时在宝塔手动执行：

```bash
/bin/bash /opt/aqsp/scripts/bt_task.sh status
```

## 时间逻辑

- `intraday`：盘中刷新，写盘中独立产物，不污染正式 ledger。
- `midday`：午盘分析，标题为 `午盘分析-YYYY-MM-DD`。
- `news`：消息面雷达，标题为 `消息面雷达-YYYY-MM-DD`，盘前和周末最有价值。
- `daily`：收盘完整主链路，生成正式复盘、通知和看板。
- `coldstart`：补冷启动样本，建议晚于 `daily` 至少 90 分钟。
- `monitor`：运行态检查，默认只推关键异常。

## 锁提示不是失败

如果日志出现：

```text
主链路仍在运行，本次任务正常跳过；这是互斥保护，不是失败
```

意思是上一轮还没跑完，新任务被保护性跳过。正常处理方式是错开时间，尤其不要把 `coldstart` 放在 `daily` 旁边。

## 通知配置

`/opt/aqsp/.env` 至少需要：

```bash
AQSP_NOTIFY=true
AQSP_NOTIFY_MODE=summary
SERVERCHAN_SENDKEY=你的Server酱SendKey
```

监控告警如需推送，额外开启：

```bash
AQSP_MONITOR_NOTIFY=true
```

消息面雷达模型复核：

```bash
AQSP_NEWS_ENABLE_LLM_REVIEW=true
AQSP_NEWS_MAX_LLM_REVIEW_EVENTS=3
AQSP_NEWS_TASK_TIMEOUT_SECONDS=45
```

模型只做复核和摘要，不改写主链路评分、排序或 Portfolio Manager 裁决。

## 手动验证

```bash
cd /opt/aqsp
/bin/bash scripts/bt_task.sh status
/bin/bash scripts/bt_task.sh news
/bin/bash scripts/bt_task.sh monitor
/bin/bash scripts/bt_task.sh midday
.venv/bin/python3 scripts/check_scheduler.py
```

收盘主链路较慢，手动跑前先看锁：

```bash
ls -la /opt/aqsp/.locks
/bin/bash scripts/bt_task.sh daily
```

## 看日志

```bash
tail -120 /opt/aqsp/logs/bt/bt-intraday-$(date +%Y-%m-%d).log
tail -120 /opt/aqsp/logs/bt/bt-midday-$(date +%Y-%m-%d).log
tail -120 /opt/aqsp/logs/bt/bt-news-$(date +%Y-%m-%d).log
tail -120 /opt/aqsp/logs/bt/bt-daily-$(date +%Y-%m-%d).log
tail -120 /opt/aqsp/logs/bt/bt-coldstart-$(date +%Y-%m-%d).log
tail -120 /opt/aqsp/logs/bt/bt-monitor-$(date +%Y-%m-%d).log
```

## 不要做

- 不要在服务器直接开发受 Git 管理代码。
- 不要把 `/opt/aqsp/.env`、`data/`、`logs/`、`reports/`、`dist/` 推回 GitHub。
- 不要用 `daily_pipeline.sh` 替代 `bt_task.sh daily` 配到宝塔。
- 不要把 Streamlit 端口直接暴露公网，只允许 Nginx/宝塔反代到 `127.0.0.1:8501`。
