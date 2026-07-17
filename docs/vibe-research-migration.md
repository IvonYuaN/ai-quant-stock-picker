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

## 正式持久部署

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

正式部署不再使用临时后台进程、`nohup` 或人工保活。仓库提供两个独立
systemd service 和一个 target：

- `aqsp-vibe-research-api.service`：FastAPI，监听 `127.0.0.1:8900`。
- `aqsp-vibe-research-preview.service`：Vite preview，监听 `127.0.0.1:5899`。
- `aqsp-vibe-research.target`：统一启动、停止和开机恢复。

systemd 的 API 和前端日志分别写入：

```text
/opt/aqsp/logs/vibe-research/api.log
/opt/aqsp/logs/vibe-research/frontend.log
```

日志同时带有独立的 `SyslogIdentifier`，也可以用 `journalctl -u` 查询。

### 现网运行时证据

当前 `aqsp-server` 只有 `root` 用户，现有 `/opt/aqsp/.venv/bin/python` 无法导入
`fastapi`，而 `aqsp-dashboard.service` 当前以 `root` 运行。因此不能把已有 dashboard
服务身份或已有 venv 当作 Vibe-Research 运行时；否则 systemd unit 会在启动前就失败。
当前快照 `/opt/aqsp/data/runtime/home_dashboard_snapshot.json` 为 `root:root 600`，隔离用户
默认也无法直接读取。
本仓库的安装脚本默认创建隔离的 `aqsp-vibe` 系统用户和
`/opt/aqsp/.venv-vibe-research`，并在启动前用该用户验证 `fastapi`、`uvicorn`、`aqsp`
和 `aqsp_bridge` 的导入路径。对 600 快照，provision 会要求 `setfacl`，给运行用户添加
文件 `r--` ACL、快照目录 default `r--` ACL 和每级父目录 `--x` ACL；ACL 不可用或验证失败
则中止，不会偷偷改成 root 运行。可用 `getfacl /opt/aqsp/data/runtime/home_dashboard_snapshot.json`
审计结果。default ACL 用于快照被流水线原子替换后仍保持可读。

先执行静态验证，再执行 provision。provision 会创建运行用户和目录、建立独立 venv、安装 `.[api]`，渲染
`User`、`Group`、venv、Node、日志路径和 `PYTHONPATH` 后再安装 systemd unit；不会复用
缺少 `fastapi` 的 `/opt/aqsp/.venv`，也不会修改 Nginx：

```bash
cd /opt/aqsp
scripts/test_vibe_research_deployment.sh
sudo install -d /etc/aqsp
sudo install -m 0640 deploy/systemd/aqsp-vibe-research.env.example /etc/aqsp/vibe-research.env
sudoedit /etc/aqsp/vibe-research.env
sudo scripts/install_vibe_research_systemd.sh \
  --env-file /etc/aqsp/vibe-research.env
```

环境样例无密钥；编辑 `/etc/aqsp/vibe-research.env`，至少确认
`AQSP_RESEARCH_SURFACE_SNAPSHOT` 指向服务器上的可读 JSON 快照或日期索引；该文件不应
包含 API key、Webhook secret 或其它密钥。安装脚本随后安装 Python API 依赖并构建静态产物。

若要显式指定运行时，使用独立路径，不要传入当前缺少 `fastapi` 的旧 venv：

```bash
sudo scripts/install_vibe_research_systemd.sh \
  --user aqsp-vibe \
  --venv-dir /opt/aqsp/.venv-vibe-research \
  --python /usr/bin/python3 \
  --npm /usr/bin/npm \
  --env-file /etc/aqsp/vibe-research.env
```

安装脚本默认执行 `npm ci` 和 `npm run build`；只复用已有构建产物时才使用
`--skip-build`，但仍会检查 `frontend/dist/index.html`。只 provision、不启动可使用
`--no-start`，之后再执行启动脚本。

如果服务器没有 `setfacl`，先安装提供 ACL 工具的系统包，再重跑 provision；等价的最小权限
手工命令如下，路径和用户必须与实际配置一致：

```bash
sudo apt-get update && sudo apt-get install -y acl
sudo setfacl -m u:aqsp-vibe:--x /opt /opt/aqsp /opt/aqsp/data /opt/aqsp/data/runtime
sudo setfacl -m d:u:aqsp-vibe:r-- /opt/aqsp/data/runtime
sudo setfacl -m u:aqsp-vibe:r-- \
  /opt/aqsp/data/runtime/home_dashboard_snapshot.json
sudo -u aqsp-vibe test -r \
  /opt/aqsp/data/runtime/home_dashboard_snapshot.json
```

启动、停止和健康检查都通过仓库脚本调用 systemd，脚本不会按端口杀进程：

```bash
cd /opt/aqsp
sudo scripts/start_vibe_research_service.sh --env-file /etc/aqsp/vibe-research.env
scripts/health_vibe_research.sh --env-file /etc/aqsp/vibe-research.env \
  --systemd-unit aqsp-vibe-research.target
sudo scripts/stop_vibe_research_service.sh
```

需要由脚本构建前端时显式加 `--build`；systemd 重启本身不会偷偷联网安装依赖或重建产物。
启动前预检会验证 Python API 依赖、`frontend/node_modules`、`frontend/dist/index.html`、
快照 JSON 和两个端口。端口已有监听者时直接失败，未知服务不会被覆盖；服务自身异常退出
由 systemd `Restart=on-failure` 恢复。

查看独立日志和状态：

```bash
sudo systemctl status aqsp-vibe-research.target
sudo journalctl -u aqsp-vibe-research-api.service -n 100 --no-pager
sudo journalctl -u aqsp-vibe-research-preview.service -n 100 --no-pager
tail -100 /opt/aqsp/logs/vibe-research/api.log
tail -100 /opt/aqsp/logs/vibe-research/frontend.log
```

### 回滚

回滚只接受本地仓库中已经存在的 Git ref，并要求工作树和暂存区干净。脚本先停止
target，再切换到目标提交并健康检查；目标版本不健康时自动恢复原提交并重启检查，避免
把坏版本留在运行态：

```bash
cd /opt/aqsp
sudo scripts/rollback_vibe_research.sh <已存在的提交或 tag> \
  --env-file /etc/aqsp/vibe-research.env
```

回滚记录只写入 `/opt/aqsp/logs/vibe-research/rollback.log`，不记录环境变量内容。
回滚前应先保存当前 commit；若目标 ref 尚未在服务器本地存在，应先按既有服务器同步流程
获取代码，不在回滚脚本中隐式执行远程操作。

### 本地独立检查

不接 systemd 的本地临时演练仍可使用旧的 `scripts/start_vibe_research.sh`，但正式服务器
不得把它当作持久服务入口。该脚本的作用是前台演练并在退出时清理自己启动的子进程；正式
运行统一使用 `start_vibe_research_service.sh`。

本地只检查已运行服务：

```bash
AQSP_RESEARCH_SURFACE_SNAPSHOT=/opt/aqsp/data/runtime/home_dashboard_snapshot.json \
  scripts/check_vibe_research.sh
```

部署模板的静态验证命令为：

```bash
bash -n scripts/health_vibe_research.sh scripts/start_vibe_research_service.sh \
  scripts/stop_vibe_research_service.sh scripts/rollback_vibe_research.sh
scripts/test_vibe_research_deployment.sh
```

以下是迁移演练期间的历史配置记录，不是当前生产入口。当前生产配置
`deploy/nginx/aqsp-dashboard.conf` 使用 React + FastAPI；下列 Streamlit
配置仅用于回滚演练：

- `location /` 反代到 `127.0.0.1:8501`；不能改成 `5899` 或 `8900` 作为本次演练的一部分。
- `/_stcore/stream` 和 `/_stcore/health` 继续走 `8501`。
- `/dashboard*`、`/dist/dashboard*`、`/beginner*`、`/agent*`、`/agents*` 和归档 HTML 旧入口继续 `302` 到根路径。
- 这份公网规则没有 Vibe-Research 的新 host/path 路由；独立端口检查应使用 `127.0.0.1`，不得 reload Nginx 或覆盖公网服务。

## 根路径切换候选配置

切换候选为 `deploy/nginx/vibe-research-mainline.conf`，目标是同一域名的唯一根
入口，而不是新增第二个公网域名。它与当前 `aqsp-dashboard.conf` 互斥：启用候选
配置前必须保留旧文件为 Streamlit 回滚副本，并确保宝塔 include 目录中只有一份
声明 `location /` 的活动片段。

```text
浏览器 -> lh.ifidy.cn
       -> /api/*             -> 127.0.0.1:8900 FastAPI
       -> /、React history     -> 127.0.0.1:5899 Vite preview
       -> /dashboard* 等旧 URL -> 302 /
```

配置要点：

- `/api` 使用不带 URI 的 `proxy_pass`，因此 `/api/aqsp/snapshot` 等路径不会被
  重写；`/api/health` 作为 Nginx/后端联合健康检查，API 默认关闭缓存。
- 根路径不使用 proxy cache，避免 `index.html` 把一次发布后的切换卡在旧版本；
  `/assets/` 仅使用一小时客户端缓存。`error_page 404 =200 /index.html` 是
  Vite preview fallback 之外的兜底，覆盖 BrowserRouter 的 `/daily-review`、
  `/paper-research`、`/intel` 直达访问。
- `Authorization`、Cookie、`Upgrade` 和 HTTP/1.1 被明确转发。当前后端没有
  WebSocket endpoint，但 `/api/chat` 是 NDJSON 流，配置关闭 proxy buffering，
  防止响应被 Nginx 聚合后才交给浏览器。
- 旧 `dashboard`、`beginner`、`agent`、`agents` 和归档 HTML URL 使用 `302`，
  保留 query string。稳定运行后是否改为 `301` 属于单独变更，不包含在本次候选中。

本次交付只生成候选配置和文档，不执行服务器复制、`nginx -t` 或 reload。正式切换
前后验收命令、鉴权检查和失败回滚步骤以 `docs/server-dashboard-deployment.md`
的“Vibe-Research 根路径切换（候选方案）”为准。
