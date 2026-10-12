"""Point-in-time 行业分类（申万）—— 消除板块归类的「前视偏差」。

问题（架构 §5 / §9 红线「拿事后才知道的板块归类做选股」）：
东财/通达信只给**当前**行业归属。若用今天的行业去套历史时点做回测/选股，
等于偷看了未来——典型前视偏差。申万官方发布每只股票的**行业变迁史**
（每次调整一行），本模块据此回答「某只票在 as_of 日到底属于哪个行业」。

移植自 `simonlin1212/a-stock-data` v3.8.0 §6.7（已获上游审计 PASS），按 AQSP 改造：
- 纯函数 `industry_as_of(df, code, as_of)` 不依赖网络，可单测；
- `IndustryPitSource` 负责取数 + 本地缓存（CSV，无额外引擎依赖）；
- 网络取数 lazy import `requests`，失败抛 `DataError`；
- 仅返回行业**代码**（申万官方不发中文名，名称表未公开，不可套东财名）。

集成点：板块去重 / 风控 / 回测在拿行业标签时，须调用本模块的 `industry_as_of`
传入信号日，而不是用数据源返回的"当前行业"。
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import pandas as pd

from aqsp.core.errors import DataError
from aqsp.core.runtime import runtime_data_root

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    import requests

SW_URL = (
    "https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls"
)


@dataclass(frozen=True)
class IndustryLabel:
    """某个时点某只票的行业归属（申万，仅代码）。"""

    code: str
    as_of: str
    industry_code: str  # 申万二级行业代码（6 位）
    l1_code: str  # 一级行业代码，如 480000
    l2_code: str  # 二级行业代码，如 480300
    since: str  # 该次调整的生效日


def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """把申万原始表规范成内部 schema。"""
    df = raw.rename(
        columns={
            "股票代码": "code",
            "计入日期": "start_date",
            "行业代码": "industry_code",
            "更新日期": "update_date",
        }
    )
    missing = {"code", "start_date", "industry_code"} - set(df.columns)
    if missing:
        raise DataError(
            f"申万行业表结构变了，缺列 {sorted(missing)}；实际列={list(df.columns)}"
        )
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["industry_code"] = df["industry_code"].astype(str).str.zfill(6)
    # 层级码补成规范 6 位（申万一级 480000、二级 480300），才能与官方指数/名称表 join。
    df["l1_code"] = df["industry_code"].str[:2] + "0000"
    df["l2_code"] = df["industry_code"].str[:4] + "00"
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
    return df.sort_values(["code", "start_date"]).reset_index(drop=True)


def _sw_session() -> "requests.Session":
    """申万源专用会话：swsresearch.com 证书链只发叶子证书、不发中间证书
    （openssl s_client 实测 Verify return code: 21），默认 verify 在 prod 直连
    时 CERTIFICATE_VERIFY_FAILED。用 truststore 走系统信任库（macOS/Windows
    原生信任链）完成完整校验——**不是** verify=False（安全红线，禁止）。

    truststore 只挂载到本会话的连接池（会话级注入），不做进程级全局
    ``inject_into_ssl()``，避免影响其他模块的 TLS 行为。
    """
    import ssl

    import requests

    try:
        import truststore
    except ImportError as e:  # pragma: no cover - 依赖缺失属环境态
        raise DataError(f"industry_pit: 缺少依赖 truststore（{e}）") from e

    class _TruststoreHTTPAdapter(requests.adapters.HTTPAdapter):
        """把 truststore SSLContext 注入本 adapter 的连接池（非全局）。"""

        def init_poolmanager(self, *args: object, **kwargs: object) -> None:
            kwargs["ssl_context"] = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            super().init_poolmanager(*args, **kwargs)  # type: ignore[arg-type]

    session = requests.Session()
    session.mount("https://", _TruststoreHTTPAdapter())
    return session


def sw_industry_history(url: str = SW_URL) -> pd.DataFrame:
    """抓取申万行业变迁史（网络）。失败抛 DataError。"""
    try:
        r = _sw_session().get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        r.raise_for_status()
    except Exception as e:  # 网络/SSL/超时统一归为数据错误
        raise DataError(f"industry_pit: 申万行业表抓取失败（{e}）") from e
    try:
        df = pd.read_excel(io.BytesIO(r.content))
    except Exception as e:
        raise DataError(f"industry_pit: 申万 xls 解析失败（{e}）") from e
    return _normalize(df)


def industry_as_of(df: pd.DataFrame, code: str, as_of: str) -> Optional[IndustryLabel]:
    """某只股票在 as_of 日所属的申万行业（取不晚于该日的最后一次调整）。

    Args:
        df: `sw_industry_history` / `IndustryPitSource.load` 返回的规范表。
        code: 6 位股票代码。
        as_of: 查询时点，ISO 日期字符串（如 "2026-08-18"）或 date 对象。

    Returns:
        IndustryLabel 或 None（该日尚未上市 / 无归属记录）。
    """
    code = str(code).zfill(6)
    as_of_ts = pd.Timestamp(as_of)
    sub = df[(df["code"] == code) & (df["start_date"] <= as_of_ts)]
    if sub.empty:
        return None
    row = sub.iloc[-1]
    return IndustryLabel(
        code=code,
        as_of=str(as_of_ts.date()),
        industry_code=str(row["industry_code"]),
        l1_code=str(row["l1_code"]),
        l2_code=str(row["l2_code"]),
        since=pd.Timestamp(row["start_date"]).strftime("%Y-%m-%d"),
    )


def _read_cache_df(path: str) -> pd.DataFrame:
    """读缓存 CSV 并恢复规范类型。

    显式 ``dtype`` 锁死代码列（同 ``longhubang.load()`` 的踩坑注释），避免
    ``"000001"`` 被 pandas 推断成 ``1`` 而**静默丢失前导零**，导致 symbol
    匹配恒失败。l1/l2 派生列一并锁死，层级 join 才能对齐。
    """
    df = pd.read_csv(
        path,
        dtype={"code": str, "industry_code": str, "l1_code": str, "l2_code": str},
    )
    for col in ("code", "industry_code", "l1_code", "l2_code"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.zfill(6)
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
    return df


class IndustryPitSource:
    """申万行业 PIT 查询源：取数 + 本地缓存 + 时点查询。"""

    def __init__(self, cache_path: Optional[str] = None) -> None:
        self._cache_path = cache_path
        self._df: Optional[pd.DataFrame] = None

    def _default_cache_path(self) -> str:
        if self._cache_path:
            return self._cache_path
        # 遵循项目统一 runtime data root 约定（#335：release 目录不可变，缓存
        # 落 repo 内等于没有；与 longhubang/lockup 等 6 个兄弟模块同款）。
        root = runtime_data_root()
        base = os.path.join(root, "pit_cache")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "industry_pit.csv")

    def load_cached(self) -> bool:
        """仅从本地缓存加载（**绝不联网**）；命中返回 True。

        供消费者（pit_enrichment 默认单例）做缓存预热：缓存由
        ``scripts/data/fetch_pit_data.py`` 预载，缺失/损坏时静默保持未加载
        状态，由调用方走优雅回退。
        """
        path = self._default_cache_path()
        if not os.path.exists(path):
            return False
        try:
            self._df = _read_cache_df(path)
            return True
        except Exception:
            self._df = None  # 缓存损坏，保持未加载
            return False

    def load(self, force: bool = False) -> pd.DataFrame:
        """加载行业表：缓存命中且非强制时直接读本地，否则抓取并落盘。"""
        path = self._default_cache_path()
        if not force and self._df is None and os.path.exists(path):
            try:
                self._df = _read_cache_df(path)
                return self._df
            except Exception:
                self._df = None  # 缓存损坏，回退到抓取
        self._df = sw_industry_history()
        try:
            self._df.to_csv(path, index=False)
        except Exception:
            pass  # 缓存写入失败不阻断查询
        return self._df

    def from_dataframe(self, df: pd.DataFrame) -> "IndustryPitSource":
        """注入已规范化的表（测试 / 预加载用）。"""
        self._df = _normalize(df) if "股票代码" in df.columns else df
        return self

    def industry_as_of(
        self, symbol: str, as_of: str, autoload: bool = True
    ) -> Optional[IndustryLabel]:
        """查询 symbol 在 as_of 日的行业；未加载时按 autoload 自动加载。"""
        if self._df is None:
            if not autoload:
                raise DataError("industry_pit: 尚未加载行业表，请先 load()")
            self.load()
        assert self._df is not None
        return industry_as_of(self._df, symbol, as_of)
