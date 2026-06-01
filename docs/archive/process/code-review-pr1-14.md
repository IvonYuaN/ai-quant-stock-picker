# 小米Pro PR1-14 代码审查报告

审查时间：2026-05-27
审查对象：`src/aqsp/{core,data,strategies,ledger,portfolio,risk,regime,backtest,reports,universe}` + `config/thresholds.yaml`
审查依据：`docs/architecture.md`、`AGENTS.md`、Python/pandas 现行规范

> 评级：🔴 阻塞合并 / 🟠 高优先级 / 🟡 应修 / ⚪ 可改进

---

## 一、模块解析冲突（已修复 ✅）

**问题**：旧 `data.py / universe.py / ledger.py` 与新 `data/ universe/ ledger/` 包并存，Python 优先选包，导致 `from aqsp.data import fetch_akshare`、`from aqsp.universe import DEFAULT_SYMBOLS` 全部 ImportError，CLI 无法启动。违反 `architecture.md §7 PR2 验收：CLI 现有命令不变`。

**已采取的修复**：
1. 在 `data/__init__.py` 中加入 `load_csv / fetch_akshare / fetch_with_source` 兼容包装。
2. 在 `universe/__init__.py` 中加入 `DEFAULT_SYMBOLS` 兼容常量。
3. 删除旧 `data.py / universe.py / ledger.py` 文件。
4. `from aqsp.cli import main` 已能成功导入。

---

## 二、🔴 阻塞级（运行即崩溃 / 数据契约破坏）

### B1. `data/akshare_source.py` `_normalize_akshare_df` 硬编码涨跌停与复权因子

```python
df["limit_up"] = 0.0      # 应基于 prev_close × (1+limit_pct)
df["limit_down"] = 0.0    # 主板 10%、ST 5%、创业板/科创板 20%、北交所 30%
df["adj_factor"] = 1.0    # 应来自真实复权因子缓存
df["suspended"] = False   # 应根据 volume==0 或 trade_status 判定
```

`sina_source.py`、`eastmoney_source.py` 同样存在该问题。这违反 `architecture.md §3.1`：DataSource 契约要求这些列含真实值。当前 `ledger._check_executable` 是靠 `entry_bar.get("volume")<=0` 和默认 `0.099` 兜底才能判涨跌停，对 ST/科创板/北交所完全错判。

**建议修复**：
- 计算 `limit_up = prev_close * (1+limit_pct)`，根据板块代码判定 `limit_pct`（`688*` 20%、`300*`/`301*` 20%、`8*`/`4*` 30%、`*ST` 5%、其余 10%）。
- `suspended = volume==0 or amount==0`。
- `adj_factor` 接入 `AdjustmentService.cache.get_adj_factor`。

### B2. `ledger/learner.py` 字段名与实际 ledger 不一致 → 一调用就 KeyError

```python
strategies = ledger_df["strategy"].unique()              # 实际是 "strategies" (list)
wins = executable[executable["return"] > 0]              # 实际是 "return_pct"
```

`base.py` 写出的字段是 `strategies: list[str]` 和 `return_pct: float`。`learner.py` 全部读错，第一次调用立即崩溃。

**建议修复**：把 `learner.py` 改为 explode `strategies` 列后逐条聚合，并改用 `return_pct`、`excess_return_pct`。

### B3. `ledger/learner.py` 完全没实现 architecture §5 的反过拟合机制

architecture 要求：`min_independent_signal_days=30`、`rolling_window_days=90`、`aggregation="per_signal_day"`、`weight_change_cooldown_days=30`、`by_regime=True`。

实际实现：
- `min_samples=20`、`window=60`、`cool_down_days=30` 但**从未被使用**（`_last_update` 字典只声明，没有读写逻辑）。
- 没有按 `signal_date` 聚合（每个 signal_day 出多只票时会被当成独立样本，违反"独立信号日"要求）。
- 没有 regime 切分。
- 权重输出 `{"base": x, "confidence": y}`，与 `base.py::strategy_weights_from_ledger` 输出 `{strategy_name: float}` 完全不兼容，CLI 不知道用哪个。

**建议**：重写 `learner.py`。这块是项目核心反过拟合屏障，目前形同虚设。

### B4. `backtest/walk_forward.py::_run_single_period` 是假回测

```python
dates = sorted(set(test_data["date"].tolist())) if test_data else []
# test_data 是 dict[str, DataFrame]，没有 .date 属性 → AttributeError

returns.append(avg_score * 0.01)
# 用"分数 × 0.01"当收益，完全不是真实交易收益
```

这是用事后排序分数当收益，正是用户最强调要避免的"事后数据预测可行性"。整个 walk-forward 模块需要重写：从 ledger 真实 entry/exit 价拉收益。

### B5. `data/multi_source.py` 一致性校验永远不会触发

```python
for source in sources:
    try:
        result = func(source)
        ...
        if result:
            return result   # 只要主源成功立刻返回，永远走不到下面的 _validate_consistency
    except Exception as e:
        ...

if len(results) >= 2:        # 只在所有源都"返回空但不抛异常"时才命中
    self._validate_consistency(...)
```

architecture §3.2 要求："多源时校验一致性差>0.5%抛错"。当前实现不会校验。

**建议修复**：成功获取主源后并行/串行获取一个 fallback，再走 `_validate_consistency`，不一致才抛错。

### B6. `data/intraday.py::merge_intraday_with_daily` 用了 pandas 已删除 API

```python
daily = daily[mask].append(today_intraday, ignore_index=True)
```

`DataFrame.append` 在 pandas 2.0 已删除。运行时崩溃。改为 `pd.concat([daily[mask], pd.DataFrame([today_intraday])], ignore_index=True)`。

### B7. `data/cache.py` 大量 `datetime.now()` 无时区

```python
df["fetched_at"] = datetime.now().isoformat()
cutoff = (datetime.now() - pd.Timedelta(...)).isoformat()
```

违反 `AGENTS.md` 红线第 1 条："禁止 datetime.now() 不带 tz"。已有 `core/time.now_shanghai()`，应统一使用。否则跨时区运行（GitHub Actions UTC）的缓存命中判断会错位 8 小时。

---

## 三、🟠 高优先级

### H1. `core/types.py::DataSource` Protocol 用了未导入的 `date`

```python
def fetch_daily(self, symbols: list[str], start: date, end: date, ...):
```

文件没 `from datetime import date`。靠 `from __future__ import annotations` 不会导入时崩，但任何 `inspect.get_type_hints()` 调用都会失败。补上 import。

### H2. `strategies/base.py` 没实现 architecture 的 Strategy Protocol

architecture §4.1 要求每个 Strategy 必须有 `id, version, hypothesis, regime_required` 字段，`evaluate(df, regime)` 方法。当前 `BaseStrategy` 只有 `name` 和 `calculate_score(data)`。`hypothesis` 字段完全缺失——这是反过拟合的"事前假设"机制，必须有。

### H3. `config/thresholds.yaml` 缺 `effective_from` 和 `last_walkforward_run`

architecture §4.2 模板要求这两个字段。当前只有 `version` 和 `description`。这意味着不能审计"什么时候改的、上次离线 walk-forward 验证是何时跑的"。

### H4. `strategies/quality.py` 和 `value.py` 字段在数据源里根本没产生

`quality` 找 `roe / roa / debt_ratio / operating_margin`，`value` 找 `pe / pb / dividend_yield`。三个 DataSource (akshare/sina/eastmoney) 的 `_normalize_*_df` 都只产生 OHLCV + suspended 等技术字段。所以 quality/value 永远走 `return 0.5` 默认分支——形同虚设。

**建议**：要么在 PR 中加基本面数据源（akshare 有对应接口），要么把 quality/value 标 `enabled=False` 直到接入。

### H5. `composite.py` 评分权重双重来源、且分数权重内嵌

`MomentumStrategy._calculate_single_score` 内有 `momentum*0.4 + trend*0.3 + rsi*0.3` 硬编码。`CompositeStrategy.calculate_score` 又加一层 `composite.momentum_weight` 等。两层权重都该到 thresholds 里。AGENTS 红线 "策略代码里禁止魔法数字"。

### H6. `risk/circuit_breaker.py` 用的是 2016 年已下线的 A 股熔断规则

5%/7%/9% 是熔断试点期规则，2016-01-08 后停用。当前 A 股没有指数级熔断，只有个股涨跌停。这模块需重定义：要么改成"组合层面的日/周/月止损"（架构 §6 真正想要的），要么删除。

### H7. `regime/detector.py` 阈值硬编码 + 无样本量门槛

`if volatility > 0.3 / momentum > 0.1 / trend > 0.02` 全部魔法数字，且只看一个指数最后 60 天，没有样本量保护、没有冷却期，会频繁切 regime。

### H8. `reports/v2.py` 与 `portfolio/diversification.py` 类型不兼容

`PortfolioReport.sector_allocations: Dict[str, float]`
`DiversificationEngine.optimize` 返回 `PortfolioResult.sector_allocations: List[SectorAllocation]`

调用方拼接时会 `dict(...)` 失败或拿到错的形状。

---

## 四、🟡 应修

### M1. `universe/filters.py::UniverseFilter.apply` 调用约定不一致

`apply` 里 `func(result, data)`，但具体 filter 类的签名是 `filter(universe, names=..., data=..., listing_dates=...)`，关键字参数不同。`UniverseFilter` 这个聚合类几乎没法用，应该直接删除，留 `FilterPipeline` 即可。

### M2. `data/akshare_source.py` 缓存命中判断不严

```python
cached = self.cache.get_ohlcv(symbol, start, end)
if cached is not None and not cached.empty:
    out[symbol] = cached
    continue
```

只要缓存里"有数据"就直接返回，但缓存可能不覆盖完整 [start, end] 区间。需要校验日期连续性，缺数才补抓。

### M3. `data/adjust.py::fetch_and_cache_factors` 静默吞异常

```python
except Exception:
    pass
```

architecture §1.2 要求"数据失效必须显眼报错"。这里静默会导致 qfq 后价格全错而无人知。至少 `logging.warning` 一下。

### M4. `ledger/base.py::strategy_weights_from_ledger` 仍用 `min_samples=3`

architecture §5 要求 `min_independent_signal_days=30`。当前 `if len(returns) < 3` 太松，3 笔交易就改权重，与 learner 的目标矛盾。

### M5. `strategies/momentum.py::_calculate_trend_score` SettingWithCopyWarning

```python
df["ma"] = df["close"].rolling(...)   # df 是 .tail(N) 的视图
```

会污染原 frame。应 `df = df.copy()` 或用临时 Series。

### M6. `data/intraday.py` 如果数据源没返回也直接静默，未抛 MissingDataError

---

## 五、⚪ 改进建议

- `core/time.is_trading_day` 只硬写 1/1、5/1、10/1 三个节假日，应接入 `chinese_calendar` 或 akshare 节假日数据。
- `ReportGenerator` 不接受 `picks` 列表，与现有 CLI 输出脱节，需要桥接层。
- 所有 dataclass 都用了 `frozen=True` 但 learner 里 `self._history.append` 和 `self._last_update[...]` 又是可变状态，建议拆分纯数据 vs 服务对象。

---

## 六、修复优先级路线图

| 优先级 | 项 | 说明 |
|--------|----|------|
| P0（已完成） | 模块冲突 | `data/__init__` `universe/__init__` 兼容导出 |
| P0（待修） | B1 涨跌停/复权 | 数据契约根基 |
| P0（待修） | B2 learner 字段名 | 一调用就崩 |
| P0（待修） | B3 learner 反过拟合 | 项目核心承诺 |
| P0（待修） | B4 walk_forward | 假回测危害大 |
| P1 | B5/B6/B7 | 多源/intraday/cache 时区 |
| P1 | H1-H4 | Protocol 契约 + 缺失基本面 |
| P2 | H5-H8 / M* | 阈值收口、模块对齐 |

---

## 七、对小米 Pro 后续协作的建议

1. 在 PR 描述中明确列出"实现了 architecture 的哪几条 §"，方便我对照。
2. 任何"补 0.0 / False / 1.0 占位"都视为未完成，DataSource 契约必须真实计算。
3. learner / walk_forward 这种核心反过拟合模块，写完先让我审查再写下一个，否则错位会传染到所有依赖模块。
4. thresholds.yaml 修改必须改 `version` 并填 `effective_from`、`last_walkforward_run`，否则 PR 不通过。