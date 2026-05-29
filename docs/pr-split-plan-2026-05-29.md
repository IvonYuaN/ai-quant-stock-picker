# PR 拆分计划 — 2026-05-29

> 本文件是纯计划，不涉及任何 git 写操作（禁止 rebase/cherry-pick/force push/新建分支）。

---

## PR-A：数据层

**目标**：数据源抽象、缓存、限速、PIT 财务数据

### commits（按时间顺序）

| hash | 标题 | 需 split？ |
|------|------|-----------|
| `281bb6d` | refactor: split core data universe and ledger modules | 否 |
| `f566645` | fix(data): 为 Sina/Eastmoney/Tencent 数据源添加缓存 + 限速 | 否 |
| `9dd2107` | feat(data): BaostockSource + point-in-time 财务数据 | **是** — 混入了 quality/value 重启（见下方拆法） |
| `ea5629e` | feat(data): SqliteDbSource 直接读本地 SQLite 数据库 | 否 |

### `9dd2107` 拆法建议

该 commit 同时改了：
- `src/aqsp/data/baostock_source.py`（新文件）→ **留 PR-A**
- `src/aqsp/data/pit_financial.py`（新文件）→ **留 PR-A**
- `src/aqsp/data/cache.py`（加 financials 表）→ **留 PR-A**
- `src/aqsp/cli.py`（加 baostock source + 财务合并逻辑）→ **留 PR-A**
- `config/thresholds.yaml`（quality.enabled: true, value.enabled: true）→ **移到 PR-E（本次不规划）**
- `tests/test_strategies.py`（改断言 quality/value enabled=True）→ **移到 PR-E**

**建议**：将 `9dd2107` split 为两个 commit：
1. `9dd2107a`: BaostockSource + pit_financial + cache + cli 集成（→ PR-A）
2. `9dd2107b`: thresholds.yaml enabled flag + 测试断言更新（→ PR-E）

### 文件白名单

```
src/aqsp/data/**
src/aqsp/data/cache.py
src/aqsp/data/baostock_source.py
src/aqsp/data/pit_financial.py
src/aqsp/data/sqlite_db_source.py
src/aqsp/data/sina_source.py
src/aqsp/data/eastmoney_source.py
src/aqsp/data/tencent_source.py
src/aqsp/data/multi_source.py
src/aqsp/universe/**
tests/test_data*.py
tests/test_cache*.py
A股量化分析数据/**
```

### 评审重点

- PIT 语义：`pubDate` vs `statDate` 的使用是否正确
- cache 命中逻辑：financials 表的 TTL 是否合理
- rate-limit：0.05s delay 是否足够
- SqliteDbSource 的 ts_code 转换逻辑

### 不允许包含

- 任何 strategy / threshold 改动
- quality/value enabled flag 翻动

---

## PR-B：新增策略

**目标**：三个新因子策略（VolumeBreakout、MeanReversion、TripleRise）

### commits

| hash | 标题 | 需 split？ |
|------|------|-----------|
| `e2e6bdf` | feat(strategy): VolumeBreakoutStrategy | **是** — 混入了 composite weight 改动 |
| `f242124` | feat(strategy): MeanReversionStrategy + CLI --pool/--horizon-days | **是** — 混入了 CLI 参数和 composite 改动 |
| `70f9d63` | feat(strategy): TripleRiseStrategy + 分级止损 + --pool all + --tiered-stop | **是** — 混入了 backtest 改动和 CLI 改动 |

### 拆法建议

**`e2e6bdf`** split 为：
1. `e2e6bdfa`: `src/aqsp/strategies/volume.py` + `tests/test_volume_strategy.py`（→ PR-B）
2. `e2e6bdfb`: `src/aqsp/strategies/composite.py`（加 volume 集成）+ `src/aqsp/strategies/thresholds.py`（加 VolumeThresholds）+ `config/thresholds.yaml`（加 volume 配置 + 改 weight）→ **PR-C**

**`f242124`** split 为：
1. `f242124a`: `src/aqsp/strategies/mean_reversion.py` + `tests/test_mean_reversion.py`（→ PR-B）
2. `f242124b`: `src/aqsp/cli.py`（加 --pool/--horizon-days）→ **PR-D**（工具脚本）
3. `f242124c`: composite/thresholds/yaml 改动 → **PR-C**

**`70f9d63`** split 为：
1. `70f9d63a`: `src/aqsp/strategies/triple_rise.py` + `tests/test_triple_rise.py`（→ PR-B）
2. `70f9d63b`: `src/aqsp/backtest/walk_forward.py`（`_resolve_exit_tiered`）→ **PR-C** 或独立 PR
3. `70f9d63c`: `src/aqsp/cli.py`（--pool all + --tiered-stop）→ **PR-D**
4. `70f9d63d`: composite/thresholds/yaml 改动 → **PR-C**

### 文件白名单

```
src/aqsp/strategies/volume.py
src/aqsp/strategies/mean_reversion.py
src/aqsp/strategies/triple_rise.py
tests/test_volume_strategy.py
tests/test_mean_reversion.py
tests/test_triple_rise.py
```

### 评审重点

- 是否触动既有策略（momentum/quality/value）的逻辑
- 每个新策略的 `hypothesis` 字段是否非空（宪法 #8）
- 评分范围是否在 [0, 1]

### 不允许包含

- `config/thresholds.yaml` 数值变更
- `src/aqsp/strategies/composite.py` 改动
- 既有策略文件（momentum.py/quality.py/value.py）

---

## PR-C：阈值与 composite

**目标**：composite 集成新因子、weight 调整、分级止损

### commits（从上面拆出的子 commit）

| 来源 | 子 commit | 内容 |
|------|-----------|------|
| `e2e6bdf` | `e2e6bdfb` | composite 集成 volume + VolumeThresholds + yaml volume 配置 |
| `f242124` | `f242124c` | composite 集成 mean_reversion + yaml weight 调整 |
| `70f9d63` | `70f9d63b` | `_resolve_exit_tiered` 分级止损 |
| `70f9d63` | `70f9d63d` | composite 集成 triple_rise + yaml weight 调整 |

### 文件白名单

```
src/aqsp/strategies/composite.py
src/aqsp/strategies/thresholds.py
src/aqsp/backtest/walk_forward.py（仅 _resolve_exit_tiered）
config/thresholds.yaml（仅 weight、min_total_score 等数值）
tests/test_strategies.py
```

### 评审重点

- 每一项 weight 数值变动需对应 walkforward 证据
- 当前 weight: momentum=0.3, quality=0.2(disabled), value=0.2(disabled), triple_rise=0.3
- 分级止损参数：3.1% 硬止损、0-2% 减 10%、>2% 减 20%

### 不允许包含

- `enabled` flag 翻动（那是 PR-E，本次不规划）
- 新策略文件本身（已在 PR-B）

---

## PR-D：CONSTITUTION 与工具脚本

**目标**：宪法修订、CLI 参数、evidence 脚本、报告格式

### commits

| hash | 标题 | 需 split？ |
|------|------|-----------|
| `d687c08` | feat(diagnostics): PR22.5 momentum 方向诊断 | 否 |
| `9a4120d` | fix(backtest): 修复 regime_winrates 为空的 bug | **是** — 混入了 quality/value disable（见下方拆法） |
| 本轮未提交 | CONSTITUTION.md §17 加条 | 否 |
| 本轮未提交 | cli.py 报告格式更新（PASS/FAIL 开头） | 否 |
| 本轮未提交 | pr_evidence.txt | 否 |

### `9a4120d` 拆法建议

该 commit 同时改了：
- `src/aqsp/backtest/walk_forward.py`（regime_winrates bug fix）→ **留 PR-D**
- `config/thresholds.yaml`（quality/value enabled: false）→ **移到 PR-E**

### 文件白名单

```
docs/CONSTITUTION.md
docs/walkforward-*.md
docs/pr-split-plan-2026-05-29.md
scripts/check_pr_evidence.sh
pr_evidence.txt
src/aqsp/cli.py（仅报告格式、CLI 参数解析）
src/aqsp/backtest/walk_forward.py（仅 regime bug fix、DSR 公式）
tests/test_backtest*.py
```

### 评审重点

- §17 新增条目措辞是否一字不改
- DSR 公式返回 z-statistic（不是概率）
- 报告格式 TL;DR 第一行必须 PASS/FAIL 开头

### 不允许包含

- 任何 src/aqsp/strategies/ 改动
- thresholds.yaml 数值变更

---

## PR-E：quality/value 重启（本次不规划，条件见工单 #5）

**条件**：至少一份带 quality_weight/value_weight 的回测新 DSR > 1.0 + PBO < 0.5。

**当前状态**：全部 9 份 FAIL（PBO=75% > 50%），**条件不满足，禁止启动**。

如条件满足，包含：
- `9dd2107b`: thresholds.yaml quality.enabled: true / value.enabled: true
- `9a4120d` 的 thresholds 部分: quality/value enabled: false（如果需要先合入再重启）
- 测试断言更新

---

## 重叠 commit 清单

| commit | 涉及 PR | 处理方式 |
|--------|---------|----------|
| `9dd2107` | A + E | 需 split commit |
| `e2e6bdf` | B + C | 需 split commit |
| `f242124` | B + C + D | 需 split commit |
| `70f9d63` | B + C + D | 需 split commit |
| `9a4120d` | D + E | 需 split commit |

共 5 个 commit 需要 split。其余 commit 各属一个 PR，无重叠。
