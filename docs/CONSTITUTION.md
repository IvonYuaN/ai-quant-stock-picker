# 项目宪法 — AI 量化选股

> 本文件是项目的 **最高准则**。任何代码、PR、阈值变更与本文件冲突时，本文件优先。
> 修改本文件需要明确标注 "宪法修订"，并写入 §10 修订记录。
> 最后更新：2026-05-27（初版，落地阶段 1）。

---

## 0. 谁在读这份文件

- **你（产品负责人）**：偶尔回顾，确认我没漂移。
- **Claude（审查者）**：每个 PR review 时第一个对照的文档。
- **小米 Pro 多 agent 团队（建造者）**：开 PR 前必读，PR 描述里必须勾选宪法自查。
- **未来加入的协作者 / 团队成员**：onboarding 第一份。

---

## 1. 不可让步条款（16 条核心原则）

这 16 条触碰任何一条 = PR 自动 reject。前 11 条是项目从开始就有的边界，第 12-16 条是 X 路线（你 2026-05-27 拍板）追加的硬约束。

### 1.1 项目方向（来自最初对话）

| # | 条款 | 来源 |
|---|---|---|
| 1 | **不能用事后数据预测可行性**——所有信号必须严格 point-in-time | 你开场原话 |
| 2 | **必须是真实交易思路，不是理想化** | 你开场原话 |
| 3 | **不自动下单、不接券商交易接口** | architecture §1.2 |
| 4 | **核心路径不硬依赖 LLM**——LLM 是增强不是必需，挂掉时必须降级而不是崩 | architecture §1.2 |
| 5 | **不依赖屏幕截图 OCR**——除非确认无 API 可用 | 你最初问的解决方案 |
| 6 | **数据失效要硬报错（fail loud），不能静默降级** | 你强调的"宁错不漏" |

### 1.2 反过拟合纪律（来自小米 Pro 反复翻车的教训）

| # | 条款 | 来源 |
|---|---|---|
| 7 | **冷启动期：前 30 个独立信号日内不调整权重、不展示胜率** | architecture §5.4 |
| 8 | **新因子上线必须有非空 `hypothesis` 字段（事前假设）** | architecture §4 |
| 9 | **walk-forward 训练区间 2018-01 ~ 2024-12，held-out 2025-01 ~ 2026-04 绝对禁止再训** | 这一轮共识 |
| 10 | **当前 v1 仅 A 股**（HK/US 是阶段 2 之后的事，不要混做） | 你的 AskUserQuestion 回答 |
| 11 | **以单人使用为基准设计；多人/SaaS 是阶段 3 之后的事** | 你 2026-05-27 确认 |

### 1.3 X 路线门槛（最严格的推送门）

你 2026-05-27 选了 X 路线（最严格），所以追加：

| # | 条款 | 强制方式 |
|---|---|---|
| 12 | **任何"候选股推送"必须先通过 DSR > 1.0 + PBO < 0.5 双门**，缺一不可 | `cli.run_scheduled` 入口检查；门未达，`--notify` 自动失效 |
| 13 | **门槛未达成期间，T3 简报可以生成但必须头部明示"未经过 walk-forward 验证，仅供观察"** | `briefing` 模块强制注入头部 |
| 14 | **冷启动期 30 个独立信号日 + walk-forward DSR/PBO 是串联门，不是择一** | 两个条件都未满足时 `--notify` 自动 no-op |
| 15 | **DSR/PBO 达成的瞬间，`thresholds.yaml` 的 `version` 必须升号 + `effective_from` 必须填当天**，否则 thresholds 不生效 | `_validate_thresholds_metadata` 启动时检查 |
| 16 | **所有 LLM 调用必须包在可降级 wrapper 里**（参考 §3.2），LLM 异常永远不能冒到 cli return code | code review 强制审查 |

---

## 2. 阶段路线图（防止你和我都忘）

> 你 2026-05-27 同意按"阶段切片"推进，避免一次摊太大。每阶段必须先满足前一阶段的 Definition of Done 才能开下一阶段。

### 阶段 1：A 股 + 单人 / 量化骨架（当前阶段，2026-05 ~ 2026-08 预期）

**目标**：把"反过拟合 + 真实交易"的量化骨架打牢，单人自用。

**范围**：
- 仅 A 股
- CLI 主导，无 Web UI
- 数据采集 + 选股 + 排雷 + ledger + walk-forward 全链路
- 简报生成（T3）+ 监控告警（T4）

**PR 列表**：
- PR16 — 数据基础设施扩容（tencent_source / mootdx_source / 抄 astock-peg 三个 scripts）
- PR17 — T2 排雷过滤层（解禁 / 股东户数 / 公告关键词）
- PR18 — 北向资金 + 融资融券作为观测因子（先入 ledger 不进评分）
- PR19 — T3 简报生成（jinja2 模板降级 + 可选 LLM）
- PR20 — `aqsp monitor` 监控告警

**Definition of Done**：
- ✅ 全部 PR 合并 + py_compile + pytest 通过
- ✅ 真实 walk-forward 跑过（2018-01 ~ 2024-12 训练区间，沪深300 全集）
- ✅ DSR > 1.0 + PBO < 0.5 双门通过 OR 写入 `walkforward-failures.md` 说明为什么没过、下一步怎么改
- ✅ Held-out（2025-01 ~ 2026-04）一次性验收（仅在 walk-forward 通过后允许跑）
- ✅ 阶段 1 不允许做：港股、美股、Web UI、TradingAgents 集成、SaaS 化

### 阶段 2：港股 + 美股 + Web UI 雏形（预期 2026-09 ~ 2027-02）

**目标**：扩大资产覆盖 + 给自己做一个能看的 UI（不是 SaaS）。

**范围**：
- 数据层抽出 region 抽象：`A` / `HK` / `US`
- 港股数据源候选：雪球 / longport（需评估）
- 美股数据源候选：yfinance / alpaca / Alpha Vantage
- Web UI 参考 astock-peg 的 Next.js 架构，但**不直接 fork**，自己重写以保持代码风格统一
- 量化层（aqsp）保持 region-agnostic

**进入门槛**：
- 阶段 1 的 DOD 全部满足
- A 股 walk-forward 真实跑过且 DSR > 1.0
- 至少有 90 天 live 运行 ledger 数据

**Definition of Done**：
- ✅ A 股 + 港股 + 美股三市场数据采集统一接口
- ✅ Web UI 单页能看到：今日候选 / ledger 历史 / walk-forward 报告
- ✅ 港股 / 美股各自跑过一轮 walk-forward
- ✅ 阶段 2 不允许做：用户认证、计费、对外 API

### 阶段 3：开放 / 小团队 / SaaS 化（预期 2027-03 之后，按需）

**目标**：从"自用工具"变成"可分享的产品"。

**范围**：
- 用户认证 + 限流 + 用户自带 LLM key 模式（参考 astock-peg）
- TradingAgents 升级为 T5 增强轨道的核心：多 agent 投研报告作为产品形态之一
- 商业模式探索：免费层 + 付费层

**进入门槛**：
- 阶段 2 的 DOD 全部满足
- 至少 3 人小团队稳定使用 30 天无重大 bug
- 法务确认（投资建议合规边界）

**重新审视宪法**：
- 第 11 条"以单人使用为基准"在阶段 3 必须明确修订
- 第 4 条"不硬依赖 LLM"对 T5 增强轨道是软约束（产品形态依赖 LLM 但量化主链路不依赖）

---

## 3. 系统四轨道架构

阶段 1 就要建立的四轨道。这是把"信号源 → 输出"的链路按反过拟合严格度分级。

### 3.1 四轨道分工

| 轨道 | 名字 | 反过拟合严格度 | 入 ledger | 入 walk-forward | 触发推送 | 失败模式 |
|---|---|---|---|---|---|---|
| **T1** | 量化因子 | 🔴 最严 | 是 | 是 | 通过 X 路线门槛后 | DSR/PBO 不过 → 不上线 |
| **T2** | 排雷过滤 | 🟠 类型 II 错误优先 | 是（status=filtered_out） | 否 | 否 | 错杀 < 漏放 |
| **T3** | 简报解读 | 🟡 给人读 | 否 | 否 | 是（带"未验证"标） | 信息丢失 |
| **T4** | 监控告警 | 🟡 事件驱动 | 否 | 否 | 是（即时） | 漏报警 |
| **T5** | 研报增强 | ⚪ 用户体验 | 否 | 否 | 否（仅展示） | LLM 不可用降级 |

### 3.2 LLM 可降级 Wrapper（强制规范，对应宪法 #16）

**所有** 调用 LLM 的代码必须封装成下面这个形状，否则 reject：

```python
from aqsp.utils.llm_safe import llm_call_or_fallback

result = llm_call_or_fallback(
    prompt=...,
    fallback_template="jinja2/path.j2",
    fallback_context={...},
    timeout_s=30,
    cost_cap_usd=0.05,
)
```

**`llm_call_or_fallback` 的契约**：
- LLM 异常（429 / 5xx / timeout / 余额不足） → 返回 fallback 模板渲染结果
- 单次调用 cost > `cost_cap_usd` → 返回 fallback
- 累计今日 cost > 配置上限 → 返回 fallback
- 不允许 raise 给上游
- 必须记录 `data/llm_calls.jsonl`（监控成本和降级率）

---

## 4. PR 模板（强制）

每个 PR 描述里必须粘贴下面这份，**不打勾的项 = 不审**：

```markdown
## 宪法自查（16 项）
- [ ] 1. 无事后数据
- [ ] 2. 真实交易思路
- [ ] 3. 不自动下单
- [ ] 4. LLM 可降级（核心路径无 LLM 依赖）
- [ ] 5. 不依赖 OCR
- [ ] 6. 数据失效硬报错
- [ ] 7. 冷启动期约束生效
- [ ] 8. 新因子有 hypothesis 字段非空
- [ ] 9. held-out 区间未污染（2025-01 ~ 2026-04 绝对禁止训练用）
- [ ] 10. 当前阶段 = 阶段 1（仅 A 股）
- [ ] 11. 单人体验为基准
- [ ] 12. 候选推送有双门校验
- [ ] 13. 简报头部有"未验证"标（如适用）
- [ ] 14. 冷启动 + DSR/PBO 双门串联生效
- [ ] 15. thresholds.yaml 的 version + effective_from 同步
- [ ] 16. LLM 调用走 llm_call_or_fallback wrapper

## 改动清单
- 文件 X：做了什么
- 文件 Y：做了什么

## 测试覆盖
| 新加/改的代码路径 | 对应的测试函数 |
|---|---|
| ... | ... |

## 反盲点检查（必须真跑，不接受"测试报告说过了"）
- [ ] 端到端 `aqsp run --csv tests/fixtures/...`，return code = ?
- [ ] 端到端 `aqsp walkforward --min-score 0 --symbols ...`，trades > 0
- [ ] 所有新 CLI 参数真传一次，逐一记录命令行
- [ ] 任何回写文件的功能：列出回写前后的 diff

## 多 agent 协作签到
- A-Builder: <agent_id>
- A-Reviewer 已确认: [ ]
- A-Tester 已确认: [ ]
- A-Doc 已确认: [ ]

## 阶段对齐
- [ ] 本 PR 属于阶段 1 范围
- [ ] 不引入阶段 2/3 才允许的能力（港股/美股/Web/SaaS/TradingAgents）
```

---

## 5. 多 Agent 协作纪律

### 5.1 角色分工

| 角色 | 职责 | 不能做的事 |
|---|---|---|
| **A-Builder** | 写功能代码、加测试、跑本地验证 | 不能改 CONSTITUTION.md / architecture.md / thresholds.yaml.version |
| **A-Reviewer** | code review、发现 bug、写 review 评论 | 不能直接改功能代码 |
| **A-Tester** | 端到端 smoke test、覆盖率检查、跑 CLI 真传 | 不能改被测代码 |
| **A-Doc** | 同步 architecture / CHANGELOG / 阶段路线 | 不能改测试用例 |

强制：A-Builder 提交后必须经过 **A-Reviewer + A-Tester 各一道独立确认**才能 merge。这是防止"自写自测自宣布通过"循环的唯一手段（小米 Pro 在 PR1-15 翻车 4 轮的教训）。

### 5.2 Claude 审查者的硬约束（自我约束）

我（Claude）在 PR review 时必须遵守：

1. **凡新 CLI 参数，必须自己手跑一次**，不接受任何"测试报告说过了"
2. **凡新数据源，必须读源码确认 hypothesis 字段非空**
3. **凡回写文件的功能，必须验证文件真被改了**
4. **每轮交付后给"真问题清单"**，不写 "looks good"
5. **不夸**：宁可少说优点也不漂亮话掩盖盲点

---

## 6. 外部依赖白名单（防漂移）

### 6.1 核心依赖（不可降级）

| 依赖 | 用途 | 阶段 |
|---|---|---|
| pandas / numpy | 数据处理 | 全阶段 |
| pyyaml | thresholds 配置 | 全阶段 |
| akshare | A 股数据主源 | 阶段 1+ |
| mootdx | A 股 TCP 备份源 | 阶段 1+ |
| requests | HTTP 数据源 | 全阶段 |
| scipy | walk-forward DSR/PBO 计算（已加入 pyproject） | 阶段 1+ |
| sqlite3 | 本地缓存 | 全阶段 |

### 6.2 可选增强（必须可降级）

| 依赖 | 用途 | 降级行为 |
|---|---|---|
| **Anthropic / OpenAI SDK** | T3 简报、T5 研报增强 | LLM 不可用 → jinja2 模板 |
| **TradingAgents-astock**（参考项目，阶段 3 才接入） | 多 agent 投研报告 | 整个 T5 模块可关 |
| **a-stock-data SKILL** | 端点设计参考 | 不是运行时依赖 |
| **astock-peg scripts** | 数据采集参考 | 抄进项目后可独立运行 |

### 6.3 严格禁止

| 依赖 | 禁止理由 |
|---|---|
| 任何券商交易 SDK（华泰、东方财富交易、Interactive Brokers 等） | 宪法 #3 |
| 屏幕截图 / OCR 库（除非显式确认无 API） | 宪法 #5 |
| 任何需要付费数据订阅的源（万得、聚源、Choice） | 单人项目，免费源已够用 |

---

## 7. 参考项目（作者主页 + 仓库地址）

> 这些项目是阶段 1-3 的参考，但**都不是运行时依赖**。后期更新可以重新拉对应版本评估。

### 7.1 强相关参考（必看）

| 项目 | 仓库地址 | 用途 | 借鉴方式 |
|---|---|---|---|
| **a-stock-data** | https://github.com/simonlin1212/a-stock-data | 28 个 A 股数据端点 SKILL 文档 | 阶段 1：抄端点设计、字段映射、踩坑 FAQ |
| **astock-peg** | https://github.com/simonlin1212/astock-peg | Next.js + AI PEG 估值（单股） | 阶段 1：抄 `scripts/collect_stock_data.py / resolve_ticker.py / detect_sector.py`<br>阶段 2：参考前端架构思路（不直接 fork） |
| **TradingAgents-astock** | https://github.com/simonlin1212/tradingagents-astock | 7 角色多 agent 投研框架 A 股 fork | 阶段 1：参考 3 个 A 股特化角色（政策 / 游资 / 解禁）的 prompt 拆解 → 启发 T2 排雷规则<br>阶段 3：作为 T5 增强轨道核心 |

### 7.2 上游参考（看大方向）

| 项目 | 仓库地址 | 用途 |
|---|---|---|
| **TradingAgents（上游）** | https://github.com/TauricResearch/TradingAgents | 65K star 多 agent 投研框架原版（美股） |
| **akshare** | https://github.com/akfamily/akshare | A 股数据接口库（我们的主数据源） |
| **mootdx** | https://github.com/mootdx/mootdx | 通达信 TCP 协议封装（我们的 fallback） |

### 7.3 学术参考（反过拟合方法论）

- López de Prado, M. (2018). *Advances in Financial Machine Learning* — Walk-forward / Purged CV / Combinatorial Purged CV / DSR / PBO 全部出处
- López de Prado & Lewis (2019). "Detection of False Investment Strategies Using Unsupervised Learning Methods" — DSR/PBO 阈值参考

### 7.4 项目作者关注

> Simon 林（前面三个仓库的作者）是当前阶段最值得关注的对外参考。他的 SKILL 体系设计 + Next.js 前端 + TradingAgents fork 串成了一条完整产品链。

---

## 8. 强制代码门（启动时检查）

阶段 1 必须落地的代码层强制：

### 8.1 启动时检查（`aqsp` 任意子命令第一步）

```python
# src/aqsp/_constitution_check.py
def assert_constitution_invariants():
    """启动时强制检查宪法不变量。任意一条不满足 → SystemExit。"""
    # #15: thresholds.yaml 必须有 version / effective_from / last_walkforward_run
    _check_thresholds_metadata()
    # #4: 不允许在 sys.modules 顶层硬 import LLM SDK
    _check_no_top_level_llm_import()
    # #10/11: 阶段 1 不允许引入港股/美股 module
    _check_phase1_scope()
```

### 8.2 cli.run_scheduled 入口门（对应 #12 / #14）

```python
def _check_notification_gate(args, walkforward_result, ledger_path):
    """X 路线双门校验。未通过则 args.notify 强制设 False。"""
    cold_start_days = _count_independent_signal_days(ledger_path)
    if cold_start_days < 30:
        return False, f"冷启动期: {cold_start_days}/30"
    if not walkforward_result or walkforward_result.deflated_sharpe <= 1.0:
        return False, f"DSR={walkforward_result.deflated_sharpe} ≤ 1.0"
    if walkforward_result.pbo >= 0.5:
        return False, f"PBO={walkforward_result.pbo} ≥ 0.5"
    return True, "通过"
```

未通过时：
- `args.notify = False`（代码层强制）
- 简报头部注入"未验证"标
- ledger 仍然写入（用于继续累积冷启动期数据）

---

## 9. 与 architecture.md 的关系

| 文件 | 角色 |
|---|---|
| **CONSTITUTION.md（本文件）** | 不可让步条款 + 阶段路线 + 协作纪律 |
| **architecture.md** | 模块结构 + 接口契约 + PR 列表细节 |
| **AGENTS.md** | 编码硬规则（魔法数字 / shift(-N) / centered rolling 等） |
| **thresholds.yaml** | 运行时参数 + version/effective_from 元数据 |

冲突时优先级：**CONSTITUTION > architecture > AGENTS > thresholds**。

---

## 10. 修订记录

| 日期 | 修订内容 | 决策人 |
|---|---|---|
| 2026-05-27 | 初版。16 条不可让步条款 + 三阶段路线 + PR 模板 + 多 agent 纪律 + 外部依赖白名单 + 参考项目 | 你（X 路线） + Claude |

---

## 附录 A：紧急情况例外

只有在以下情况允许临时违反某条宪法，**但必须在 24 小时内补齐 PR 修订宪法**：

- 数据源全挂（akshare + mootdx 同时不可用） — 允许临时切第三方
- 监管要求（合规） — 任何政府 / 交易所要求修改
- 安全漏洞 — 数据泄漏 / 凭证泄漏

不属于上述情况但想"临时绕一下"的：reject。

---

## §17 PR 证据规则（不可违反）

任何 PR 提交必须满足：

1. **PR description 中必须包含每个修改文件对应的真实命令输出**：
   - 改了 `tests/`：附 `pytest tests/test_xxx.py -v 2>&1 | tail -30` 的完整输出
   - 改了 `src/aqsp/`：附 `pytest tests/ -q 2>&1 | tail -10` + `ruff check src/ tests/ 2>&1 | tail -5`
   - 改了 `config/thresholds.yaml`：附 `python3 -c "from aqsp.strategies.thresholds import load_thresholds; print(load_thresholds())"` 输出
   - 改了 `scripts/`：附实际脚本运行的 stdout 前 20 行

2. **禁止口头描述**：
   - "测试已通过"、"全部 264 passed"、"All checks passed"、"功能已实现" 这类无具体数字、无命令、无 stdout 的描述视为无效报告
   - 主 agent 收到这类报告必须 reject 并要求重新提交带证据的版本

3. **退出码必须显式**：
   每个证据块结尾附 `echo "exit_code=$?"`，退出码非 0 视为失败

4. **PR 合入前主 agent 必须本机复跑**：
   不允许仅凭子 agent 报告就标记 PR 通过。主 agent 必须自己跑一遍 `pytest tests/ -q` 和 `ruff check src/ tests/`，把自己的输出附到 PR description

5. **违反本条的 PR 视为未提交**：
   即使代码改动有效，证据缺失即驳回，重新走流程

6. **Walkforward 报告格式强制**：
   - 任何 walkforward 报告 TL;DR 第一行必须以 `PASS` 或 `FAIL` 开头，紧跟 DSR / PBO 数值；收益类指标放后面。
   - commit message 引用回测结果时，DSR 与 PBO 必须与收益同行出现，不允许只截取收益。
   - 失败的回测（FAIL）不计入"成绩"，只作研究记录，不允许在 README / 对外材料里引用。