# 实盘前清单

本项目当前只做候选池、纸面验证、信号台账和通知，不自动下单，也不接券商交易接口。本清单用于判断系统是否可以从“研究/纸面运行”提升到“人工参考的半实盘运行”；任何未通过项都应阻止升级。

## 1. 不可让步边界

- [ ] 仓库仍不包含自动下单、券商交易接口或下单凭据。
- [ ] 通知、报告和 Dashboard 使用“纸面验证、观察、复核、阻塞、归档记录”等措辞，不出现“立即买入、执行开仓、真实持仓”等交易指令。
- [ ] LLM 只用于摘要、解释或辩论文本，不覆盖确定性评分、风控、ledger 结果。
- [ ] 生产密钥只存在服务器 `.env` 或 GitHub Secrets，不写入代码、yaml、报告、日志或 Dashboard runtime 产物。

## 2. 数据和时间口径

- [ ] `aqsp doctor --probe-auth --probe-llm` 通过；失败数据源有清晰降级说明。
- [ ] 主链数据源成功记录 `source_health`，报告可反查本次使用的数据源。
- [ ] ledger 和真实 walk-forward 使用不复权价格或 point-in-time 复权因子；前复权数据只用于展示、研究扫描或候选生成。
- [ ] 交易日历、指数成分、财报披露日等 point-in-time 数据可复核，缺失时 fail loud。
- [ ] 全项目无裸 `datetime.now()`；运行日志、报告和 ledger 时间戳带上海时区偏移。

## 3. 策略和阈值冻结

- [ ] `config/thresholds.yaml` 的 `version`、`effective_from`、`last_walkforward_run` 与最新验证报告一致。
- [ ] 新策略或新因子都有非空 `hypothesis`，并说明为什么在 A 股有效。
- [ ] 阈值变更附 walk-forward 报告；没有 DSR/PBO 证据时不得升版本进入主链。
- [ ] 训练区间和 held-out 区间没有混用；held-out 只在 walk-forward 通过后一次性验收。
- [ ] 冷启动期少于 30 个独立信号日时，不展示胜率、不学习权重、不放大通知语气。

## 4. Ledger、纸面验证和风控

- [ ] 每条信号写入 `thresholds_version`、`regime_at_signal`、`signal_day_group` 和可成交状态。
- [ ] 涨跌停、停牌、买不到的信号标记为 `not_executable`，不进入胜率统计。
- [ ] 纸面入场使用 next open，不使用 signal close 伪造收益。
- [ ] 熔断触发后停止生成新信号或降级为仅供参考，并在通知与 Dashboard 显示保护状态。
- [ ] 板块集中度、相关性拥挤、T+1、排雷过滤都在报告中可追溯原因。

## 5. 运行、通知和 Dashboard

- [ ] `scripts/daily_pipeline.sh` 或服务器计划任务完整跑批 exit code 为 0。
- [ ] `reports/latest.md`、`reports/briefing.md`、`reports/closing_review.md` 和 Dashboard 静态产物成功刷新。
- [ ] 通知模式默认为 summary；异常告警与正常流水不会重复轰炸。
- [ ] 公网 Dashboard health 可用；视觉检查只用隔离无头浏览器和 AQSP 专属锁。
- [ ] 历史归档内容在首页/推进板降噪为归档记录，不渲染成今日行动建议。

## 6. 上传和部署前检查

```bash
python3 -m pytest -q
python3 -m ruff check src tests scripts
python3 scripts/check_no_secrets.py
python3 -m scripts.preflight_upload
git status --short --ignored
```

这些命令全部通过后，才允许 push、合并或部署到服务器。若 secret scan、preflight 或 CI 任一失败，先修仓库内 guardrail，不用服务器配置绕过。

## 7. 升级判定

满足以下条件时，才可以把当前阶段描述为“人工参考的半实盘运行”：

- [ ] PR1-20 主线全部合并，main CI 通过。
- [ ] 真实 walk-forward 通过 DSR > 1.0 且 PBO < 0.5，或已有 `walkforward-failures.md` 说明失败原因与下一步。
- [ ] 至少 30 个独立信号日的纸面 ledger 可验证。
- [ ] 服务器完整跑批连续 5 个交易日成功，异常监控可通知。
- [ ] 仓主确认继续保持“不自动下单、不接券商交易接口”的边界。
