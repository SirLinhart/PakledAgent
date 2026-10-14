"""Percentiles of recorded values, by the nearest-rank method.

Nearest rank never interpolates: a percentile is always one of the values
that were recorded, so a reported latency is one that was really measured.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

PERCENTILES = (50, 95, 99)


def numbers(values: Iterable[Any]) -> list[float]:
    """The numbers among `values`: records can hold nulls and text where a figure was expected."""
    return [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]


def percentile(values: Iterable[Any], percent: int) -> float | None:
    """The nearest-rank percentile of the numbers among `values`; None when there is none."""
    ordered = sorted(numbers(values))
    if not ordered:
        return None
    rank = math.ceil(percent / 100 * len(ordered))
    return ordered[max(rank, 1) - 1]


def percentiles(values: Iterable[Any]) -> dict[str, float | None]:
    """The 50th, 95th and 99th nearest-rank percentiles of the numbers among `values`, as `p50`, `p95`, `p99`."""
    recorded = numbers(values)
    return {f"p{percent}": percentile(recorded, percent) for percent in PERCENTILES}
