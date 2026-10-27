# 每日运行手册（Phase 1 半实盘）

默认模式：**本地优先**。GitHub Actions 不是必需项。

## 自动跑（本地 legacy）

生产服务器统一使用：

```bash
bash scripts/bt_task.sh daily
```

`daily_run.sh` 只保留给本地 smoke run，默认 fail-closed，必须显式设置 `AQSP_ALLOW_LEGACY_ENTRY=1`。

### 盘外空跑与主锁（#478，2026-10-27）

`bt_task.sh intraday` 在交易日检查、午盘桥接之后，先检查北京时间
09:35–11:30 / 13:05–14:57（含边界）；盘外只记录跳过，不进入
`server_sync_and_run.sh`，因此不会抢占 22:30 `variant-refresh` 等任务的主锁。
面板入口与 `intraday_refresh.sh` 共用 `scripts/maintenance/intraday_window.sh`，
后者仍在同步/等待后再次检查，避免窗口规则漂移或排队跨时段。

午盘桥接仍优先检查 11:35–12:30：未完成则走原同步与互斥链路，已完成则盘外退出。
显式诊断开关 `AQSP_INTRADAY_REQUIRE_MARKET_HOURS=false` 保留原有用途；
面板的交易日保护仍生效。该修复不改计划任务、环境配置或互斥策略。

加载 launchd 任务（一次性）：
```bash
export AQSP_PROJECT_ROOT="/absolute/path/to/AI量化选股"
cp scripts/launchd/aqsp_daily_run_wrapper.sh ~/.aqsp/aqsp_daily_run_wrapper.sh
chmod +x ~/.aqsp/aqsp_daily_run_wrapper.sh
cp scripts/launchd/com.aqsp.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aqsp.daily.plist
launchctl list | grep com.aqsp
```

## 手动跑（验证用）

```bash
AQSP_ALLOW_LEGACY_ENTRY=1 bash scripts/daily_run.sh
tail -50 logs/daily/run-$(date +%Y-%m-%d).log
```

如果只是本地长期运行，这条链路已经够用，不需要依赖 GitHub。

## 当前前端

当前 AQSP React 看板地址：

```text
http://127.0.0.1:5899
```

启动/复用当前 React + FastAPI 服务：

```bash
bash scripts/start_vibe_research.sh
```

历史 Streamlit 回滚调试需要打开系统浏览器时，必须显式授权：

```bash
AQSP_ALLOW_FOREGROUND_BROWSER=1 python3 scripts/open_dashboard.py --open-browser
```

只刷新静态页面和 SQLite 快照，不启动浏览器：

```bash
python3 scripts/open_dashboard.py --render-only
```

Agent 调试时默认只能走后台检查，不抢前台浏览器：

```bash
python3 scripts/headless_dashboard_check.py --url https://lh.ifidy.cn --mode raw
python3 scripts/headless_dashboard_check.py --url https://lh.ifidy.cn --screenshot outputs/dashboard-check.png --headless-lock /tmp/aqsp-headless-dashboard.lock
```

截图检查会使用临时 profile、随机 DevTools 端口和 AQSP 专属锁，不复用用户 Chrome，也不连接其它项目的无头浏览器。

`daily_run.sh` 仍会刷新 `dist/dashboard/archive.html` 和 `dist/dashboard/aqsp.db` 供离线归档；这些产物和
`scripts/open_dashboard.py` 属于历史兼容链，不是生产前端入口。生产入口统一是 React `5899` + FastAPI `8900`。

## 卸载

```bash
launchctl unload ~/Library/LaunchAgents/com.aqsp.daily.plist
```

## 检查任务状态

```bash
launchctl list | grep com.aqsp
ls -la /tmp/aqsp-daily.{out,err}
```

## 30 天冷启动追踪

冷启动需要 30 个独立信号日（CONSTITUTION §1.3 #14）。
每日跑结束后，检查：
```bash
wc -l data/predictions.jsonl
```

每个独立交易日至少写入一行才算数。
