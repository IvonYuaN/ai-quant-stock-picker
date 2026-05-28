# scripts/

来源：[simonlin1212/astock-peg](https://github.com/simonlin1212/astock-peg) 仓库的 `scripts/` 目录。
按 CONSTITUTION §7.1 的"借鉴方式"原文照抄进来作 PR16 的补做。

## 三个脚本

| 文件 | 用途 | 入口 |
|---|---|---|
| `collect_stock_data.py` | 一键采集单只 A 股全量数据（行情/财务/估值），输出 JSON 到 stdout | `python3 collect_stock_data.py 688017` |
| `resolve_ticker.py` | 中文股票名 → 6 位代码解析（mootdx 全市场映射） | `python3 resolve_ticker.py 贵州茅台` |
| `detect_sector.py` | 板块/概念识别（百度股市通） | `python3 detect_sector.py 600519` |

## 使用约束（不要忘）

1. 这些脚本是**离线工具**，不属于 aqsp 主链路。它们的输出仅用于：
   - 人工核对（"这只股属于什么板块"）
   - PR 评估时的临时数据采集
2. 不进 aqsp 主依赖，不进 ledger，不进 walk-forward。
3. 改动这些脚本不需要走宪法 PR 模板自查（因为不影响选股决策）。
4. 如果某个采集逻辑后续要进主链路，必须按 aqsp DataSource 抽象重写并加 `hypothesis` 字段。

## 与主项目的边界

- 这些脚本依赖 mootdx + urllib，无 akshare 依赖，可以独立运行。
- 输出 JSON 字段命名沿用 astock-peg 的中文 key，不强制对齐 aqsp 英文 schema。
- 如需在 aqsp 内消费这些数据，请在 `src/aqsp/data/` 下新建 adapter 转换字段名。

## 来源版本

抄入日期：2026-05-27
对应 astock-peg commit：见 https://github.com/simonlin1212/astock-peg/commits/main