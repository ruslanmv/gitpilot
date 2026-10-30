"""Small-sample statistics for pass rates."""
from __future__ import annotations

import math
from typing import Iterable, Optional, Tuple


def wilson_interval(successes: float, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a proportion; well-behaved at n < 30 and at 0 or 1.

    ``successes`` may be fractional (mean over repeats times n).
    """
    if n <= 0:
        return (0.0, 0.0)
    p = min(max(successes / n, 0.0), 1.0)
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def median(values: Iterable[float]) -> Optional[float]:
    data = sorted(values)
    if not data:
        return None
    mid = len(data) // 2
    return data[mid] if len(data) % 2 else (data[mid - 1] + data[mid]) / 2


def percentile(values: Iterable[float], q: float) -> Optional[float]:
    """Nearest-rank percentile, q in [0, 100]."""
    data = sorted(values)
    if not data:
        return None
    rank = max(1, math.ceil(q / 100 * len(data)))
    return data[min(rank, len(data)) - 1]


def intervals_overlap(a: Tuple[float, float], b: Tuple[float, float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]
