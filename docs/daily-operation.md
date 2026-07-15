# 每日运行手册（Phase 1 半实盘）

默认模式：**本地优先**。GitHub Actions 不是必需项。

## 自动跑（本地 legacy）

生产服务器统一使用：

```bash
bash scripts/bt_task.sh daily
```

`daily_run.sh` 只保留给本地 smoke run，默认 fail-closed，必须显式设置 `AQSP_ALLOW_LEGACY_ENTRY=1`。

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

## 本地前端

当前 Streamlit 看板地址：

```text
http://127.0.0.1:8501
```

启动/复用当前 Streamlit 服务，不打开前台浏览器：

```bash
python3 scripts/open_dashboard.py
```

人工确实要打开系统浏览器时，必须显式授权：

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

`daily_run.sh` 仍会在每日跑完后刷新 `dist/dashboard/archive.html` 和 `dist/dashboard/aqsp.db` 供归档/静态产物使用；`dist/dashboard/index.html` 只是迁移入口，用户入口统一是当前 Streamlit 看板 `8501`。

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
