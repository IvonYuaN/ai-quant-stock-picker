# P0 日线覆盖门禁绕过路径只读复核

> **性质**：只读复核。未修改任何代码/测试/配置，未提交 git，未连服务器，未跑重型测试，未启动服务或浏览器；唯一写入文件为本文 `docs/data-coverage-bypass-audit.md`。
> **复核日期**：2026-11-06
> **基准 commit**：`922809b714d8c52fbbc2b41529acb3b3775cbb89`（`fix(data): 不完整日线不得当作完整市场日（P0 fail-closed）`）
> **本文所有 `文件:行号` 均指 `git show 922809b7:<file>` 输出的行号**（commit 对象，不受工作树脏改影响）。
> **工作树差异声明**：复核期间 `git status` 显示队友并行写入 `backend/app.py`、`frontend/src/lib/api.ts`、`backend/factor_*.py`、`backend/strategy_backtests.py`、`frontend/src/pages/{Research,Strategy}WorkbenchPage.tsx` 等 14 个 `M` 文件及 `backend/tests/test_lifecycle_and_configs_api.py`、`docs/tickflow-merge-audit.md`、`uv.lock` 等未跟踪文件。这些改动**均非 P0 改动**，本文一律以 `922809b7` commit 对象为准，未把它们计入门禁或绕过结论。
> **上游证据**：`docs/data-refresh-readiness.md`（本机 5,738 票逐票 `MAX(trade_date)` 分布）、`docs/tickflow-merge-audit.md` §2.4。

---

## 0. 结论摘要

- **绕过路径：4 条「真正会作为市场级 as-of / 等价使用」的直接绕过**，另有 **6 条条件缺口或同类风险**（合计登记 10 条）。4 条直接绕过全部是**独立于 `get_latest_daily_date()` 的裸 `SELECT MAX(trade_date) FROM daily_qfq`**，完全不受 `daily_coverage.py` 覆盖门槛约束，其中 2 条在**生产 runner 日更链 / 生产 freshness 口径**内。
- **最严重 3 条**：
  1. `scripts/analysis/ic_diagnosis.py:43-51` 的 `_as_of()`（裸 MAX 作 IC 诊断窗口右端），被 `ic_dual_verdict.py:39` import 复用、由 `ic_diagnosis_runner.sh:78,88,124,133` 在生产 runner 调度；
  2. `scripts/data/check_data_freshness.py:42-47,114-115` 的 `_db_max_trade_date()` + `stale` 判定（裸 MAX 当「数据自身最新日」），且 `backend/app.py:4197,4258` 明确以它为生产判据口径；
  3. `src/aqsp/data/sqlite_db_source.py:408-435` `get_liquid_symbols()` —— **与守卫同一个文件内的第二处裸 MAX**，被 `src/aqsp/universe/runtime.py:137-155` 的收盘主链调用。
- **门禁结论**：**在「`get_latest_daily_date()` 的 9 处生产调用」这条链上成立且完整**（逐项核对全部 fail-closed，无 fallback 日期、无静默降级为默认值）；但**作为「全仓数据就绪守卫」不成立**——存在 4 条独立裸 MAX 路径完全绕过它，且**写入侧**（`refresh_sqlite_batch.py` 当日快照分支）未设防、仍是 `000001.SZ` 哨兵行的高度可疑制造者。守卫只封住了「读侧 10 个消费者」这一个口子。
- **配置预设**：仓库内（`.env.example`、`deploy/`、`.github/workflows/`、`docker-compose.yml`、`scripts/`）**没有**把 `AQSP_MIN_MARKET_DAILY_COVERAGE` 或 `AQSP_RAW_PARTIAL_COVERAGE_MIN_RATIO` 预设成低值，默认 `0.8` / `0.98` 生效；但两个门槛函数都允许运行时 env 显式设成任意 `(0, 1]` 小数（含 `0.0001`）来放宽。

---

## 1. 复核范围与实际搜过的模式

在 `922809b7` 全 tree（`src/`、`backend/`、`scripts/`、`deploy/`、`.github/`、`.env.example`、`docker-compose.yml`、`tests/`、`docs/`）实际执行：

```bash
# 模式 1：直接 SQL 取最新交易日
git grep -n -E 'MAX\(trade_date\)|MAX\( *trade_date|ORDER BY trade_date|order by trade_date' 922809b7 -- .
# 模式 2：日期取数 API 聚合
git grep -n -E 'get_available_daily_dates|get_latest_daily_dates|sessions\[-1\]|available_days|market_sessions' 922809b7 -- .
# 模式 3：状态文件 / 门槛 / 覆盖判定
git grep -n -E 'update_stamp|db_max_trade_date|market_max_date|max_trade_date|eligible_coverage_pct|coverage_pct' 922809b7 -- .
# 模式 4：门槛 env 全仓（含工作树，Grep 工具）
#   AQSP_MIN_MARKET_DAILY_COVERAGE | AQSP_RAW_PARTIAL_COVERAGE_MIN_RATIO
# 模式 5：配置文件清单
git ls-tree -r --name-only 922809b7 | grep -E '^\.env|docker-compose|^deploy/|^\.github/workflows/'
# 模式 6：调用方追踪
git grep -n 'get_liquid_symbols|check_data_freshness|ic_diagnosis|auction_benchmark' 922809b7 -- .
```

**逐文件下钻**（`git show 922809b7:<file> | awk` 取行范围）：`src/aqsp/data/sqlite_db_source.py`、`src/aqsp/data/daily_coverage.py`、`src/aqsp/data/daily_universe.py`、`src/aqsp/universe/runtime.py`、`src/aqsp/monitor/checker.py`、8 个消费者文件、`backend/daily_data_refresh.py`、`backend/daily_data_refresh_worker.py`、`scripts/data/refresh_sqlite_batch.py`、`scripts/data/update_sqlite_daily.py`、`scripts/data/check_data_freshness.py`、`scripts/analysis/ic_diagnosis.py`、`scripts/analysis/write_home_snapshot.py`、`scripts/runner/run_production_walkforward_gate.py`、`scripts/runner/runner_gate.sh`、`scripts/runner/runner_sync.sh`、`backend/app.py:3289-3330,4190-4270`、`backend/auction_benchmark.py`。

---

## 2. 绕过路径（按严重度排序）

### A 类：直接绕过（独立取市场级 as-of，门禁管不到）—— 4 条

| # | 严重度 | 文件:行号（922809b7） | 实际写法与用途 | 是否作为市场级 as-of | 判定 |
|---|---|---|---|---|---|
| A1 | **高** | `scripts/analysis/ic_diagnosis.py:43-51`（`_as_of`）、`:54-65`（`_distinct_dates`）；`scripts/analysis/ic_dual_verdict.py:39`（`from ... import _as_of, _distinct_dates, run`）；`scripts/analysis/ic_diagnosis_runner.sh:78,88,124,133` | `_as_of()` 执行 `SELECT MAX(trade_date) FROM daily_qfq`（`mode=ro`），docstring「窗口右端由它截断，永不写未来」；`_distinct_dates()` 用 `WHERE trade_date <= as_of ORDER BY trade_date DESC LIMIT ?`。`ic_diagnosis_runner.sh` 是 **runner 侧日常调度版**（`TIMEOUT_SEC=1800`，成功写 `IC_READY`），并调用 `ic_dual_verdict.py`（双窗 B 窗「截至库 MAX(trade_date)」） | **是**。窗口右端 as-of 直接由裸 MAX 决定，无横截面覆盖门槛；单票 `000001.SZ` 把 MAX 拉到 20261102 时，IC 诊断/双窗判决会在只有 1 只票有数据的「截面」上计算 | **绕过**。生产 runner 日更链，完全独立于 `get_latest_daily_date()`；`ic_dual_verdict.py:39` 显式 import 复用该 `_as_of` |
| A2 | **高** | `scripts/data/check_data_freshness.py:42-47`（`_db_max_trade_date`）、`:109-115`（`expected`/`actual`/`stale`）、`:66-83`（`_provenance_note`）；`backend/app.py:4197,4258` | `_db_max_trade_date()` 执行 `SELECT MAX(CAST(trade_date AS TEXT)) FROM daily_qfq WHERE trade_date != 'SKIP'`；`main()` 用 `actual = _db_max_trade_date(...)`，`stale = (actual is None) or (actual < threshold)`，并据此写/删 `--flag-file`。`backend/app.py:4197,4258` 明确写「与 `check_data_freshness.py` 的源库 MAX 口径一致（数据自身最新日）」并把 `as_of_b` 落后天数作为双窗 `available` 护栏 | **是**。把全表 MAX 当「数据自身最新日」与最后收盘交易日比对，直接判整个库新鲜/陈旧 | **绕过**。单票把 MAX 刷到当日 → `stale=False` → 判「fresh」；仓库内未见显式调度入口（`git grep check_data_freshness -- scripts .github deploy` 只命中注释/tests/`MIGRATION_GUIDE`），但 `app.py` 已把该口径作为生产判据，需人工确认宝塔 cron。`_provenance_note` 读 stamp 的 `db_max_trade_date` 仅作「推库」诊断注记，不是市场日 |
| A3 | **中高** | `src/aqsp/data/sqlite_db_source.py:408-435`（`get_liquid_symbols`）；`src/aqsp/universe/runtime.py:137-155` | `get_liquid_symbols()` 内 `latest = conn.execute("SELECT MAX(trade_date) FROM daily_qfq")`（`:408-410`），`latest_day` 经 `_parse_db_date` 后作为 `WHERE trade_date = ?`（`:433`）筛选流动性池，并用 `latest_date - timedelta(days=_LIQUID_SYMBOL_LOOKBACK_CALENDAR_DAYS)` 算回看起点。`runtime.py:141-150` 在收盘主链（`source_limit=0` 全市场）调用它，之后才 `_filter_symbols_with_daily_coverage` | **间接是**。不返回日期给消费者，但把裸 MAX 当作「市场横截面基准日」：当前数据下该日只有 `000001.SZ` 有 bar，流动性池会塌缩到 1 只票，等价于「把单票当日当全市场日」 | **绕过，且最值得修**：与守卫**同一个文件**内的第二处裸 MAX，说明 P0 只改了 `get_latest_daily_date()` 一个方法，未横扫同源文件 |
| A4 | **中** | `scripts/runner/run_production_walkforward_gate.py:1163-1171`（`_raw_sqlite_max_trade_date`）、`:1193-1216`（`validate_production_cutoff_consistency`） | `SELECT MAX(trade_date) FROM daily_qfq` 作为 `raw_max`；`if requested_day > raw_max_day: return "exceeds raw sqlite MAX(trade_date)..."`，即**只拒绝超界证据** | 上界校验而非「宣称新鲜」，方向 fail-safe；但单票把 MAX 拉到 20261102 会**放宽允许的上界**，使声称 `end_date ≤ 20261102` 的证据被接受，而该日实际只有 1 只票 | **绕过（放宽上界）**。严重度低于 A1/A2，因为它是拒绝式门禁而非放行式 |

### B 类：条件缺口（让「单票即全市场」得以成立或被放宽）—— 3 条

| # | 严重度 | 文件:行号 | 问题 | 判定 |
|---|---|---|---|---|
| B1 | **中高** | `scripts/data/refresh_sqlite_batch.py:110-153`（`_persist_target_snapshot`）、`:156-190`（`_supplement_current_target`）；`scripts/data/update_sqlite_daily.py:565-574,612-630` | `_supplement_current_target()` 在当日 EOD 后取 `SinaSource().fetch_daily_snapshot()`，经 `_persist_target_snapshot()` 用 `INSERT OR REPLACE INTO daily_qfq(...)` 写入 `target_day` 行：`:121` 只按 `symbol in eligible_symbols` 过滤，**无任何横截面覆盖门槛**；`:140-141` 只要 `rows` 非空就写。`:180` 直接把 `raw_max_trade_date=target_day` 写进 `UpdateSummary`（绕过 `update_sqlite_daily.py:565-574` 那个限定 `ts_code IN (symbols)` 的 `_raw_max_trade_date` 查询），进而进 `last_batch` → cursor → stamp | **`000001.SZ` 哨兵行的高度可疑写入路径**：`docs/data-refresh-readiness.md` 实测 2026-10-01 之后每交易日恰好 1 行且全部 `000001.SZ`，与「当日快照只成功写入 1 只票」的模式吻合。守卫只封读侧，**写入侧未设防**，全表 MAX 仍会被单票持续拉高 |
| B2 | **中** | `src/aqsp/data/sqlite_db_source.py:62-79`（`min_market_daily_coverage_ratio`）；`scripts/analysis/write_home_snapshot.py:1830-1839`（`_raw_partial_coverage_floor`）、`:113`（`DEFAULT_RAW_PARTIAL_COVERAGE_FLOOR=0.98`） | 两个门槛都「显式 env 可放宽」：前者对 `0 < value <= 1` 的任何值直接返回（含 `0.0001`，足以让 1/5728≈0.0175% 通过），非法值（空/非数字/0/负/>1）才 fallback `0.8`；后者 `AQSP_RAW_PARTIAL_COVERAGE_MIN_RATIO` 同理，默认 `0.98` | **仓库内无低值预设**（见 §5），但运行时 env 是设计上的放宽口子；`tests/test_market_daily_coverage_gate.py:98-104` 用 `["", "abc", "0", "-1", "2"]` 验证非法值不放宽，**未测试 `0.0001` 这类合法小值**，说明放宽是有意设计而非疏漏，需作为运维红线登记 |
| B3 | **中低** | `scripts/analysis/write_home_snapshot.py:2156-2190`（`_raw_rebuild_universe_snapshot`） | `covered_symbols` 直接取 cursor JSON 的 `target_day_symbols`（`:2166-2171`），`coverage_pct = len(covered_symbols)/total`（`:2176`）**不查库核验**；`verified_exclusions` 还要求 `batch["raw_max_trade_date"] == target_day`（`:2183`，该值正是 B1 直写的）；`cycle_complete`（`:2188-2189`）据此放行 | 默认门槛 `0.98` 严格且要求 `target_day == latest_completed_trading_day()`（`:2161-2165`），单票 cursor 单独不会通过；但 **B1 单票写入 + B2 把 `AQSP_RAW_PARTIAL_COVERAGE_MIN_RATIO` 设成低值 + `raw_max_trade_date` 直写** 三者组合可让 `cycle_complete=True`。属条件性绕过 |

### C 类：同类缺陷 / 仅诊断（登记但不计为直接绕过）—— 3 条

| # | 严重度 | 文件:行号 | 用途 | 判定 |
|---|---|---|---|---|
| C1 | 低 | `scripts/runner/runner_sync.sh:161-192`、`scripts/runner/runner_gate.sh:102,106` | `runner_sync.sh:169` 用 `SELECT MAX(trade_date) FROM daily_qfq` 作 prod/runner 新鲜度探针（落后 > `AQSP_SYNC_STALE_DAYS` 输出 `[ALERT]`）；`runner_gate.sh:102` 是诊断打印、`:106` 是 `END_DATE ≤ MAX` 上界 | 探针/上界，不宣称「全市场新鲜」；但单票 MAX 会误判同步状态（prod 看似新、runner 看似落后）。`runner_gate.sh` 方向 fail-safe |
| C2 | 低 | `src/aqsp/monitor/checker.py:179-235`（`_check_data_freshness`） | `SELECT MAX(date) FROM ohlcv WHERE symbol != '000300'`（`:209-212`），对象是 **`data/cache.db` 的 `ohlcv` 表**，不是 `daily_qfq`；`lag_days > max_lag_days` 判 `stale_data` | **同类缺陷的另一实例**（命名与 `check_data_freshness.py` 相同但实现独立）：同样用裸 MAX、无覆盖门槛，单票刷新缓存会让检查判 fresh。不在 `get_latest_daily_date()` 链路，但同样应纳入统一守卫 |
| C3 | 低 | `backend/app.py:4194-4197`（`_parse_iso_date`）、`:4250-4259`（双窗 stale 护栏） | 解析双窗产物里的 `as_of_b`（注释称「源库 MAX 口径」），落后 > `DUAL_MAX_LAG_TRADING_DAYS` 时自降 `available=false` + `stale=true` | 值来自**产物 JSON 自报**而非运行时查库；方向是「判陈旧」的 fail-safe，单票 MAX 只会让该护栏**不触发**（弱化），不会主动宣布完整市场日 |

---

## 3. 非绕过判定清单（逐条给路径与用途，避免误报）

| 文件:行号 | 写法 | 判定 |
|---|---|---|
| `src/aqsp/data/daily_coverage.py:62-64`、`:119-123` | 守卫自身读 `SELECT MAX(trade_date)` 后立刻用 `COUNT(DISTINCT ts_code)` / 逐票直方图算覆盖 | **守卫实现**，非绕过；`:40-42` 用 `mode=ro` + `PRAGMA query_only=ON` |
| `src/aqsp/data/sqlite_db_source.py:259-284` | `get_latest_daily_date()` 当前实现：先 `summarize_daily_coverage()`，再 `market_date_is_usable(..., min_coverage_ratio=ratio)`，不通过返回 `None` + warning | **门禁本体**，正确 |
| `src/aqsp/data/daily_universe.py:20-44` | `WITH coverage AS (... GROUP BY trade_date) SELECT MAX(trade_date) FROM coverage WHERE symbol_count >= (SELECT MAX(symbol_count)*ratio FROM coverage)` | **正确实现**：MAX 前先按横截面覆盖过滤，自带门槛，是门禁应向其看齐的范式 |
| `backend/backtest_runs.py:165` | `SELECT MAX(trade_date) ... WHERE ts_code = ? AND trade_date <= ?` | 逐票 + 上界，非市场日 |
| `scripts/data/backfill_akshare.py:126,137` | `HAVING MAX(trade_date) < ? ...` / `WHERE ts_code=?` | 逐票回补筛选 |
| `scripts/data/update_sqlite_daily.py:565-574` | `WHERE ts_code IN (symbols)` 的 MAX | 限定 symbols，非全表；但 B1 会绕过它直写 `raw_max_trade_date` |
| `scripts/data/backfill_index_to_sqlite.py:134`、`import_tdx_vipdoc_to_sqlite.py:89` | `SELECT ts_code, MAX(trade_date) ... GROUP BY ts_code` | 逐票分组，非单一市场日 |
| `scripts/analysis/refresh_variant_results_from_market_db.py:395-407` | `WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 16`，且 `required_symbols = min(MIN_LATEST_DATE_SYMBOLS, len(symbols))` | 逐票 + 最低标的数要求，正确方向 |
| `backend/app.py:3289-3325` | `/api/aqsp/data-coverage`：`verified_market_date = summary.market_max_date if usable else None`（`:3323`），另给 `market_date_usable`、`sentinel_symbols` | 展示守卫，`market_max_date` 字段虽暴露 20261102 但带 `market_date_usable=False` 且 `verified_market_date=None`，非绕过 |
| `check_data_freshness.py:66-83` | `_provenance_note()` 读 `update_stamp.json` 的 `db_max_trade_date` | 仅作「数据来自日更 vs 推库」诊断注记，不是市场日 |
| `scripts/data/refresh_sqlite_batch.py:229-235,241-248` | 主循环 `covered_batch = get_symbols_with_daily_coverage(..., min_coverage_ratio=1.0)` 后才写入 cursor | **DB 核验**，单票不会宣布完成（详见 §6） |
| `backend/strategy_scans.py:193-198`、`strategy_backtest_worker.py:123`、`strategy_backtests.py:147-148` | `get_available_daily_dates(start, end)` 用于「指定日是否在库」或回测回放序列，且外层已由 `get_latest_daily_date()` fail-closed | 区间/单日存在性校验，非 `[-1]` 聚合成市场日 |
| `backend/abnormal_deviation.py:134,191,203,211,390` 的 `sessions[-1]` | 取「实际有 bar 的最后交易日」用于 `_empty_snapshot`（`availability="blocked"`）或计算窗口，外层 `:380-383` 已先 `if latest_data_date is None: raise` | 非绕过：在门禁之后、在已限定的 `as_of` 范围内 |

---

## 4. `get_latest_daily_date()` 消费者 `None` 分支逐项复核

> `git grep -n 'get_latest_daily_date' 922809b7 -- '*.py'` 实证共 **8 个生产文件 / 9 处调用**（team-lead 清单称「10 个消费者」，为约数；另有接口定义 `src/aqsp/data/source.py:134` 与实现 `sqlite_db_source.py:259`）。全部按 commit `922809b7` 版本复核。

| # | 文件:行号 | `None` 时分支 | 是否 fallback 到别的日期 / 静默降级 / 转默认值 | 结论 |
|---|---|---|---|---|
| 1 | `backend/abnormal_deviation.py:380-383` | `if latest_data_date is None: raise DataError("本地 SQLite 历史日线没有有效日期")` | 无 | ✅ fail-closed |
| 2 | `backend/abnormal_history.py:38-41` | `if latest_data_date is None: raise DataError("本地 SQLite 历史日线没有有效日期")` | 无 | ✅ fail-closed |
| 3 | `backend/auction_benchmark.py:80-83` | `if latest is None or latest < trade_date: return items, latest.isoformat() if latest else None`；`get_auction_benchmark():195-206` 把返回值写成 `"returns_as_of": None`、`"returns_source": None` | 无日期 fallback；`items` 原样返回但**不带收益 enrichment**，`state` 仍为 `"ok"`（展示层面轻微静默降级，非日期绕过） | ✅ 日期层面 fail-closed；建议把 `state` 在 `returns_as_of is None` 时标 `degraded` |
| 4 | `backend/daily_data_refresh.py:148,163-173` | `availability="ready"` 要求 `covered_count == eligible_count and latest == expected_day`（`:166`）；`latest is None` 时 `ready` 永不成立，`latest_as_of` 输出 `None`（`:178`） | 无；`ready` 判定额外依赖门禁返回值 | ✅ **守卫生效的正面效果**：不会因单票宣布 ready（但 `elif covered_count` 会给 `partial`，不是 `blocked`，属展示措辞） |
| 5 | `backend/live_risk_service.py:77-84` | `if latest != expected_as_of: actual = latest.isoformat() if latest else "缺失"; return fail_all(actual, ...)` | `None` → `"缺失"` 文案，全部指标 block | ✅ fail-closed |
| 6 | `backend/live_risk_service.py:198-204` | `if latest != expected_as_of: actual = latest.isoformat() if latest else None; block(key, reason, as_of=actual); return snapshots` | `None` → `as_of=None`，逐项 block | ✅ fail-closed |
| 7 | `backend/signal_scans.py:203-212` | `if market_as_of is None or market_as_of < end_date: blockers... return _finish_scan(..., scanned_symbols=0, blockers)` | 无 | ✅ fail-closed（返回 0 扫描 + blockers） |
| 8 | `backend/strategy_backtests.py:144-146` | `if market_latest is None or outcome_as_of > market_latest: raise StrategyBacktestError("结果数据日超出本机 raw SQLite 最新市场日线")` | 无 | ✅ fail-closed |
| 9 | `backend/strategy_scans.py:178-180` | `if market_day is None: raise StrategyScanError("raw SQLite 没有日线数据")` | 无 | ✅ fail-closed |

**结论**：9 处调用**没有任何一处**把 `None` 转成默认日期、fallback 到其它日期、或静默把 `None` 当有效值继续跑。`team-lead` 的「10 个消费者本来都正确处理 None」在这条链上成立。

---

## 5. 门禁配置预设检查

`git grep -n 'AQSP_MIN_MARKET_DAILY_COVERAGE' 922809b7 -- .` 全 tree 仅 7 处命中；`Grep` 工具对工作树全仓搜 `AQSP_MIN_MARKET_DAILY_COVERAGE|AQSP_RAW_PARTIAL_COVERAGE_MIN_RATIO` 结果一致：

| 位置 | 性质 |
|---|---|
| `src/aqsp/data/sqlite_db_source.py:56-57` | 常量定义 `_MARKET_DAILY_COVERAGE_ENV` / `_DEFAULT_MIN_MARKET_DAILY_COVERAGE=0.8` |
| `src/aqsp/data/sqlite_db_source.py:62-79` | 读取函数（见 B2） |
| `scripts/analysis/write_home_snapshot.py:113,1832,1834,1838-1839` | `DEFAULT_RAW_PARTIAL_COVERAGE_FLOOR=0.98` 与 `AQSP_RAW_PARTIAL_COVERAGE_MIN_RATIO` 读取 |
| `tests/test_market_daily_coverage_gate.py:74,82,102` | `monkeypatch.setenv` 测试夹具（`0.2` / `1.5` / 非法值） |
| `backend/tests/test_data_coverage_api.py:15` | `monkeypatch.delenv` 测试夹具 |
| `docs/data-refresh-readiness.md:70`、`docs/tickflow-aqsp-integration.md:10`、`docs/tickflow-merge-audit.md` | 文档说明 |

**核对过的配置载体**（`git ls-tree -r --name-only 922809b7` 清单）：`.env.example`、`.github/workflows/{ci,dry-run,monitor,scheduled-screen}.yml`、`deploy/baota/aqsp-intraday-task.sh`、`deploy/setup.sh`、`deploy/systemd/aqsp-vibe-research.env.example`、`deploy/systemd/*.service`、`deploy/nginx/*`、`docker-compose.yml` —— **`git grep -n -i 'coverage' 922809b7 -- .env.example deploy .github docker-compose.yml` 无任何输出**。

**结论**：仓库内**没有**把 `AQSP_MIN_MARKET_DAILY_COVERAGE` 或 `AQSP_RAW_PARTIAL_COVERAGE_MIN_RATIO` 预设成低值；默认 `0.8` / `0.98` 生效，测试夹具不算配置预设。**但**两个读取函数都允许运行时显式设成任意合法小数（包括 `0.0001`）来放宽，属于设计上的放宽口子，应作为运维红线登记「不得在生产 env 设置该变量」并考虑加审计日志。

---

## 6. 刷新完成判定与 `000001.SZ` 哨兵行来源

**正确的部分（单票不会宣布完成）**：

| 环节 | 文件:行号 | 判定 |
|---|---|---|
| `daily_data_refresh.py:146-184` | `ready` 需 `covered_count == eligible_count and latest == expected_day`（`:166`）；`latest` 来自门禁，现为 `None` ⇒ `ready` 永不成立 | ✅ 不会因单票宣布 ready |
| `daily_data_refresh_worker.py:100-102` | `status = "complete" if coverage >= 1 else "partial"`，`coverage = cursor["eligible_coverage_pct"]` | 依赖下一行 |
| `refresh_sqlite_batch.py:83-85`（`_write_cursor`） | `eligible_coverage_pct = eligible_covered_count / universe_size`，`eligible_covered_count = len(target_day_symbols)` | 分子来自下一行 |
| `refresh_sqlite_batch.py:229-235,241-248` | 主循环 `batch_symbols=covered_batch`，而 `covered_batch = source.get_symbols_with_daily_coverage(batch, target_day, target_day, min_rows=1, min_coverage_ratio=1.0)` —— **从 DB 核验**本批哪些票真在 target_day 有 bar；`universe_size=len(symbols)` 全 universe | ✅ 单票写入时 `eligible_coverage_pct = 1/5738`，远小于 1，worker 只会判 `partial` |

**缺口部分（哨兵行来源与写入侧无门禁）**：

`refresh_sqlite_batch.py:156-190` `_supplement_current_target()` 是另一条写入路径，绕开上述主循环的 DB 核验：

```
:159-164  仅当日 EOD 后触发
:165-172  source = SqliteDbSource(...); eligible = set(_refresh_universe(...))
          inserted_symbols = _persist_target_snapshot(db_path, target_day,
              snapshot=SinaSource().fetch_daily_snapshot(), eligible_symbols=eligible)
:173-174  if not inserted_symbols: return summary
:175-181  updated = replace(summary, ..., raw_max_trade_date=target_day)   ← 直写当日
:182-189  _write_cursor(..., universe_size=len(eligible), batch_symbols=inserted_symbols)
```

`_persist_target_snapshot()`（`:110-153`）对 `snapshot.to_dict(orient="records")` 每行只做 `if symbol not in eligible_symbols: continue`（`:121`），`rows` 非空即 `INSERT OR REPLACE INTO daily_qfq(...)`（`:140-151`）—— **没有横截面覆盖门槛、没有最低标的数校验**。若 `SinaSource().fetch_daily_snapshot()` 当日只成功返回 1 只票（`000001.SZ`），就会在 `daily_qfq` 写入当日唯一一行，并把 `raw_max_trade_date=target_day` 写进 summary → `last_batch`（`:86`）→ cursor → `update_stamp.json`（`update_sqlite_daily.py:612-630`）。

这与 `docs/data-refresh-readiness.md` §2「2026-10-01 之后每个交易日恰好 1 行、`ts_code` 全部是 `000001.SZ`」的实测完全吻合，是**哨兵行的高度可疑写入路径**。注意：本文只做静态只读复核，**未验证** `SinaSource().fetch_daily_snapshot()` 的实际返回内容、未查明写入者身份、未写库、未联网取数；结论为「代码路径可疑」而非「已证实唯一来源」。

**另一条 cursor 消费链**：`scripts/analysis/write_home_snapshot.py:2156-2190` 的 `_raw_rebuild_universe_snapshot()` 直接读 cursor 的 `target_day_symbols` 算 `coverage_pct`（`:2166-2176`，不查库），配合 `:2183` 的 `batch["raw_max_trade_date"] == target_day`（该值由 B1 直写）与 B2 的 env 低值，可组合出 `cycle_complete=True`。默认门槛 `0.98` + `target_day == latest_completed_trading_day()`（`:2161-2165`）使其单独不成立，属条件性风险。

---

## 7. 建议修复顺序（只读审计，不实施）

1. **统一市场日单一事实源**：把 `summarize_daily_coverage()` + `market_date_is_usable()` 抽成全仓唯一入口，A1–A4 四条裸 MAX 全部改为调用它；`get_latest_daily_date()` 只是其中一个便捷封装。**尤其 A3 就在 `sqlite_db_source.py` 同一个文件里**，应优先横扫同源文件。
2. **给 `get_liquid_symbols()` 加覆盖门槛**（A3）：用 `daily_universe.py:20-44` 那个「按 `symbol_count >= MAX(symbol_count)*ratio` 选日」的既有正确范式替换 `:408-410` 的裸 MAX。
3. **IC 诊断 / 双窗链**（A1）：`_as_of()` 改为读通过覆盖门槛的 `market_max_date`；`ic_dual_verdict.py:39` 的 import 会随之生效，无需改双窗逻辑。
4. **freshness 口径**（A2 / C2）：`check_data_freshness.py:_db_max_trade_date()` 与 `checker.py:209` 各自加横截面覆盖判定；`backend/app.py:4197,4258` 的注释与 `as_of_b` 语义同步更新。
5. **写入侧设防**（B1）：`_persist_target_snapshot()` 增加最低标的数/覆盖比例校验，拒绝单票或极低覆盖的当日快照写入；`_supplement_current_target()` 不得在覆盖不足时把 `raw_max_trade_date` 直写成 `target_day`。这是**从根上停止哨兵行继续产生**的关键，优先级不低于 A 类。
6. **门槛 env 收口**（B2）：为 `AQSP_MIN_MARKET_DAILY_COVERAGE` 设合理下限（如 ≥ 0.5）或要求显式二次确认并写审计日志；补一条测试覆盖 `0.0001` 这类「合法但危险」的小值。
7. **诊断项统一**（C1/C3）：`runner_sync.sh` 探针、`app.py` 双窗护栏改用统一的市场日摘要，避免各自读 MAX 给出矛盾结论。
8. **回归验证**（不属本次复核）：`python -m pytest -q tests/test_daily_coverage.py tests/test_market_daily_coverage_gate.py backend/tests/test_data_coverage_api.py`，加隔离真实 API `COVERAGE_GATE=PASS`；真实库 `get_latest_daily_date()` 应仍返回 `None`。

---

## 8. 证据限制

1. 本复核为**静态只读**：基于 `git show 922809b7:<file>` 与 `git grep`，未运行 pytest/ruff/typecheck/build，未连服务器、未写库、未联网取数、未部署。所有「生产链路」判断来自脚本内的调度注释与 import 关系（`ic_diagnosis_runner.sh:78,88,124,133` 等），**未验证宝塔 cron / systemd 的实际启用状态**——尤其 `check_data_freshness.py` 在仓库内无显式调度入口，其生产调用需人工确认。
2. **未验证 `000001.SZ` 哨兵行的真实写入者**：B1 是代码路径上的高度可疑点，但 `SinaSource().fetch_daily_snapshot()` 实际返回内容、盘中快照分支是否真被触发、是否有人工导入，均未核查；`docs/data-refresh-readiness.md` §7 亦列此项为未验证。
3. **未验证 runner / 生产库**：本机结论不外推到 `origin/main` runner 库或生产库（`docs/data-refresh-readiness.md` §7）。
4. **工作树队友并行改动未计入**：`git status` 的 14 个 `M` 文件与 3 个未跟踪文件为队友开发链，本文全部以 commit 对象为准；若随后这些改动提交并改变 `app.py` / `api.ts` 的路由或数据契约，需重跑本文 §1 的搜索。
5. **搜索模式边界**：`git grep` 覆盖文本写法，未覆盖运行时拼接 SQL（如 f-string 动态表名 + `MAX` 变量）、未覆盖 `.pyc`/二进制、未覆盖仓库外部署产物；`ORDER BY trade_date DESC LIMIT 1` 形式的等价写法已通过 `ORDER BY trade_date` 模式扫过，命中的均为逐票或诊断用途（见 §3）。
6. 本文不证明任何策略有效性，也不构成 alpha 或判据结论。
