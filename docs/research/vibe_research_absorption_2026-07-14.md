# Vibe-Research 吸收记录（2026-07-14）

来源：[simonlin1212/Vibe-Research](https://github.com/simonlin1212/Vibe-Research)，当前公开仓库最新提交 `352d0a4`，版本 `0.1.3`。

## 看到的结构

- `a-stock-data`：把 A 股行情、估值、财报、公告、研报、资金和情绪拆成数据工具箱。
- `global-stock-data`：把美股、港股、韩股和全球指数作为独立跨市场数据层。
- `newsradar.py`：按赛道组织公开 RSS，逐源失败不阻断全局，缓存原子替换，并保留来源统计。
- `market.py`：市场总览使用共享 TTL 缓存，空结果不缓存，避免一次失败污染后续请求。
- `chat.py` / `mcp_server.py`：AI 通过工具读取客观数据，分析出口与数据层分离，并有工具轮次和 SSRF 防护。
- React 路由：每日复盘、资讯、板块、个股、自选和研究记录是独立工作区；首页先给数据总览，再进入 AI 分析。

## AQSP 吸收结果

| Vibe-Research 能力 | AQSP 落点 | 处理 |
| --- | --- | --- |
| A 股多端点目录 | `config/data_sources.yaml`、`aqsp.data.source_catalog` | 已登记为设计参考，不直接复制端点 |
| 美港韩跨市场层 | `aqsp.market_context` | 已有跨市传导；补充时区/发布时间/来源质量门 |
| 源级资讯雷达 | `aqsp.data.news_source`、`aqsp.news.catalysts` | 已吸收源健康、失败可见、当前日和原子字段 |
| 客观数据与 AI 分离 | `briefing` advisory contract | 保持 LLM 不能覆盖确定性评分 |
| MCP / AI 工具出口 | AQSP 现有 Agent 讨论与审计链 | 只吸收 typed tool / provenance 思路，不引入交易执行 |
| React 多页结构 | 当前 Streamlit 两栏工作区 | 吸收页面边界和信息层级，不整体替换已上线看板 |

## 不吸收

- 不复制 broker、下单、自动交易或任何执行接口。
- 不把 Vibe-Research 的中立 AI 输出直接改成 AQSP 的推荐分。
- 不把 108 个 RSS 源一次性放入盘中主链；先做源级健康、限时、当天过滤和 shadow 观察。
- 不引入 FastAPI/React 重构作为当前任务的前置条件，避免再次造成线上入口分叉。

## 下一步

1. 将 `Vibe-Research` 的源级健康字段统一进当前日首页消息卡：成功源、失败源、最新发布时间、抓取时间、有效条数。
2. 将跨市场事实卡统一成“事件 -> 传导路径 -> A 股验证 -> 失效条件”，继续保持 advisory-only。
3. 只在当前数据链路稳定后，再评估是否需要独立 API/MCP 出口；不改变当前两栏看板入口。
