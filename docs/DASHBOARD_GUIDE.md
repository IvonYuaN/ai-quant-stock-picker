# 研究工作台使用指南

## 定位

`src/aqsp/web/dashboard.py` 现在是 **按日期与任务回看的研究工作台**，只展示项目已经落盘的确定性数据：

- `data/ledger.jsonl`
- `data/paper_trades.jsonl`
- `logs/trades/*.jsonl`
- `reports/*.md`

它 **不是** 券商账户终端，也 **不会** 推断真实账户总资产、实时市值、当日盈亏或策略胜率。

## 当前入口

```bash
bash scripts/start_vibe_research.sh
```

本机访问：

```text
http://127.0.0.1:5899
```

API 健康检查：

```text
http://127.0.0.1:8900/api/health
```

## 页面内容

当前页面不是单一候选表，而是由几块职责明确的区域组成：

- 顶部日期导航
  - 先按日期回看，再切换当天的 `盘前主链 / 早盘观察 / 尾盘确认 / 收盘复盘 / 次日预案`
- 决策首页
  - 首屏驾驶舱
  - 今日决策流
  - 同日流程导航
  - 虚拟盘状态
- 候选复盘
  - 单候选深挖
  - 当日候选路径
  - 同日联动与执行证据
- 虚拟盘执行
  - 按标的聚焦的执行工作台
- 归档回看
  - 同日归档中心
  - 执行摘要 / 明日重点 / 运行快照
  - 原始 markdown 正文

如果对应文件不存在，页面会显示空态提示；这代表“没有可展示的运行结果”，不是系统自动补了样例。

## 数据前提

建议先跑主链，再打开看板：

```bash
python -m aqsp run
```

如果还启用了虚拟盘同步，通常也会生成：

- `data/paper_trades.jsonl`
- `reports/paper.md`

如果还产出了简报与复盘，则研究工作台会额外读取：

- `reports/latest.md`
- `reports/briefing-YYYY-MM-DD.md`

## 生产部署

现在有已备案域名时，推荐默认走：

- `dashboard.yourdomain.com` 这类二级域名
- Nginx / Caddy 反向代理
- HTTPS
- 统一登录或 Basic Auth

生产入口由 Nginx/宝塔统一代理：

```bash
前端 -> 127.0.0.1:5899
API   -> 127.0.0.1:8900
```

域名根路径和 React 路由转发到前端，`/api/*` 转发到 FastAPI。不要把旧 `8501`
或 `dist/dashboard/` 配成公网根入口。

最低要求：

- HTTPS
- 反向代理 Basic Auth 或统一登录
- 仅允许可信 IP 或额外 WAF / CDN 访问控制

唯一配置源是 `deploy/nginx/aqsp-dashboard.conf`；它还会将历史
`/dashboard*`、`/beginner*`、`/agent*` 和归档 HTML 路径临时 `302` 到根路径。

### 历史 Streamlit 回滚

`src/aqsp/web/dashboard.py`、`scripts/start_dashboard.sh` 和 `127.0.0.1:8501`
只用于正式入口故障时的临时恢复，不是当前生产入口。恢复验收必须显式使用
`scripts/headless_dashboard_check.py --allow-legacy`，问题排除后恢复 React/FastAPI。
