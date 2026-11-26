# 长期 Goal 能力地图：可运行的有效策略推荐

目标不是增加更多页面或模型角色，而是形成一条可审计的推荐闭环：在交易日 `t`，系统只能使用 `t` 时刻可取得且有来源时点的数据，产出候选、理由、风险、条件和失败原因；当证据、研究或验证不满足门槛时，明确等待或阻断。

| 模块 | 责任 | 依赖 | 首个可交付 |
|---|---|---|---|
| strategy-contract | 固定推荐对象、策略版本、阈值、适用市场、失效条件和状态语义 | architecture / thresholds | 推荐合同与 fail-closed 门禁 |
| data-integrity | 行情、行业、市场宽度、财务和事件的 PIT、时点、新鲜度、覆盖率 | data sources / calendar | 推荐所需证据的覆盖审计 |
| research-engine | 候选生成、因子计算、研究日期冻结、同日证据绑定 | strategy-contract, data-integrity | 可重放的候选快照 |
| validation-gate | walk-forward、OOS、CSCV/PBO、交易成本、可成交性、样本量和冷却期 | research-engine | 只允许通过门禁的策略版本 |
| runtime-recommendation | daily/intraday 调度、失败状态、候选发布、ledger 和通知 | validation-gate, data-integrity | 交易日可运行的推荐任务 |
| explanation-workbench | 推荐理由、反证、风险、条件、来源、当前状态和未知项 | runtime-recommendation | 前端/通知可读推荐卡 |
| monitoring-feedback | 采用后条件跟进、来源变化、去重、回放和长期噪音统计 | runtime-recommendation, explanation-workbench | 离页监控与真实提醒审计 |
| release-acceptance | 本地测试、CI、制品、服务、公网、真实只读验收和回滚 | all | 可回滚发布闭环 |

构建顺序：

`strategy-contract → data-integrity → research-engine → validation-gate → runtime-recommendation → explanation-workbench → monitoring-feedback → release-acceptance`

并行规则：`strategy-contract`、`data-integrity`、`research-engine` 可并行审计；`validation-gate` 只在三者合同明确后集成；运行、展示、发布按同一版本串行验收。
