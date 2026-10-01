"""AQSP 后端 —— A股数据层 HTTP 接口（FastAPI）。

端点全部在 /api 下，前端 vite 代理 /api → localhost:8900。
行情接口按用户传入代码返回客观数据；持仓、研报和 RSS 缓存保存在本地。不预置标的、不下单、不建议。

启动：
    uvicorn app:app --host 127.0.0.1 --port 8900
"""

from __future__ import annotations

import json
import os
import time as _time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
import structlog

# 配置结构化日志（应用启动时）
# 检查 aqsp 包是否可用
try:
    from aqsp.core.logging import configure_logging
    configure_logging()
    logger = structlog.get_logger(__name__)
    logger.info("backend_started", version="0.1.3")
except ImportError:
    # aqsp 包未安装，回退到标准日志
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    logger.info("Backend started (aqsp package not available, using stdlib logging)")

import astock
import aqsp_bridge
import chat as chat_layer
import cli_runtime
import gstock
import metrics
import newsradar
import portfolio as pf
import market
import myreports as mr
import performance_bridge
import tenant as _tenant

app = FastAPI(
    title="AQSP API",
    version="0.1.3",
    description="""
# AQSP —— A 股量化选股 API

A 股数据层 HTTP 接口，提供行情、财务、资讯、持仓管理及量化研究功能。

## 功能模块

- **持仓管理**：本地持仓记录与实时盈亏计算
- **行情数据**：实时行情、K线、指数快照
- **财务数据**：财务指标、估值分位、一致预期
- **资讯雷达**：12 赛道公开 RSS 资讯聚合
- **市场情绪**：连板梯队、板块资金流、全球指数
- **事件日历**：解禁预警、龙虎榜、停复牌、业绩预告
- **资金面**：融资融券、大宗交易、主力资金流
- **AQSP 研究**：量化选股快照只读接口（需独立 aqsp 包）
- **AI 对话**：系统 AI 对话流式接口

## 数据源

- 行情：腾讯财经、东方财富、同花顺
- 财务：akshare、mootdx（可选依赖）
- 资讯：公开 RSS 源
- 研究：本地 AQSP runtime 快照

## 注意事项

- 行情接口按用户传入代码返回客观数据，不预置标的、不下单、不建议
- 持仓、研报和 RSS 缓存保存在本地
- 公网模式需配置 `VR_API_KEY` 环境变量
    """,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "系统", "description": "健康检查与版本信息"},
        {"name": "持仓管理", "description": "本地持仓记录、盈亏计算与已实现收益"},
        {"name": "我的研报", "description": "用户上传研报的本地存储与管理"},
        {"name": "行情", "description": "实时行情、K线、指数快照"},
        {"name": "财务", "description": "财务指标、估值分位、一致预期"},
        {"name": "资讯", "description": "个股新闻、公告、研报"},
        {"name": "资讯雷达", "description": "12 赛道公开 RSS 资讯聚合"},
        {"name": "市场情绪", "description": "连板梯队、板块资金流、成交额榜"},
        {"name": "全球市场", "description": "全球指数、美港股行情"},
        {"name": "事件日历", "description": "解禁预警、龙虎榜、停复牌、业绩预告、分红"},
        {"name": "资金面", "description": "融资融券、大宗交易、主力资金流、股东户数"},
        {"name": "板块概念", "description": "个股板块归属、热门概念"},
        {"name": "互动易", "description": "投资者问答"},
        {"name": "AQSP 研究", "description": "量化选股快照只读接口"},
        {"name": "AI 对话", "description": "系统 AI 对话流式接口"},
    ],
)

# 每半小时后台刷新持仓数据（仅本地/私有模式：单用户，tenant 恒为 local）。
# 公网/鉴权模式（设了 VR_API_KEY）下多用户各自有独立租户目录，全局调度器无法
# 靶向单个用户，关掉它，改由前端「手动刷新」按需重算盈亏。
if not os.environ.get("VR_API_KEY", "").strip():
    pf.start_scheduler(1800)

# 本地自托管默认开放；设置 VR_API_KEY 或 VR_PUBLIC_MODE=1 即进入公网模式。
_API_KEY = os.environ.get("VR_API_KEY", "").strip()
_PUBLIC_MODE = bool(_API_KEY) or os.environ.get(
    "VR_PUBLIC_MODE", ""
).strip().lower() in {"1", "true", "yes", "on"}


def _cors_origins(raw: str | None, public_mode: bool) -> list[str]:
    """Parse CORS origins; public mode never permits a wildcard origin."""
    origins = [origin.strip() for origin in (raw or "").split(",") if origin.strip()]
    if public_mode:
        return [origin for origin in origins if origin != "*"]
    return origins or ["*"]


# 公网模式不接受通配符 CORS。未配置白名单时仍支持同源前端，但拒绝跨源浏览器请求。
_raw_origins = os.environ.get("VR_ALLOW_ORIGINS")
_ORIGINS = _cors_origins(_raw_origins, _PUBLIC_MODE)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,
)


@app.middleware("http")
async def _require_api_key(request: Request, call_next):
    is_api = request.url.path.startswith("/api/")
    is_health = request.url.path == "/api/health"
    is_metrics = request.url.path == "/metrics"

    # Prometheus /metrics 端点不需要鉴权（内网监控专用）
    if is_metrics:
        return await call_next(request)

    if (
        _PUBLIC_MODE
        and not _API_KEY
        and request.method != "OPTIONS"
        and is_api
        and not is_health
    ):
        return JSONResponse(
            {"detail": "公网模式未配置 VR_API_KEY，接口已拒绝服务"},
            status_code=503,
        )
    if _API_KEY and request.method != "OPTIONS" and is_api and not is_health:
        if request.headers.get("authorization", "") != f"Bearer {_API_KEY}":
            return JSONResponse(
                {"detail": "未授权：缺少或错误的 API Key（VR_API_KEY）"},
                status_code=401,
            )
    # 解析租户（X-User-Id > API Key 哈希 > local），让不同用户的数据目录互相隔离
    tid = _tenant.resolve_tenant_id(request.headers.get("x-user-id", ""), _API_KEY)
    token = _tenant.current_tenant.set(tid)
    try:
        return await call_next(request)
    finally:
        _tenant.current_tenant.reset(token)


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    """记录所有 HTTP 请求的指标（请求数、响应时间）。"""
    # /metrics 端点本身不记录指标，避免递归
    if request.url.path == "/metrics":
        return await call_next(request)

    # 简化端点路径（将路径参数替换为占位符，避免高基数）
    endpoint = request.url.path
    for pattern in [r"/api/myreports/file/", r"/api/myreports/", r"/api/aqsp/candidate/", r"/api/aqsp/candidates/"]:
        if pattern in endpoint:
            # 简化为模板路径
            parts = endpoint.split("/")
            if len(parts) > 3 and parts[-1] and parts[-1] not in ["refresh", "close", "holding"]:
                endpoint = "/".join(parts[:-1]) + "/{id}"
            break

    method = request.method
    start_time = _time.time()

    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        status_code = 500
        raise
    finally:
        duration = _time.time() - start_time
        metrics.http_requests_total.labels(
            method=method,
            endpoint=endpoint,
            status_code=status_code
        ).inc()
        metrics.http_request_duration_seconds.labels(
            method=method,
            endpoint=endpoint
        ).observe(duration)

    return response


_CODE_RE = r"^\d{6}$"


@app.get("/metrics", tags=["系统"])
def prometheus_metrics():
    """Prometheus 指标端点（文本格式）。

    返回所有已注册的 Prometheus 指标，供 Prometheus Server 抓取。
    此端点不需要鉴权，适用于内网监控环境。
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _validate(code: str) -> str:
    code = (code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "代码必须是 6 位数字")
    return code


def _release_sha() -> str:
    """部署时由 runner_sync.sh 在 release 根目录写入 RELEASE_SHA。

    用于部署自检：运行中 API 返回的 SHA 应 == 刚部署的 commit，否则就是
    『部署了但旧代码在跑』的静默事故（09-21 曾因重启早于切链导致）。缺失时
    返回 "unknown"（不报错，避免影响 health 探测）。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    sha_file = os.path.join(os.path.dirname(here), "RELEASE_SHA")
    try:
        with open(sha_file, encoding="utf-8") as fh:
            return fh.read().strip() or "unknown"
    except OSError:
        return "unknown"


def _radar_cache_dir() -> str:
    """与 backend/newsradar._resolve_cache_dir 保持一致（避免 import 重依赖）。"""
    env = os.environ.get("VR_RADAR_CACHE_DIR", "").strip()
    if env:
        return env
    data_dir = os.environ.get("VR_DATA_DIR", "").strip()
    if data_dir:
        return os.path.join(data_dir, "radar")
    return os.path.expanduser("~/.vibe-research/radar")


def _radar_freshness() -> dict:
    """雷达缓存新鲜度（防御式，失败返回 present=false 不影响 health）。"""
    try:
        cache_file = os.path.join(_radar_cache_dir(), "radar.json")
        with open(cache_file, encoding="utf-8") as fh:
            data = json.load(fh)
        return {
            "present": True,
            "generated_at": data.get("generated_at"),
            "industries": len(data.get("industries") or []),
        }
    except Exception:
        return {"present": False, "generated_at": None, "industries": 0}


def _gate_freshness() -> dict:
    """生产 gate 判定新鲜度（防御式，找不到文件返回 present=false）。"""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(repo_root, "data", "walkforward_gate.json"),
        "/opt/aqsp/data/walkforward_gate.json",
        os.path.expanduser("~/.vibe-research/walkforward_gate.json"),
    ]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return {
                "present": True,
                "verdict": data.get("verdict"),
                "generated_at": data.get("generated_at"),
                "updated": data.get("updated"),
            }
        except Exception:
            continue
    return {"present": False, "verdict": None, "generated_at": None, "updated": None}


@app.get(
    "/api/health",
    tags=["系统"],
    summary="健康检查",
    description="""
返回服务健康状态、版本信息及关键数据新鲜度。

- `ok`: 服务是否正常运行
- `version`: API 版本号
- `release_sha`: 部署时的 Git commit SHA（用于部署自检）
- `radar`: 资讯雷达缓存新鲜度
- `gate`: 生产 gate 判定新鲜度
    """,
    response_description="服务健康状态与元信息",
)
def health():
    return {
        "ok": True,
        "service": "aqsp-api",
        "version": "0.1.3",
        "release_sha": _release_sha(),
        "radar": _radar_freshness(),
        "gate": _gate_freshness(),
    }


@app.get(
    "/api/version",
    tags=["系统"],
    summary="版本信息",
    description="返回 API 版本号与部署 commit SHA",
)
def api_version():
    return {"service": "aqsp-api", "release_sha": _release_sha(), "app_version": "0.1.3"}


class LLMConfig(BaseModel):
    provider: str = ""  # cli-* = 订阅接入（调本机 CLI）；其余 = API 接入
    baseURL: str = ""  # 订阅接入时留空
    apiKey: str = ""  # 订阅接入时留空
    model: str


class ChatReq(BaseModel):
    messages: list[dict]
    context: str = ""
    llm: LLMConfig


@app.post(
    "/api/chat",
    tags=["AI 对话"],
    summary="系统 AI 对话（流式）",
    description="""
系统 AI 对话，返回 **流式** NDJSON（每行一个事件 {type: tool|delta|done|error}）。

## 接入方式

- **API 接入**：OpenAI 兼容 function-calling，边流答案边推工具调用事件
- **订阅接入**（provider=cli-*）：调本机已登录的 CLI，stdout 边出边流

## 错误处理

- 配置错误（缺 key / 未装 CLI）：HTTP 400
- 运行时错误：流内 error 事件

用户配置随请求传入，后端不持久化。
    """,
)
def chat(req: ChatReq):
    """系统 AI 对话，**流式** NDJSON（每行一个事件 {type: tool|delta|done|error}）。

    - API 接入：OpenAI 兼容 function-calling，边流答案边推工具调用事件。
    - 订阅接入（provider=cli-*）：调本机已登录的 CLI，stdout 边出边流（数据靠 context）。
    配置错误（缺 key / 未装 CLI）走 HTTP 400；运行时错误走流内 error 事件。用户配置随请求传入，后端不持久化。
    """
    if not req.messages:
        raise HTTPException(400, "messages 不能为空")
    if not req.llm.model:
        raise HTTPException(400, "缺少模型配置，请先在「接入 AI」里选择")

    is_cli = req.llm.provider.startswith("cli-")
    if is_cli:
        kind = req.llm.provider[4:]
        if not cli_runtime.detect_cli(kind):
            raise HTTPException(
                400,
                f"未检测到「{kind}」对应的本机命令。请先安装并登录该 CLI，或改用「API 接入」。",
            )
    elif not req.llm.apiKey or not req.llm.baseURL:
        raise HTTPException(400, "缺少 Base URL 或 API Key，请先在「接入 AI」里填写")

    cfg = req.llm.model_dump()

    def gen():
        try:
            events = (
                chat_layer.run_chat_cli_stream if is_cli else chat_layer.run_chat_stream
            )(cfg, req.messages, req.context)
            for ev in events:
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:  # noqa: BLE001 — 运行时错误以流内事件上报，不中断连接
            yield (
                json.dumps(
                    {"type": "error", "message": f"对话失败：{e}"}, ensure_ascii=False
                )
                + "\n"
            )

    return StreamingResponse(gen(), media_type="application/x-ndjson")


class HoldingIn(BaseModel):
    code: str
    shares: float
    cost: float


@app.get(
    "/api/portfolio",
    tags=["持仓管理"],
    summary="获取持仓与实时盈亏",
    description="""
返回本地持仓列表及实时盈亏计算。

- 持仓数据存储在本地，按租户隔离
- 实时拉取最新行情计算浮动盈亏
- 浮动盈亏采用红涨绿跌显示
    """,
)
def portfolio_get():
    """持仓 + 实时盈亏（浮动盈亏红涨绿跌）。"""
    try:
        return {"data": pf.get_portfolio()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"持仓读取异常：{e}") from e


@app.post(
    "/api/portfolio/holding",
    tags=["持仓管理"],
    summary="添加持仓",
    description="""
添加一笔持仓记录。

- 同代码多次添加会按加权平均成本合并
- 数据存储在本地，不上传到服务器
- 成本价支持负数（融券/返息/摊薄等场景）

**参数：**
- `code`: 6位股票代码
- `shares`: 持仓数量（必须大于0）
- `cost`: 成本价（支持负数）
    """,
)
def portfolio_add(h: HoldingIn):
    """加一笔持仓（同代码按加权平均成本合并）。存本地，不上传。"""
    code = (h.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "代码必须是 6 位数字")
    if h.shares <= 0:
        raise HTTPException(400, "数量必须大于 0")
    # 成本价不限正负：融券 / 返息 / 摊薄后为负成本等情形按结果计算，用户想怎么输就怎么输。
    return {"data": pf.add_holding(code, h.shares, h.cost)}


@app.delete(
    "/api/portfolio/holding",
    tags=["持仓管理"],
    summary="删除持仓",
    description="删除指定代码的持仓记录",
)
def portfolio_remove(code: str = Query(..., description="6位股票代码")):
    return {"data": pf.remove_holding(code.strip())}


# ---- 我的研报（用户上传自己的研报，存本地、不上传、不进开源仓库）----


class ReportIn(BaseModel):
    name: str
    content_b64: str


@app.get(
    "/api/myreports",
    tags=["我的研报"],
    summary="研报列表",
    description="获取用户上传的研报列表，包含研报 ID、文件名、行业标签等信息",
)
def myreports_list():
    return {"data": mr.list_reports()}


@app.post(
    "/api/myreports",
    tags=["我的研报"],
    summary="上传研报",
    description="""
上传一份研报并存储到本地。

- 研报内容以 base64 编码传输
- 自动根据文件名打行业标签
- 数据仅存储在本地，不上传到服务器
    """,
)
def myreports_upload(r: ReportIn):
    """上传一份研报（base64）→ 存本地 + 按文件名自动打行业标签。"""
    try:
        return {"data": mr.save_report(r.name, r.content_b64)}
    except mr.ReportError as e:
        raise HTTPException(400, str(e)) from e


@app.get(
    "/api/myreports/file/{rid}",
    tags=["我的研报"],
    summary="下载研报文件",
    description="下载或预览指定 ID 的研报原文件",
)
def myreports_file(rid: str):
    """下载/预览某份研报原文件。"""
    hit = mr.report_path(rid)
    if not hit:
        raise HTTPException(404, "研报不存在")
    path, name = hit
    return FileResponse(str(path), filename=name)


@app.delete(
    "/api/myreports/{rid}",
    tags=["我的研报"],
    summary="删除研报",
    description="删除指定 ID 的研报",
)
def myreports_delete(rid: str):
    return {"data": {"ok": mr.delete_report(rid)}}


class CloseIn(BaseModel):
    code: str
    date: str
    price: float
    shares: float
    cost: float


@app.post(
    "/api/portfolio/close",
    tags=["持仓管理"],
    summary="记录已清仓",
    description="""
记录一笔已清仓的交易，计算已实现盈亏。

- 数据存储在本地
- 清仓价与股数必须大于 0
- 成本价支持负数（融券等场景）
- 已实现盈亏 = (清仓价 - 成本) × 股数

**参数：**
- `code`: 6位股票代码
- `date`: 清仓日期 (YYYY-MM-DD)
- `price`: 清仓价格
- `shares`: 清仓股数
- `cost`: 买入成本价
    """,
)
def portfolio_close(c: CloseIn):
    """记一笔已清仓（已实现盈亏）。存本地。"""
    code = (c.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "代码必须是 6 位数字")
    if c.price <= 0 or c.shares <= 0:
        raise HTTPException(400, "清仓价与股数必须大于 0")
    # 买入成本不限正负（同持仓录入）：按 (清仓价 - 成本) × 股数 的结果计算已实现盈亏。
    date = (c.date or "").strip()
    if not date:
        raise HTTPException(400, "请填清仓日期")
    from datetime import datetime

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "清仓日期格式应为 YYYY-MM-DD") from None
    return {"data": pf.close_position(code, date, c.price, c.shares, c.cost)}


@app.delete(
    "/api/portfolio/close",
    tags=["持仓管理"],
    summary="删除清仓记录",
    description="删除指定索引的已清仓记录",
)
def portfolio_close_remove(index: int = Query(..., description="清仓记录索引")):
    return {"data": pf.remove_closed(index)}


@app.post(
    "/api/portfolio/refresh",
    tags=["持仓管理"],
    summary="手动刷新持仓",
    description="立即重新拉取行情并计算盈亏",
)
def portfolio_refresh():
    """手动刷新：立即重拉行情算盈亏。"""
    try:
        return {"data": pf.get_portfolio()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"刷新失败：{e}") from e


@app.get(
    "/api/radar",
    tags=["资讯雷达"],
    summary="资讯雷达",
    description="""
返回 12 赛道的公开 RSS 资讯聚合。

- 数据从缓存读取
- 无缓存时返回赛道骨架结构
- 包含各赛道最新资讯列表
    """,
)
def radar():
    """资讯雷达：12 赛道公开 RSS 资讯（读缓存，无缓存返回赛道骨架）。"""
    try:
        return {"data": newsradar.get_radar(force=False)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"资讯雷达异常：{e}") from e


@app.post(
    "/api/radar/refresh",
    tags=["资讯雷达"],
    summary="刷新资讯雷达",
    description="""
强制重新抓取全部 RSS 源并更新缓存。

- 耗时约 20-40 秒
- 更新所有赛道的资讯数据
    """,
)
def radar_refresh():
    """强制重抓全部 RSS 源（耗时约 20-40s），更新缓存。"""
    try:
        return {"data": newsradar.fetch_radar()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"资讯雷达刷新失败：{e}") from e


# 新闻催化「事件中枢」：只读 runtime 产物。生产由定时任务
# （scripts/bt_task.sh news → scripts/news_catalysts.sh）写入，消费端与生产端
# 共用同一 env 覆盖点（AQSP_NEWS_JSON_OUTPUT），未配置时回落规范 runtime 路径
# （经 AQSP_PROJECT_ROOT 解析）。报告尚未生成时必须失败降级（返回空 events），
# 不得抛 500。
@app.get(
    "/api/catalyst",
    tags=["资讯雷达"],
    summary="新闻催化事件中枢",
    description="""
读取最新的新闻催化报告。

- 只读 runtime 产物，不生成新数据
- 数据由定时任务生成
- 无数据时返回空事件列表，不报错
- 包含事件详情、生成时间、数据源状态
    """,
)
def catalyst():
    """新闻催化事件中枢（event hub）：读取最新催化报告，无数据则失败降级。"""
    try:
        from aqsp.news.catalysts import (
            load_catalyst_report_artifact,
            serialize_catalyst_report,
        )

        # 与生产者 scripts/news_catalysts.sh 的 JSON_OUTPUT 使用同一 env 覆盖点，
        # 保证「写哪就读哪」，避免生产/消费路径分叉。
        artifact_path = str(
            os.getenv("AQSP_NEWS_JSON_OUTPUT", "")
            or "data/runtime/news_catalysts_latest.json"
        ).strip()
        report = load_catalyst_report_artifact(artifact_path)
        if report is None:
            return {
                "data": {
                    "events": [],
                    "generated_at": None,
                    "source_status": "no_data",
                    "warnings": ["新闻催化报告尚未生成，运行新闻催化采集后可见"],
                }
            }
        return {"data": serialize_catalyst_report(report)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"新闻催化读取异常：{e}") from e


def _events_empty_payload(symbol: str) -> dict:
    """事件日历的 fail-soft 空结构（与正常响应字段一一对应）。"""
    return {
        "symbol": symbol,
        "as_of": "",
        "unlock_horizon_days": 30,
        "longhubang_lookback_days": 5,
        "has_unlock_data": False,
        "has_longhubang_data": False,
        "has_suspend_resume_data": False,
        "has_earnings_forecast_data": False,
        "has_dividend_plan_data": False,
        "upcoming_unlocks": [],
        "recent_longhubang": [],
        "suspend_resumes": [],
        "recent_earnings_forecasts": [],
        "upcoming_dividends": [],
    }


def _events_jsonable(event_map: dict) -> dict:
    """dataclass asdict 里可能混入 NaN（缓存行数值列缺失时特意保留的语义）。

    starlette JSONResponse 用 ``allow_nan=False`` 序列化，NaN 会直接抛 ValueError
    把整个响应打挂 —— 这里把非有限浮点收敛成 ``null``，语义是「未披露」。
    """
    for key, value in event_map.items():
        if isinstance(value, float) and value != value:
            event_map[key] = None
    return event_map


@app.get(
    "/api/events",
    tags=["事件日历"],
    summary="事件日历",
    description="""
返回个股事件日历，包括：

- **解禁预警**：未来 30 天限售股解禁计划
- **龙虎榜**：近 5 日上榜记录
- **停复牌**：停牌与复牌事件
- **业绩预告**：最新业绩预告
- **分红计划**：即将到来的分红方案

## 数据来源

- 只读 pit_cache，不联网
- 数据由 `scripts/preload_event_data.sh` 预加载
- 缺缓存不等于没事件，通过 `has_*_data` 字段区分

## Fail-soft 策略

任何异常返回 200 + 空结构，不返回 500 错误
    """,
)
def events(code: str = Query(..., description="6位股票代码")):
    """事件日历（解禁预警 + 近 5 日龙虎榜）：只读 pit_cache，绝不联网。

    数据由 scripts/preload_event_data.sh 预加载到
    $AQSP_RUNTIME_DATA_ROOT/pit_cache/{lockup,longhubang}.csv；
    缺缓存 ≠ 没事件，用 has_unlock_data / has_longhubang_data 区分。
    fail-soft：任何异常（含 aqsp 未装、缓存损坏）→ 200 + 空结构，绝不 500。
    """
    code = _validate(code)
    try:
        from dataclasses import asdict

        from aqsp.core.time import today_shanghai
        from aqsp.features.event_calendar import EventCalendar

        cal = EventCalendar.from_cache(
            runtime_data_root=os.environ.get("AQSP_RUNTIME_DATA_ROOT")
        )
        as_of = today_shanghai().isoformat()
        return {
            "data": {
                "symbol": code,
                "as_of": as_of,
                "unlock_horizon_days": cal.unlock_horizon_days,
                "longhubang_lookback_days": cal.longhubang_lookback_days,
                "has_unlock_data": cal.has_unlock_data(),
                "has_longhubang_data": cal.has_longhubang_data(),
                "has_suspend_resume_data": cal.has_suspend_resume_data(),
                "has_earnings_forecast_data": cal.has_earnings_forecast_data(),
                "has_dividend_plan_data": cal.has_dividend_plan_data(),
                "upcoming_unlocks": [
                    _events_jsonable(asdict(ev))
                    for ev in cal.upcoming_unlocks(code, as_of)
                ],
                "recent_longhubang": [
                    _events_jsonable(asdict(ev))
                    for ev in cal.recent_longhubang(code, as_of)
                ],
                "suspend_resumes": [
                    _events_jsonable(asdict(ev))
                    for ev in cal.suspend_resumes(code, as_of)
                ],
                "recent_earnings_forecasts": [
                    _events_jsonable(asdict(ev))
                    for ev in cal.recent_earnings_forecasts(code, as_of)
                ],
                "upcoming_dividends": [
                    _events_jsonable(asdict(ev))
                    for ev in cal.upcoming_dividends(code, as_of)
                ],
            }
        }
    except Exception:  # noqa: BLE001 — 事件面缺数据不报错，降级为空结构
        return {"data": _events_empty_payload(code)}


@app.get(
    "/api/market/overview",
    tags=["市场情绪"],
    summary="市场总览",
    description="""
返回市场情绪与板块资金流数据。

- 板块/大盘级数据
- 全站共享缓存 5 分钟
- 包含市场整体情绪指标和板块资金流向
    """,
)
def market_overview():
    """市场情绪 + 板块资金流（板块/大盘级，全站共享缓存 5 分钟）。"""
    try:
        return {"data": market.get_overview()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"市场总览异常：{e}") from e


@app.get(
    "/api/market/emotion",
    tags=["市场情绪"],
    summary="短线情绪",
    description="""
返回短线市场情绪指标，包括：

- 连板梯队个股清单（代码、名称、连板数等）
- 最高连板天数
- 炸板率
- 封板率
- 晋级率
- 涨跌停家数

## 数据说明

- 客观展示东财公开榜单数据
- 只呈现事实，不附推荐/评分/预测/买卖时机
- 全站共享缓存 5 分钟
    """,
)
def market_emotion():
    """短线情绪：连板梯队 / 最高连板 / 炸板率 / 封板率 / 晋级率 / 涨跌停家数。

    含连板梯队个股清单（code/name/连板数等）——2026-07-05 起如实展示客观公开榜单（东财同款），
    只呈现事实，不附推荐/评分/预测/买卖时机。全站共享缓存 5 分钟。
    """
    try:
        return {"data": market.get_short_term_emotion()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"短线情绪异常：{e}") from e


@app.get(
    "/api/market/turnover-top",
    tags=["市场情绪"],
    summary="成交额榜 Top20",
    description="""
返回全市场成交额排名前 20 的个股。

- 客观公开榜单数据
- 非推荐/非预测/不评分
- 全站共享缓存 5 分钟
    """,
)
def market_turnover_top():
    """全市场成交额榜 Top20（客观公开榜单数据，非推荐/非预测/不评分）。全站共享缓存 5 分钟。"""
    try:
        return {"data": market.get_turnover_top()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"成交额榜异常：{e}") from e


@app.get(
    "/api/global/indices",
    tags=["全球市场"],
    summary="全球指数快照",
    description="""
返回全球主要指数的实时行情。

## 包含指数

- 道琼斯工业指数
- 标普 500
- 纳斯达克综合指数
- 恒生指数
- 恒生科技指数

用于观察隔夜外围市场对 A 股的影响。缓存 5 分钟。
    """,
)
def global_indices():
    """全球指数快照（道指 / 标普500 / 纳斯达克 / 恒生 / 恒生科技）—— A 股看隔夜外围脸色。缓存 5 分钟。"""
    try:
        return {"data": market.get_global_indices()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"全球指数异常：{e}") from e


@app.get(
    "/api/global/stock",
    tags=["全球市场"],
    summary="美港股个股行情",
    description="""
返回美股或港股个股的聚合数据。

## 数据内容

- 实时行情
- 关键财务指标

## 参数

- `symbol`: 股票代码（如 AAPL / BABA / 00700）

数据源：东方财富
    """,
)
def global_stock(symbol: str = Query(..., min_length=1, max_length=16, description="美股或港股代码，如 AAPL / BABA / 00700")):
    """美股 / 港股个股聚合：行情 + 关键财务指标（东财域内源）。symbol 如 AAPL / BABA / 00700。"""
    try:
        data = gstock.us_hk_stock(symbol.strip())
        if not data:
            raise HTTPException(404, f"未找到美股/港股代码「{symbol}」")
        return {"data": data}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"美港股查询异常：{e}") from e


@app.get(
    "/api/indices",
    tags=["行情"],
    summary="A股大盘指数行情",
    description="""
返回 A 股主要指数的实时行情。

## 包含指数

- 上证指数
- 深证成指
- 创业板指
- 沪深 300

仅使用标准库，无额外依赖。
    """,
)
def indices():
    """A股大盘指数实时行情（上证/深证成指/创业板指/沪深300）。仅标准库。"""
    try:
        return {"data": astock.index_quote()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"指数行情异常：{e}") from e


@app.get(
    "/api/quote",
    tags=["行情"],
    summary="实时行情",
    description="""
批量获取个股实时行情数据。

## 返回数据

- 现价
- 涨跌幅
- PE（市盈率）
- PB（市净率）
- 市值
- 换手率
- 涨跌停价格

## 参数

- `codes`: 逗号分隔的 6 位股票代码（如 "000001,600519"）

仅使用标准库，永远可用。
    """,
)
def quote(codes: str = Query(..., description="逗号分隔的 6 位代码")):
    """实时行情：现价/涨跌/PE/PB/市值/换手/涨跌停。仅标准库，永远可用。"""
    lst = [c.strip() for c in codes.split(",") if c.strip()]
    if not lst or any(not c.isdigit() or len(c) != 6 for c in lst):
        raise HTTPException(400, "codes 必须是逗号分隔的 6 位数字")
    try:
        return {"data": astock.tencent_quote(lst)}
    except Exception as e:  # noqa: BLE001 — 边界统一兜底
        raise HTTPException(502, f"行情源异常：{e}") from e


_PCT_CACHE: dict = {}
_ANN_CACHE: dict = {}  # key=code -> (ts, data) 个股公告，TTL 15min
_FIN_CACHE: dict = {}  # key=code -> (ts, data) 财务关键指标，TTL 30min


@app.get(
    "/api/valuation/percentile",
    tags=["财务"],
    summary="估值历史分位",
    description="""
返回个股 PE-TTM 和 PB 的历史分位数据（近 5 年）。

- 帮助判断当前估值在历史中的位置
- 全站缓存 30 分钟/代码
- 历史序列为日频数据，变化较慢

**依赖：** 需要安装可选依赖
    """,
)
def valuation_percentile(code: str = Query(..., description="6位股票代码")):
    """PE-TTM / PB 历史分位（近5年）。全站缓存 30 分钟/代码（历史序列日频、变化慢）。"""
    code = _validate(code)
    hit = _PCT_CACHE.get(code)
    if hit and _time.time() - hit[0] < 1800:
        return {"data": hit[1]}
    try:
        data = astock.valuation_percentile(code)
        _PCT_CACHE[code] = (_time.time(), data)
        return {"data": data}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"估值分位异常：{e}") from e


@app.get(
    "/api/announcements",
    tags=["资讯"],
    summary="个股公告",
    description="""
返回个股近期公告列表。

- 数据源：东方财富
- 仅需 requests 库
- 缓存 15 分钟/代码
    """,
)
def announcements(code: str = Query(..., description="6位股票代码")):
    """个股近期公告（东财，仅 requests）。缓存 15 分钟/代码。"""
    code = _validate(code)
    hit = _ANN_CACHE.get(code)
    if hit and _time.time() - hit[0] < 900:
        return {"data": hit[1]}
    try:
        data = astock.announcements(code)
        _ANN_CACHE[code] = (_time.time(), data)
        return {"data": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"公告源异常：{e}") from e


@app.get(
    "/api/financials",
    tags=["财务"],
    summary="财务关键指标",
    description="""
返回个股财务关键指标（最新报告期）。

- 数据源：同花顺财务摘要
- 缓存 30 分钟/代码
- **依赖：** 需要安装可选依赖
    """,
)
def financials(code: str = Query(..., description="6位股票代码")):
    """财务关键指标（同花顺财务摘要，最新报告期）。缓存 30 分钟/代码。"""
    code = _validate(code)
    hit = _FIN_CACHE.get(code)
    if hit and _time.time() - hit[0] < 1800:
        return {"data": hit[1]}
    try:
        data = astock.financials(code)
        _FIN_CACHE[code] = (_time.time(), data)
        return {"data": data}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"财务摘要异常：{e}") from e


@app.get(
    "/api/valuation",
    tags=["财务"],
    summary="完整估值",
    description="""
返回个股完整估值数据。

## 包含数据

- 实时行情
- 一致预期（机构预测）
- 前向 PE（市盈率）
- PEG（市盈率相对盈利增长比率）
- 消化年数
    """,
)
def valuation(code: str = Query(..., description="6位股票代码")):
    """完整估值：行情 + 一致预期 + 前向PE/PEG/消化年数。"""
    code = _validate(code)
    try:
        return {"data": astock.full_valuation(code)}
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"估值计算异常：{e}") from e


@app.get(
    "/api/reports",
    tags=["资讯"],
    summary="个股研报",
    description="""
返回个股研报列表，包含 PDF 下载链接。

## 参数

- `code`: 6位股票代码
- `pages`: 获取页数（1-5，默认 2）

数据源：东方财富，仅需 requests 库
    """,
)
def reports(code: str = Query(..., description="6位股票代码"), pages: int = Query(2, ge=1, le=5, description="获取页数")):
    """个股研报列表（东财，含 PDF 链接）。仅需 requests。"""
    code = _validate(code)
    try:
        rows = astock.eastmoney_reports(code, max_pages=pages)
        for r in rows:
            r["pdfUrl"] = (
                astock.pdf_url(r.get("infoCode", "")) if r.get("infoCode") else None
            )
        return {"data": rows}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"研报源异常：{e}") from e


@app.get(
    "/api/news",
    tags=["资讯"],
    summary="个股新闻",
    description="""
返回个股相关新闻列表。

## 参数

- `code`: 6位股票代码
- `limit`: 返回数量（1-50，默认 20）

**依赖：** 需要 akshare
    """,
)
def news(code: str = Query(..., description="6位股票代码"), limit: int = Query(20, ge=1, le=50, description="返回数量")):
    """个股新闻（东财，需 akshare）。"""
    code = _validate(code)
    try:
        return {"data": astock.stock_news(code, limit=limit)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"新闻源异常：{e}") from e


@app.get(
    "/api/info",
    tags=["行情"],
    summary="个股基本面",
    description="""
返回个股基本面信息。

## 包含数据

- 所属行业
- 总股本/流通股本
- 上市时间

**依赖：** 需要 akshare
    """,
)
def info(code: str = Query(..., description="6位股票代码")):
    """个股基本面：行业/股本/上市时间（需 akshare）。"""
    code = _validate(code)
    try:
        return {"data": astock.individual_info(code)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"基本面源异常：{e}") from e


@app.get(
    "/api/disclosure",
    tags=["资讯"],
    summary="巨潮公告",
    description="""
返回个股在巨潮资讯网的公告列表。

**依赖：** 需要 akshare
    """,
)
def disclosure(code: str = Query(..., description="6位股票代码")):
    """巨潮公告列表（需 akshare）。"""
    code = _validate(code)
    try:
        return {"data": astock.disclosure(code)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"公告源异常：{e}") from e


@app.get(
    "/api/kline",
    tags=["行情"],
    summary="K线数据",
    description="""
返回个股 K 线数据。

## 参数

- `code`: 6位股票代码
- `category`: K线类型（4=日线, 5=周线, 6=月线, 11=60分钟）
- `offset`: 数据条数（1-800，默认 60）

**依赖：** 需要 mootdx
    """,
)
def kline(
    code: str = Query(..., description="6位股票代码"),
    category: int = Query(4, description="K线类型：4=日 5=周 6=月 11=60分钟"),
    offset: int = Query(60, ge=1, le=800, description="数据条数"),
):
    """K线（需 mootdx）。category 4=日 5=周 6=月 11=60分钟。"""
    code = _validate(code)
    try:
        return {"data": astock.kline(code, category=category, offset=offset)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"K线源异常：{e}") from e


@app.get(
    "/api/finance",
    tags=["财务"],
    summary="季报财务快照",
    description="""
返回个股季报财务数据快照。

**依赖：** 需要 mootdx
    """,
)
def finance(code: str = Query(..., description="6位股票代码")):
    """季报财务快照（需 mootdx）。"""
    code = _validate(code)
    try:
        return {"data": astock.finance(code)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"财务源异常：{e}") from e


# ---------------------------------------------------------------------------
# 资金面 / 筹码 / 信号（东财数据中心，v3.3 并入）—— 均为「用户查的那只股」的公开数据。
# 东财有 1s 限流，这些多为日/季级静态数据，统一走 30 分钟缓存，进一步降低被封风险。
# ---------------------------------------------------------------------------

_DC_CACHE: dict = {}  # key=(endpoint, code) -> (ts, data)


def _cached(endpoint: str, code: str, ttl: int, fetch):
    key = (endpoint, code)
    hit = _DC_CACHE.get(key)
    if hit and _time.time() - hit[0] < ttl:
        return hit[1]
    data = fetch()
    _DC_CACHE[key] = (_time.time(), data)
    return data


@app.get(
    "/api/margin",
    tags=["资金面"],
    summary="融资融券",
    description="""
返回个股融资融券明细数据。

- 数据源：东方财富
- 日级数据
- 缓存 30 分钟
    """,
)
def margin(code: str = Query(..., description="6位股票代码")):
    """融资融券明细（东财，日级）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {
            "data": _cached("margin", code, 1800, lambda: astock.margin_trading(code))
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"融资融券异常：{e}") from e


@app.get(
    "/api/block-trade",
    tags=["资金面"],
    summary="大宗交易",
    description="""
返回个股大宗交易记录。

- 数据源：东方财富
- 缓存 30 分钟
    """,
)
def block_trade(code: str = Query(..., description="6位股票代码")):
    """大宗交易（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("block", code, 1800, lambda: astock.block_trade(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"大宗交易异常：{e}") from e


@app.get(
    "/api/holders",
    tags=["资金面"],
    summary="股东户数变化",
    description="""
返回个股股东户数变化趋势。

- 数据源：东方财富
- 季度级数据
- 缓存 30 分钟
    """,
)
def holders(code: str = Query(..., description="6位股票代码")):
    """股东户数变化（东财，季度级）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {
            "data": _cached(
                "holders", code, 1800, lambda: astock.holder_num_change(code)
            )
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"股东户数异常：{e}") from e


@app.get(
    "/api/dividend",
    tags=["事件日历"],
    summary="分红送转历史",
    description="""
返回个股历史分红送转记录。

- 数据源：东方财富
- 缓存 30 分钟
    """,
)
def dividend(code: str = Query(..., description="6位股票代码")):
    """分红送转历史（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {
            "data": _cached(
                "dividend", code, 1800, lambda: astock.dividend_history(code)
            )
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"分红送转异常：{e}") from e


@app.get(
    "/api/fund-flow",
    tags=["资金面"],
    summary="个股资金流",
    description="""
返回个股资金流数据（120 日主力净流入）。

- 数据源：东方财富 push2his
- 缓存 15 分钟
- **注意：** 部分大陆住宅 IP 可能受风控影响返回空数据
    """,
)
def fund_flow(code: str = Query(..., description="6位股票代码")):
    """个股资金流（东财 push2his，120 日主力净流入）。缓存 15 分钟。
    注：push2his 对部分大陆住宅 IP 有间歇风控，可能返回空（非代码问题）。"""
    code = _validate(code)
    try:
        return {
            "data": _cached(
                "fundflow", code, 900, lambda: astock.stock_fund_flow_120d(code)
            )
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"资金流异常：{e}") from e


@app.get(
    "/api/dragon-tiger",
    tags=["事件日历"],
    summary="龙虎榜",
    description="""
返回个股龙虎榜数据。

## 包含数据

- 近期上榜记录
- 买卖席位明细
- 机构净买额

数据源：东方财富，缓存 30 分钟
    """,
)
def dragon_tiger(code: str = Query(..., description="6位股票代码")):
    """龙虎榜：该股近期上榜记录 + 买卖席位 + 机构净买（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {
            "data": _cached("dt", code, 1800, lambda: astock.dragon_tiger_board(code))
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"龙虎榜异常：{e}") from e


@app.get(
    "/api/lockup",
    tags=["事件日历"],
    summary="限售解禁日历",
    description="""
返回个股限售解禁日历。

## 包含数据

- 历史解禁记录
- 未来 90 天待解禁计划

数据源：东方财富，缓存 30 分钟
    """,
)
def lockup(code: str = Query(..., description="6位股票代码")):
    """限售解禁日历：历史解禁 + 未来 90 天待解禁（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {
            "data": _cached("lockup", code, 1800, lambda: astock.lockup_expiry(code))
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"解禁日历异常：{e}") from e


@app.get(
    "/api/blocks",
    tags=["板块概念"],
    summary="个股板块归属",
    description="""
返回个股所属板块和概念列表。

- 数据源：东方财富 slist
- 缓存 30 分钟
    """,
)
def blocks(code: str = Query(..., description="6位股票代码")):
    """个股所属板块/概念归属（东财 slist）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {
            "data": _cached("blocks", code, 1800, lambda: astock.concept_blocks(code))
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"板块归属异常：{e}") from e


@app.get(
    "/api/hot-concepts",
    tags=["板块概念"],
    summary="热门概念",
    description="""
返回个股当下被市场归到哪些概念在炒。

- 数据源：东方财富热门概念命中
- 缓存 15 分钟
    """,
)
def hot_concepts(code: str = Query(..., description="6位股票代码")):
    """个股当下被市场归到哪些概念在炒（东财热门概念命中）。缓存 15 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("hotcon", code, 900, lambda: astock.hot_concepts(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"热门概念异常：{e}") from e


@app.get(
    "/api/investor-qa",
    tags=["互动易"],
    summary="互动易问答",
    description="""
返回个股在巨潮互动易的投资者问答。

## 包含内容

- 投资者提问
- 公司回复

缓存 15 分钟
    """,
)
def investor_qa(code: str = Query(..., description="6位股票代码")):
    """互动易问答（巨潮）：投资者提问 + 公司回复。缓存 15 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("irm", code, 900, lambda: astock.investor_qa(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"互动易异常：{e}") from e


@app.get(
    "/api/industry",
    tags=["板块概念"],
    summary="行业涨跌幅排名",
    description="""
返回全行业涨跌幅排名。

- 数据源：东方财富行业板块
- 板块级数据，不包含个股
- 缓存 5 分钟

**参数：**
- `top`: 返回前 N 个行业（5-50，默认 20）
    """,
)
def industry(top: int = Query(20, ge=5, le=50, description="返回前 N 个行业")):
    """全行业涨跌幅排名（东财行业板块，板块级、零个股名单）。缓存 5 分钟。"""
    key = ("industry", str(top))
    hit = _DC_CACHE.get(key)
    if hit and _time.time() - hit[0] < 300:
        return {"data": hit[1]}
    try:
        data = astock.industry_comparison(top_n=top)
        _DC_CACHE[key] = (_time.time(), data)
        return {"data": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"行业排名异常：{e}") from e


# ---------------------------------------------------------------------------
# AQSP 只读研究桥接：仅读 runtime snapshot，不访问行情源或 LLM。
#
# 绩效（命中率）走单独的只读桥接 `performance_bridge`：
# 它复用 aqsp.ledger.learner 的计算，只读台账、不写台账、不触发权重落盘。
# ---------------------------------------------------------------------------


def _aqsp_bridge_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except aqsp_bridge.AQSPInvalidRequest as exc:
        raise HTTPException(400, str(exc)) from exc
    except aqsp_bridge.AQSPDateNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except aqsp_bridge.AQSPCandidateNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except aqsp_bridge.AQSPSnapshotStale as exc:
        raise HTTPException(503, f"AQSP 研究快照不可用：{exc}") from exc
    except aqsp_bridge.AQSPSnapshotUnavailable as exc:
        raise HTTPException(503, f"AQSP 研究快照不可用：{exc}") from exc


@app.get(
    "/api/aqsp/snapshot",
    tags=["AQSP 研究"],
    summary="AQSP 研究快照",
    description="""
读取当前或指定日期的 AQSP 量化选股研究快照。

## 参数

- `date`: 可选，指定日期（YYYY-MM-DD）。不传则返回最新快照

## 注意事项

- 只读 runtime 产物，不生成新数据
- 历史日期需严格精确匹配
- 需要独立安装 aqsp 包
    """,
)
def aqsp_snapshot(date: str | None = Query(default=None, description="日期 (YYYY-MM-DD)，不传则返回最新快照")):
    """读取当前或指定日期的 AQSP 研究快照；历史日期严格精确匹配。"""
    return _aqsp_bridge_call(aqsp_bridge.snapshot_response, date)


@app.get(
    "/api/aqsp/dates",
    tags=["AQSP 研究"],
    summary="AQSP 快照日期列表",
    description="""
返回 AQSP 快照实际提供的日期列表。

- 只读现有数据，不生成或补齐历史
- 用于前端日期选择器
    """,
)
def aqsp_dates():
    """读取 AQSP 快照实际提供的日期列表，不生成或补齐历史数据。"""
    return {"data": _aqsp_bridge_call(aqsp_bridge.dates_payload)}


def _aqsp_candidate(symbol: str, date: str | None = None):
    return {"data": _aqsp_bridge_call(aqsp_bridge.candidate_payload, symbol, date)}


@app.get(
    "/api/aqsp/candidate/{symbol}",
    tags=["AQSP 研究"],
    summary="AQSP 候选详情",
    description="""
读取指定日期的单个候选股票详情。

## 包含数据

- 候选股票基本信息
- Advisory-only 讨论摘要
- 量化评分与因子

## 参数

- `symbol`: 6位股票代码
- `date`: 可选，指定日期（YYYY-MM-DD）
    """,
)
def aqsp_candidate(symbol: str, date: str | None = Query(default=None, description="日期 (YYYY-MM-DD)")):
    """读取指定日期的单个候选及 advisory-only 讨论摘要。"""
    return _aqsp_candidate(symbol, date)


@app.get(
    "/api/aqsp/candidates/{symbol}",
    tags=["AQSP 研究"],
    summary="AQSP 候选详情（复数形式）",
    description="兼容复数资源名的 AQSP 候选只读详情端点，功能同 `/api/aqsp/candidate/{symbol}`",
)
def aqsp_candidates(symbol: str, date: str | None = Query(default=None, description="日期 (YYYY-MM-DD)")):
    """兼容复数资源名的 AQSP 候选只读详情端点。"""
    return _aqsp_candidate(symbol, date)


@app.get(
    "/api/aqsp/performance",
    tags=["AQSP 研究"],
    summary="AQSP 策略表现",
    description="""
返回纸面交易台账的命中率与策略表现（只读）。

## 统计口径

- 整体按 signal_date 合成观察（§5.2）
- not_executable 不计入统计（§5.3）
- 独立信号日不足 30 时标记为冷启动期（§5.4）

## 冷启动期说明

**前端在冷启动期内不得展示胜率**，因为样本量不足，统计不稳定。

数据完全复用 `aqsp.ledger.learner` 的计算逻辑。
    """,
)
def aqsp_performance():
    """纸面交易台账的命中率与策略表现（只读）。

    口径完全复用 aqsp.ledger.learner：整体按 signal_date 合成观察（§5.2）、
    not_executable 不计入（§5.3）、独立信号日不足 30 时标记冷启动期（§5.4）。
    前端在冷启动期内**不得**展示胜率。
    """
    return {"data": performance_bridge.performance_payload()}


_DASHBOARD_CACHE: dict = {}  # key="dashboard_metrics" -> (ts, data)


@app.get(
    "/api/dashboard/metrics",
    tags=["AQSP 研究"],
    summary="业务指标监控仪表盘",
    description="""
返回业务指标监控数据，专为前端仪表盘设计。

## 包含数据

- **总体统计**：总信号数、胜率、平均收益、夏普率
- **策略表现**：每个策略的胜率、收益、信号数
- **时间序列**：按日期的累计收益曲线
- **数据源健康**：台账数据的可用性和新鲜度
- **最近信号**：最近10条信号的执行情况

## 缓存策略

缓存 5 分钟，避免频繁计算。
    """,
)
def dashboard_metrics():
    """业务指标监控仪表盘（缓存 5 分钟）。"""
    key = "dashboard_metrics"
    hit = _DASHBOARD_CACHE.get(key)
    if hit and _time.time() - hit[0] < 300:
        return {"data": hit[1]}

    try:
        data = performance_bridge.dashboard_metrics()
        _DASHBOARD_CACHE[key] = (_time.time(), data)
        return {"data": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"仪表盘指标读取失败：{e}") from e


# ==================== 复盘笔记系统 ====================


class ReviewCreateIn(BaseModel):
    signal_id: str
    date: str
    symbol: str
    rating: int
    tags: list[str] | None = None
    notes: str = ""


class ReviewUpdateIn(BaseModel):
    rating: int | None = None
    tags: list[str] | None = None
    notes: str | None = None


@app.get(
    "/api/reviews",
    tags=["AQSP 研究"],
    summary="查询复盘记录",
    description="""
查询历史信号的复盘笔记。

## 查询参数

- `symbol`: 股票代码过滤
- `date`: 日期过滤（YYYY-MM-DD）
- `signal_id`: 信号ID过滤
- `tags`: 标签过滤（逗号分隔，任一匹配即返回）
- `min_rating`: 最低评分过滤（1-5）

所有参数可选，不传参数返回全部记录（按创建时间倒序）。
    """,
)
def get_reviews_endpoint(
    symbol: str | None = Query(default=None, description="股票代码"),
    date: str | None = Query(default=None, description="信号日期 (YYYY-MM-DD)"),
    signal_id: str | None = Query(default=None, description="信号ID"),
    tags: str | None = Query(default=None, description="标签（逗号分隔）"),
    min_rating: int | None = Query(default=None, description="最低评分（1-5）"),
):
    """查询复盘记录。"""
    try:
        from aqsp.review import get_reviews

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

        reviews = get_reviews(
            symbol=symbol,
            date=date,
            signal_id=signal_id,
            tags=tag_list,
            min_rating=min_rating,
        )

        return {
            "data": [
                {
                    "id": r.id,
                    "signal_id": r.signal_id,
                    "date": r.date,
                    "symbol": r.symbol,
                    "rating": r.rating,
                    "tags": list(r.tags),
                    "notes": r.notes,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
                for r in reviews
            ]
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"查询复盘记录失败：{e}") from e


@app.post(
    "/api/reviews",
    tags=["AQSP 研究"],
    summary="创建复盘记录",
    description="""
为历史信号创建复盘笔记。

## 参数

- `signal_id`: 关联的信号ID（来自 predictions.jsonl）
- `date`: 信号日期（YYYY-MM-DD）
- `symbol`: 股票代码
- `rating`: 评分（1-5星）
- `tags`: 标签列表（可选）
- `notes`: 复盘笔记（Markdown格式，可选）

返回创建的复盘记录ID。
    """,
)
def create_review_endpoint(review_in: ReviewCreateIn):
    """创建复盘记录。"""
    try:
        from aqsp.review import add_review

        review_id = add_review(
            signal_id=review_in.signal_id,
            date=review_in.date,
            symbol=review_in.symbol,
            rating=review_in.rating,
            tags=review_in.tags,
            notes=review_in.notes,
        )

        return {"data": {"id": review_id}}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"创建复盘记录失败：{e}") from e


@app.put(
    "/api/reviews/{review_id}",
    tags=["AQSP 研究"],
    summary="更新复盘记录",
    description="""
更新已有的复盘笔记。

## 参数

- `review_id`: 复盘记录ID（路径参数）
- `rating`: 新评分（1-5星，可选）
- `tags`: 新标签列表（可选）
- `notes`: 新笔记（可选）

仅更新提供的字段，未提供的字段保持不变。
    """,
)
def update_review_endpoint(review_id: str, review_in: ReviewUpdateIn):
    """更新复盘记录。"""
    try:
        from aqsp.review import update_review

        found = update_review(
            review_id=review_id,
            rating=review_in.rating,
            tags=review_in.tags,
            notes=review_in.notes,
        )

        if not found:
            raise HTTPException(404, "复盘记录不存在")

        return {"data": {"ok": True}}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"更新复盘记录失败：{e}") from e


@app.delete(
    "/api/reviews/{review_id}",
    tags=["AQSP 研究"],
    summary="删除复盘记录",
    description="""
删除指定的复盘笔记。

## 参数

- `review_id`: 复盘记录ID（路径参数）

返回是否成功删除。
    """,
)
def delete_review_endpoint(review_id: str):
    """删除复盘记录。"""
    try:
        from aqsp.review import delete_review

        found = delete_review(review_id=review_id)

        if not found:
            raise HTTPException(404, "复盘记录不存在")

        return {"data": {"ok": True}}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"删除复盘记录失败：{e}") from e


@app.get(
    "/api/reviews/tags",
    tags=["AQSP 研究"],
    summary="获取所有标签",
    description="""
返回所有使用过的标签列表（按使用频率倒序）。

用于标签选择器的自动完成功能。
    """,
)
def get_tags_endpoint():
    """获取所有使用过的标签。"""
    try:
        from aqsp.review import get_all_tags

        tags = get_all_tags()
        return {"data": tags}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"获取标签失败：{e}") from e


@app.get(
    "/api/reviews/insights",
    tags=["复盘笔记"],
    summary="复盘洞察聚合",
    description="""
把复盘记录从「流水」聚合成「模式」：

- 总量：复盘次数 / 覆盖票数 / 平均评分 / 最近一次复盘日期
- 标签维度：每个标签的出现次数与平均评分（同标签反复出现且平均分低 = 行为模式预警）
- 个股维度：复盘次数最多的票
- 评分分布：1-5 星直方图

无复盘记录时 total=0、列表为空（前端如实显示「暂无数据」，不硬凑结论）。
""",
)
def review_insights_endpoint():
    """复盘洞察聚合（只读）。"""
    try:
        from aqsp.review import summarize_reviews

        return {"data": summarize_reviews()}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"复盘洞察聚合失败：{e}") from e


@app.get(
    "/api/aqsp/signals",
    tags=["AQSP 研究"],
    summary="历史信号列表",
    description="""
返回最近的历史信号（**含 pending**，按 signal_date 新→旧），供复盘页挑选复盘对象。

- 数据源：`predictions.jsonl`（只读台账，不写台账、不触发权重落盘）
- 每条带 `id`（台账行 uuid）—— 复盘记录的 `signal_id` 引用的就是它
- `win` / `return_pct` 对未结算信号为 `null`（validate 之后才写入，不冒充"已出结果"）

## 查询参数

- `limit`: 最多返回条数（1..1000，默认 100）
- `since`: 只返回 signal_date >= 此日（YYYY-MM-DD，可选）
""",
)



def list_signals_endpoint(
    limit: int = Query(default=100, ge=1, le=1000, description="最多返回条数"),
    since: str | None = Query(default=None, description="signal_date 下限（YYYY-MM-DD）"),
):
    """历史信号列表（复盘页数据源）。"""
    try:
        return {"data": performance_bridge.signals_payload(limit=limit, since=since)}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"查询历史信号失败：{e}") from e


@app.get(
    "/api/aqsp/ic-history",
    tags=["AQSP 研究"],
    summary="因子 IC 趋势（每日滚动诊断回流）",
    description="""
返回 runner 每日滚动 IC 诊断的历史序列（`ic_history.jsonl`，只读回流产物）。

每点含 `as_of`（窗口右端交易日）、`run_at`（跑批时刻 UTC）与各因子 IC 均值。
用于直观判断「哪个因子在稳定达标」——是换族决策（预注册判据）的监控面。

数据缺失/文件不存在时返回空序列（fail-soft，绝不 500）。
""",
)
def ic_history_endpoint():
    """因子 IC 历史序列（只读）。"""
    try:
        import json as _json
        from pathlib import Path as _Path

        root = os.environ.get("AQSP_RUNTIME_DATA_ROOT", "").strip() or "/opt/aqsp/data"
        path = _Path(root) / "pit_cache" / "factor_ic" / "ic_history.jsonl"
        points: list[dict] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    points.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue
        points.sort(key=lambda p: str(p.get("run_at", "")))
        latest_factors = points[-1].get("factors", {}) if points else {}
        return {"data": {"points": points, "latest_factors": latest_factors}}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"读取因子 IC 历史失败：{e}") from e


def _parse_iso_utc_z(value: object):
    """把 UTC ISO-8601 时间戳（`…Z` 或 `…+00:00`）解析成 aware datetime；取不到返回 None。

    用于双窗判决新鲜度护栏：`dual_window_latest.json` 的 `run_at` / `generated_at`
    都是 producer 写入的 UTC ISO。解析失败绝不抛（fail-soft），返回 None 让上层
    按「判不出龄」处理（不压制可用性）。
    """
    from datetime import datetime, timezone

    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _dual_verdict_age_hours(latest: dict, now) -> float | None:
    """双窗判决「产出至今」的小时龄（取 producer 的 run_at / generated_at），取不到返回 None。

    用 producer 时间戳（而非回流文件 mtime）= 判决真正计算所依据的数据时点，
    才符合「新鲜度」语义。None 表示无法判龄（缺字段/格式异常）⇒ 上层不压制。
    """
    ts = _parse_iso_utc_z(latest.get("run_at") or latest.get("generated_at"))
    if ts is None:
        return None
    if getattr(now, "tzinfo", None) is None:
        from datetime import timezone

        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 3600.0)


def _parse_iso_date(value: object):
    """把双窗产物的 ``as_of_b``（ISO 日期串，如 "2026-09-24"）解析为 date；取不到返回 None。

    与 ``check_data_freshness.py`` 的源库 MAX(trade_date) 口径一致（数据自身最新日）。
    fail-soft：缺字段 / 格式异常 ⇒ None（上层按「判不出」处理，不压制可用性）。
    """
    if not value:
        return None
    s = str(value).strip()
    if len(s) < 10:
        return None
    from datetime import date

    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _dual_verdict_data_threshold(max_lag_trading_days: int):
    """双窗判决「数据最新日」可容忍下限（as_of_b 早于它 ⇒ 源库冻死 ⇒ 判陈旧）。

    口径与 ``check_data_freshness.py`` 完全一致：``threshold =
    get_previous_trading_day(today) 往前 max_lag_trading_days 个交易日``。
    默认 max_lag=1 ⇒ 容忍 1 个交易日滞后（正常 T+1 不算陈旧），≥2 交易日 ⇒ 判陈旧。
    """
    from aqsp.core.time import get_previous_trading_day, today_shanghai

    expected = get_previous_trading_day(today_shanghai())
    cur = expected
    for _ in range(max_lag_trading_days):
        cur = get_previous_trading_day(cur)
    return cur


@app.get(
    "/api/aqsp/ic-dual-verdict",
    tags=["AQSP 研究"],
    summary="双窗因子 IC 滚动判决（每日 · proposal-only）",
    description="""
返回 runner 每日双窗因子 IC 判决（`ic_dual_verdict.py` 的 `dual_window_latest.json`，
只读回流产物）：两个相邻不重叠等长窗（各 73 截面，3 年红线内）逐因子的
IC/t 同号且双 |t|≥2 达标判定 + 连续达标日数 + `revisit_family` 事件。

用途：换族决策的**监控面**（方案 B §六 判据的每日滚动版）——「某因子何时稳定达标、
该回换族流程」一眼可见。

红线：
- **proposal-only**：纯只读展示，产物/端点/前端均**绝不写回打分/排序/下单、不自动改参数**。
- **fail-soft**：双窗产物缺失（尚未启用/未回流）/ 读失败 / 字段残缺 ⇒ `available=false`、
  `latest=null`，端点仍 200，绝不 500（区别于单窗 ic-history，双窗是新增监控面，缺了不拖主链路）。
""",
)
def ic_dual_verdict_endpoint():
    """双窗因子 IC 滚动判决（只读 · proposal-only · fail-soft）。

    新鲜度护栏（与 fetch 双窗段 quarantine 双保险，堵「静默失效≠健康」）：
    产物 `run_at`/`generated_at` 龄 > `DUAL_MAX_AGE_HOURS`（默认 36h，env 可覆盖，
    与 fetch 侧 `DUAL_MAX_AGE_HOURS`/`MAX_AGE_HOURS` 同值）⇒ 判定为陈旧（runner 双窗
    连败且尚未到下一次 fetch）⇒ **自降 `available=false`**，绝不把陈旧判决静默当「最新」
    展示；`latest` 保留供观测、`stale` 标记本次是否被抑制。判不出龄（缺时间戳/格式异常）
    ⇒ 不压制（fail-safe），维持原可用性语义。

    第二道护栏（数据自身最新日，堵「run_at 新鲜 + 数据冻死」静默窗口）：产物 `as_of_b`
    （源库 MAX(trade_date)，与 `check_data_freshness.py` 同口径）落后 > `DUAL_MAX_LAG_TRADING_DAYS`
    （默认 1 交易日，env 可覆盖）⇒ 源库冻死 ⇒ 同样自降 `available=false` + `stale=true`。
    这是修源库监控的补强：修好源库后，今天跑在冻死库上的旧产物要等下一次重算才翻正，
    期间端点本「绿但数据旧」，此护栏把该窗口也标 stale。缺 `as_of_b`/日历异常 ⇒ 不压制。
    """
    from aqsp.briefing.closing_review import _factor_ic_runtime_root

    try:
        import json as _json
        import os as _os
        from datetime import datetime, timezone
        from pathlib import Path as _Path

        root = _factor_ic_runtime_root()
        path = _Path(root) / "pit_cache" / "factor_ic" / "dual_window_latest.json"

        # 新鲜度上限：默认 36h，与 fetch 双窗段 quarantine 的 DUAL_MAX_AGE_HOURS 同值同 env。
        try:
            max_age_hours = float(_os.environ.get("DUAL_MAX_AGE_HOURS", "36"))
        except (TypeError, ValueError):
            max_age_hours = 36.0

        latest = None
        available = False
        stale = False
        streak_n = 5
        if path.exists():
            try:
                latest = _json.loads(path.read_text(encoding="utf-8"))
                available = bool(latest.get("factors"))
                streak_n = int(latest.get("streak_n", 5))
            except Exception:  # 读失败/字段残缺 ⇒ 降级空，不 500
                latest = None
                available = False

        # 新鲜度护栏（双保险）：
        #  (1) run_at/generated_at 龄 > DUAL_MAX_AGE_HOURS（默认36h）⇒ 陈旧（runner 双窗连败）；
        #  (2) 数据自身最新日 as_of_b 落后 > DUAL_MAX_LAG_TRADING_DAYS（默认1 交易日）⇒ 源库冻死
        #        （堵「run_at 新鲜 + 数据冻死」静默窗口：修好源库后旧产物要等下一次重算才翻正，
        #         ~1 天内端点会「绿但数据旧」）。两道任一命中 ⇒ 自降 available=False + stale=True；
        #         判不出（缺字段/格式异常）不压制（fail-safe）。
        if available and latest:
            # 第(1)道：产出时间戳龄
            age_hours = _dual_verdict_age_hours(latest, datetime.now(timezone.utc))
            if age_hours is not None and age_hours > max_age_hours:
                available = False
                stale = True

            # 第(2)道：数据自身最新日（as_of_b）滞后 ⇒ 源库冻死
            if not stale:
                try:
                    max_lag_td = int(_os.environ.get("DUAL_MAX_LAG_TRADING_DAYS", "1"))
                except (TypeError, ValueError):
                    max_lag_td = 1
                as_of_b = _parse_iso_date(latest.get("as_of_b"))
                if as_of_b is not None:
                    try:
                        threshold = _dual_verdict_data_threshold(max_lag_td)
                        if as_of_b < threshold:
                            available = False
                            stale = True
                    except Exception:  # 交易日历异常 ⇒ 不压制（fail-safe）
                        pass

        return {
            "data": {
                "available": available,
                "latest": latest,
                "streak_n": streak_n,
                "stale": stale,
            }
        }
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001  读失败/字段残缺 ⇒ fail-soft，绝不 500
        return {"data": {"available": False, "latest": None, "streak_n": 5, "stale": False}}


@app.get(
    "/api/aqsp/closing-review",
    tags=["AQSP 研究"],
    summary="收评日报 6 段（市场级 · 只读聚合）",
    description="""
聚合收评日报的 6 个市场级只读段（因子 IC 健康 / 板块资金面 / 龙虎榜关注 / 财经快讯 /
重大公告 / 股东户数异动），复用 `aqsp.briefing.closing_review` 的 `build_*_section()`
（读全市场 pit_cache，默认走 `runtime_data_root()`，与 IC cron 回流写读同源）。

- **红线**：只读展示，各段 builder 绝不写回打分/排序/下单。
- **fail-soft**：任一段缺数据/读失败/表头残缺 ⇒ 该段 `available=false`、`markdown` 空，
  整端点仍 200，绝不 500。
- **口径**：市场级（全市场 pit_cache），区别于 `/api/events` 等单票级端点；
  前端据此做收评日报卡片流。
""",
)
def aqsp_closing_review_endpoint():
    from aqsp.briefing.closing_review import (
        build_announcements_section,
        build_board_fund_section,
        build_factor_ic_section,
        build_holder_concentration_section,
        build_longhubang_section,
        build_news_section,
    )

    try:
        from aqsp.core.time import today_shanghai

        as_of = today_shanghai().isoformat()
    except Exception:  # noqa: BLE001 — 时间取不到不拖垮整端点，降级为本地墙钟（北京时区）
        from datetime import datetime, timedelta, timezone

        as_of = datetime.now(timezone(timedelta(hours=8))).isoformat()

    # 顺序 = 收评日报呈现顺序：决策相关的放前（IC 健康），市场环境证据放后。
    spec = [
        ("factor_ic", "因子 IC 健康", build_factor_ic_section),
        ("board_fund", "板块资金面", build_board_fund_section),
        ("longhubang", "龙虎榜关注", build_longhubang_section),
        ("news", "财经快讯", build_news_section),
        ("announcements", "重大公告", build_announcements_section),
        ("holder_concentration", "股东户数异动", build_holder_concentration_section),
    ]
    sections = []
    for key, title, builder in spec:
        try:
            md = builder() or ""
        except Exception:  # noqa: BLE001 — 单段失败不拖垮整端点（fail-soft）
            md = ""
        sections.append(
            {"key": key, "title": title, "markdown": md, "available": bool(md.strip())}
        )
    return {"data": {"as_of": as_of, "sections": sections}}
