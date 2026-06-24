from __future__ import annotations

import argparse
from collections.abc import Callable


def run_scheduled_service(
    args: argparse.Namespace,
    *,
    legacy_runner: Callable[[argparse.Namespace], int],
) -> int:
    """Service boundary for the scheduled research chain.

    The current implementation delegates to the legacy runner while the large
    CLI body is migrated behind this boundary in smaller, testable steps.
    """
    return legacy_runner(args)
