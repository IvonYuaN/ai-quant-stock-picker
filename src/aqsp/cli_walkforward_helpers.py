"""Walkforward helper functions extracted from ``cli.py``.

This module contains walk-forward validation helpers: HS300 symbol
resolution, date defaults, threshold metadata management, held-out
boundary guards, gate sidecar writing, and report formatting.

All symbols are re-exported by ``cli.py`` for backward compatibility.
"""

from __future__ import annotations

import argparse
import logging
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from aqsp.cli_notification_gate import HELDOUT_TRAIN_CUTOFF, WALKFORWARD_GATE_PATH
from aqsp.core.time import today_shanghai
from aqsp.data.index_constituents import load_optional_index_constituents
from aqsp.data.source_factory import resolve_sqlite_db_path, sqlite_price_mode
from aqsp.utils.jsonl_io import atomic_write_text
from aqsp.walkforward_gate import build_walkforward_gate_payload

LOGGER = logging.getLogger(__name__)

DEFAULT_WALKFORWARD_LOOKBACK_YEARS = 3


def _resolve_sqlite_db_path() -> str | None:
    return resolve_sqlite_db_path()


def _shift_years(raw: date, years: int) -> date:
    try:
        return raw.replace(year=raw.year - years)
    except ValueError:
        return raw.replace(month=2, day=28, year=raw.year - years)


def _default_walkforward_end() -> str:
    return today_shanghai().isoformat()


def _default_walkforward_start(
    *, end: str, lookback_years: int = DEFAULT_WALKFORWARD_LOOKBACK_YEARS
) -> str:
    return (
        _shift_years(date.fromisoformat(end), max(int(lookback_years), 1))
        + timedelta(days=1)
    ).isoformat()


def _walkforward_fetch_days(start: str, end: str) -> int:
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    span_days = max((end_d - start_d).days, 0)
    return max(260, int(span_days * 1.8) + 90)


def _get_hs300_symbols(as_of: date | None = None) -> list[str]:
    """沪深300成分股的近似快照（手工维护，去重后保序）。

    若本地已配置 `TUSHARE_TOKEN`，优先按 `as_of` 读取 000300.SH 成分；
    否则回退到手工快照。
    """
    target_day = as_of or today_shanghai()
    live_symbols = load_optional_index_constituents("000300.SH", target_day)
    if live_symbols:
        return live_symbols

    raw = [
        "600519",
        "601318",
        "600036",
        "000858",
        "600276",
        "601166",
        "600900",
        "601888",
        "000333",
        "002415",
        "300750",
        "601012",
        "000001",
        "600000",
        "002594",
        "600887",
        "002475",
        "300059",
        "000725",
        "002714",
        "601398",
        "601288",
        "600030",
        "600048",
        "601668",
        "600050",
        "601857",
        "601985",
        "600104",
        "600016",
        "601328",
        "600019",
        "601601",
        "601628",
        "600585",
        "601138",
        "600837",
        "601225",
        "600309",
        "601211",
        "600547",
        "601360",
        "600196",
        "601390",
        "600031",
        "601186",
        "600009",
        "601766",
        "601669",
        "600436",
        "600028",
        "600015",
        "601919",
        "601111",
        "600690",
        "600089",
        "601006",
        "601800",
        "600346",
        "601117",
        "601688",
        "600570",
        "600176",
        "601236",
        "601877",
        "600183",
        "600010",
        "600029",
        "601155",
        "600061",
        "600741",
        "600660",
        "601881",
        "600115",
        "601336",
        "601939",
        "601998",
        "600011",
        "600018",
        "600025",
        "600085",
        "600111",
        "600150",
        "600256",
        "600332",
        "600352",
        "600362",
        "600406",
        "600438",
        "600489",
        "600588",
        "600600",
        "600655",
        "600703",
        "600745",
        "600760",
        "600795",
        "600809",
        "600845",
        "600848",
        "600867",
        "600871",
        "600875",
        "600885",
        "600886",
        "600893",
        "600918",
        "600919",
        "600926",
        "600938",
        "600941",
        "600989",
        "601009",
        "601021",
        "601066",
        "601077",
        "601088",
        "601100",
        "601108",
        "601162",
        "601169",
        "601229",
        "601231",
        "601238",
        "601298",
        "601319",
        "601377",
        "601456",
        "601555",
        "601577",
        "601607",
        "601618",
        "601633",
        "601658",
        "601698",
        "601728",
        "601788",
        "601816",
        "601818",
        "601838",
        "601878",
        "601898",
        "601899",
        "601901",
        "601916",
        "601933",
        "601966",
        "601988",
        "601989",
        "601992",
        "603019",
        "603077",
        "603127",
        "603160",
        "603195",
        "603233",
        "603259",
        "603288",
        "603290",
        "603345",
        "603369",
        "603392",
        "603486",
        "603501",
        "603517",
        "603568",
        "603605",
        "603613",
        "603658",
        "603799",
        "603806",
        "603816",
        "603833",
        "603882",
        "603886",
        "603899",
        "603986",
        "603993",
        "000002",
        "000063",
        "000066",
        "000069",
        "000100",
        "000157",
        "000166",
        "000301",
        "000338",
        "000425",
        "000538",
        "000568",
        "000596",
        "000625",
        "000651",
        "000661",
        "000703",
        "000708",
        "000723",
        "000728",
        "000768",
        "000776",
        "000783",
        "000786",
        "000800",
        "000876",
        "000895",
        "000938",
        "000963",
        "000977",
        "001979",
        "002001",
        "002007",
        "002008",
        "002024",
        "002027",
        "002032",
        "002044",
        "002049",
        "002050",
        "002065",
        "002074",
        "002120",
        "002128",
        "002142",
        "002146",
        "002157",
        "002179",
        "002180",
        "002202",
        "002230",
        "002236",
        "002241",
        "002252",
        "002271",
        "002304",
        "002311",
        "002340",
        "002352",
        "002371",
        "002375",
        "002382",
        "002410",
        "002414",
        "002422",
        "002430",
        "002456",
        "002460",
        "002463",
        "002466",
        "002468",
        "002493",
        "002507",
        "002508",
        "002555",
        "002557",
        "002568",
        "002600",
        "002601",
        "002602",
        "002607",
        "002624",
        "002625",
        "002736",
        "002739",
        "002745",
        "002756",
        "002812",
        "002821",
        "002832",
        "002841",
        "002916",
        "002920",
        "002938",
        "002939",
        "002945",
        "002958",
        "003816",
        "004997",
        "300003",
        "300014",
        "300015",
        "300033",
        "300122",
        "300124",
        "300142",
        "300144",
        "300146",
        "300347",
        "300408",
        "300413",
        "300433",
        "300450",
        "300454",
        "300498",
        "300529",
        "300601",
        "300628",
        "300661",
        "300676",
        "300760",
        "300763",
        "300782",
        "300832",
        "300866",
        "300896",
        "300919",
        "300999",
    ]
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for s in raw:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _regime_description(regime: str) -> str:
    descriptions = {
        "stable_bull": "稳定上涨：低波动+正趋势",
        "volatile_bull": "波动上涨：高波动+正趋势",
        "stable_bear": "稳定下跌：低波动+负趋势",
        "volatile_bear": "波动下跌：高波动+负趋势",
        "stable_sideways": "稳定盘整：低波动+无趋势",
        "volatile_sideways": "波动盘整：高波动+无趋势",
        "bull_trend": "牛市趋势：20日均收益 > 0.5%",
        "mild_bear": "温和熊市：20日均收益 -0.5% ~ -2%",
        "sideways": "震荡市：20日均收益 -0.5% ~ 0.5%",
        "bear_filter": "熊市过滤：20日均收益 < -2%",
    }
    return descriptions.get(regime, "未知 regime")


def _find_thresholds_yaml() -> Path | None:
    """Locate config/thresholds.yaml relative to this file or CWD.

    cli.py lives at <repo>/src/aqsp/cli.py — repo root is parents[2].
    Fall back to CWD-relative for non-standard installs.
    """
    candidate = Path(__file__).resolve().parents[2] / "config" / "thresholds.yaml"
    if candidate.exists():
        return candidate
    cwd_candidate = Path.cwd() / "config" / "thresholds.yaml"
    if cwd_candidate.exists():
        return cwd_candidate
    return None


def _update_thresholds_metadata(run_date: str) -> bool:
    """Rewrite last_walkforward_run in thresholds.yaml.

    Returns True if the field was found and updated, False otherwise.
    Tolerant to double-quoted, single-quoted, or bare values.
    """
    import re

    path = _find_thresholds_yaml()
    if path is None:
        return False
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'^(last_walkforward_run:\s*)("[^"]*"|\'[^\']*\'|[^\s#].*?)(\s*(?:#.*)?)$',
        flags=re.MULTILINE,
    )
    new_content, n = pattern.subn(
        lambda m: f'{m.group(1)}"{run_date}"{m.group(3)}',
        content,
    )
    if n == 0:
        return False
    path.write_text(new_content, encoding="utf-8")
    return True


def _assert_not_heldout(end: str, *, allow: bool, logger=None) -> None:
    """宪法 §1.3 #9：end 不得越过 held-out 边界。

    end > 2024-12-31 且未显式 --allow-heldout → fail loud（SystemExit）。
    开了 --allow-heldout → 红字警告放行并留痕（一次性 held-out 验收专用）。

    日期用 date.fromisoformat 解析后比较，而非字符串字典序——避免
    "2024/12/31" 或带空格等非标准格式被误判。非法日期本身 fail loud。
    """
    from datetime import date

    cutoff = date.fromisoformat(HELDOUT_TRAIN_CUTOFF)
    try:
        end_d = date.fromisoformat(end.strip())
    except (ValueError, AttributeError) as exc:
        raise SystemExit(
            f"[宪法 §1.3 #9] --end={end!r} 不是合法 ISO 日期 (YYYY-MM-DD): {exc}"
        ) from exc

    if end_d <= cutoff:
        return
    msg = f"[宪法 §1.3 #9] --end={end} 越过 held-out 边界 {HELDOUT_TRAIN_CUTOFF}，会把 2025-01~2026-04 held-out 区间卷入训练。"
    if not allow:
        raise SystemExit(
            msg
            + "\n  这是绝对禁止条款。如确为 held-out 一次性验收，显式加 --allow-heldout（且必须在双门通过后）。"
        )
    warn = (
        "⚠️  "
        + msg
        + "\n  已显式 --allow-heldout 放行。请确认这是双门通过后的一次性 held-out 验收，结果不得回灌训练。"
    )
    print(warn)
    if logger:
        logger.warning(warn)


def _resolve_walkforward_window_args(args: argparse.Namespace) -> None:
    if not str(getattr(args, "end", "") or "").strip():
        args.end = _default_walkforward_end()
    if not str(getattr(args, "start", "") or "").strip():
        args.start = _default_walkforward_start(end=str(args.end))


def _write_walkforward_gate(
    *,
    dsr: float,
    pbo: float,
    run_date: str,
    start: str,
    end: str,
    n_periods: int,
    metadata: dict[str, object] | None = None,
    diagnostics: dict[str, object] | None = None,
    pbo_verified: bool | None = None,
    gate_path: str | Path = WALKFORWARD_GATE_PATH,
) -> None:
    """写双门 sidecar，供 run_scheduled 的 notify gate 读取。
    独立 JSON，不污染 thresholds.yaml，不 bump version（§1.3 #12/#14）。
    """
    import json

    payload = build_walkforward_gate_payload(
        dsr=dsr,
        pbo=pbo,
        run_date=run_date,
        start=start,
        end=end,
        n_periods=n_periods,
        metadata=metadata,
        pbo_verified=pbo_verified,
    )
    if diagnostics:
        payload["grid_diagnostics"] = diagnostics
    p = Path(gate_path)
    atomic_write_text(p, json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"✅ 双门 sidecar 已写入: {p}（both_pass={payload['both_pass']}）")


def _walkforward_gate_metadata(
    args: argparse.Namespace,
    *,
    effective_symbols: int | None = None,
    fee_bps: float | None = None,
    slippage_bps: float | None = None,
    purge_days: int | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source": str(getattr(args, "source", "") or ""),
        "window_mode": str(
            getattr(args, "window_mode", "rolling_recent") or "rolling_recent"
        ),
        "skip_pit_financials": bool(getattr(args, "skip_pit_financials", False)),
    }
    if effective_symbols is not None:
        metadata["effective_symbols"] = int(effective_symbols)
    if bool(getattr(args, "grid_cscv", False)):
        metadata["grid_profile"] = str(
            getattr(args, "grid_profile", "stable") or "stable"
        )
    if bool(getattr(args, "streaming", False)):
        metadata["memory_mode"] = "streaming"
        metadata["stream_batch_size"] = int(getattr(args, "stream_batch_size", 0) or 0)
    if metadata["source"] == "sqlite_db":
        db_path = _resolve_sqlite_db_path()
        if db_path:
            metadata["sqlite_db_path"] = db_path
            try:
                metadata["price_mode"] = sqlite_price_mode(db_path)
            except Exception:  # noqa: BLE001
                metadata["price_mode"] = "unknown"
        else:
            metadata["price_mode"] = "unknown"
    # 声明回测假设,让 walkforward gate 的 assumption audit 真正生效
    # (audit_backtest_assumptions 校验 7 项硬约束 + cost_model + price_mode)
    price_mode_str = str(metadata.get("price_mode", "raw") or "raw").strip().lower()
    resolved_fee = float(fee_bps) if fee_bps is not None else 0.0
    resolved_slippage = float(slippage_bps) if slippage_bps is not None else 0.0
    resolved_purge = (
        int(purge_days)
        if purge_days is not None
        else int(getattr(args, "purge_days", 5) or 5)
    )
    end_date = str(getattr(args, "end", "") or "")
    metadata["backtest_assumptions"] = {
        "uses_raw_prices": price_mode_str == "raw",
        "uses_point_in_time_data": True,
        "train_test_separated": True,
        "has_purge_window": resolved_purge > 0,
        "includes_transaction_costs": resolved_fee > 0,
        "includes_slippage": resolved_slippage > 0,
        "excludes_not_executable": True,
        "cost_model": f"fee_bps={resolved_fee:.4g},slippage_bps={resolved_slippage:.4g}",
        "price_mode": price_mode_str,
        "future_data_used": False,
        "data_cutoff": end_date,
        "signal_cutoff": end_date,
    }
    return metadata


def _format_walkforward_count_map(
    items: dict[str, int] | tuple[tuple[str, int], ...],
) -> str:
    if not items:
        return "无"
    pairs = items.items() if isinstance(items, dict) else items
    return "；".join(f"{key}: {value}" for key, value in sorted(pairs))


def _append_walkforward_diagnostics(report_lines: list[str], result: Any) -> None:
    diagnostics = getattr(result, "diagnostics", None)
    if diagnostics is None:
        return

    report_lines.extend(
        [
            "",
            "## 失败诊断",
            "",
            "| 指标 | 值 |",
            "|------|-----|",
            f"| 总信号交易 | {diagnostics.total_trades} |",
            f"| 可成交交易 | {diagnostics.executable_trades} |",
            f"| 不可成交 | {diagnostics.not_executable} |",
            f"| 退出原因 | {_format_walkforward_count_map(diagnostics.exit_reason_counts)} |",
            f"| 不可成交原因 | {_format_walkforward_count_map(diagnostics.not_executable_reason_counts)} |",
            "",
        ]
    )

    if diagnostics.worst_symbols:
        report_lines.extend(
            [
                "### 拖累最大的标的",
                "",
                "| Symbol | 交易次数 | 平均收益点 | 累计收益点 |",
                "|--------|----------|------------|------------|",
            ]
        )
        for symbol, trades, avg_return, sum_return in diagnostics.worst_symbols:
            report_lines.append(
                f"| {symbol} | {trades} | {avg_return:.4f}% | {sum_return:.4f}% |"
            )
    else:
        report_lines.append("*无可成交标的诊断*")


def _format_walkforward_pbo(pbo: float, pbo_is_valid: bool) -> str:
    if not pbo_is_valid or not math.isfinite(pbo):
        return "未验证（CSCV 需 ≥2 配置，单策略无法估计过拟合概率）"
    return f"{pbo:.2%}"


def _walkforward_runtime_rows(
    args: argparse.Namespace,
    effective_horizon: int,
    *,
    fee_bps: float,
    slippage_bps: float,
) -> list[tuple[str, str]]:
    min_score = "thresholds.yaml"
    if getattr(args, "min_score", None) is not None:
        min_score = str(args.min_score)
    return [
        ("source", str(args.source)),
        ("pool", str(getattr(args, "pool", ""))),
        ("symbols", str(args.symbols or "AQSP_WALKFORWARD_SYMBOLS/default_pool")),
        ("engine", str(getattr(args, "engine", "") or "runtime_config/auto")),
        ("min_score", min_score),
        ("horizon_days", str(effective_horizon)),
        (
            "grid_profile",
            str(getattr(args, "grid_profile", "stable") or "stable")
            if bool(getattr(args, "grid_cscv", False))
            else "-",
        ),
        ("fee_bps", f"{fee_bps:.4g}"),
        ("slippage_bps", f"{slippage_bps:.4g}"),
        ("tiered_stop", str(bool(getattr(args, "tiered_stop", False)))),
        ("cache_path", str(getattr(args, "cache_path", "") or "")),
        ("allow_heldout", str(bool(getattr(args, "allow_heldout", False)))),
    ]
