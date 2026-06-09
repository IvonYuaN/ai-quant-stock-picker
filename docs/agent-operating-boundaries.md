# Agent Operating Boundaries

本文件定义 Codex/审查 agent 托管本项目时的运行边界。目标是让本地、GitHub、服务器和公网入口各司其职，避免版本漂移、密钥泄露、前台干扰和把历史归档误读成今日动作。

## 1. 四层职责

| 层级 | 可以做 | 不可以做 |
|---|---|---|
| 本地 Mac | 开发、单元测试、ruff、截图草稿、提交和 push | 保存生产密钥、把本地临时截图/报告提交进仓库、依赖用户前台浏览器调试 |
| GitHub | 代码、文档、测试、可复现配置、公开研究元数据 | `.env`、token、webhook、私有行情库、ledger、日志、运行报告、Dashboard runtime 产物 |
| 云服务器 `/opt/aqsp` | clean checkout、`.env`、`.venv`、私有数据库、ledger、reports、logs、systemd/宝塔计划任务 | 直接开发代码、提交本地脏改、把服务器运行数据推回 GitHub |
| 公网入口 `lh.ifidy.cn` | Nginx/证书/反向代理到 `127.0.0.1:8501` | 暴露 Streamlit 端口、放密钥、提供下单或券商接口 |

## 2. 默认调试方式

Codex 需要验证远程时，默认使用后台、非侵入方式：

```bash
ssh aqsp-server 'cd /opt/aqsp && git log --oneline -3 && systemctl is-active aqsp-dashboard'
ssh aqsp-server 'cd /opt/aqsp && tail -80 logs/bt/bt-status-$(date +%Y-%m-%d).log 2>/dev/null || true'
curl -Ik https://lh.ifidy.cn/_stcore/health
```

允许：

- 后台 SSH 读日志、跑测试、重启 `aqsp-dashboard`。
- 本地用 `pytest`、`ruff`、`scripts/check_no_secrets.py` 验证。
- 用 `scripts/headless_dashboard_check.py` 做公网/本地 Dashboard 检查。
- `scripts/open_dashboard.py` 默认不得打开前台浏览器；即使命令显式传 `--open-browser`，也必须同时设置 `AQSP_ALLOW_FOREGROUND_BROWSER=1` 才允许触碰系统前台浏览器。
- 需要视觉截图时，只能启动独立无头 Chromium/Chrome 进程，并且必须使用临时 `user-data-dir`、`--remote-debugging-port=0`、独立输出文件。
- 默认检查脚本只自动寻找 Chromium；如需专用 Chrome/其它浏览器，必须用 `AQSP_HEADLESS_BROWSER=/path/to/dedicated-browser` 或 `--browser` 显式指定隔离二进制。
- AQSP 无头检查必须串行占用 AQSP 专属锁，默认 `/tmp/aqsp-headless-dashboard.lock`；同机多项目并行时用 `AQSP_HEADLESS_LOCK` 或 `--headless-lock` 指定本项目自己的锁文件。
- `scripts/headless_dashboard_check.py` 输出里的 `browser=-` 表示只做 raw/health 检查，没有启动浏览器；`headless_lock=...` 表示启动的是 AQSP 隔离无头进程，不是用户前台浏览器。

禁止：

- 要求用户反复手工运行可由 SSH 完成的命令。
- 在服务器上手改受 Git 管理代码再长期保留。
- 为了调试把生产 `.env`、数据库、ledger、报告复制进 GitHub。
- 把公网 Dashboard 上的历史归档文案渲染成“今日建议/首选/移出”等行动指令。
- 使用 Codex Browser/Chrome 插件、用户正在操作的 Chrome/浏览器窗口、已有 Playwright 会话或固定调试端口做项目调试。
- 为了截图或 DOM 检查打开前台标签页、复用用户浏览器 profile、占用其他项目的 headless browser 端口。
- 连接别的项目已经启动的无头浏览器、复用其 `user-data-dir`、复用其 DevTools websocket 或指定固定 `--remote-debugging-port`。

## 3. 部署闭环

涉及运行效果的代码变更必须走同一条链：

1. 本地改代码和测试。
2. 本地跑相关测试；重要 UI/通知改动至少跑 `tests/test_dashboard.py` 或 `tests/test_notify_templates.py`。
3. 跑 secret 扫描。
4. commit + push。
5. 服务器 `git fetch && git reset --hard <commit>`，重启服务或运行目标脚本。
6. 用服务器日志、公网 health 和隔离无头检查验证。

推荐验证命令：

```bash
python3 scripts/headless_dashboard_check.py --url https://lh.ifidy.cn --mode raw
python3 scripts/headless_dashboard_check.py --url https://lh.ifidy.cn --screenshot outputs/dashboard-check.png --headless-lock /tmp/aqsp-headless-dashboard.lock
```

服务器只接受 GitHub commit，不把服务器当开发机。若服务器出现受 Git 管理的脏改，先查明来源；除非用户明确要求，不直接覆盖用户改动。

## 4. 前端与通知表达边界

本项目是选股研究工作台，不是自动交易系统。

- 使用“纸面验证、纸面入场、纸面持有、观察、复核、阻塞、归档记录”等措辞。
- 避免“真实持仓、立即买入、执行开仓、首选下单”等可能让人误解为交易指令的措辞。
- 首页只放当前日最重要的 3 到 5 条信息，先结论后证据。
- 归档内容只作为历史记录展示；在首页/推进板必须降噪为“历史报告摘要/归档记录”，不能沿用原报告里的行动 emoji 和粗体标签。
- 通知要可扫读：标题、结论、候选简表、风险/阻塞、下一步；少写解释性长段落。

## 5. 自动化任务边界

- 宝塔计划任务是服务器生产定时入口。
- `intraday` 只写盘中产物，不污染正式 ledger。
- `coldstart` 只在收盘后补冷启动样本。
- `daily` 是收盘完整链路，负责复盘、纸面验证、通知和看板刷新。
- `monitor` 只做异常告警，不应重复推正常流水。

当用户前台需要工作时，Codex 仍可继续后台托管，但必须只使用上述边界内的命令和隔离验证。
