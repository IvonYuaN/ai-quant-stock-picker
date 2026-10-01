#!/usr/bin/env python3
"""planb_b_trigger.py — 方案B 口径B 触发巡检（机械 5 数据日 streak，proposal-only）。

背景（`outputs/预注册裁决单_方案B_口径B_2026-10-01.md`）：
  09-30 版裁决单的双窗 73/73 主判因截面拆半稀释结构性不可达；口径 B 把主判升为
  146 单窗 |t|≥2（3 年全样本），双窗 73/73 降为同号辅判。触发 = 某因子连续
  **5 个数据日**（按 as_of 去重，只算 as_of < 触发日 2026-11-07）三判全过：
    1) 该数据日单窗 146 截面 |t| >= 2（逐日真 t：优先读 IC ledger 的 per-day
       `t` 字段；旧行无该字段 ⇒ 保守回退到当日 factor_ic_latest 的 t）
    2) 当日双窗 73/73 sign_match == true（同号，不要求双 |t|≥2 —— 拆半稀释
       是已知口径性质，非否决项）
    3) held-out：recent（末 30 截面）mean 与 overall mean 同号
  ⇒ 在 --dir 下落 planb_b_event_<factor>.marker（JSON 证据：达标日/读数/streak 明细）。

设计约束（红线）：
  - 纯机械（无 LLM、无手工、无阈值参数化口子）；缺文件/缺字段 = 当日记 absent，不 crash；
  - 只读 IC 产物 + 写 marker 文件（幂等：marker 已存在则跳过，不改名不删）；
  - 不写打分/排序/下单，不动 yaml/cron（proposal-only）；
  - 事件路由（V1→V2→V3 执行序）在裁决单 §二 钉死，本脚本只产「哪个因子触发」证据。

用法（prod cron `5 11 * * 1-5`，fetch_ic_diagnosis.sh `0 11` 之后 5 分钟）：
  python3 scripts/analysis/planb_b_trigger.py \
    --dir /opt/aqsp/data/pit_cache/factor_ic \
    --single factor_ic_latest.json --dual dual_window_latest.json \
    --ledger ic_history.jsonl
本机冒烟（指向拉回的副本）：
  python3 scripts/analysis/planb_b_trigger.py --dir /tmp/planb_b_smoke \
    --single <path>/factor_ic_latest.json --dual <path>/dual_window_latest.json
  # 相对文件名解析在 --dir 下；也接受绝对路径。
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timezone
from pathlib import Path

from aqsp.core.time import now_shanghai, today_shanghai

SIG_T = 2.0          # 单窗 146 截面 |t| 门槛（口径 B 主判）
STREAK_DAYS = 5      # 连续数据日数（IC ledger 按 as_of 去重）
INVALID_DATE = date(2026, 11, 7)  # 兜底失效线（裁决单 §五）：as_of < 该日才计入 streak


def _load_json(path: str, base: Path) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = base / p
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _factors(data: dict) -> dict:
    fac = data.get("factors")
    return fac if isinstance(fac, dict) else {}


def _num(v, key: str):
    if isinstance(v, dict):
        val = v.get(key)
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _same_sign(a, b) -> bool:
    return a is not None and b is not None and a * b > 0.0


def read_ledger(base: Path, name: str) -> list:
    """IC ledger（每数据日一行）：[{as_of, run_at, factors, t}, ...]，按 as_of 升序去重。

    `factors` 字段各 reader 语义 = {name: mean_IC}（向后兼容不动）；
    口径B 起新行额外带 `t` = {name: overall_t}（逐日真 t，旧行缺失）。
    """
    p = base / name
    if not p.is_file():
        return []
    best: dict[str, dict] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        as_of = str(row.get("as_of") or "")[:10]
        if not as_of:
            continue
        best[as_of] = row
    return [best[k] for k in sorted(best)]


def _asof_date(s) -> object:
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def day_t(row: dict, name: str, today_t):
    """该数据日的 146 截面 |t|：优先 ledger per-day t；旧行无 ⇒ 回退当日 t（保守：旧行少，不会虚增 streak）。"""
    t_field = row.get("t")
    if isinstance(t_field, dict) and t_field.get(name) is not None:
        return _num(t_field, name)
    return today_t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="/opt/aqsp/data/pit_cache/factor_ic",
                    help="IC 产物目录（marker 也落这里；缺省 = prod 运行时路径）")
    ap.add_argument("--single", default="factor_ic_latest.json")
    ap.add_argument("--dual", default="dual_window_latest.json")
    ap.add_argument("--ledger", default="ic_history.jsonl")
    a = ap.parse_args()
    base = Path(a.dir)

    single = _load_json(a.single, base)
    dual = _load_json(a.dual, base)
    s_fac = _factors(single)
    d_fac = _factors(dual)
    ledger = read_ledger(base, a.ledger)

    today_t = {n: _num(s, "t") for n, s in s_fac.items()}
    today_mean = {n: _num(s, "mean") for n, s in s_fac.items()}
    today_recent = {n: s.get("recent") for n, s in s_fac.items()}

    names = sorted(
        set(s_fac) | set(d_fac)
        | {k for r in ledger for k in ((r.get("factors") or {}) | (r.get("t") or {}))}
    )

    # 每个因子的「数据日 → 是否三判全过」序列（ledger as_of 升序，逐日真 t）
    triggered: list[str] = []
    rows_out = []
    for name in names:
        seq = []
        for row in ledger:
            as_of = str(row.get("as_of") or "")[:10]
            dd = _asof_date(as_of)
            if dd is None or dd >= INVALID_DATE:
                seq.append((as_of, False))
                continue
            t = day_t(row, name, today_t.get(name))
            if t is None or abs(t) < SIG_T:
                seq.append((as_of, False))
                continue
            dual_entry = d_fac.get(name)
            if not isinstance(dual_entry, dict) or not bool(dual_entry.get("sign_match")):
                seq.append((as_of, False))
                continue
            recent = today_recent.get(name)
            mean_all = today_mean.get(name)
            if recent is None:
                # 缺 held-out = 无法核 ⇒ 保守不通过（proposal-only 从严）
                seq.append((as_of, False))
                continue
            m30 = _num(recent, "mean")
            if m30 is None or not _same_sign(m30, mean_all):
                seq.append((as_of, False))
                continue
            # ledger 交叉核对：该数据日 mean_IC（旧字段语义）与当日 overall mean 同号（防产物错位）
            lf = row.get("factors")
            lmean = _num(lf, name) if isinstance(lf, dict) else None
            if lmean is not None and not _same_sign(lmean, mean_all):
                seq.append((as_of, False))
                continue
            seq.append((as_of, True))
        streak = 0
        for _as_of, ok in reversed(seq):
            if ok:
                streak += 1
            else:
                break
        marker_path = base / f"planb_b_event_{name}.marker"
        if streak >= STREAK_DAYS:
            if marker_path.is_file():
                print(f"  [skip] {name}: streak={streak} 已有 marker（幂等，不重写）")
                rows_out.append((name, streak, "[marker-exists]"))
                continue
            days = [as_of for as_of, ok in reversed(seq) if ok][:STREAK_DAYS]
            evidence = {
                "factor": name,
                "streak": streak,
                "days": days,
                "today_single_t": today_t.get(name),
                "today_dual_sign_match": bool(d_fac.get(name, {}).get("sign_match"))
                if isinstance(d_fac.get(name), dict) else False,
                # §3.4 全项目禁裸 datetime.now()/date.today()：统一走项目时钟
                "checked_at_utc": now_shanghai().astimezone(timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "criteria": "口径B: 逐日单窗146|t|>=2 AND 双窗73/73 sign_match AND "
                            "held-out30同向, 5数据日streak(as_of去重, 限as_of<2026-11-07)",
            }
            marker_path.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            triggered.append(name)
            print(f"  [EVENT] {name}: 口径B 主判 streak={streak}（近 {days}）⇒ marker 已落 {marker_path.name}")
        else:
            print(
                f"  {name:12} streak={streak:<3}（需 {STREAK_DAYS}）"
                f" 当日单窗t={today_t.get(name)} sign_match="
                f"{bool(d_fac.get(name, {}).get('sign_match')) if isinstance(d_fac.get(name), dict) else 'N/A'}"
            )
            rows_out.append((name, streak, str(today_t.get(name))))

    print(
        f"date={today_shanghai()} single_as_of={single.get('as_of')} "
        f"dual_as_of_b={dual.get('as_of_b')} ledger_days={len(ledger)}"
    )
    if triggered:
        print(
            "口径B 触发事件: "
            + ", ".join(triggered)
            + "（按裁决单§二事件路由执行；proposal-only，不写打分/排序/下单）"
        )
    else:
        print("口径B 未触发（streak 未达 5 数据日）；marker 目录保持幂等，下一巡检日重判。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
