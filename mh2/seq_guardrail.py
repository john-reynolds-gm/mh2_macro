"""
seq_guardrail.py -- pure readout arithmetic for a sequence or module.

Authoritative formula: docs/seq_v1_contract.md §5.8 (the JS computeGuardrail
mirrors it).  No DB, no FastAPI.  There is deliberately NO pass/fail key in
the output, so the page cannot render a verdict (data model §7).
"""
from __future__ import annotations

import math
from typing import Iterable

COUNT_TARGET = {"deep": 0.25, "functional": 0.50, "illuminating": 0.25}
TIME_TARGET = {"deep": 0.40, "functional": 0.45, "illuminating": 0.15}
TIME_MARK_MIN_COVERAGE = 0.5
_LEVELS = ("deep", "functional", "illuminating")


def round_half_up(x: float, dp: int) -> float:
    """math.floor(x * 10**dp + 0.5) / 10**dp   (x >= 0)."""
    return math.floor(x * 10 ** dp + 0.5) / 10 ** dp


def compute(placements: Iterable[dict]) -> dict:
    rows = list(placements)
    n = len(rows)
    counts = {lv: 0 for lv in _LEVELS}
    counts["unset"] = 0
    raw = {lv: 0.0 for lv in _LEVELS}
    raw["unset"] = 0.0
    n_timed = 0
    n_from_ladder = 0
    total_raw = 0.0
    for r in rows:
        cal = r.get("calibration")
        est = r.get("period_estimate")
        bucket = cal if cal in _LEVELS else "unset"
        counts[bucket] += 1
        if est is not None:
            raw[bucket] += est          # accumulated in input order
            total_raw += est
            n_timed += 1
            if r.get("estimate_source") == "ladder":
                n_from_ladder += 1   # autofilled, not yet edited or confirmed (O8/O9)
    n_cal = n - counts["unset"]
    t = sum(raw[lv] for lv in _LEVELS)   # raw (unrounded) calibrated total
    return {
        "n": n,
        "n_calibrated": n_cal,
        "n_timed": n_timed,
        "n_from_ladder": n_from_ladder,
        "counts": counts,
        "count_share": {lv: (round_half_up(counts[lv] / n_cal, 4) if n_cal > 0 else None)
                        for lv in _LEVELS},
        "periods": {k: round_half_up(v, 2) for k, v in raw.items()},
        "total_periods": round_half_up(total_raw, 2),
        "time_share": {lv: (round_half_up(raw[lv] / t, 4) if t > 0 else None)
                       for lv in _LEVELS},
        "time_coverage": round_half_up(n_timed / n, 4) if n > 0 else None,
        "show_time_targets": bool(n > 0 and (n_timed / n) >= TIME_MARK_MIN_COVERAGE),
        "count_target": dict(COUNT_TARGET),
        "time_target": dict(TIME_TARGET),
        "time_mark_min_coverage": TIME_MARK_MIN_COVERAGE,
    }
