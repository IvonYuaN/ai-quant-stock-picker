#!/usr/bin/env python3
"""预加载并缓存东财「事件」数据源到本地 CSV。生产机联网执行。

覆盖：停复牌 / 业绩预告 / 分红送转。单个源失败不阻断其余源。
"""

from __future__ import annotations

import sys

from aqsp.core.errors import DataError
from aqsp.data.dividend_plan import DividendPlanSource
from aqsp.data.earnings_forecast import EarningsForecastSource
from aqsp.data.suspend_resume import SuspendResumeSource


def _preload(name: str, source: object) -> bool:
    try:
        items = source.load(force=True)  # type: ignore[attr-defined]
    except DataError as exc:
        print(f"[{name}] 跳过（网络/解析失败）: {exc}", file=sys.stderr)
        return False
    path = source._default_cache_path()  # type: ignore[attr-defined]
    print(f"[{name}] 已缓存 {len(items)} 条 -> {path}")
    return True


def main() -> int:
    results = [
        _preload("suspend_resume", SuspendResumeSource()),
        _preload("earnings_forecast", EarningsForecastSource()),
        _preload("dividend_plan", DividendPlanSource()),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
