# 双机交接方案：prod（在线服务） × runner（计算节点）

> 目标：把「重算力」从 2C/1.6G 的生产机彻底剥离，让它只做在线服务；所有离线计算（walkforward gate、证据脚本、数据回填、pytest 全量）迁到另一台机器（下称 runner）。

---

## 1. 为什么要拆

| 事实 | 后果 |
|---|---|
| prod 只有 **2 核 / 1612MB** 总内存，常态仅 ~80MB free | 任何额外任务都可能 OOM |
| stable_plus gate = **8 variant × 19 期 = 152 期**，子进程峰值 RSS ~570MB | prod 属超载运行，实测 ~17 期/小时，全量 ~9h |
| 2026-09-09 在 prod 上跑了一次全表 `GROUP BY`（321 万行） | sshd 直接发不出 banner，失联 2.5h |
| OOM killer 优先杀最大进程 | = 先杀 gate 本身，几小时进度归零 |

结论：prod 不是"慢"，是**没有余量**。继续在 prod 上跑重活，每次都在赌不崩。

---

## 2. 角色划分（硬边界）

| 职责 | prod（8.130.124.238） | runner |
|---|---|---|
| 盘中调度 `*/10` 选股（宝塔 cron） | ✅ **唯一** | ❌ |
| 行情落库 / 写 `astocks_raw.db` | ✅ **唯一写者** | ❌ 只读副本 |
| monitor 判级 / 日报 / 告警 | ✅ **唯一** | ❌ |
| walkforward gate（152 期） | ❌ **禁止** | ✅ |
| 止损/出场证据脚本 | ❌ | ✅ |
| baostock 数据回填 | ❌（与 gate 抢写同一 sqlite） | ✅ |
| pytest 全量 | ❌（占内存） | ✅ |
| dashboard / 用户可见服务 | ✅ | ❌ |

**数据流单向**：`prod --(数据+代码)--> runner --(结果文件)--> prod`。
runner 永远不写 prod 的数据文件，只回传 `report.md` / `walkforward_gate.json` / `gate_summary.md`。

---

## 3. runner 需要什么（一次性搭建）

1. **代码**：不用手动装。`runner_sync.sh` 会把 prod 当前生效 release（**27MB**，排除 `.git`/`node_modules`）同步到
   `$RUNNER_ROOT/releases/<sha>/` 并软链 `aqsp-scheduler-current`。
   > 关键是 **SHA 对齐**：两机跑同一个 release，口径才可比。回传前 `runner_gate.sh` 会打印 SHA 供核对。
2. **venv**（约 337MB，建议自建而非拷贝，避免 glibc/路径差异）：
   ```bash
   python3.12 -m venv /opt/aqsp-runner/venv
   /opt/aqsp-runner/venv/bin/pip install -U pip
   cd /opt/aqsp-runner/aqsp-scheduler-current && /opt/aqsp-runner/venv/bin/pip install -e ".[dev]"
   ```
   最小集若装不动：`numpy pandas pyyaml scipy`（gate 走 `--skip-pit-financials --engine builtin`）。
   注意：venv 不装 `streamlit`/`hmmlearn` 也能跑 gate（后者会 fallback 到简单规则，与 prod 行为一致）。
3. **数据**：`/opt/market-data/astocks_raw.db`（**502MB**，软链指向 `.rebuild`）。
   由 `runner_sync.sh` rsync 过去，runner 端 `PRAGMA integrity_check` 兜底。
4. **warm cache**（可选，`walkforward_raw_production_cache.db` ~894MB）：带上可让 runner **跳过取数阶段**，只重算 grid，省掉约 1/3 时间。
5. **免密**：把 prod 的 `/root/.ssh/id_ed25519.pub` 加进 runner 的 `~/.ssh/authorized_keys`（单向，prod→runner）。

---

## 4. 日常操作

### 4.1 prod → runner 同步（数据变更后 / 每次跑批前）
```bash
# 在 prod 上（默认值已内置，直接跑即可）
SYNC_CACHE=1 bash scripts/runner_sync.sh
```

> **计算节点现状（2026-09-09 建好）**：`38.147.170.174`，SSH 端口 **31777**（22 拒绝连接），root 登录，
> Ubuntu 22.04 / **4C** / **8G**(可用 6G) / 盘 88G 可用 71G / Python 3.10.12 / rsync 3.2.7 / pypi+github 出网正常。
> 授权的是 prod 的 `/root/.ssh/id_ed25519.pub`，prod → runner 单向免密。
⚠️ **请在盘后落库完成之后执行**，避免 rsync 到写一半的 sqlite（runner 端有 integrity_check 兜底，但脏快照会白跑）。

### 4.2 runner 上跑 gate
```bash
# 在 runner 上
BACK_HOST=root@8.130.124.238 BATCH_SIZE=500 MIN_MEMORY_GIB=2 \
  bash /opt/aqsp-runner/scripts/runner_gate.sh
```
（`--start/--end` 默认留空，让父脚本按 `--lookback-years` 自行推导，与 prod 口径逐位一致；
只有需要指定历史截止日时才显式传，且必须 ≤ 库内 `MAX(trade_date)`，否则父脚本 BLOCK。）

跑完自动回传 `report.md` + `walkforward_gate.json` + `gate_summary.md` 到 prod 的 `/opt/aqsp/data/gate_run/`——
**prod 的 monitor / dashboard 读的就是这个路径，判级链路完全不变**。

### 4.3 定时（可选，prod 彻底解耦）
runner 上挂 cron：**北京时间每周日 02:00**（周一开盘前出结果）
```
0 2 * * 0 /opt/aqsp-runner/scripts/runner_gate.sh >> /opt/aqsp-runner/gate_run/cron.log 2>&1
```

---

## 5. 为什么"在别的机器上跑"结果仍然可信

walkforward gate 是**纯离线确定性计算**，输入只有三样：

1. raw sqlite 行情（`astocks_raw.db`）
2. 代码（`releases/<sha>/`，SHA 对齐）
3. 参数（`--grid-profile` / `--lookback-years` / `--start` / `--end` / batch size 等）

三者一致 ⇒ 输出（Sharpe / 总收益 / PBO / DSR）**在哪台机器跑都一样**（浮点非确定性差异可忽略）。
所以迁移**不引入任何口径偏差**，不需要"重新校准"。

唯一会变的量：`--stream-batch-size`（prod 200、runner 可 500+）与 `--min-memory-gib`。
这两个只影响**速度与内存**，不影响计算语义——但为可复现起见，回传的 `gate_summary.md` 会带上这些参数。

---

## 6. 当前这轮（2026-09-09）怎么处置

- prod 上这轮已跑 **61/152 期（40%）**，3 个 variant 出结果（WF-001 -0.90 / WF-B01 -0.73 / WF-B02 -0.48），
  ETA prod-local ~21:30，超时 deadline 22:47（`--timeout-seconds 36000`，12:47 起算），余量仅 ~1.2h。
- **建议：让它跑完**，拿到首个完整的 8-variant 读数；runner 同时搭建，不打断当前跑批。
- 若中途 prod 再被 OOM/僵死打断：把 warm cache 一起 rsync 到 runner，在 runner 上**复用 cache 续跑**（免取数，约 6h 出结果）——这正是 `SYNC_CACHE=1` 的意义。
- **此后所有重活一律上 runner**，prod 只保留在线服务。

---

## 7. 待补信息（阻塞项）

| 项 | 用途 |
|---|---|
| runner 的 IP / SSH 端口 / 登录用户 | 建同步链路 |
| 是否允许把 prod 公钥写入其 `authorized_keys` | 免密单向同步（否则改密码，无法自动化） |
| CPU 核数 / 内存 / 磁盘剩余 | 定 `BATCH_SIZE`、`MIN_MEMORY_GIB`，判断能否跑 5 年窗口 |
| 能否出网（pip / baostock） | 决定 venv 自建方式，以及能否在 runner 上直接回填 2021-2023 数据 |

---

## 8. 红线（本方案不改变任何既有红线）

- runner 是**只读计算节点**，不得写生产库、不得发通知、不得触发告警。
- 结果回传只写 `/opt/aqsp/data/gate_run/` 下的**结果文件**。
- 门禁读数仍以 **PR + walk-forward 验证**为准，不得因"换了机器"就改策略参数（冻结原则）。
- `secrets` 不同步：runner 只拿最小 env（`PREFILTERED_SYMBOLS` / `AQSP_SQLITE_ALLOW_EMPTY_SYMBOLS` / `PYTHONPATH`），
  **不传 `/opt/aqsp/.env`**（含通知 token 等）。
