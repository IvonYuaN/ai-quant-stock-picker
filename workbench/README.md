# AQSP Workbench

这是开发 agent 的独立实验区，和正式研究链路隔离。

## 边界

- `workbench/variants/`：实验变体，只允许使用合成输入或脱敏样本。
- `workbench/tests/`：实验变体测试，不被根目录默认 pytest 收集。
- 这里的代码不会被 `src/aqsp`、生产脚本或部署包自动导入。
- 实验结果不能直接写入正式 ledger、通知、快照或候选评分。
- 只有经过正式评审、迁移到 `src/aqsp` 并补齐主链回归测试后，才允许进入生产。

## 运行

```bash
PYTHONPATH=. pytest -q workbench/tests
```

## 当前变体

`variants/event_transmission.py` 是事件传导实验：将可核验的外部事件映射为短线板块观察假设，明确方向、时间窗、证据和置信度。它刻意不输出买卖指令，也不改变正式策略分数。
