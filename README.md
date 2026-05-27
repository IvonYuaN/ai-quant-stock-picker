# AI 量化选股定时通知

一个独立 GitHub 项目：每天定时获取最新 A 股数据，筛选开盘/尾盘候选股，把“选了什么、依据是什么、风险是什么、参考买点/止损/止盈”发到 Telegram、企业微信、飞书或通用 Webhook。系统只负责筛选和通知，最终下单由人完成。

策略来源不是 `daily_stock_analysis`。它只作为后续报告/通知增强参考；选股逻辑来自互联网常见开源量化策略形态：RPS 相对强度、放量突破、均线缩量回踩、碗口反弹、低波趋势，并通过每日预测账本滚动验证。

## 快速使用

```bash
pip install -e ".[dev]"
python -m aqsp.cli screen --symbols 600519,300750 --mode close --limit 20
```

定时任务同款命令：

```bash
pip install -e ".[data]"
aqsp run --mode close --symbols 600519,300750,000001 --notify
```

使用本地 CSV：

```bash
python -m aqsp.cli screen --csv data/sample_ohlcv.csv --mode close --limit 10
```

生成 Markdown 报告：

```bash
python -m aqsp.cli screen --csv data/sample_ohlcv.csv --mode open --report reports/open.md
```

## 策略框架

核心不是单一神奇公式，而是多因子打分 + 风控否决：

- 趋势：MA5/10/20/60 多头排列、均线斜率、MACD 状态。
- 动能：20 日相对强度、20 日新高、近 5/10/20 日收益。
- 量价：量比、放量突破、缩量回踩。
- 买点：强趋势回踩、平台突破、底部反转三类入口。
- 风险：过度乖离、破 MA20、流动性不足、连续下跌、尾盘长上影。

`mode=open` 更保守，只用最新完整日线，偏向“次日开盘观察池”；`mode=close` 允许使用最新交易日收盘形态，偏向“尾盘/收盘后备选池”。

## 与 daily_stock_analysis 的关系

本项目抽取 `daily_stock_analysis` 的数据源、策略问股、报告推送思路，但保持为轻量选股引擎。推荐后续集成路径：

1. 本项目产出候选池 CSV/Markdown。
2. 把候选股票写入 `daily_stock_analysis` 的 `STOCK_LIST`。
3. 交给 `daily_stock_analysis` 做新闻、公告、LLM 决策报告和推送。

## GitHub 定时通知

1. 新建 GitHub 仓库并上传本项目。
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

数据新鲜度由 `aqsp.freshness.assert_fresh_data` 强制检查。超过允许滞后时任务直接失败，不发送陈旧选股。

## 每日验证与自优化

每次 `aqsp run` 会先验证 `data/predictions.jsonl` 中已经到期的历史预测，再生成今天的新候选并追加到账本。验证指标包括：

- `return_pct`: 买入参考价到验证日收盘价的收益。
- `win`: 收益是否大于 0。
- `strategy_weights`: 至少 3 条历史样本后，按胜率和平均收益动态调节策略权重。

GitHub Actions 使用 cache 保存 `data/predictions.jsonl`，所以每天运行可以持续积累验证结果。

## 风险声明

本项目只做研究和自动化筛选，不构成投资建议。A 股数据接口可能变化，实盘前必须做滚动回测、样本外验证、交易成本和滑点评估。
