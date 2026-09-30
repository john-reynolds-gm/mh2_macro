"""seq_guardrail tests: contract §5.8 shared fixture table G0-G5 + no verdict key."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mh2 import seq_guardrail as g  # noqa: E402


def _pl(pairs):
    return [{"calibration": c, "period_estimate": e} for c, e in pairs]


def _shares(d, dd=None, ff=None, ii=None):
    return {"deep": dd, "functional": ff, "illuminating": ii}


CASES = {
    "G0": ([], dict(
        n=0, counts={"deep": 0, "functional": 0, "illuminating": 0, "unset": 0},
        n_calibrated=0, count_share=_shares(0), n_timed=0,
        periods={"deep": 0.0, "functional": 0.0, "illuminating": 0.0, "unset": 0.0},
        total_periods=0.0, time_share=_shares(0), time_coverage=None,
        show_time_targets=False)),
    "G1": ([("deep", 2), ("functional", 1), ("functional", None), (None, 1.5)], dict(
        n=4, counts={"deep": 1, "functional": 2, "illuminating": 0, "unset": 1},
        n_calibrated=3, count_share=_shares(0, .3333, .6667, 0.0), n_timed=3,
        periods={"deep": 2.0, "functional": 1.0, "illuminating": 0.0, "unset": 1.5},
        total_periods=4.5, time_share=_shares(0, .6667, .3333, 0.0),
        time_coverage=.75, show_time_targets=True)),
    "G2": ([(None, None), (None, None)], dict(
        n=2, counts={"deep": 0, "functional": 0, "illuminating": 0, "unset": 2},
        n_calibrated=0, count_share=_shares(0), time_share=_shares(0), n_timed=0,
        time_coverage=0.0, show_time_targets=False)),
    "G3": ([("deep", 1), ("deep", None)], dict(
        count_share=_shares(0, 1.0, 0.0, 0.0), time_coverage=0.5,
        show_time_targets=True)),
    "G4": ([("illuminating", 0.25), ("illuminating", 0.125)], dict(
        periods={"deep": 0.0, "functional": 0.0, "illuminating": 0.38, "unset": 0.0},
        total_periods=0.38, time_share=_shares(0, 0.0, 0.0, 1.0), time_coverage=1.0)),
    "G5": ([("deep", 1), ("functional", 1), ("illuminating", 1)], dict(
        count_share=_shares(0, .3333, .3333, .3333),
        time_share=_shares(0, .3333, .3333, .3333), total_periods=3.0)),
}


def _check(name):
    pairs, expected = CASES[name]
    out = g.compute(_pl(pairs))
    for key, want in expected.items():
        assert out[key] == want, f"{name}.{key}: got {out[key]!r} want {want!r}"


def test_G0_empty():
    _check("G0")


def test_G1_mixed():
    _check("G1")


def test_G2_all_unset():
    _check("G2")


def test_G3_boundary_shows_time_targets():
    _check("G3")


def test_G4_half_up_rounding():
    _check("G4")


def test_G5_thirds():
    _check("G5")


def test_constants_and_no_verdict_key():
    out = g.compute(_pl([("deep", 1)]))
    assert out["count_target"] == {"deep": 0.25, "functional": 0.5, "illuminating": 0.25}
    assert out["time_target"] == {"deep": 0.4, "functional": 0.45, "illuminating": 0.15}
    assert out["time_mark_min_coverage"] == 0.5
    assert set(out) & {"pass", "fail", "ok", "status"} == set()
    # targets are copies: mutating the result must not change the constants
    out["count_target"]["deep"] = 9
    assert g.COUNT_TARGET["deep"] == 0.25


def test_round_half_up():
    assert g.round_half_up(0.375, 2) == 0.38
    assert g.round_half_up(2.5, 0) == 3.0
    assert g.round_half_up(1 / 3, 4) == 0.3333


def test_json_safe():
    import json
    json.dumps(g.compute(_pl([("deep", 1), (None, None)])))


def _main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, repr(e))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
