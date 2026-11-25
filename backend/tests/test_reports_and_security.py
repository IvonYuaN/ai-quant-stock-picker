"""我的研报 + 安全守卫（SSRF / 成本负数 / 日期）回归测。全部离线、不联网。

覆盖 2026-07-07 粉丝反馈批量修复中新增/加固的后端面：
- myreports：文件名分行业、存取删、类型白名单、data:URI 守卫、原子写。
- chat._check_base_url：防 SSRF（本地放行本机、始终挡云元数据、公网姿态挡内网）。
- 成本允许负数、清仓日期格式校验。
"""

import base64

import pytest
from fastapi.testclient import TestClient

import app as app_module
import chat
import myreports as mr

client = TestClient(app_module.app)

_B64 = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4 test").decode()


# ---- 我的研报 ----


def test_classify_by_filename():
    assert mr.classify("东吴证券_中际旭创_光模块深度.pdf") == "光互联"
    assert mr.classify("宇树科技_人形机器人.pdf") == "人形机器人"
    assert mr.classify("随手记.txt") == "未分类"


def test_report_roundtrip_and_delete():
    r = client.post(
        "/api/myreports", json={"name": "长鑫_HBM_深度.pdf", "content_b64": _B64}
    )
    assert r.status_code == 200
    meta = r.json()["data"]
    assert meta["industry"] == "HBM存储"
    rid = meta["id"]
    try:
        assert any(x["id"] == rid for x in client.get("/api/myreports").json()["data"])
        assert client.get(f"/api/myreports/file/{rid}").status_code == 200
    finally:
        assert client.delete(f"/api/myreports/{rid}").json()["data"]["ok"] is True
    assert client.get(f"/api/myreports/file/{rid}").status_code == 404


def test_report_illegal_type_400():
    r = client.post("/api/myreports", json={"name": "x.exe", "content_b64": _B64})
    assert r.status_code == 400


def test_report_data_uri_without_comma_400():
    # 之前会 IndexError→500，现应 400
    assert (
        client.post(
            "/api/myreports", json={"name": "a.pdf", "content_b64": "data:"}
        ).status_code
        == 400
    )


def test_report_missing_file_404():
    assert client.get("/api/myreports/file/does-not-exist").status_code == 404


# ---- 研报索引损坏 / 删除失败：必须与「确实没有」区分（数据诚实性） ----


@pytest.fixture()
def reports_dir(tmp_path, monkeypatch):
    """把研报根目录指到 tmp，避免污染真实的 ~/.vibe-research。"""
    root = tmp_path / "myreports"
    root.mkdir()
    monkeypatch.setattr(mr, "_REPORTS_ROOT", root)
    monkeypatch.setattr(mr, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(mr, "_MIGRATED_TENANTS", set())
    return root


def test_missing_index_file_is_really_empty(reports_dir):
    """对照组：索引文件不存在 = 确实没有研报 ⇒ 200 + 空数组。"""
    r = client.get("/api/myreports")
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_corrupt_index_is_not_reported_as_empty_list(reports_dir):
    (reports_dir / "index.json").write_text("{not json", "utf-8")
    r = client.get("/api/myreports")
    assert r.status_code == 500, "索引损坏必须报错，不能回空数组让界面显示「暂无研报」"
    assert "不代表没有研报" in r.json()["detail"]


def test_non_list_index_is_not_reported_as_empty_list(reports_dir):
    (reports_dir / "index.json").write_text('{"a": 1}', "utf-8")
    r = client.get("/api/myreports")
    assert r.status_code == 500
    assert "结构异常" in r.json()["detail"]


def test_upload_refuses_to_overwrite_corrupt_index(reports_dir):
    """索引坏了还照传，会把旧索引覆盖成「只有新文件」——旧研报全部失联。"""
    (reports_dir / "index.json").write_text("{not json", "utf-8")
    before = sorted(p.name for p in reports_dir.iterdir())
    r = client.post("/api/myreports", json={"name": "a.pdf", "content_b64": _B64})
    assert r.status_code == 500
    assert (reports_dir / "index.json").read_text("utf-8") == "{not json"
    assert sorted(p.name for p in reports_dir.iterdir()) == before, "不能落下无法索引的孤儿文件"


def test_file_download_does_not_404_on_corrupt_index(reports_dir):
    """索引读不出来时不能说成「研报不存在」——那是把故障说成文件真没有。"""
    (reports_dir / "index.json").write_text("{not json", "utf-8")
    r = client.get("/api/myreports/file/whatever")
    assert r.status_code == 500
    assert "不代表没有研报" in r.json()["detail"]


def test_delete_keeps_index_when_file_cannot_be_removed(reports_dir):
    """文件删不掉时必须保留索引条目并报错，否则条目变成既删不掉也下载不到的孤儿。"""
    meta = client.post(
        "/api/myreports", json={"name": "b.pdf", "content_b64": _B64}
    ).json()["data"]
    rid = meta["id"]
    fp = reports_dir / f"{rid}.pdf"
    fp.unlink()
    fp.mkdir()  # unlink 同名目录 ⇒ IsADirectoryError（OSError 子类）

    r = client.delete(f"/api/myreports/{rid}")
    assert r.status_code == 500, "文件删不掉时不能回 ok:true 报「已删除」"
    assert "索引未改动" in r.json()["detail"]

    ids = [row["id"] for row in client.get("/api/myreports").json()["data"]]
    assert rid in ids, "删失败不能摘索引"
    assert fp.exists()


# ---- SSRF 守卫 ----


def _allowed(url: str) -> bool:
    try:
        chat._check_base_url(url)
        return True
    except RuntimeError:
        return False


def test_ssrf_local_mode():
    assert chat._PUBLIC_MODE is False  # 测试进程未设 VR_API_KEY
    assert _allowed("https://api.deepseek.com") is True
    assert _allowed("http://127.0.0.1:11434") is True  # 本机 Ollama 等，本地放行
    assert _allowed("http://169.254.169.254/latest") is False  # 云元数据，始终挡
    assert _allowed("ftp://evil/x") is False


def test_ssrf_public_mode_blocks_internal(monkeypatch):
    monkeypatch.setattr(chat, "_PUBLIC_MODE", True)
    assert _allowed("http://192.168.1.1") is False
    assert _allowed("http://10.0.0.5") is False
    assert _allowed("http://127.0.0.1:11434") is False
    assert _allowed("http://[::ffff:192.168.1.1]") is False
    assert _allowed("http://[::ffff:10.0.0.5]") is False
    assert _allowed("http://[::ffff:127.0.0.1]:11434") is False
    # 注：公网域名在 public 姿态会走真实 DNS 解析核对，为保持离线不在此断言


def test_ssrf_ipv4_mapped_metadata_blocked_in_local_mode(monkeypatch):
    monkeypatch.setattr(chat, "_PUBLIC_MODE", False)
    assert _allowed("http://[::ffff:169.254.169.254]") is False


def test_ssrf_dns_ipv4_mapped_internal_blocked_in_public_mode(monkeypatch):
    monkeypatch.setattr(chat, "_PUBLIC_MODE", True)
    monkeypatch.setattr(
        chat.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("::ffff:10.0.0.5", 0))],
    )
    assert _allowed("https://model.example.test/v1") is False


# ---- 成本负数 / 日期 ----


def test_negative_cost_accepted():
    r = client.post(
        "/api/portfolio/holding", json={"code": "600519", "shares": 100, "cost": -5.5}
    )
    assert r.status_code == 200
    client.request(
        "DELETE", "/api/portfolio/holding", params={"code": "600519"}
    )  # 清理


def test_zero_shares_rejected():
    assert (
        client.post(
            "/api/portfolio/holding", json={"code": "600519", "shares": 0, "cost": 10}
        ).status_code
        == 400
    )


def test_close_bad_date_400():
    r = client.post(
        "/api/portfolio/close",
        json={
            "code": "600519",
            "date": "2025-13-45",
            "price": 10,
            "shares": 100,
            "cost": 5,
        },
    )
    assert r.status_code == 400
