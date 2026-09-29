import json
import math
import os
import shutil
import subprocess

import pytest

import anim

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VECTORS = os.path.join(HERE, "anim_vectors.json")


def _cmp(a, b):
    if isinstance(b, dict):
        assert set(a) == set(b)
        return max([0.0] + [_cmp(a[k], b[k]) for k in b])
    if isinstance(b, str):
        assert a == b
        return 0.0
    return abs(a - b)


def test_python_matches_shared_vectors():
    v = json.load(open(VECTORS))
    worst = 0.0
    for c in v["cases"]:
        got = getattr(anim, c["fn"])(*c["args"])
        e = _cmp(got, c["out"])
        worst = max(worst, e)
        assert e <= v["tolerance"], c
    assert len(v["cases"]) > 3000 and worst == 0.0      # same code that generated them: bit-identical


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_matches_shared_vectors(tmp_path):
    shutil.copy(os.path.join(ROOT, "web", "js", "anim.js"), tmp_path / "anim.mjs")
    r = subprocess.run(["node", os.path.join(HERE, "anim_node_check.mjs"), str(tmp_path / "anim.mjs"), VECTORS],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    res = json.loads(r.stdout)
    assert res["bad"] == [] and res["n"] > 3000 and res["max_err"] <= 1e-9


# ---- independent facts (not derived from the twin implementations)
def test_css_bezier_reference_values():
    assert abs(anim.ease("ease", 0.5) - 0.8024033875) < 1e-6          # CSS `ease` at 0.5
    # independent brute-force solver (dense bisection on x(t)), not the Newton code under test
    def brute(x1, y1, x2, y2, x):
        fx = lambda t: 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3
        fy = lambda t: 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3
        lo, hi = 0.0, 1.0
        for _ in range(80):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if fx(mid) < x else (lo, mid)
        return fy((lo + hi) / 2)
    for pts in ([0.42, 0, 0.58, 1], [0.7, 0, 0.84, 0], [0.34, 1.56, 0.64, 1], [0.05, 0.9, 0.95, 0.1]):
        for x in (0.02, 0.25, 0.5, 0.77, 0.98):
            assert abs(anim.ease(pts, x) - brute(*pts, x)) < 1e-9
    assert anim.ease([0, 0, 1, 1], 0.3) == pytest.approx(0.3, abs=1e-9)  # linear bezier


def test_every_ease_hits_its_endpoints():
    for fam in anim.EASE_FAMILIES:
        for d in anim.EASE_DIRS:
            assert anim.ease(f"{d}-{fam}", 0) == 0 and anim.ease(f"{d}-{fam}", 1) == 1
    for s in ("hold", "steps(3)", "steps(3,start)", "cubic-bezier(.2,1.6,.3,1)", "linear"):
        assert anim.ease(s, 0) == 0 and anim.ease(s, 1) == 1


def test_known_easing_values():
    assert anim.ease("out-quad", 0.5) == 0.75 and anim.ease("in-cubic", 0.5) == 0.125
    assert anim.ease("in-out-quad", 0.25) == 0.125 and anim.ease("in-out-quart", 0.75) == pytest.approx(0.96875)
    assert anim.ease("out-back", 0.5) > 1.0 and anim.ease("out-bounce", 0.5) == 0.765625
    assert anim.ease("steps(4)", 0.3) == 0.25 and anim.ease("steps(4,start)", 0.3) == 0.5
    assert anim.ease("hold", 0.99) == 0 and anim.ease("nonsense", 0.4) == 0.4
    assert anim.parse_ease("out-nope") is None and anim.parse_ease("steps(0)") is None and anim.parse_ease([1, 2, 3]) is None


def test_keys_hold_segments_and_step():
    keys = [{"t": 1, "v": 10, "ease": "out-quad"}, {"t": 2, "v": 20, "hold": True}, {"t": 3, "v": 0}, {"t": 3, "v": 5}]
    kv = anim.key_value
    assert kv(keys, 0) == 10 and kv(keys, 1) == 10 and kv(keys, 1.5) == 17.5      # before first held; out-quad .5 = .75
    assert kv(keys, 2.9) == 20 and kv(keys, 3) == 5 and kv(keys, 99) == 5         # hold, duplicate t = step
    lay = {"x": 7, "keys": {"y": [{"t": 0, "v": 1}, {"t": 1, "v": 3}]}}
    assert anim.value(lay, "x", 5) == 7 and anim.value(lay, "y", 0.5) == 2 and anim.value({"opacity": 2}, "opacity", 0) == 1
    assert anim.value({}, "anchor_x", 0) == 0.5 and anim.value({}, "scale_y", 0) == 1


def test_noise_is_seeded_bounded_and_prop_salted():
    m = {"type": "wiggle", "amp": 10, "freq": 6, "octaves": 3, "seed": 5}
    a = [anim.mod_value(m, "x", 0, i / 30) for i in range(120)]
    assert a == [anim.mod_value(m, "x", 0, i / 30) for i in range(120)]
    assert max(abs(v) for v in a) <= 10 and max(a) > 3 and min(a) < -3
    assert a != [anim.mod_value(m, "y", 0, i / 30) for i in range(120)]           # x and y do not move in lockstep
    assert anim.mod_value({**m, "t0": 1, "t1": 2}, "x", 0, 0.5) == 0 == anim.mod_value({**m, "t0": 1, "t1": 2}, "x", 0, 2)
    d = {"type": "wiggle", "amp": 10, "freq": 6, "decay": 8}
    assert abs(anim.mod_value(d, "x", 0, 3)) < 1e-9 * 100
    assert anim.lowbias32(0) == 0 and 0 < anim.hash32(1, 2, 3) < 2 ** 32
