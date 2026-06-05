#!/usr/bin/env python3
# ruff: noqa: E402
"""
Example: Using MultiSourceFetcher with Tushare/Akshare fallback.

This script demonstrates:
1. Creating a MultiSourceFetcher instance
2. Fetching daily OHLCV data with fallback
3. Handling source switching
4. Logging and monitoring
"""

from datetime import date, timedelta
import logging
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from aqsp.core.errors import DataError
from aqsp.data.akshare_source import AkshareSource
from aqsp.data.cache import DataCache
from aqsp.data.fetcher import MultiSourceFetcher

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)-8s [%(name)s] %(message)s",
)


def main() -> None:
    """Demonstrate MultiSourceFetcher usage."""
    print("=" * 60)
    print("MultiSourceFetcher Example")
    print("=" * 60)

    cache = DataCache()

    primary = AkshareSource(cache=cache)
    fallback = AkshareSource(cache=cache)

    fetcher = MultiSourceFetcher(primary, fallback, cache=cache)

    symbols = ["000001", "000002", "000858"]
    end = date.today()
    start = end - timedelta(days=30)

    print(f"\nFetching data for: {symbols}")
    print(f"Date range: {start} to {end}")
    print()

    try:
        data = fetcher.fetch_daily_data(symbols, start, end, adjust="qfq")

        print("\n" + "=" * 60)
        print("Fetch Results")
        print("=" * 60)

        for symbol in symbols:
            if symbol in data:
                df = data[symbol]
                source = fetcher.get_last_source_used(symbol)
                print(f"\n{symbol}:")
                print(f"  Source: {source}")
                print(f"  Rows: {len(df)}")
                print(f"  Date range: {df['date'].iloc[0]} to {df['date'].iloc[-1]}")
                print(f"  Columns: {', '.join(df.columns)}")

                print("\n  Sample (last 3 rows):")
                for _, row in df.tail(3).iterrows():
                    print(
                        f"    {row['date']} | open={row['open']:.2f} high={row['high']:.2f} "
                        f"close={row['close']:.2f} volume={row['volume']:.0f}"
                    )
            else:
                print(f"\n{symbol}: NOT FOUND")

        print("\n" + "=" * 60)
        print("Source Usage Summary")
        print("=" * 60)
        sources = fetcher.get_all_last_sources()
        for symbol, source_name in sources.items():
            print(f"  {symbol}: {source_name}")

    except DataError as exc:
        print(f"\nError: {exc}")
        print("\nTroubleshooting:")
        print("  - Check network connectivity")
        print("  - Verify akshare is installed: pip install akshare")
        print("  - Check API rate limits")
    except Exception as exc:
        print(f"\nUnexpected error: {exc}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
