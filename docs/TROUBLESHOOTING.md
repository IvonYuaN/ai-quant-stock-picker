# AQSP 故障排查手册

本手册只覆盖项目内可复现排查。不要把服务器运行数据、`.env`、ledger、报告或截图提交进仓库。

## 先跑快速体检

本地开发或服务器运行异常时，先看当前链路是否完整：

```bash
python3 -m pytest -q
python3 -m ruff check src tests scripts
python3 scripts/check_no_secrets.py
python3 -m scripts.preflight_upload
```

服务器侧优先用只读诊断：

```bash
bash scripts/server_status.sh
python3 scripts/server_doctor.py
python3 scripts/check_scheduler.py
```

## 日常任务没有运行

先确认调度器和最近日志：

```bash
launchctl list | grep com.aqsp
tail -50 logs/daily/run-$(date +%Y-%m-%d).log
```

如果服务器任务由宝塔或 cron 触发，先看 `scripts/server_status.sh` 输出里的最新 commit、日志文件和锁状态。不要直接在服务器手改受 Git 管理的代码。

## 锁文件残留

项目有两类锁：

- 运行锁：`.locks/server-runtime.lock`、`.locks/server-monitor.lock`
- 无头 Dashboard 检查锁：默认 `/tmp/aqsp-headless-dashboard.lock`

先检查锁是否仍有活跃进程：

```bash
bash scripts/server_status.sh
python3 scripts/check_scheduler.py
```

只清理陈旧锁：

```bash
bash scripts/clear_locks.sh
```

只有确认没有 AQSP daily、bt、coldstart 或 monitor 任务仍在运行时，才允许强制清理：

```bash
AQSP_CLEAR_LOCKS_FORCE=true bash scripts/clear_locks.sh
```

## 选股结果为空

空结果不一定是失败，可能是风险门控、生效阈值或数据新鲜度挡住了。按顺序检查：

```bash
tail -80 logs/daily/run-$(date +%Y-%m-%d).log
python3 scripts/diagnose_runtime.py
python3 scripts/resolve_notify_level.py --ledger data/predictions.jsonl --field label
```

重点看：

- 数据源健康标签是否为 `critical` 或 `stale`
- `AQSP_MAX_DATA_LAG_DAYS` 是否过严
- 当前是否触发 circuit breaker
- `reports/latest.md` 是否写明“无候选”还是“被阻塞”
- `data/predictions.jsonl` 最新行是否有 `candidate_blocker` 或 `not_executable_reason`

## 通知没有收到

先区分“没有生成通知”和“通知渠道失败”。

```bash
tail -80 logs/daily/run-$(date +%Y-%m-%d).log
python3 scripts/resolve_notify_level.py --ledger data/predictions.jsonl --field level
```

邮件通道用环境变量配置，验证命令：

```bash
aqsp briefing --output /tmp/test_briefing.md --email
```

如果输出显示跳过邮件发送，按 `docs/email-setup.md` 补齐 `AQSP_SMTP_HOST`、`AQSP_SMTP_USER`、`AQSP_SMTP_PASSWORD`、`AQSP_EMAIL_FROM`、`AQSP_EMAIL_TO`。不要把这些值写进仓库文件。

## Dashboard 没更新或打不开

只刷新静态面板和 SQLite 快照：

```bash
python3 scripts/open_dashboard.py --render-only
```

本地只启动或复用静态服务，不打开前台浏览器：

```bash
python3 scripts/open_dashboard.py
```

公网或服务器检查优先走 raw 模式：

```bash
python3 scripts/headless_dashboard_check.py --url https://lh.ifidy.cn --mode raw
```

确实需要截图时，只能用隔离无头浏览器和 AQSP 专属锁：

```bash
python3 scripts/headless_dashboard_check.py \
  --url https://lh.ifidy.cn \
  --screenshot outputs/dashboard-check.png \
  --headless-lock /tmp/aqsp-headless-dashboard.lock
```

禁止用用户前台 Chrome、Codex Browser/Chrome 插件、已有 Playwright 会话或固定调试端口调试本项目。

## 上传前被拦截

上传前检查失败时，先按输出修仓库内问题：

```bash
python3 scripts/check_no_secrets.py
python3 -m scripts.preflight_upload
git status --short
```

常见原因：

- `.env`、日志、ledger、`reports/`、`dist/dashboard/` 或本地数据库被误加
- 私有 token 出现在 yaml、Markdown 或测试 fixture
- 服务器运行产物被复制回 Git 仓库

确认修复后再提交。不要用跳过检查来绕过密钥或运行产物问题。
