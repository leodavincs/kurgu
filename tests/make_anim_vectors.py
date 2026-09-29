"""Generate tests/anim_vectors.json from anim.py (run once; re-run only when the maths deliberately changes).

    python3 tests/make_anim_vectors.py

The JSON is then the shared contract: test_anim.py checks anim.py against it and runs web/js/anim.js under node
against the same file (tolerance 1e-9)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import anim  # noqa: E402

cases = []


def add(fn, *args):
    out = getattr(anim, fn)(*args)
    cases.append({"fn": fn, "args": list(args), "out": out})


XS = [0, 1 / 64, 0.05, 0.1, 0.2, 0.25, 0.3, 0.37, 0.5, 0.5000001, 0.63, 0.75, 0.8, 0.9, 0.95, 63 / 64, 1]
for fam in anim.EASE_FAMILIES:
    for d in anim.EASE_DIRS:
        for x in XS:
            add("ease", f"{d}-{fam}", x)
for name in ("linear", "hold", "ease", "ease-in", "ease-out", "ease-in-out", "ease-in-quad", "out-quad", "bogus"):
    for x in XS:
        add("ease", name, x)
for pts in ([0.25, 0.1, 0.25, 1], [0.7, 0, 0.84, 0], [0.34, 1.56, 0.64, 1], [0.5, -0.5, 0.5, 1.5], [0, 0, 1, 1], [1, 0, 0, 1],
            [0.9, 0.1, 0.1, 0.9], [-1, 0, 2, 1]):
    for x in XS:
        add("ease", pts, x)
        add("ease", "cubic-bezier(%s)" % ",".join(str(p) for p in pts), x)
for spec in ("steps(4)", "steps(4,start)", "steps(5,none)", "steps(3,both)", {"type": "steps", "n": 6, "jump": "end"},
             {"type": "back", "overshoot": 3}, {"type": "back", "mode": "in-out", "overshoot": 1}, {"type": "elastic", "amp": 1.5, "period": 0.5},
             {"type": "elastic", "mode": "in", "period": 0.2}, {"type": "elastic", "mode": "in-out"}, {"type": "bounce", "mode": "in-out"}):
    for x in XS:
        add("ease", spec, x)
for seed in (0, 1, 7, 12345, 4294967295, -1):
    for a in (0, 1, -1, 17, 1000003):
        for b in (0, 1, 3):
            add("hash32", seed, a, b)
for s in ("x", "rotation", "fx.blur1.radius", "scale_x"):
    add("fnv1a", s)
for seed in (0, 3, 99):
    for octv in (1, 2, 4):
        for k in range(0, 40):
            add("fbm", seed * 1000 + 17, k * 0.137 + 0.01, octv)
MODS = [{"type": "wiggle", "amp": 8, "freq": 5, "octaves": 2, "seed": 1},
        {"type": "wiggle", "amp": 12, "freq": 9, "octaves": 3, "seed": 4, "t0": 0.5, "t1": 1.5, "decay": 6},
        {"type": "noise", "amp": 3, "freq": 12, "seed": 2},
        {"type": "noise", "amp": 3, "freq": 12, "seed": 2, "t0": 0.2, "decay": 2},
        {"type": "unknown"}]
for m in MODS:
    for prop in ("x", "y", "rotation"):
        for k in range(0, 30):
            add("mod_value", m, prop, k % 2, k * 0.083)
KEYS = {
    "scale": [{"t": 0, "v": 0.6, "ease": "out-back"}, {"t": 0.35, "v": 1}],
    "opacity": [{"t": 0, "v": 0, "ease": "out-quad"}, {"t": 0.2, "v": 1}, {"t": 1, "v": 1, "ease": "in-cubic"}, {"t": 1.4, "v": 0.2}],
    "y": [{"t": 0.5, "v": 540, "hold": True}, {"t": 1, "v": 620, "ease": [0.7, 0, 0.84, 0]}, {"t": 1, "v": 100}, {"t": 2, "v": 300, "ease": "steps(4)"}, {"t": 3, "v": 0}],
    "rotation": [{"t": 0, "v": -6}, {"t": 0.35, "v": 0, "ease": "out-elastic"}],
    "fx.blur1.radius": [{"t": 0, "v": 24, "ease": "out-cubic"}, {"t": 0.3, "v": 0}],
}
LAYERS = [
    {"x": 100, "y": 200, "scale": 2, "opacity": 0.5, "keys": KEYS},
    {"x": 100, "y": 200, "keys": KEYS, "mods": {"x": [MODS[0]], "y": [MODS[1], MODS[2]], "scale": [MODS[3]], "opacity": [MODS[2]]}},
    {"x": 5, "mods": {"x": [MODS[0]]}},
    {},
]
for L in LAYERS:
    for prop in ("x", "y", "scale", "opacity", "rotation", "anchor_x", "scale_x", "volume_db"):
        for k in range(0, 34):
            add("value", L, prop, k * 0.11 - 0.1)
EFF = {"id": "blur1", "type": "blur", "radius": 5}
for k in range(0, 12):
    add("effect_params", LAYERS[1], EFF, k * 0.05)
add("effect_params", LAYERS[1], {"type": "blur", "radius": 5}, 0.1)
for L in ({"scale": 1.5}, {"scale": 2, "scale_x": -3}, {"keys": {"scale": [{"t": 0, "v": 0}, {"t": 1, "v": 1.3}]}},
          {"scale": 0}, {"keys": {"scale": [{"t": 0, "v": 1}], "scale_y": [{"t": 0, "v": 0}, {"t": 1, "v": 2.5}]}}):
    add("raster_scale", L)
out = os.path.join(ROOT, "tests", "anim_vectors.json")
with open(out, "w") as f:
    json.dump({"tolerance": 1e-9, "cases": cases}, f, separators=(",", ":"))
print(len(cases), "cases ->", out)
