# AI 量化选股本地工作台

本项目以**本地运行**为主，GitHub 只负责代码备份、版本同步和可选的远程定时任务。

它的目标是：每天基于最新 A 股数据筛选开盘/尾盘候选股，把“选了什么、依据是什么、风险是什么、参考买点/止损/止盈”输出到本地报表、静态面板和通知渠道。系统只负责筛选和通知，最终下单由人完成。

选股逻辑主要来自公开开源量化项目、A 股常见交易理论和持续的 walk-forward 验证；当前主策略家族包括 RPS 相对强度、放量突破、均线缩量回踩、碗口反弹和低波趋势。日报、通知和面板部分则继续吸收适合本地工作台的产品形态。

## 项目定位

- 主运行环境：本地 Mac / 本地服务器
- 主数据位置：本地私有数据目录、外部数据源、运行时缓存
- GitHub 作用：备份代码、保存文档、可选跑 Actions
- 不上传：本地大数据、账本、缓存、私钥、token、运行日志

## 建议仓库说明

如果你把它公开放在 GitHub，建议仓库名和简介写成这种风格：

- 仓库名：`ai-quant-local-workbench`
- 简介：`Local-first A-share quant screening workbench. GitHub is used for code backup and optional automation only.`

## 快速使用

```bash
pip install -e ".[dev]"
python -m aqsp.cli screen --symbols 600519,300750 --mode close --limit 20
python -m aqsp.cli screen --pool zz500 --mode close --limit 20
```

定时任务同款命令。本地有 TDX `private_data/tdx` 时，`AQSP_SYMBOLS` 留空会先按最新成交额从全市场预筛 `AQSP_MAX_UNIVERSE` 只，再进入策略评分；显式传 `--symbols` 则只跑指定小池。默认 100 只是为了本地每日定时稳定，手动研究可提高到 300/800/1500：

```bash
pip install -e ".[data]"
aqsp run --mode close --source auto --max-universe 100 --notify
aqsp run --mode close --pool sh300 --notify
```

北向资金、融资融券属于附加观察因子，默认不联网抓取，避免本地定时被外部接口拖慢；需要时加 `--enable-online-factors` 或设置 `AQSP_ENABLE_ONLINE_FACTORS=true`。

使用本地 CSV：

```bash
python -m aqsp.cli screen --csv data/sample_ohlcv.csv --mode close --limit 10
```

生成 Markdown 报告：

```bash
python -m aqsp.cli screen --csv data/sample_ohlcv.csv --mode open --report reports/open.md
```

生成并打开本地前端面板（固定端口 `127.0.0.1:9876`）：

```bash
python3 scripts/open_dashboard.py
```

只刷新页面文件、不启动服务：

```bash
python3 scripts/open_dashboard.py --render-only
```

页面会展示候选股、评分、策略依据、风险、参考买点/止损/止盈、ledger 统计和最近信号。默认地址固定为 `http://127.0.0.1:9876`，`dist/` 已被忽略，不会上传 GitHub。

验证 Tushare PIT 接口：

```bash
export TUSHARE_TOKEN='...'
python -m aqsp.cli pit --kind trade_calendar --start 2026-06-01 --end 2026-06-10 --json
python -m aqsp.cli pit --kind index_weights --index-code 000300.SH --start 2026-06-01 --end 2026-06-10
python -m aqsp.cli pit --kind disclosure_dates --symbols 600519,300750 --start 2026-04-01 --end 2026-06-30
```

若使用 `walkforward --source baostock` 或 `walkforward --source sqlite_db`，并且本地已配置 `TUSHARE_TOKEN`，系统会自动用披露日覆盖 Baostock 财报公告时点，避免把晚披露财报提前泄漏进回测。
若 `walkforward` 未显式传 `--symbols`，默认沪深300股票池也会优先按回测开始日读取可选 Tushare 指数成分；没有 `TUSHARE_TOKEN` 时才退回仓库内置快照。

## 策略框架

核心不是单一神奇公式，而是多因子打分 + 风控否决：

- 趋势：MA5/10/20/60 多头排列、均线斜率、MACD 状态。
- 动能：20 日相对强度、20 日新高、近 5/10/20 日收益。
- 量价：量比、放量突破、缩量回踩。
- 买点：强趋势回踩、平台突破、底部反转三类入口。
- 风险：过度乖离、破 MA20、流动性不足、连续下跌、尾盘长上影。

`mode=open` 更保守，只用最新完整日线，偏向“次日开盘观察池”；`mode=close` 允许使用最新交易日收盘形态，偏向“尾盘/收盘后备选池”。

## 研究来源与数据层

策略进入主链路前，必须先写入 registry、转成可测试纯函数，并通过样本外 walk-forward 验证。数据层按“本地优先、外部补充”的方式组织：

- 本地优先：TDX / 私有 SQLite / 本地缓存
- 公网补充：AKShare、Sina、Eastmoney、Tencent、可选 Tushare PIT
- 研究吸收：公开开源项目、论文、交易理论、产品形态调研

- 文档索引：`docs/README.md`
- 测试索引：`tests/README.md`
- 数据源清单：`config/data_sources.yaml`
- 策略来源清单：`config/strategy_sources.yaml`
- 本地 registry 输出：`python scripts/collect_research_registry.py`

## 仓库入口

- `docs/README.md`: 当前有效文档入口；阶段性试验和过程记录已经移到 `docs/archive/`
- `tests/README.md`: 测试分层说明，方便定位该跑哪一组回归
- `scripts/README.md`: 脚本边界说明，避免把临时采集工具误接入主链路

## GitHub 备份与可选定时通知

1. 新建 GitHub 仓库并上传本项目代码。
2. 在 `Settings -> Secrets and variables -> Actions -> Variables` 配置：
   - `AQSP_SYMBOLS`: 股票池，如 `600519,300750,000001`
   - `AQSP_MODE`: `open` 或 `close`
   - `AQSP_LIMIT`: 推送前 N 只
   - `AQSP_MAX_DATA_LAG_DAYS`: 最大数据滞后天数，默认 3
3. 在 `Settings -> Secrets and variables -> Actions -> Secrets` 至少配置一个通知渠道：
   - Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - 企业微信: `WECHAT_WEBHOOK_URL`
   - 飞书: `FEISHU_WEBHOOK_URL`
   - 通用 Webhook: `GENERIC_WEBHOOK_URL`
4. Workflow 默认北京时间工作日 09:10 和 14:45 运行，也支持手动运行。

如果你主要在本地跑，这一节可以完全不启用；GitHub 只保留仓库备份也没问题。

数据新鲜度由 `aqsp.freshness.assert_fresh_data` 强制检查。超过允许滞后时任务直接失败，不发送陈旧选股。
若已配置 `TUSHARE_TOKEN`，运行时会优先用 Tushare 交易日历按真实交易日判断滞后和 T+1，长假期间不会把正常停市误判成数据过期。

## 私有前端和数据库

推荐把代码放 GitHub 备份，把每日结果留在你自己的机器或服务器:

- GitHub Actions 定时跑 `aqsp run`。
- `aqsp dashboard` / `scripts/render_dashboard.py` 生成 `dist/dashboard/index.html`。
- `scripts/export_dashboard_db.py` 生成 `dist/dashboard/aqsp.db`。
- `scripts/deploy_dashboard.sh` 通过 SSH/rsync 发布到服务器。

开启方式见 `docs/server-dashboard-deployment.md`。敏感信息只放 GitHub Actions Secrets,不要放仓库文件:

- `AQSP_DEPLOY_HOST`
- `AQSP_DEPLOY_PORT`
- `AQSP_DEPLOY_USER`
- `AQSP_DEPLOY_PATH`
- `AQSP_DEPLOY_SSH_KEY`

是否部署由 GitHub Variable `AQSP_DEPLOY_DASHBOARD=true` 控制。默认不发布前端,也不上传 ledger/report/cache。

## 每日验证与自优化

每次 `aqsp run` 会先验证 `data/predictions.jsonl` 中已经到期的历史预测，再生成今天的新候选并追加到账本。验证指标包括：

- `entry_price`: 信号日之后下一交易日开盘价成交，不使用信号日收盘价假装能买到。
- `return_pct`: 买入参考价到验证日收盘价的收益。
- `fee_bps/slippage_bps`: 默认计入交易成本和滑点。
- `stop_loss/take_profit`: 验证窗口内触发止损/止盈则按触发价退出，否则按持有期最后收盘退出。
- `win`: 收益是否大于 0。
- `excess_return_pct`: 相对基准的超额收益；学习优先按超额收益加权。
- `strategy_weights`: 至少 3 条历史样本后，按胜率和平均超额收益动态调节策略权重。

GitHub Actions 当前不上传 `data/predictions.jsonl`。如果需要跨运行保留验证账本,优先部署到私有服务器或对象存储,不要把 ledger 提交到 GitHub。

这个协议避免“事后数据预测”：当天只产生信号，不把当天收盘当作可成交价；下一次运行才用后来真实出现的 K 线验证。

## 风险声明

本项目只做研究和自动化筛选，不构成投资建议。A 股数据接口可能变化，实盘前必须做滚动回测、样本外验证、交易成本和滑点评估。
