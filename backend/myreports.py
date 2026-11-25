"""我的研报 —— 用户上传/归档自己的研报文件，存本地、不上传、不进开源仓库。

设计取舍：
- 走 base64 JSON 上传（不引入 python-multipart 依赖，契合本项目「秒装必可用」）；研报文件不大，够用。
- 存到 `VR_REPORTS_DIR`（默认 ~/.vibe-research/myreports/，也可用 VR_DATA_DIR 换根目录）——用户私有资料，绝不进仓、不上传。
  放仓库外，重新下载/覆盖项目文件夹不会丢（issue #12）；≤v0.1.1 存 backend/.cache/myreports/，首次启动自动迁移（复制，旧目录保留作备份）。
- 元数据存目录内 index.json；按文件名关键词自动打「行业」标签（best-effort，未命中记「未分类」）。

合规/隐私：与「持仓 / 关注股只存本地」同一红线——研报是用户私有数据，只落本地磁盘。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

import tenant as _tenant

_OLD_DEFAULT_DIR = (
    Path(__file__).resolve().parent / ".cache" / "myreports"
)  # ≤v0.1.1 旧位置
_DATA_DIR = Path(os.environ.get("VR_DATA_DIR") or Path.home() / ".vibe-research")
_DEFAULT_DIR = _DATA_DIR / "myreports"
# 空串视同未设置（与 VR_DATA_DIR 语义一致，避免 Path("") 落到进程工作目录）
_REPORTS_ROOT = Path(os.environ.get("VR_REPORTS_DIR") or str(_DEFAULT_DIR))


def _tenant_id() -> str:
    return _tenant.current_tenant.get()


def _tenant_dir() -> Path:
    """当前租户研报根；local 用原始 _REPORTS_ROOT，其余落到 users/<tid>/myreports。"""
    tid = _tenant_id()
    if tid == "local":
        return _REPORTS_ROOT
    return _DATA_DIR / "users" / tid / "myreports"


def _reports_dir() -> Path:
    return _tenant_dir()


def _index() -> Path:
    return _tenant_dir() / "index.json"


def _legacy_local_dir() -> Path:
    return _REPORTS_ROOT


def _migrate_legacy() -> None:
    """旧版研报在仓库内 .cache/ 里，重下载项目会丢；迁到用户目录（显式设了 VR_REPORTS_DIR 或新位置已有则不动）。"""
    try:
        if (
            os.environ.get("VR_REPORTS_DIR")
            or _REPORTS_ROOT.exists()
            or not _OLD_DEFAULT_DIR.exists()
        ):
            return
        tmp = _REPORTS_ROOT.with_name(_REPORTS_ROOT.name + ".migrate.tmp")
        if tmp.exists():
            shutil.rmtree(tmp)  # 上次中断留下的半截目录，重来
        shutil.copytree(_OLD_DEFAULT_DIR, tmp)
        os.replace(tmp, _REPORTS_ROOT)  # 同盘原子改名：复制中断不会留半套研报挡住下次重试
    except OSError as e:
        # 迁移失败不阻塞启动，但要出声——旧数据原样保留在 _OLD_DEFAULT_DIR，可手工复制
        print(
            f"[vibe-research] 研报数据迁移失败（旧数据仍在 {_OLD_DEFAULT_DIR}）: {e}",
            file=sys.stderr,
        )


_migrate_legacy()
_LOCK = (
    threading.Lock()
)  # 索引读-改-写串行化（与 portfolio.py 同款），防并发上传/删除互相覆盖
# 已尝试过迁移的租户，避免每次读都 stat 旧目录
_MIGRATED_TENANTS: set[str] = set()

MAX_BYTES = 25 * 1024 * 1024  # 单文件上限 25MB
# 允许的文档类型（白名单——不存可执行 / 网页等，避免下载回放风险）
ALLOWED_EXT = {
    ".pdf",
    ".doc",
    ".docx",
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}

# 文件名关键词 → 行业标签（顺序即优先级，先命中先用）。纯文件名匹配、零依赖、离线可用。
_INDUSTRY_KEYWORDS: list[tuple[str, list[str]]] = [
    (
        "人形机器人",
        [
            "人形",
            "机器人",
            "humanoid",
            "谐波",
            "丝杠",
            "滚柱",
            "灵巧手",
            "减速器",
            "optimus",
            "宇树",
            "特斯拉",
        ],
    ),
    (
        "光互联",
        [
            "光互联",
            "硅光",
            "cpo",
            "光模块",
            "磷化铟",
            "inp",
            "光芯片",
            "源杰",
            "中际旭创",
            "天孚",
        ],
    ),
    (
        "HBM存储",
        [
            "hbm",
            "存储",
            "内存",
            "dram",
            "长鑫",
            "美光",
            "海力士",
            "颗粒",
            "闪存",
            "nand",
        ],
    ),
    (
        "AI算力",
        [
            "算力",
            "gpu",
            "英伟达",
            "nvidia",
            "服务器",
            "液冷",
            "pcb",
            "交换机",
            "cowos",
            "沪电",
            "工业富联",
        ],
    ),
    (
        "半导体",
        ["半导体", "芯片", "晶圆", "光刻", "封测", "台积电", "刻蚀", "存储芯片"],
    ),
    ("新能源", ["锂电", "电池", "光伏", "储能", "固态", "钠电", "宁德", "比亚迪"]),
    ("创新药", ["创新药", "医药", "生物", "cxo", "临床", "adc", "glp", "药明"]),
    ("商业航天", ["航天", "卫星", "火箭", "星链", "starlink", "spacex", "蓝箭"]),
    ("电力电网", ["电力", "电网", "特高压", "变压器", "输配电", "燃气轮机"]),
]


class ReportError(ValueError):
    """上传/校验类错误（对应 HTTP 400/413）。"""


class IndexUnreadable(RuntimeError):
    """索引文件存在但读不出来（损坏 / 无权限 / 顶层结构异常）。

    必须与「确实没有研报」区分：把它当空列表会让界面显示「暂无研报」，更糟的是
    下一次上传会把空列表**写回索引** —— 磁盘上的旧研报从此全部失联（索引里没有
    ⇒ 既下载不到也删不掉，只能手工翻目录）。对应 HTTP 500，不是 404。
    """


def _maybe_migrate_to_tenant() -> None:
    """非 local 租户首次访问时，若自身为空且本地 legacy 研报存在，复制过来
    （让「从单用户升级到多用户」的老数据无缝落到主人自己的租户桶）。"""
    tid = _tenant_id()
    if tid == "local" or tid in _MIGRATED_TENANTS:
        return
    _MIGRATED_TENANTS.add(tid)
    tdir = _reports_dir()
    if tdir.exists() or not _legacy_local_dir().exists():
        return
    try:
        shutil.copytree(_legacy_local_dir(), tdir)
    except OSError:
        pass


def _ensure_dir() -> None:
    _maybe_migrate_to_tenant()
    _reports_dir().mkdir(parents=True, exist_ok=True)


def _load_index() -> list[dict]:
    """读索引。**文件不存在**才是「没有研报」；存在却读不出来一律抛
    ``IndexUnreadable``，绝不降级成空列表（否则上传会把旧索引覆盖成空）。"""
    _maybe_migrate_to_tenant()
    path = _index()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise IndexUnreadable(
            f"研报索引读取失败（{type(exc).__name__}: {exc}）。"
            "这是索引文件损坏或不可读，不代表没有研报；"
            f"请检查 {path}（改名后即可重建索引，磁盘上的原文件不会丢）。"
        ) from exc
    if not isinstance(data, list):
        raise IndexUnreadable(
            f"研报索引结构异常（顶层应为数组，实际 {type(data).__name__}）："
            "不代表没有研报，请检查索引文件。"
        )
    return data


def _save_index(items: list[dict]) -> None:
    _ensure_dir()
    tmp = _index().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, _index())  # 原子改名，避免半截写入损坏索引（进程被 kill / OOM）


def classify(filename: str) -> str:
    """按文件名关键词判行业；未命中记「未分类」。"""
    low = filename.lower()
    for industry, kws in _INDUSTRY_KEYWORDS:
        if any(kw.lower() in low for kw in kws):
            return industry
    return "未分类"


def _sanitize_name(name: str) -> str:
    """只保留基名，去掉路径分隔符；空名给个兜底。"""
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    return base or "未命名"


def list_reports() -> list[dict]:
    """按上传时间倒序返回元数据列表。"""
    return sorted(_load_index(), key=lambda r: r.get("ts", 0), reverse=True)


def save_report(name: str, content_b64: str) -> dict:
    """解码 base64 存盘 + 打行业标签 + 记录元数据。返回该条元数据。"""
    fname = _sanitize_name(name)
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ReportError(
            f"不支持的文件类型 {ext or '（无扩展名）'}；支持：PDF / Word / txt / md / 表格 / 图片"
        )
    # base64 可能带 data:URI 前缀（前端 FileReader.readAsDataURL），剥掉逗号前半段
    if content_b64.startswith("data:"):
        parts = content_b64.split(",", 1)
        if len(parts) < 2:
            raise ReportError("无效的 data URI（缺少逗号分隔的 base64 数据）")
        raw_b64 = parts[1]
    else:
        raw_b64 = content_b64
    try:
        blob = base64.b64decode(raw_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ReportError(f"文件内容解码失败：{e}") from e
    if not blob:
        raise ReportError("文件为空")
    if len(blob) > MAX_BYTES:
        raise ReportError(
            f"文件过大（{len(blob) // 1024 // 1024}MB），上限 {MAX_BYTES // 1024 // 1024}MB"
        )

    _ensure_dir()
    rid = uuid.uuid4().hex
    meta = {
        "id": rid,
        "name": fname,
        "industry": classify(fname),
        "size": len(blob),
        "ext": ext,
        "ts": int(time.time() * 1000),
    }
    with _LOCK:
        # 先读索引再落盘：索引损坏时直接抛 IndexUnreadable，不留下无法索引的孤儿文件。
        items = _load_index()
        (_reports_dir() / f"{rid}{ext}").write_bytes(blob)
        items.append(meta)
        _save_index(items)
    return meta


def report_path(rid: str) -> tuple[Path, str] | None:
    """按 id 取 (磁盘路径, 原始文件名)；不存在返回 None。"""
    for r in _load_index():
        if r.get("id") == rid:
            p = _reports_dir() / f"{rid}{r.get('ext', '')}"
            return (p, r.get("name", rid)) if p.exists() else None
    return None


def delete_report(rid: str) -> bool:
    """删文件 + 移除索引条目。

    返回 ``False`` 只表示「索引里没有这个 id」。文件删不掉（权限 / 只读挂载 /
    被占用）时**索引保持原样并直接抛 OSError** —— 以前这里吞掉异常后照摘索引、
    照样返回 True，界面会报「已删除」，文件却留在盘上：索引已无此条 ⇒ 既下载
    不到也删不掉，只能手工翻目录。
    """
    with _LOCK:
        items = _load_index()
        hit = next((r for r in items if r.get("id") == rid), None)
        if hit is None:
            return False
        fp = _reports_dir() / f"{rid}{hit.get('ext', '')}"
        fp.unlink(missing_ok=True)  # 失败就抛，索引保持原样
        _save_index([r for r in items if r.get("id") != rid])
    return True
