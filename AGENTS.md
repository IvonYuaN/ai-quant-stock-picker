# AGENTS.md — 协作与编码硬约束

本项目由仓主(决策)+ 编码 agent(实现)+ 审查 agent(校验)协作。

**所有 agent 在动手前必须先读 `docs/architecture.md`,以那份文件为唯一规划来源。本文件只补编码层面的硬约束。**

---

## 1. 角色分工

- **仓主(决策)**:确定优先级、合并 PR、定义业务边界。
- **小米Pro(编码)**:按 `docs/architecture.md` 拆分的 PR 顺序提交。
- **Claude(审查)**:逐 PR 按本文件第 4 节"审查清单"逐项检查;P0 级别风险性修复也直接动手。

---

## 2. 必读顺序(每次开始工作前)

1. `docs/architecture.md`(项目宪法,边界 + 模块契约 + PR 顺序)
2. `AGENTS.md`(本文件,编码约定)
3. `docs/agent-operating-boundaries.md`(本地/GitHub/服务器/公网职责 + 后台调试边界)
4. 当前 PR 涉及模块的 spec 段落

不读上面三份,不允许动代码。

---

## 3. 编码硬约束

### 3.1 语言与工具链

- Python 3.10+。
- 所有公共函数必须有 type hints。
- 风格:`ruff format` + `ruff check`,CI 卡死。
- 包管理:`pyproject.toml`,不要再加 `requirements.txt`。

### 3.2 命名与结构

- DataFrame 列名统一英文小写下划线,中文列在 `normalize_ohlcv` 入口转一次。
- 错误类型:数据问题 `DataError`,新鲜度 `FreshnessError`,不可成交 `NotExecutableError`,配置 `ValueError`。
- 测试命名:`test_<module>_<behavior>_when_<condition>`。
- commit message:Conventional Commits(`feat:` / `fix:` / `refactor:` / `test:` / `docs:` / `chore:`)。

### 3.3 数据模型

- 公共数据结构优先 `@dataclass(frozen=True)`,不用裸 dict。
- 不允许从函数返回神秘 `None` 表示失败 → 要么抛异常,要么返回 `Optional[T]` 并在文档说明。
- DataFrame 必须有列 schema 注释或在模块顶部声明。

### 3.4 时间

- 全项目禁止裸 `datetime.now()`,统一用 `core/time.py:now_shanghai()`。
- 时间戳序列化必须带时区(ISO 8601 with offset)。
- cron 表达式注释里必须写明"北京时间 X:XX"。

### 3.5 配置 & 阈值

- 策略阈值禁止字面量,必须从 `thresholds.yaml` 注入。
- 任何"魔法数字"(滑点、手续费、止损倍数等)必须有出处或 walk-forward 验证报告链接。
- 阈值 yaml 改动必须更新 `version` 字段并附 PR 说明。

### 3.6 回测 / 学习

- 回测/校验路径必须用 **不复权** 价格 + point-in-time 复权因子;前复权数据只用于展示。
- 不允许 `df.shift(-N)`、中心化 rolling、用全期 mean/std 归一化。
- 任何"自适应/学习"机制必须有最低样本量门槛和冷却期。
- `not_executable` 状态的 ledger 行不进入胜率统计。

### 3.7 CLI & 业务分层

- `cli.py` 只做参数解析 + 调 service 层,不允许写业务逻辑。
- service 层不允许直接 import `akshare`,必须走 `data.source.DataSource` 抽象。

### 3.8 测试

- 每个新模块的 PR 必须带测试。覆盖率 < 80% 的 PR 不合并。
- 单元测试用合成数据;`tests/fixtures/` 放脱敏真实数据用于回归。
- 涉及随机性的测试必须 `random.seed` / `np.random.seed`。

### 3.9 PR 体量

- 单 PR 变更 ≤ ~300 行(测试不计)。超出必须拆。
- PR 描述必须包含:做了什么 / 为什么 / 风险 / 怎么验证。

---

## 4. Claude 审查清单(每个 PR 必过)

```
[ ] 公共接口有完整 type hints,且符合 docs/architecture.md 中契约
[ ] 没有硬编码魔法数字(都在 yaml 或 dataclass 默认值里)
[ ] 没有 look-ahead:无 shift(-N)、中心化 rolling、全期归一化
[ ] 没有裸 datetime.now()
[ ] 测试覆盖正常 + 边界 + 错误三类场景
[ ] 不复权数据走 ledger;前复权只走展示
[ ] 信号写入 ledger 时记录 thresholds.version
[ ] 任何"加权/学习"机制有最低样本量门槛和冷却期
[ ] not_executable 状态正确标记,不污染胜率
[ ] 没有从 evaluate/纯计算函数访问磁盘或网络
[ ] PR 描述完整,变更 ≤ 300 行
[ ] CHANGELOG / docs 同步更新
```

---

## 5. 红线(发现立即拒绝合并)

- 在策略代码里使用未来数据(包括隐式:复权、归一化、index 中心化)
- 把 LLM 输出直接覆盖打分结果
- 引入"看到上周亏损就调参数"的逻辑(违反冻结原则)
- 在 GitHub Actions 跑屏幕截图采集
- 把交易/下单逻辑塞进本仓库(本项目 explicit 不下单)
- 把 secrets / API key 硬编码进代码或 yaml

---

## 6. 提交工作流

```
1. 读 docs/architecture.md 确认 PR 在拆分表的位置
2. 拉新分支 feat/<module-name> 或 fix/<scope>
3. 写代码 + 单元测试
4. 本地跑 ruff + pytest 全绿
5. 提 PR,描述按 §3.9 模板
6. Claude 按 §4 清单审查
7. 仓主合并
```

---

## 7. 状态恢复

如果会话被打断或 agent 失忆:

1. 读 `docs/architecture.md` §7 "PR 顺序" 找当前 PR 位置
2. 读 `git log --oneline -20` 确认最新进度
3. 跑 `pytest` 看现状是否绿
4. 继续下一个 PR

---

## 8. 不要做的事(给小米Pro)

- ❌ 自行扩大 PR 范围(发现额外问题 → 开 issue,不要顺手改)
- ❌ 删除测试或跳过失败的测试
- ❌ 修改 `docs/architecture.md` 的"项目宪法"小节(§1)
- ❌ 引入新的全局状态或单例
- ❌ 提交未运行过的代码
- ❌ 使用 `os.system` / `subprocess.shell=True` 调用外部
- ❌ 引入新的运行时大依赖(>10MB)而不开 issue 讨论
