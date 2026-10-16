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

    # 确认 diff 无误后落盘（替换该年份条目，其他年份保留）
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
2. **人工复核 diff**：重点看「新增」里是否包含法定长假的工作日，
   以及「删除」是否为 0（不该删掉已确认的休市日）。
3. `--write` 落盘 → `pytest tests/test_core_time.py tests/test_trading_holidays_generator.py`
   → 提 PR（`config/**` 会触发 CI）。

🔴 临时约束：**只对新年份执行 `--write`**（issue #392）
----------------------------------------------------
覆盖门 `scripts/monitor/check_before_live.py::_check_trading_calendar_coverage` 的
`critical_holidays` 仍硬编码要求 `2026-02-15` / `2026-02-21` / `2026-02-22` 在名单里，
而它们是**周末**、本工具的口径不写 ⇒ 对已覆盖年份落盘会把覆盖门打红。

`merge_year_holidays` 只替换目标年份，因此 `--year <次年> --write` 不碰既有年份，
是安全的。口径统一见 issue #392。
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
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
