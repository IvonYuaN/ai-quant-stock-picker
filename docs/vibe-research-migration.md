# Vibe-Research 迁移契约

本文档定义 AQSP 向 Vibe-Research 暴露的最小只读桥接契约。bridge 负责实现，验收集中在 `backend/tests/test_aqsp_api.py`；页面不应通过其他入口读取 AQSP 运行时文件。

## HTTP 入口

`GET /api/aqsp/snapshot`

请求参数：

- `date` 可选，格式必须为 `YYYY-MM-DD`。
- 不传 `date` 时读取当前快照；传入日期时必须返回该日期的精确快照，不得静默替换成最新日期。

数据源由以下环境变量指定：

- `AQSP_RESEARCH_SURFACE_SNAPSHOT`：当前快照或日期索引 JSON；索引文件必须与当前快照同目录，并命名为 `home_dashboard_snapshot_index.json`。

成功响应使用 Vibe-Research 现有的 `{"data": ..., "meta": ...}` 包装：

```json
{
  "data": {
    "schema_version": "v1",
    "generated_at": "2026-07-14T09:30:00+08:00",
    "selected_date": "2026-07-14",
    "available_dates": ["2026-07-14"],
    "candidates": [],
    "debates": [],
    "summaries": [],
    "source": {
      "effective": "eastmoney",
      "latest_trade_date": "2026-07-14",
      "lag_days": 0,
      "status": "fresh"
    },
    "coldstart": {"status": "完成", "detail": ""},
    "stale_after": "2026-07-15T09:30:00+08:00",
    "message_status": "未产出",
    "messages": []
  },
  "meta": {"historical": false, "stale": false}
}
```

`debates` 是 Agent 讨论的结构化结果，不是交易指令。空 Agent 必须返回 `[]`，不得伪造 Agent、结论或消息；无消息同样返回 `messages: []`，并保留 `message_status`。

## 错误与日期语义

| 情况 | HTTP | 约束 |
| --- | ---: | --- |
| 当前快照正常 | 200 | `meta.historical=false`、`meta.stale=false` |
| 快照源文件缺失/损坏 | 503 | 不静默降级到网络、ledger 或其他文件 |
| 日期不存在 | 404 | 不回退到其他日期 |
| 日期非法 | 400 | 不接受宽松日期解析 |
| 当前快照超过 `stale_after` | 503 | 当前入口 fail-closed，不返回陈旧数据冒充今日 |
| 显式历史日期已过 TTL | 200 | 允许历史回看，`meta.historical=true`、`meta.stale=true` |

所有 `generated_at`、`stale_after` 和消息 `published_at` 必须是带时区的 ISO 8601 时间戳。历史快照只能作为历史记录展示，不能改变当前日期或当前运行状态。

## 安全与文案边界

- `/api/aqsp/*` 只允许 `GET`；不得新增 `POST`、`PUT`、`PATCH`、`DELETE`。当前只读资源包括快照、日期列表和候选详情。
- AQSP 路由不得暴露 `portfolio` 读写入口或任何持仓写入能力。
- 响应、OpenAPI 摘要和描述不得出现买入、下单等交易动作文案。
- 候选、Agent 讨论、消息和摘要均是研究事实或纸面复核上下文；确定性评分不能被 Agent 输出覆盖。
- 过期数据必须显式失败或标记历史，不能伪装成当前消息。

## 前端类型验收

项目当前没有前端测试运行器，迁移类型约束放在 `frontend/src/lib/aqsp-contract.ts` 和同目录编译期样例中。安装依赖后执行：

```bash
cd frontend
npm ci
npm exec tsc -b --pretty false
```

该检查覆盖正常快照及消息/Agent 为空的合法形状，不修改任何页面实现。

## 独立端口部署演练

当前仓库的部署边界是：

```text
浏览器 -> Vite preview 127.0.0.1:5899 -> /api 代理 -> FastAPI 127.0.0.1:8900
                                      -> /api/aqsp/* -> aqsp_bridge -> AQSP_RESEARCH_SURFACE_SNAPSHOT
```

`frontend/dist/` 是 Vite 的构建产物。当前 `backend/app.py` 没有挂载
`StaticFiles` 或 `frontend/dist`，所以生产演练不能声称“由 FastAPI 提供前端”；
本地/独立端口模式由 `vite preview` 提供静态页面，FastAPI 只提供 `/api/*`。
Vite 的 preview 配置会复用 `/api` 代理到 `127.0.0.1:8900`，因此构建后的页面仍保持同源 API 路径。

先准备依赖并构建：

```bash
cd frontend
npm ci
npm run build
cd ..
```

AQSP 快照必须显式指向当前快照或日期索引。生产服务器示例：

```bash
export AQSP_RESEARCH_SURFACE_SNAPSHOT=/opt/aqsp/data/runtime/home_dashboard_snapshot.json
```

若使用日期索引，索引文件必须与当前快照同目录，并命名为
`home_dashboard_snapshot_index.json`。不要把临时快照、ledger、`.env` 或密钥提交到仓库。

启动演练不会触碰公网服务：

```bash
AQSP_RESEARCH_SURFACE_SNAPSHOT=/opt/aqsp/data/runtime/home_dashboard_snapshot.json \
  scripts/start_vibe_research.sh
```

脚本默认使用 `127.0.0.1:5899` 和 `127.0.0.1:8900`；健康进程会复用，未知进程占用端口会失败，
不会 kill、重启或覆盖已有服务。仅检查已经运行的本地进程：

```bash
AQSP_RESEARCH_SURFACE_SNAPSHOT=/opt/aqsp/data/runtime/home_dashboard_snapshot.json \
  scripts/check_vibe_research.sh
```

当前生产配置 `deploy/nginx/aqsp-dashboard.conf` 仍是 AQSP Streamlit 入口：

- `location /` 反代到 `127.0.0.1:8501`；不能改成 `5899` 或 `8900` 作为本次演练的一部分。
- `/_stcore/stream` 和 `/_stcore/health` 继续走 `8501`。
- `/dashboard*`、`/dist/dashboard*`、`/beginner*`、`/agent*`、`/agents*` 和归档 HTML 旧入口继续 `302` 到根路径。
- 这份公网规则没有 Vibe-Research 的新 host/path 路由；独立端口检查应使用 `127.0.0.1`，不得 reload Nginx 或覆盖公网服务。
