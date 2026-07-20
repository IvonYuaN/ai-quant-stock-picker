# AI 量化选股 — 架构与编码规划

本文件是项目的 **唯一规划源**。所有新模块、PR、阈值变更必须先来这里对齐。
两人协作:小米Pro 编码,Claude 审查。任何与本文件冲突的代码默认不合并。

最后更新:2026-07-20。实时短线研究与独立 T+1 变体账户已纳入当前主线。

---

## 1. 项目宪法(边界,改不动)

### 1.1 做什么

- A 股(第一版)、港股、美股(后续扩展)的 **候选池生成器 + 信号台账 + 通知**。
- 服务器 / GitHub Actions 自动运行,所有下单决策由人完成。
- 所有策略 **可解释、可回测、可替换数据源**。
- 数据过期、信号不可成交 → 失败,不发通知,不污染统计。

### 1.2 不做什么

- 不自动下单,不接券商交易接口。
- 不做 LLM 决策的硬依赖(LLM 只能作为通知附件,不参与选股打分)。
- 不做屏幕截图采集(数据源接口已经够用,截图是兜底,优先级最低)。
- 不预测涨跌幅,只产出"候选 + 评分 + 命中策略 + 风险 + 参考买点/止损/止盈"。
- 不对外提供 API/服务,单用户自用。

### 1.3 核心原则

1. **真实交易思维**:回测/校验里不能出现"事后才知道的信息"。任何一个数字都要回答 "在 t 时刻能不能拿到"。
2. **冻结优先**:阈值上线后冻结到下次 walk-forward,中途不允许"看到亏了就调"。
3. **学习的尺度慢于噪音的尺度**:权重学习按月级别,不按日级别。
4. **不可成交 ≠ 失败的样本**:涨跌停/停牌买不到的信号 status="not_executable",**不计入胜率**,否则系统会被自己骗。
5. **风控是硬约束,不是优化目标**:熔断触线就停,不参与"再平衡决策"。

---

## 2. 模块结构

```
src/aqsp/
├── core/
│   ├── types.py           # PickResult / SignalDay / OHLCV schema
│   ├── time.py            # Asia/Shanghai 时区工具
│   └── errors.py          # DataError / FreshnessError / NotExecutableError
├── data/
│   ├── source.py          # 抽象 DataSource (Protocol)
│   ├── akshare_source.py  # 默认实现
│   ├── sina_source.py     # 备份(akshare 挂了顶上)
│   ├── eastmoney_source.py
│   ├── multi_source.py    # 故障切换包装器
│   ├── cache.py           # SQLite/Parquet 本地缓存
│   ├── adjust.py          # 复权因子表(point-in-time)
│   └── intraday.py        # 盘中分时 + 5min bar 合成
├── universe/
│   ├── pool.py            # 默认股票池
│   └── filters.py         # ST、退市、停牌、新股过滤
├── indicators/
│   └── core.py            # 现有 indicators.py 拆出来
├── strategies/
│   ├── base.py            # Strategy 协议
│   ├── rps_momentum.py
│   ├── volume_breakout.py
│   ├── ma_pullback.py
│   ├── bowl_rebound.py
│   ├── low_vol_trend.py
│   └── thresholds.yaml    # 所有魔法数字,带版本号 + 生效日
├── regime/
│   └── detector.py        # 简单二分类:趋势市 vs 震荡市
├── portfolio/
│   ├── correlation.py     # 候选池内相关性去重
│   ├── sector.py          # 行业去重
│   └── sizing.py          # 等权 / 风险平价
├── ledger/
│   ├── store.py           # 读写 jsonl
│   ├── validator.py       # 验证 pending 信号
│   ├── learner.py         # 策略权重学习
│   └── execution.py       # ExecutionConfig + 涨跌停/停牌判定
├── risk/
│   └── circuit_breaker.py # 账户级熔断
├── reports/                # 现有 report.py
├── notify/                 # 现有 notifier.py
├── cli.py
└── config.py
```

---

## 3. 数据层契约

### 3.1 DataSource 抽象(Protocol)

```python
# src/aqsp/data/source.py
from typing import Protocol, Literal
import pandas as pd
from datetime import date

# OHLCV 标准列:
#   date, symbol, name, open, high, low, close, volume, amount,
#   suspended (bool), limit_up (float), limit_down (float)
OhlcvFrame = pd.DataFrame

class DataSource(Protocol):
    name: str  # "akshare" / "sina" / "eastmoney"

    def fetch_daily(
        self,
        symbols: list[str],
        start: date,
        end: date,
        adjust: Literal["", "qfq", "hfq"] = "",  # **默认不复权**
    ) -> dict[str, OhlcvFrame]: ...

    def fetch_intraday(
        self,
        symbols: list[str],
        period: Literal["1", "5", "15", "30", "60"] = "5",
    ) -> dict[str, OhlcvFrame]: ...

    def fetch_realtime_quote(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        # 返回字段至少含:price, bid1, ask1, volume, amount, ts(带时区 ISO 字符串)
        ...

    def fetch_index(
        self,
        index_codes: list[str],  # ["000300", "000905", "HSI", "SPX"]
        start: date,
        end: date,
    ) -> dict[str, OhlcvFrame]: ...
```

**契约规则**:

- 返回的 OHLCV **必须是不复权原始价**。复权在 `adjust.py` 用 point-in-time 因子表后处理。
- `suspended/limit_up/limit_down` 三列必填。无信息时显式 `False`/`NaN`,不可以省略。
- 时间戳带时区(`Asia/Shanghai`),禁止裸 `datetime.now()`。
- fetch 失败必须抛 `DataError` 子类,不允许静默返回空 dict。

### 3.2 故障切换

`MultiSource(primary, fallbacks=[...])`:primary 失败时按顺序尝试 fallbacks,记录哪一路成功;两路数据不一致(open/close 差异 > 0.5%)时抛 `DataInconsistencyError`。

### 3.3 实时数据路线

按优先级:
1. **akshare**(`stock_zh_a_spot_em` / `stock_zh_a_minute` / `stock_bid_ask_em`)— 默认,3-5 秒延迟。
2. **新浪 hq.sinajs.cn**— 备份。请求必须带 `Referer: http://finance.sina.com.cn`,否则 403。
3. **东方财富 push2.eastmoney.com**— 备份。
4. **券商 QMT** — 实盘升级路径(后续)。
5. **屏幕截图 OCR** — 只在某个数据真的没接口时启用,默认不实现。

---

## 4. 策略层契约

### 4.1 Strategy 协议

```python
# src/aqsp/strategies/base.py
from typing import Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class SignalScore:
    strategy_id: str
    score: float
    reasons: tuple[str, ...]
    fired: bool       # 没触发时 score=0,reasons=()

class Strategy(Protocol):
    id: str
    version: str           # "v1.0" 改阈值必须加版本号
    hypothesis: str        # 必填:一句话经济假设
    regime_required: tuple[str, ...]  # 适用 regime,空 tuple 表示全状态

    def evaluate(self, df: pd.DataFrame, regime: str) -> SignalScore: ...
```

**契约规则**:

- `hypothesis` 不允许为空字符串。提交策略 PR 时必须写清楚"为什么这条规则在 A 股有效"。
- 所有阈值通过构造函数从 `thresholds.yaml` 注入,**禁止字面量魔法数字**。
- `evaluate` 必须是纯函数。
- 不允许跨策略共享状态。
- 不允许在 `evaluate` 内部访问磁盘、网络。

### 4.2 thresholds.yaml 格式

```yaml
version: "2026.05.27"
effective_from: "2026-06-01"
last_walkforward_run: "2026-05-15"
notes: "首版冻结值,基于 InStock / A-share-Selector 项目经验值"

strategies:
  volume_breakout:
    breakout_proximity: 0.995
    volume_ratio_min: 1.35
    range_pos_min: 0.62
    score_base: 18
  ma_pullback:
    upper_band: 1.025
    lower_band: 0.985
    volume_ratio_max: 1.1
    score_base: 16
  # ...
```

**约定**:CLI 启动时把 `version` 写到日志和 ledger 每条记录,事后能反查"那次信号用的哪一版阈值"。

---

## 5. Ledger / 学习层(改进版)

### 5.1 LedgerRow Schema

每条记录新增字段:

```python
{
  # 已有字段保持不变
  "thresholds_version": "2026.05.27",
  "regime_at_signal": "trend",      # 或 "range"
  "executable": True,                # 次日开盘是否真能成交
  "not_executable_reason": "",       # "limit_up_at_open" / "suspended"
  "signal_day_group": "2026-05-27_volume_breakout",  # 用于"按信号日聚合"
}
```

### 5.2 Learner 配置

```python
@dataclass
class LearnerConfig:
    min_independent_signal_days: int = 30
    rolling_window_days: int = 90
    weight_floor: float = 0.65
    weight_ceiling: float = 1.45
    aggregation: Literal["per_signal_day", "per_pick"] = "per_signal_day"
    weight_change_cooldown_days: int = 30
    by_regime: bool = True
```

**契约规则**:

- 不满足 `min_independent_signal_days` 的策略权重必须为 `1.0`,不参与学习。
- 同一个 `signal_day_group` 内多个 pick 合成 **1 个观察**(平均收益)。
- 只看 `rolling_window_days` 内的样本。
- 权重变更必须写入 `data/weight_history.jsonl`(前后值、reason、生效时间)。
- 冷却期内不允许再次调整同一策略权重。
- `status="not_executable"` 的记录**不进入胜率统计**。

### 5.4 冷启动期规则

**冷启动期**:系统首次运行或 ledger 清空后的前 30 个独立信号日。

| 行为 | 冷启动期内 | 冷启动期后 |
|------|-----------|-----------|
| 策略权重调整 | ❌ 不调整,所有策略权重 = 1.0 | ✅ 按 learner 计算 |
| 策略胜率展示 | ❌ 不展示,显示"数据积累中" | ✅ 正常展示 |
| walk-forward 报告 | ❌ 不生成 | ✅ 正常生成 |
| 信号生成 | ✅ 正常生成 | ✅ 正常生成 |

**原因**:前 30 个信号日的样本量不足以支撑统计推断。展示空表或调整权重会误导用户以为系统故障或产生虚假信心。

**实现**:Learner 在 `min_independent_signal_days` 未满足时返回 `weights = {}`,CLI 检测到空权重时在报告中显示"⏳ 冷启动期:已积累 X/30 个独立信号日"。

### 5.3 Execution 判定

```python
def is_executable(
    bar_next_open: dict,   # 信号日次日开盘 bar
    prev_close: float,
    market: Literal["A", "HK", "US"] = "A",
) -> tuple[bool, str]:
    """
    A 股:
      - suspended == True → (False, "suspended")
      - open >= prev_close * 1.099 → (False, "limit_up_at_open")
      - open <= prev_close * 0.901 → (False, "limit_down_at_open")
      - ST 股阈值改用 5%(±0.05)
    HK/US 暂不限,但保留扩展位。
    """
```

---

## 6. 组合 + 风控

### 6.1 Portfolio

候选池 → 行业去重(单板块 ≤ 30%)→ 相关性去重(60日相关系数 > 0.7 的强制只留得分高的)→ 仓位规划(等权 / 风险平价)。

### 6.2 Circuit Breaker

```python
@dataclass
class CircuitBreakerConfig:
    daily_loss_pct: float = 3.0      # 单日组合浮亏阈值
    weekly_loss_pct: float = 6.0
    monthly_loss_pct: float = 10.0
    cooldown_days: int = 5            # 触发后停止生成新信号的天数
```

熔断状态持久化到 `data/risk_state.json`,通知模板内 banner 显示"组合保护中,本期信号仅供参考"。

---

## 7. 任务拆分(PR 顺序)

每个 PR 不超过 ~300 行变更。小米Pro 按下面顺序提,Claude 逐个审查。

| # | PR 标题 | 依赖 | 验收 | 状态 |
|---|---|---|---|---|
| 1 | core/types + errors + 时区工具 | 无 | 单测 100% | ✅ |
| 2 | data/source 抽象 + akshare 迁移 | #1 | CLI 现有命令不变 | ✅ |
| 3 | data/cache 本地缓存 + adjust 因子表 | #2 | 第二次 fetch 只走缓存 | ✅ |
| 4 | data/sina + eastmoney 备份源 + MultiSource | #2 | akshare 失败时自动降级 | ✅ |
| 5 | data/intraday + realtime quote | #4 | 14:45 班次能拿到当天数据 | ✅ |
| 6 | universe/filters(ST/停牌/新股) | #2 | fixture 验证已知股票被过滤 | ✅ |
| 7 | strategies 拆模块 + thresholds.yaml + version | #1 | 输出与旧版字节级一致 | ✅ |
| 8 | ledger/execution:涨跌停/停牌 + benchmark 修复 | #2 | 一字涨停信号 status="not_executable" | ✅ |
| 9 | ledger/learner v2:聚合+滚动+样本门槛 | #8 | 30 样本以下保持 1.0 | ✅ |
| 10 | regime/detector + 分 regime 学习 | #9 | 趋势/震荡市权重独立 | ✅ |
| 11 | portfolio/correlation + sector 去重 | #7 | top10 输出后再过 portfolio 层 | ✅ |
| 12 | risk/circuit_breaker | #9 | 模拟回撤超阈值 → 通知改 banner | ✅ |
| 13 | reports v2:展示 version、regime、熔断状态 | 上面所有 | 报告能反查阈值版本 | ✅ |
| 14 | walk-forward 回测脚本 + DSR/PBO | #7 #8 | 输出折扣后 Sharpe | ✅ |
| 15 | walk-forward 真实回测:从 ledger 拉真实收益 | #14 | 真实 open/close 价格计算收益,支持手续费/滑点/止损止盈 | ✅ 已通过验收 |
| 16 | 数据基础设施扩容:tencent + mootdx 数据源 | #4 | MultiSource(akshare,[tencent,mootdx,sina,eastmoney]) fallback 真触发 | ✅ |
| 17 | 排雷过滤层 filters_lethal | #6 | LockupRelease/HolderCount/AnnouncementKeyword 三过滤器 + hypothesis 非空 | ✅ |
| 18 | 北向资金 + 融资融券观察因子 | #2 | northbound_flow_5d_z/margin_balance_change_5d 入 ledger,不进评分 | ✅ |
| 19 | 简报生成 briefing 模块 | #13 | jinja2 模板降级 + LLM 可选 + notifier 复用 | ✅ |
| 20 | 监控告警 aqsp monitor | #12 | monitors.yaml 配置 + GitHub Actions 每30分钟 | ✅ |

**P0 三件**(本仓库当前最大风险,Claude 直接修了,不走小米Pro):

- benchmark 000300 拉取没生效 → `excess_return_pct` 永远是 0
- ledger 验证不处理涨跌停/停牌 → 不可成交信号污染胜率
- 14:45 那班拿不到当天日 K → close 模式名不副实

---

## 8. 自我校验(回到核心问题)

任何机制要进项目,先回答这 5 个问题:

1. **策略来源**:经济或市场结构假设是什么?写进 `hypothesis` 字段。
2. **数据消毒**:幸存者偏差、复权口径、停牌涨跌停、财报时间戳是否都对齐?
3. **验证方式**:用 Purged + Embargoed Walk-Forward 或 CPCV,**不要用普通 K 折**。
4. **统计折扣**:试了 N 组参数挑出最好的,Sharpe 必须用 Deflated Sharpe Ratio 折扣。
5. **运行后学习**:学习对象是 IC / 命中率分布 / 特征漂移(KS 检验),**不是 PnL**。漂移触发停用,不触发"调参"。

参考资料:
- López de Prado, *Advances in Financial Machine Learning*, ch.7(Purged CV)和 ch.14(DSR)。
- Bailey & López de Prado, *The Deflated Sharpe Ratio*。
- CPCV 对比:https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4686376

---

## 9. 不允许的操作清单(给小米Pro 看)

- ❌ 写 `datetime.now()` 不带 tz
- ❌ 在策略代码里写字面量阈值
- ❌ 用前复权数据进 ledger / 回测
- ❌ 用 `df.shift(-N)` / 中心化 rolling / `df.mean()` 全期归一化做特征
- ❌ 把 `not_executable` 信号当成"亏损样本"计入胜率
- ❌ 拿事后才知道的指数/板块归类做选股
- ❌ 在 `evaluate` 内部访问磁盘、网络或全局状态
- ❌ 用 LLM 输出直接覆盖打分结果
- ❌ 让权重学习"看到上周亏了就调"(必须有冷却期)
- ❌ 用普通 K 折做时序数据验证

---

## 10. 联系点

- 文档同步:任何改动 `architecture.md` 的 PR 必须包含 `docs:` 前缀,Claude 优先审查。
- 阈值变更:阈值 PR 必须附 walk-forward 回测报告,DSR 折扣后 > 1.0 才允许合并。
- 实盘前清单:见 `docs/checklist_before_live.md`。
