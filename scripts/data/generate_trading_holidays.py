#!/usr/bin/env python3
"""由深交所官方日历生成 / 复核 ``config/trading_holidays.json``（issue #314）。

把「每年 12 月人肉照抄交易所公告」换成「一条命令生成 + 人工复核 diff」。

用法
----
::

    # 默认：dry-run 复核**次年**（不写文件，只打印 diff）
    python scripts/data/generate_trading_holidays.py

    # 复核指定年份
    python scripts/data/generate_trading_holidays.py --year 2027

    # 确认 diff 无误后落盘（只补缺失条目，**不删**任何既有条目）
    python scripts/data/generate_trading_holidays.py --year 2027 --write

    # 机器可读（CI / 报告用）
    python scripts/data/generate_trading_holidays.py --year 2027 --json

退出码
------
- ``0``：12 个月全部取到交易所数据（dry-run 或写入成功）
- ``2``：有月份未取到数据（**尚未公布 / 取数失败**）⇒ 结果不可信，**拒绝落盘**
- ``1``：参数或 IO 错误

🔴 这是**离线批处理**，会联网打深交所接口（``szse_calendar.fetch_months``）。
运行时 ``is_trading_day()`` 只读缓存、绝不联网 —— 详见
``src/aqsp/data/szse_calendar.py`` 的模块 docstring。

年度运行手册（建议 12 月执行）
------------------------------
1. `python scripts/data/generate_trading_holidays.py --year <次年>`
   —— 交易所通常 12 月公布下一年日历；若返回退出码 2 说明还没公布，过几天再来。
2. **人工复核报告**：
   - 「待补」里应当是法定长假的工作日；核对是否与交易所公告一致；
   - 「冲突」**必须**为 0 —— 非 0 说明文件里有条目被交易所判为开市（#310 那类
     事实错误），要人工改文件，本工具**不自动删**；
   - 「文件中的周末条目」是人工补的**窗口内周末**（#314 要求列上），自动生成
     不会覆盖，也**不需要**核对。
3. **人工补窗口内周末**：深交所接口没有节假日名称字段，机器分不清「窗口内周末」
   与「普通周末」⇒ 按交易所公告把每个节日窗口内的周六/周日补进 `holidays`
   （例：2026 春节窗口 02-15 周日 ~ 02-23 周一，其中 02-15/02-21/02-22 是周末）。
   同时按 #314 待办第 2 条给 `check_before_live.py` 的 `critical_holidays` 补该年窗口。
4. `--write` 落盘 → `pytest tests/test_core_time.py tests/test_trading_holidays_generator.py`
   → 提 PR（`config/**` 会触发 CI）。

⚠️ `--write` 是**只增不删**：它不会移除任何既有条目（包括人工补的窗口内周末）。
要删错误条目只能人工改文件。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from aqsp.core.time import now_shanghai  # noqa: E402
from aqsp.data import trading_holidays as th  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INCOMPLETE = 2


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--year",
        type=int,
        default=None,
        help="目标年份（默认：次年）",
    )
    p.add_argument(
        "--write",
        action="store_true",
        help="确认后落盘（默认只做 dry-run 复核）",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"配置文件路径（默认 {th.DEFAULT_CONFIG_PATH}）",
    )
    p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="输出机器可读 JSON（报告 + 将要写入的内容）",
    )
    return p.parse_args(argv)


def _resolve_config_path(raw: Path | None) -> Path:
    if raw is not None:
        return raw
    # 允许从仓库任意子目录调用：以本脚本为锚点找仓库根（scripts/data/x.py ⇒ parents[2]）
    root = Path(__file__).resolve().parents[2]
    return root / th.DEFAULT_CONFIG_PATH


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    now = now_shanghai()
    year = args.year if args.year is not None else th.next_year(now.date())
    config_path = _resolve_config_path(args.config)

    try:
        report = th.build_holiday_report(year, config_path=config_path)
    except OSError as exc:  # 配置/缓存目录不可读等
        print(f"[generate-trading-holidays] IO 错误: {exc}", file=sys.stderr)
        return EXIT_ERROR

    merged = th.merge_year_holidays(
        report.existing, year=year, holidays=report.holidays
    )
    rendered = th.render_holiday_config(merged)

    # 落盘只在这里发生一次，且**仅在完整时**：缺月说明取数不完整，
    # 拿半份名单覆盖权威文件比不更新更危险。
    written = False
    if report.complete and args.write:
        try:
            current = (
                config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
            )
            if current != rendered:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(rendered, encoding="utf-8")
                written = True
        except OSError as exc:
            print(f"[generate-trading-holidays] IO 错误: {exc}", file=sys.stderr)
            return EXIT_ERROR

    if args.as_json:
        # ⚠️ 无论 dry-run / 写入 / 拒绝，只输出**一个** JSON 文档，便于管道解析。
        payload = report.as_dict()
        payload["config_path"] = str(config_path)
        payload["written"] = written
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(th.render_report(report, generated_at=now))
        if not report.complete:
            pass  # 拒绝落盘的原因由下面 stderr 的说明行给出
        elif not args.write:
            print("\n[dry-run] 未写入。确认上面的 diff 后加 --write 落盘。")
        elif written:
            print(f"\n[written] {config_path}（{len(merged)} 条）")
        else:
            print(f"\n[no-op] {config_path} 已是最新，未改动。")

    if not report.complete:
        print(
            f"[generate-trading-holidays] {year} 年有 "
            f"{len(report.missing_months)} 个月未取到交易所数据 "
            f"（{list(report.missing_months)}）⇒ 不落盘",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE

    if report.has_conflicts:
        # 本工具只增不删 ⇒ 冲突条目会被**保留**下来。必须响亮提示人工去改文件，
        # 否则 #310 那类事实错误（把交易日误列成休市）会一直留在生产名单里。
        print(
            f"[generate-trading-holidays] ⚠️ {year} 年有 "
            f"{len(report.conflicting_open_days)} 个条目被交易所判为**开市**，"
            f"但仍在名单里（本工具不自动删，需人工改文件）: "
            f"{[d.isoformat() for d in report.conflicting_open_days]}",
            file=sys.stderr,
        )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
