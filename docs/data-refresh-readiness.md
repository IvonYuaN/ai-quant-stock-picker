# 本机日线数据覆盖诊断与刷新就绪评估

> 只读诊断，2026-11-06 15:20 北京。未修改任何数据库、未启动刷新、未联网取数。
> 本文回答一个具体问题：阶段 A 反复记录的「真实数据验收 freshness=stale」到底卡在哪里。

## 1. 结论先行

**不是「整个库停在 2026-09-30」，而是「5738 只票中只有 1 只被刷新到 2026-11-02」。**

全表 `MAX(trade_date)` 被单只票拉高，造成「库看起来是新的」的假象；实际绝大多数标的的逐票 as-of 停在 9 月甚至 5 月。因此所有依赖「市场级最新日 = 最近完成交易日」的验收都会失败，且失败原因被归因成「数据陈旧」而非「刷新根本没覆盖全市场」。

## 2. 实测证据

库：`A股量化分析数据/astocks_raw.db`，2.4 GB，mtime 2026-11-02 17:59。由 `src/aqsp/data/source_factory.py:24` 的 `resolve_sqlite_db_path()` 解析（优先级：`AQSP_SQLITE_DB_PATH` → `.env` → `/opt/market-data/astocks_raw.db` → 该相对路径）。

| 项 | 实测值 |
|---|---|
| 表 | `daily_qfq`、`stocks` |
| `daily_qfq` 行数 / 标的数 | 14,151,621 / 5,738 |
| `stocks` 标的数 | 5,832 |
| 全表 `MIN/MAX(trade_date)` | 20100104 / 20261102 |
| 列 | `id, ts_code, trade_date, open, high, low, close_qfq, volume, amount, open_qfq, high_qfq, low_qfq, close` |
| 索引 | `idx_daily_qfq_tscode`、`idx_daily_qfq_date` |

**逐票 `MAX(trade_date)` 分布（`SELECT MAX(trade_date), COUNT(*) FROM daily_qfq GROUP BY ts_code`）：**

| 逐票最新日 | 标的数 | 占比 |
|---|---:|---:|
| 20261102 | **1** | 0.02% |
| 20260930 | 2,818 | 49.1% |
| 20260911 | 1,061 | 18.5% |
| 20260529 | 1,327 | 23.1% |
| 更早零散日期 | 531 | 9.3% |

**2026-10-01 之后的全部行**：每个交易日恰好 1 行，`ts_code` 全部是 `000001.SZ`（平安银行），例如 20261030 `close=11.64`、20261102 `close=11.54`。即 10 月之后只有这一只票有连续日线。

这与 `docs/tickflow-aqsp-integration.md` 里「4994/4994 支持标的仍截至 2026-09-30」的记录一致：文档说的是**逐票覆盖**，不是全表 MAX。

## 3. 为什么这会污染新鲜度判定

`src/aqsp/data/sqlite_db_source.py:230` 的 `get_latest_daily_date()`：

```sql
SELECT MAX(trade_date) FROM daily_qfq WHERE ts_code NOT IN (<指数代码>)
```

它排除了指数，但**没有要求该日期有足够多的标的覆盖**。单只 `000001.SZ` 就足以把市场级 as-of 报成 20261102。

消费者 `backend/live_risk_service.py:77`（指标监控）、各类回测前置校验与策略扫描都读这个值。当前（2026-11-06）`expected_as_of` 晚于 20261102，门禁仍是 fail-closed，没有误触发；但一旦那只票被刷到当天，市场级门禁就会宣布「全市场新鲜」，随后逐票校验把 5737 只票判为 `stale`/`missing_data`。结果是**不会发出错误信号，但会把根因误报成逐个标的的问题**，并让「数据已就绪」的假象进入诊断与报告。

已有的逐票守卫在 `get_latest_daily_dates(symbols, as_of)` 与 `get_symbols_with_daily_bars(start, end)`，这些是正确方向；缺的是**市场级日期本身也要有覆盖门槛**。

## 4. 刷新链现状（只读）

- `backend/daily_data_refresh.py:64` 解析同一个库路径；`:98` 用 `start_new_session=True` 起独立 worker，状态与 cursor 落文件。
- `backend/daily_data_refresh_worker.py:55` 启动 `scripts/data/refresh_sqlite_batch.py`，默认参数：`AQSP_DATA_REFRESH_BATCHES=0`（不限批数）、`BATCH_SIZE=120`、`MAX_RUNTIME_SECONDS=480`、`QUERY_TIMEOUT_SECONDS=4`。
- `scripts/data/refresh_sqlite_batch.py` 用 `daily_universe.refreshable_daily_universe()` 取 universe，按 cursor 分批推进，并把 `eligible_coverage_pct` 写进 cursor；`_persist_target_snapshot()` 还有盘中当日快照写入分支。
- worker 终态判定（`daily_data_refresh_worker.py:101`）：`coverage >= 1` 才 `complete`，否则 `partial`。
- **实测 `data/` 与 `backend/data/` 下没有任何刷新状态 / cursor 文件**（`find` 无结果），说明这条链在本机没有留下一次完整运行痕迹。

## 5. 为什么本轮没有启动刷新

1. 目标是 2.4 GB **真实行情库**，刷新会写入数百万行；本机库同时被本地验证、runner 同步规则与生产数据源解析路径引用，误写会扩散。
2. 刷新需要联网取全 universe 行情，耗时与供应商可用性不可控；`--max-runtime-seconds 480` 只是单轮上限，`batches=0` 意味着跑完整个 universe。
3. `docs/agent-operating-boundaries.md` 与项目红线：本机只做开发/验证，重型跑批放 runner；线上/数据变更需仓主授权。
4. 三条并行开发线正在跑定向测试，此时启动重写行情库会让它们的证据漂移。

## 6. 已实施的守卫（2026-11-06，仓主点名后处理）

1. **数据源层 fail-closed（已实施）**：新增 `src/aqsp/data/daily_coverage.py`（只读覆盖摘要 + `market_date_is_usable()`）；`src/aqsp/data/sqlite_db_source.py:230` 的 `get_latest_daily_date()` 不再直接返回 `MAX(trade_date)`，只有最新日覆盖达到 `AQSP_MIN_MARKET_DAILY_COVERAGE`（默认 0.8，必须显式设环境变量才能放宽）才返回该日，否则返回 `None` 并写 warning（含覆盖数、总数、哨兵标的）。**全部 10 个消费者本来都正确处理 `None`**（抛明确错误 / 降级 / blocker），因此一处改动让它们自动全部 fail-closed，零消费者改动。真实库实测：最大日 20261102、覆盖 1/5728（0.0175%）、`get_latest_daily_date()` 返回 `None`。
2. **让覆盖可见（已实施）**：`GET /api/aqsp/data-coverage` 与 `/data` 页新增「日线完整性」卡片，显示最大日、实际覆盖数/总数、覆盖率、哨兵标的、门槛，以及「可作市场日 = 否（已拦截）」。判定走 `frontend/src/lib/data-coverage-view.ts`，不完整时绝不渲染成可用。
3. **回归证据**：Ruff 全仓绿；定向 173 passed（覆盖全部 10 个消费者与新增守卫测试，其中新增门禁回归 20 项、端点 4 项、前端契约 +7 项共 1030 项全过）；前端 typecheck 与 build 通过；隔离真实 API 冒烟 `COVERAGE_GATE=PASS`；隔离无头 Chrome 桌面 1440 与 390px 页面验收 `UI_RESULT=PASS`（横向溢出 0）。
4. **堵住 4 条绕过路径（已实施，2026-11-06 复核后）**：`src/aqsp/data/daily_coverage.py` 新增统一入口 `verified_market_date()`；`scripts/analysis/ic_diagnosis.py::_as_of`（runner 周六 IC cron 在用）、`scripts/data/check_data_freshness.py::_db_max_trade_date`（runner 日常日更在用）、`SqliteDbSource.get_liquid_symbols()` 三处裸 `MAX(trade_date)` 全部改走该入口，覆盖不足时分别抛 `DataError` / 返回 `None` / 返回空列表；`scripts/data/refresh_sqlite_batch.py::_persist_target_snapshot()` 写入侧要求快照覆盖达到同一门槛，单票快照不再能伪造最新市场日。回归测试 `tests/test_market_date_bypass_guards.py` 覆盖 4 条路径与正常全覆盖不误伤。审计证据见 `docs/data-coverage-bypass-audit.md`。**注意**：这些修复只落在本地阶段 A 分支，runner 当前运行的仍是 main 线 `610de287` 代码，需合流发布后才会生效。
5. **仍未执行（需仓主授权）**：① 在 runner 上按 `refresh_sqlite_batch.py` 的 cursor 跑完整 universe 全量回补，核对 `eligible_coverage_pct=1` 与逐票 `MAX(trade_date)` 收敛；② PIT 复权因子覆盖核验；③ 查明并清理 `000001.SZ` 哨兵行的写入者（刷新脚本 / 盘中快照分支 / 人工导入）；④ 数据齐备后重跑阶段 A 的真实数据验收。本轮**没有写库、没有启动刷新、没有联网取数、没有部署**。

## 7. 证据限制

- 本文只诊断**本机** `A股量化分析数据/astocks_raw.db`；`origin/main` 的 runner 库（记忆里 3y `data/astocks_raw.db` MAX=20260930、5267 票）与生产库未核查，不能把本机结论外推。
- 未验证 000001.SZ 单票数据的来源与写入者（刷新脚本、盘中快照分支或人工导入）；需要先查明写入路径再决定是否清理该哨兵行。
- 未验证 PIT 复权因子表 `adjust.py` 的覆盖情况；`pit_cache/` 下只有 announcements / cls_news / concept_board / holder_count 等 CSV，没有可直接核验的因子全量快照。
- 本文不证明任何策略有效性，也不构成 alpha 或判据结论。
