"""Kurgu motion maths (keyframes, easing, noise modifiers). Python twin of web/js/anim.js.

Single source of truth for *values over time*. Both twins are pure functions, use no state and no random
source, and are checked against the same vectors in tests/anim_vectors.json (tolerance 1e-9).
Keep the two files line-for-line parallel: any change here must be made in web/js/anim.js too.

Determinism rules (ROADMAP §4.3): integer hash lowbias32 on uint32, value noise built only from
+ - * floor on doubles and smoothstep; sin/cos/sqrt/pow/exp only in the easing closed forms.

Key model (SPEC §1.8): layer["keys"][prop] = [{"t": s, "v": n, "ease": spec, "hold": bool}, ...]
  * t is layer-local seconds (time - start), keys sorted by t; before the first / after the last key the value is held;
  * the ease of a key shapes the segment LEAVING that key; two keys with the same t make a step (the later wins);
  * value = keys (or the static base when there are none) + sum of modifiers (layer["mods"][prop]), then clamped.
Effect parameters use the prop name "fx.<effect id>.<param>".
"""
import math

M32 = 0xFFFFFFFF

TRANSFORM_PROPS = ("x", "y", "scale", "scale_x", "scale_y", "rotation", "anchor_x", "anchor_y")
DEFAULTS = {"scale": 1.0, "scale_x": 1.0, "scale_y": 1.0, "rotation": 0.0, "anchor_x": 0.5, "anchor_y": 0.5,
            "opacity": 1.0, "volume_db": 0.0}
EASE_FAMILIES = ("sine", "quad", "cubic", "quart", "quint", "expo", "circ", "back", "elastic", "bounce")
EASE_DIRS = ("in", "out", "in-out")
CSS_EASES = {"ease": (0.25, 0.1, 0.25, 1.0), "ease-in": (0.42, 0.0, 1.0, 1.0), "ease-out": (0.0, 0.0, 0.58, 1.0),
             "ease-in-out": (0.42, 0.0, 0.58, 1.0)}
JUMPS = ("end", "start", "none", "both")


def named_eases():
    """All string ease names accepted (for error messages)."""
    return ["linear", "hold"] + [f"{d}-{f}" for f in EASE_FAMILIES for d in EASE_DIRS] + \
        list(CSS_EASES) + ["cubic-bezier(x1,y1,x2,y2)", "steps(n[,end|start|none|both])"]


# ---------------------------------------------------------------- integer hash + noise
def lowbias32(x):
    x &= M32
    x ^= x >> 16
    x = (x * 0x7FEB352D) & M32
    x ^= x >> 15
    x = (x * 0x846CA68B) & M32
    x ^= x >> 16
    return x


def hash32(seed, a, b):
    """uint32 hash of three integers (negative / large values are taken modulo 2**32)."""
    h = lowbias32(int(seed) & M32)
    h = lowbias32(h ^ (int(a) & M32))
    return lowbias32(h ^ (int(b) & M32))


def fnv1a(s):
    """32-bit FNV-1a of an ASCII string (prop names)."""
    h = 0x811C9DC5
    for ch in s.encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & M32
    return h


def lattice(seed, i, o):
    """Lattice value in [-1, 1) (exact in doubles)."""
    return hash32(seed, i, o) / 4294967296.0 * 2.0 - 1.0


def fbm(seed, x, octaves):
    """Smooth value noise in [-1, 1]: `octaves` layers (each 2x frequency, half amplitude), smoothstep interpolation."""
    total = 0.0
    norm = 0.0
    amp = 1.0
    f = 1.0
    for o in range(octaves):
        xx = x * f
        i = math.floor(xx)
        fr = xx - i
        u = fr * fr * (3.0 - 2.0 * fr)
        a = lattice(seed, i, o)
        b = lattice(seed, i + 1, o)
        total += amp * (a + (b - a) * u)
        norm += amp
        amp *= 0.5
        f *= 2.0
    return total / norm


def _num(v, d):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d


def mod_value(mod, prop, index, t):
    """Contribution of one modifier at layer-local time t.
    wiggle: smooth fbm noise (freq Hz, octaves); noise: sample-and-hold jitter (new random value freq times a second).
    Window [t0, t1) (default: always); `decay` fades exp(-decay * (t - t0)) from the window start."""
    typ = mod.get("type")
    if typ not in ("wiggle", "noise"):
        return 0.0
    t0 = _num(mod.get("t0"), 0.0)
    t1 = mod.get("t1")
    if t < t0 or (isinstance(t1, (int, float)) and not isinstance(t1, bool) and t >= t1):
        return 0.0
    amp = _num(mod.get("amp"), 1.0)
    freq = _num(mod.get("freq"), 5.0)
    decay = _num(mod.get("decay"), 0.0)
    seed = hash32(int(_num(mod.get("seed"), 0.0)), fnv1a(prop), index)
    if typ == "wiggle":
        octaves = max(1, min(6, int(_num(mod.get("octaves"), 1.0))))
        n = fbm(seed, t * freq, octaves)
    else:
        n = lattice(seed, math.floor(t * freq), 0)
    env = math.exp(-decay * (t - t0)) if decay > 0 else 1.0
    return amp * n * env


# ---------------------------------------------------------------- easing
def _bezier_x(ax, bx, cx, t):
    return ((ax * t + bx) * t + cx) * t


def bezier(x1, y1, x2, y2, x):
    """CSS cubic-bezier(x1,y1,x2,y2) at progress x: x1/x2 clamped to 0..1, Newton (8) then bisection (64)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    x1 = min(1.0, max(0.0, x1))
    x2 = min(1.0, max(0.0, x2))
    cx = 3.0 * x1
    bx = 3.0 * (x2 - x1) - cx
    ax = 1.0 - cx - bx
    cy = 3.0 * y1
    by = 3.0 * (y2 - y1) - cy
    ay = 1.0 - cy - by
    t = x
    solved = False
    for _ in range(8):
        e = _bezier_x(ax, bx, cx, t) - x
        if abs(e) < 1e-12:
            solved = True
            break
        d = (3.0 * ax * t + 2.0 * bx) * t + cx
        if abs(d) < 1e-12:
            break
        t -= e / d
        if t < 0.0 or t > 1.0:
            break
    if not solved and abs(_bezier_x(ax, bx, cx, t) - x) >= 1e-12:
        lo, hi, t = 0.0, 1.0, x
        for _ in range(64):
            e = _bezier_x(ax, bx, cx, t)
            if abs(e - x) < 1e-12:
                break
            if x > e:
                lo = t
            else:
                hi = t
            t = (hi - lo) * 0.5 + lo
    return ((ay * t + by) * t + cy) * t


def steps(n, jump, x):
    """CSS steps(n, jump) for 0 < x < 1 (ease_p handles the ends: every ease maps 0 -> 0 and 1 -> 1)."""
    n = max(1, int(n))
    if jump == "start":
        return math.ceil(x * n) / n
    if jump == "none":
        return min(1.0, math.floor(x * n) / max(1, n - 1)) if n > 1 else 0.0
    if jump == "both":
        return (math.floor(x * n) + 1) / (n + 1)
    return math.floor(x * n) / n


def _bounce_out(x):
    n1, d1 = 7.5625, 2.75
    if x < 1.0 / d1:
        return n1 * x * x
    if x < 2.0 / d1:
        x -= 1.5 / d1
        return n1 * x * x + 0.75
    if x < 2.5 / d1:
        x -= 2.25 / d1
        return n1 * x * x + 0.9375
    x -= 2.625 / d1
    return n1 * x * x + 0.984375


def _elastic_out(x, amp, period):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    a = max(1.0, amp)
    s = period / (2.0 * math.pi) * math.asin(1.0 / a)
    return a * math.pow(2.0, -10.0 * x) * math.sin((x - s) * (2.0 * math.pi) / period) + 1.0


def _pow_in(n, x):
    r = x
    for _ in range(n - 1):
        r *= x
    return r


def _family(name, d, x, p):
    """Penner easing `name` in direction d ('in'|'out'|'in-out') at x in (0,1). p = params dict."""
    if name in ("quad", "cubic", "quart", "quint"):
        n = {"quad": 2, "cubic": 3, "quart": 4, "quint": 5}[name]
        if d == "in":
            return _pow_in(n, x)
        if d == "out":
            return 1.0 - _pow_in(n, 1.0 - x)
        if x < 0.5:
            return _pow_in(n, 2.0 * x) * (2.0 ** (n - 1)) / (2.0 ** n)
        return 1.0 - _pow_in(n, 2.0 - 2.0 * x) / 2.0
    if name == "sine":
        if d == "in":
            return 1.0 - math.cos(x * math.pi / 2.0)
        if d == "out":
            return math.sin(x * math.pi / 2.0)
        return -(math.cos(math.pi * x) - 1.0) / 2.0
    if name == "expo":
        if d == "in":
            return math.pow(2.0, 10.0 * x - 10.0)
        if d == "out":
            return 1.0 - math.pow(2.0, -10.0 * x)
        if x < 0.5:
            return math.pow(2.0, 20.0 * x - 10.0) / 2.0
        return (2.0 - math.pow(2.0, -20.0 * x + 10.0)) / 2.0
    if name == "circ":
        if d == "in":
            return 1.0 - math.sqrt(1.0 - x * x)
        if d == "out":
            return math.sqrt(1.0 - (x - 1.0) * (x - 1.0))
        if x < 0.5:
            return (1.0 - math.sqrt(1.0 - (2.0 * x) * (2.0 * x))) / 2.0
        return (math.sqrt(1.0 - (-2.0 * x + 2.0) * (-2.0 * x + 2.0)) + 1.0) / 2.0
    if name == "back":
        s = p.get("overshoot", 1.70158)
        if d == "in":
            return (s + 1.0) * x * x * x - s * x * x
        if d == "out":
            y = x - 1.0
            return 1.0 + (s + 1.0) * y * y * y + s * y * y
        c2 = s * 1.525
        if x < 0.5:
            return ((2.0 * x) * (2.0 * x) * ((c2 + 1.0) * 2.0 * x - c2)) / 2.0
        y = 2.0 * x - 2.0
        return (y * y * ((c2 + 1.0) * y + c2) + 2.0) / 2.0
    if name == "elastic":
        a, per = p.get("amp", 1.0), p.get("period", 0.3)
        if d == "out":
            return _elastic_out(x, a, per)
        if d == "in":
            return 1.0 - _elastic_out(1.0 - x, a, per)
        per2 = per * 1.5
        if x < 0.5:
            return (1.0 - _elastic_out(1.0 - 2.0 * x, a, per2)) / 2.0
        return (1.0 + _elastic_out(2.0 * x - 1.0, a, per2)) / 2.0
    if name == "bounce":
        if d == "out":
            return _bounce_out(x)
        if d == "in":
            return 1.0 - _bounce_out(1.0 - x)
        if x < 0.5:
            return (1.0 - _bounce_out(1.0 - 2.0 * x)) / 2.0
        return (1.0 + _bounce_out(2.0 * x - 1.0)) / 2.0
    return x


def parse_ease(spec):
    """Normalise any ease spec to a tuple, or None if invalid.
    ('linear',) ('hold',) ('bezier', x1,y1,x2,y2) ('steps', n, jump) ('fam', name, dir, params)"""
    if spec is None:
        return ("linear",)
    if isinstance(spec, str):
        s = spec.strip().lower()
        if s == "linear":
            return ("linear",)
        if s == "hold":
            return ("hold",)
        if s in CSS_EASES:
            return ("bezier",) + CSS_EASES[s]
        if s.startswith("cubic-bezier(") and s.endswith(")"):
            try:
                v = [float(a) for a in s[13:-1].split(",")]
            except ValueError:
                return None
            return ("bezier",) + tuple(v) if len(v) == 4 and all(math.isfinite(a) for a in v) else None
        if s.startswith("steps(") and s.endswith(")"):
            parts = [a.strip() for a in s[6:-1].split(",")]
            try:
                n = int(parts[0])
            except ValueError:
                return None
            j = parts[1] if len(parts) > 1 else "end"
            j = {"jump-end": "end", "jump-start": "start", "jump-none": "none", "jump-both": "both"}.get(j, j)
            return ("steps", n, j) if n >= 1 and j in JUMPS and len(parts) <= 2 else None
        if s.startswith("ease-") and s not in CSS_EASES:
            s = s[5:]
        for d in ("in-out", "in", "out"):
            if s.startswith(d + "-") and s[len(d) + 1:] in EASE_FAMILIES:
                return ("fam", s[len(d) + 1:], d, {})
        return None
    if isinstance(spec, (list, tuple)):
        if len(spec) == 4 and all(isinstance(a, (int, float)) and not isinstance(a, bool) for a in spec):
            return ("bezier",) + tuple(float(a) for a in spec)
        return None
    if isinstance(spec, dict):
        typ = spec.get("type")
        if typ == "bezier" and isinstance(spec.get("points"), (list, tuple)):
            return parse_ease(list(spec["points"]))
        if typ == "steps":
            j = spec.get("jump", "end")
            n = spec.get("n", 1)
            return ("steps", int(n), j) if isinstance(n, (int, float)) and n >= 1 and j in JUMPS else None
        if typ in ("back", "elastic", "bounce"):
            d = spec.get("mode", "out")
            if d not in EASE_DIRS:
                return None
            p = {}
            if typ == "back":
                p["overshoot"] = _num(spec.get("overshoot"), 1.70158)
            if typ == "elastic":
                p["amp"] = _num(spec.get("amp"), 1.0)
                p["period"] = max(1e-3, _num(spec.get("period"), 0.3))
            return ("fam", typ, d, p)
    return None


def ease_p(parsed, x):
    k = parsed[0]
    if k == "linear":
        return x
    if x <= 0.0:
        return 0.0
    if k == "hold":
        return 1.0 if x >= 1.0 else 0.0
    if x >= 1.0:
        return 1.0
    if k == "bezier":
        return bezier(parsed[1], parsed[2], parsed[3], parsed[4], x)
    if k == "steps":
        return steps(parsed[1], parsed[2], x)
    return _family(parsed[1], parsed[2], x, parsed[3])


def ease(spec, x):
    """Eased progress for `spec` at x (clamped to 0..1). Unknown specs behave as linear."""
    p = parse_ease(spec)
    x = min(1.0, max(0.0, x))
    return x if p is None else ease_p(p, x)


# ---------------------------------------------------------------- keyframes
def key_value(keys, t):
    """Value of a sorted key list at layer-local time t (held outside, ease of the leaving key, `hold` = step)."""
    n = len(keys)
    if n == 0:
        return None
    if t < keys[0]["t"]:
        return keys[0]["v"]
    i = 0
    for j in range(n):
        if keys[j]["t"] <= t:
            i = j
        else:
            break
    a = keys[i]
    if i == n - 1:
        return a["v"]
    b = keys[i + 1]
    if a.get("hold") is True:
        return a["v"]
    f = (t - a["t"]) / (b["t"] - a["t"])
    return a["v"] + (b["v"] - a["v"]) * ease(a.get("ease"), f)


def prop_value(layer, prop, t, base):
    """base -> keys -> modifiers (additive, in order). No clamping (see `value`)."""
    keys = (layer.get("keys") or {}).get(prop)
    v = key_value(keys, t) if keys else base
    mods = (layer.get("mods") or {}).get(prop)
    if mods:
        for j, m in enumerate(mods):
            v += mod_value(m, prop, j, t)
    return v


def value(layer, prop, t, dflt=None):
    """Animated value of a layer property at layer-local time t (static value / default when nothing animates it)."""
    base = layer.get(prop)
    if isinstance(base, bool) or not isinstance(base, (int, float)):
        base = DEFAULTS.get(prop, 0.0) if dflt is None else dflt
    v = prop_value(layer, prop, t, float(base))
    if prop == "opacity":
        v = min(1.0, max(0.0, v))
    return v


def is_animated(layer, props=None):
    """True if keys or mods exist for any of `props` (default: any prop at all)."""
    for src in (layer.get("keys") or {}, layer.get("mods") or {}):
        for k, v in src.items():
            if v and (props is None or k in props):
                return True
    return False


def effect_params(layer, effect, t):
    """Copy of an effect dict with keyed / modified numeric params at layer-local time t."""
    eid = effect.get("id")
    if not eid:
        return effect
    prefix = f"fx.{eid}."
    names = set()
    for src in (layer.get("keys") or {}, layer.get("mods") or {}):
        for k, v in src.items():
            if v and k.startswith(prefix):
                names.add(k[len(prefix):])
    if not names:
        return effect
    out = dict(effect)
    for n in names:
        base = effect.get(n)
        base = float(base) if isinstance(base, (int, float)) and not isinstance(base, bool) else 0.0
        out[n] = prop_value(layer, prefix + n, t, base)
    return out


def raster_scale(layer):
    """Scale at which text / media is rasterised so that animating the scale never needs a bigger bitmap:
    max |scale| over keys (or the static value) times max(1, |scale_x|, |scale_y|) extremes. Static layers: = scale."""
    def ext(prop, base):
        ks = (layer.get("keys") or {}).get(prop)
        vals = [abs(k["v"]) for k in ks] if ks else [abs(base)]
        return max(vals)

    def st(prop):
        b = layer.get(prop)
        return b if isinstance(b, (int, float)) and not isinstance(b, bool) else 1.0

    s = ext("scale", st("scale"))
    m = max(1.0, ext("scale_x", st("scale_x")), ext("scale_y", st("scale_y")))
    return max(0.01, s * m)
