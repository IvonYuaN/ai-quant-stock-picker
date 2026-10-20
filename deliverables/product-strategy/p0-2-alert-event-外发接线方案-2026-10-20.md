# P0-2 该卖提醒 · 外发接线方案（Q5=B 定稿 · 2026-10-20）

> 老大已拍板 **Q5=B（外发）**，并提示"已有外部 push 的接口和通知"。
> 核实码库后确认：**外发基建现成且已投产，Q5=B 不是"从零建推送服务"，是把 alert_event 触发接到现成 notifier**。
> 工时从路线图的"+3~5 人日基建"下调为 **~1.5 人日接线**。

## 一句话结论
`alert_event` 触发 → 复用现成 `notification_runtime`（去重/状态机）+ `notifier`（10 通道）外发；
**不新建任何推送服务/通道/重试基建**，只做"触发源接线上 + 去重键扩展到 per-(symbol,rule)"两件事。

## 二、接线数据流（全用现成件）
```
盘中风控(LIVE 入口)  对实盘持仓跑 stop_loss/dynamic_stop/unified_risk
      │  产生该卖信号 + basis_json（可解释依据，无依据不入库）
      ▼
alert_event 落库（OPEN）  唯一新增数据模型（M0 唯二之一）
      │  should_send_notification(kind, markdown, state_path)  ← 现成去重
      │  reserve → notify_markdown_via_config(...) → mark_sent/failed  ← 现成状态机
      ▼
notifier 10 通道外发（env 驱动，配了哪个走哪个）
   summary: serverchan / wechat / bark / pushplus / telegram
   full    : feishu / dingtalk / discord / slack / generic_webhook
```
生产侧 `DINGTALK_WEBHOOK_URL`/`WECHAT_WEBHOOK_URL`/`SERVERCHAN_SENDKEY`… 配了哪个，
`configured_notification_channels()` 就路由到哪个 ⇒ **接线不用挑通道，跟着生产配置走**。

## 三、三个关键设计决策（附码库依据）
1. **外发零新建**：`send_notification`/`notify_markdown_via_config`（`notifier.py:154/114`）+
   `notification_runtime`（`should_send_notification:59` + `reserve/mark_sent/mark_failed`）
   已用于门禁通知、monitor 告警，投产验证过 ⇒ alert 直接复用，不写新推送层。
   分段（`notify_chunk.split_markdown`）、失败不续发、钉钉关键词、业务错误解析全部现成。
2. **去重键要扩展（真改动点）**：现有 `should_send_notification` 是 **per-kind 一条指纹**
   （`sent[str(kind)]`），而 freq_mode 三档要 **per-(symbol, rule)**。
   ⇒ P0-2 把 state 结构扩为 `sent[str(kind)][f"{symbol}|{rule_key}"] = entry`，
   `freq_mode` 落到 TTL 语义：`once`=永驻 / `daily`=TTL 至收盘线 / `always`=不写 sent（每次都发）。
   这是**唯一要写的新代码**（去重维度 + TTL），~0.5 人日。
3. **风控走独立 LIVE 入口（PRD 7.3-1）**：`stop_loss.py`/`dynamic_stop.py`/`unified_risk.py`
   现挂在回测路径上，接实盘须新开 `mode=LIVE`（或新函数），**实盘持仓不进回测统计、
   回测时间轴不污染实时阈值**。这是 P0-2 的主体改动（`portfolio/manager.py:816`
   由"展示字段"改"告警触发源"），~1 人日。

## 四、唯一待老大确认（信息性，非设计决策）
生产侧 `notifier` 实际启用的是**哪个通道 env**（钉钉 / 微信 / ServerChan / Bark / PushPlus /
Telegram / …）？
- 只影响"alert 外发到哪个 summary/full 通道"，**不影响接线代码**（代码跟随 `configured_notification_channels()`）。
- 若**一个都没配**：alert 自动降级站内（`alert_event` OPEN + 首屏红点）+ 控制台日志，不报错、不阻塞 M0。

## 五、工时修订
| 项 | 原估（假设无现成通道） | 修订（复用现成 notifier） |
|---|---|---|
| 外发推送基建 | +3~5 人日 | **0（已投产）** |
| 去重键扩展 + freq_mode TTL | — | ~0.5 人日 |
| 风控 LIVE 入口接实盘持仓 | 1~2 人日 | ~1 人日 |
| **Q5=B 净增** | +3~5 人日 | **~1.5 人日** |
