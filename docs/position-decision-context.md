# 持仓决策背景契约

对应 `architecture.md` §7.1 的持仓风险解释，只补显示证据，不替代风险引擎。

## 调用

`backend.decision_context.build_position_context(holding, events, *, quote=None,
stock_history=None, market_overview=None, sector_snapshot=None, checked_at=None)`
返回冻结的 `PositionDecisionContext`，通过 `.to_dict()` 提供给 API 的
`position_context` 字段。输入必须是当前租户持仓和已读取的源数据；模块不访问网络、
不写文件、不改变提醒状态或策略阈值。

- `name`：仅取现有持仓或行情的真实名字；只有代码时为空，前端显示名称未知。
- `opened_at` / `holding_trade_days`：从真实开仓日计算已过交易日，开仓日为 0；
  缺日期、未来日期、非交易日或本地权威日历不覆盖的年份为 `null`。
  与既有 `resolve_holding_days` 的 0/1 T+1 标记完全独立。
- `market` / `sector` / `stock`：`status, summary, as_of, source, evidence`。
  未知、缺时间、过期、未来或无效数据不能冒充中性/良好。指数涨跌不是市场宽度的替身。
- `assessment`：`stance, title, reason, options, t_plan`。`stance` 仅有
  `risk_review / exit_review / observe`，不生成交易指令。

## 来源与降级

`load_cached_market_overview()` 只读既有 `market_cache/overview.json`，沿用
`market._TTL`，不触发 `get_overview` 或缓存重建。市场宽度沿用既有产物标签。

板块必须有持仓明确的行业归属，且与板块源名称准确匹配。现有 overview 的
行业资金流若没有源时间，仍可列事实值，但方向保持 `unknown`；不能拿缓存写入时间
或另一数据源的日期充当板块源时间。

个股优先使用带 `quote_time/as_of` 的现有腾讯行情；盘中沿用
`LiveCandidateViewConfig.max_age` 新鲜度窗口。否则可展示已有不复权日线的最近
已完成交易日涨跌，并注明不是当前分时。今天未完成的 bar 不冒充昨日收盘。

只有三个维度都有同一交易日的已验证弱势证据时，才追加“复核减仓或退出”分析。
L1 已触发始终保留并明确风险提醒，不能被背景回暖或做T表述掩盖，也不单独推出清仓结论。
只有大盘、所属板块、个股都有同交易日的已验证弱势证据时，才突出复核减仓或退出；其他情形
保留持有、减仓和做T观察三种人工选择。账面亏损或止损线触发本身不是做T依据；没有日内区间、
流动性和往返成本证据时，`t_plan.eligible=null`，仅表示不能确认适合执行，用户仍可记录观察意向。
没有可卖底仓或三维同步走弱时为 `false`。本模块不会凭不足的证据返回 `true`。

验证：`PYTHONPATH=src:backend python -m pytest -q backend/tests/test_decision_context.py`。
