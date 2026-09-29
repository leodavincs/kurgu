#!/usr/bin/env python3
"""Motion presets for the engine slice: cam.punch (E01), cam.ken-burns (E02), cam.shake (E03).

A layer may carry `"use": [{"preset": "cam.punch", "at": [2.4, 5.1], "params": {"amount": 1.12}}]`.
`bake_layer()` expands every entry into ordinary `keys` / `mods` and removes `use`, so the browser preview and
render.py only ever see raw keys (they share anim.js / anim.py and know nothing about presets).
`schema.normalize()` bakes on load (a hand-written `use` still renders), the MCP `edit_project` bakes before saving.

    python3 presets.py list
    python3 presets.py bake <project_dir>        # rewrite project.json with `use` expanded into keys/mods

`at` is in TIMELINE seconds (what an agent reads off the video); baked keys are layer-local (t - layer.start).
`at`: number | [numbers] | "in" (layer start) | "out" (ends at the layer end). `dur` overrides the preset duration.
Decisions: the engine slice ships presets as Python builders + a JSON-serialisable manifest (`describe()`); the
JSON-template format of ROADMAP §5.5 (I01) comes with the preset library. Keys of a preset replace the layer's own
keys on the same property inside the preset window; the base for scale/x/y is the value there.
"""
import copy
import json
import math
import os
import sys

import anim

HERE = os.path.dirname(os.path.abspath(__file__))


def _n(v, d):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d


# ---------------------------------------------------------------- builders
def _punch(ctx, ta, tb, p):
    """scale base -> base*amount over `dur` (out-expo); optional hold, then settle back to base."""
    amount, dur = _n(p["amount"], 1.12), tb - ta
    hold, settle = _n(p["hold"], 0.0), _n(p["settle"], 0.0)
    b = ctx["base"]("scale", ta)
    end = ta + dur
    keys = [{"t": ta, "v": b, "ease": p["ease"]}, {"t": end, "v": b * amount}]
    if settle > 0:
        if hold > 0:
            keys.append({"t": end + hold, "v": b * amount})
            end += hold
        keys[-1]["ease"] = p["settle_ease"]
        keys.append({"t": end + settle, "v": b})
        end += settle
    return {"keys": {"scale": keys}}, end


def _ken_burns(ctx, ta, tb, p):
    f, t = p["from"], p["to"]
    fs, ts = _n(f.get("scale"), 1.0), _n(t.get("scale"), 1.15)
    out = {}
    b = ctx["base"]("scale", ta)
    out["scale"] = [{"t": ta, "v": b * fs, "ease": p["ease"]}, {"t": tb, "v": b * ts}]
    for prop in ("x", "y"):
        a, z = _n(f.get(prop), 0.0), _n(t.get(prop), 0.0)
        if a != 0 or z != 0:
            b = ctx["base"](prop, ta)
            out[prop] = [{"t": ta, "v": b + a, "ease": p["ease"]}, {"t": tb, "v": b + z}]
    return {"keys": out}, tb


def _shake(ctx, ta, tb, p):
    amp, rot = _n(p["amp"], 14.0), _n(p["rot"], 0.6)
    common = {"type": "wiggle", "freq": _n(p["freq"], 12.0), "octaves": int(_n(p["octaves"], 2)), "seed": int(_n(p["seed"], 1)),
              "t0": ta, "t1": tb, "decay": _n(p["decay"], 0.0)}
    mods = {"x": [{**common, "amp": amp}], "y": [{**common, "amp": amp}]}
    if rot:
        mods["rotation"] = [{**common, "amp": rot}]
    res = {"mods": mods}
    if p["overscan"] is None:
        overscan = ctx["type"] in ("video", "image")
    else:
        overscan = bool(p["overscan"])
    if overscan:
        W, H = ctx["W"], ctx["H"]
        k = 1.0 + 2.0 * (amp + math.radians(abs(rot)) * math.hypot(W, H) / 2.0) / min(W, H)
        b = ctx["base"]("scale", ta)
        ramp = min(0.08, (tb - ta) / 4.0)
        res["keys"] = {"scale": [{"t": ta, "v": b, "ease": "out-quad"}, {"t": ta + ramp, "v": b * k},
                                 {"t": tb - ramp, "v": b * k, "ease": "in-quad"}, {"t": tb, "v": b}]}
    return res, tb


def _fade_up(ctx, ta, tb, p):
    dist, ease = _n(p["distance"], 60.0), p["ease"]
    by, bo = ctx["base"]("y", ta), ctx["base"]("opacity", ta)
    return {"keys": {"opacity": [{"t": ta, "v": 0, "ease": ease}, {"t": tb, "v": bo}],
                     "y": [{"t": ta, "v": by + dist, "ease": ease}, {"t": tb, "v": by}]}}, tb


def _slide(sign):
    def build(ctx, ta, tb, p):
        dist = _n(p["distance"], 0.0) or ctx["W"] * 0.25
        bx, bo = ctx["base"]("x", ta), ctx["base"]("opacity", ta)
        keys = {"x": [{"t": ta, "v": bx + sign * dist, "ease": p["ease"]}, {"t": tb, "v": bx}]}
        if p["fade"]:
            keys["opacity"] = [{"t": ta, "v": 0, "ease": "out-quad"}, {"t": ta + (tb - ta) * 0.6, "v": bo}]
        return {"keys": keys}, tb
    return build


def _scale_pop(ctx, ta, tb, p):
    frm, bs, bo = _n(p["from"], 0.6), ctx["base"]("scale", ta), ctx["base"]("opacity", ta)
    return {"keys": {"scale": [{"t": ta, "v": bs * frm, "ease": p["ease"]}, {"t": tb, "v": bs}],
                     "opacity": [{"t": ta, "v": 0, "ease": "out-quad"}, {"t": ta + (tb - ta) * 0.4, "v": bo}]}}, tb


def _spin_in(ctx, ta, tb, p):
    ease, bs, bo, br = p["ease"], ctx["base"]("scale", ta), ctx["base"]("opacity", ta), ctx["base"]("rotation", ta)
    return {"keys": {"rotation": [{"t": ta, "v": br + _n(p["turns"], -0.5) * 360, "ease": ease}, {"t": tb, "v": br}],
                     "scale": [{"t": ta, "v": bs * _n(p["from"], 0.3), "ease": ease}, {"t": tb, "v": bs}],
                     "opacity": [{"t": ta, "v": 0, "ease": "out-quad"}, {"t": ta + (tb - ta) * 0.5, "v": bo}]}}, tb


def _fade_down(ctx, ta, tb, p):
    dist, ease = _n(p["distance"], 60.0), p["ease"]
    by, bo = ctx["base"]("y", ta), ctx["base"]("opacity", ta)
    return {"keys": {"opacity": [{"t": ta, "v": bo, "ease": ease}, {"t": tb, "v": 0}],
                     "y": [{"t": ta, "v": by, "ease": ease}, {"t": tb, "v": by + dist}]}}, tb


def _wobble(ctx, ta, tb, p):
    common = {"type": "wiggle", "amp": _n(p["amp"], 5.0), "freq": _n(p["freq"], 7.0), "octaves": 1, "seed": int(_n(p["seed"], 1)),
              "t0": ta, "t1": tb, "decay": _n(p["decay"], 5.0)}
    return {"mods": {"rotation": [common]}}, tb


PRESETS = {
    "cam.punch": {
        "name": "Punch-in", "name_tr": "Yakınlaş", "group": "camera", "default_at": "playhead", "kind": "camera", "applies_to": ["video", "image", "text"], "default_dur": 0.18,
        "doc": "Scale snaps from base to base*amount at each `at` time (out-expo, 0.18 s); with settle > 0 it eases back after `hold` s.",
        "params": {"amount": 1.12, "ease": "out-expo", "hold": 0.0, "settle": 0.0, "settle_ease": "in-out-sine"}, "build": _punch},
    "cam.ken-burns": {
        "name": "Ken Burns", "name_tr": "Ken Burns", "group": "camera", "default_at": "in", "kind": "camera", "applies_to": ["video", "image"], "default_dur": None,
        "doc": "Slow drift over the layer (or `dur` from `at`): scale and x/y offsets from -> to (x, y are pixel offsets to the layer position).",
        "params": {"from": {"scale": 1.0, "x": 0, "y": 0}, "to": {"scale": 1.15, "x": -40, "y": -20}, "ease": "in-out-sine"}, "build": _ken_burns},
    "cam.shake": {
        "name": "Camera shake", "name_tr": "Kamera sarsıntısı", "group": "camera", "default_at": "playhead", "kind": "camera", "applies_to": ["video", "image", "text"], "default_dur": 0.4,
        "doc": "Seeded wiggle on x, y (amp px) and rotation (rot deg) during [at, at+dur), optional decay; overscan scales the layer "
               "up inside the window so edges do not show (default on for video/image).",
        "params": {"amp": 14.0, "rot": 0.6, "freq": 12.0, "octaves": 2, "seed": 1, "decay": 0.0, "overscan": None}, "build": _shake},
    "in.fade-up": {
        "name": "Fade up", "name_tr": "Yukarı belirme", "group": "in", "default_at": "in", "kind": "in", "applies_to": ["video", "image", "text"], "default_dur": 0.5,
        "doc": "Opacity 0 -> base while the layer rises `distance` px into place (out-cubic).",
        "params": {"distance": 60.0, "ease": "out-cubic"}, "build": _fade_up},
    "in.slide-left": {
        "name": "Slide in from left", "name_tr": "Soldan kay", "group": "in", "default_at": "in", "kind": "in", "applies_to": ["video", "image", "text"], "default_dur": 0.5,
        "doc": "x slides in from the left by `distance` px (default a quarter of the frame width), with a quick fade.",
        "params": {"distance": 0.0, "ease": "out-cubic", "fade": True}, "build": _slide(-1)},
    "in.slide-right": {
        "name": "Slide in from right", "name_tr": "Sağdan kay", "group": "in", "default_at": "in", "kind": "in", "applies_to": ["video", "image", "text"], "default_dur": 0.5,
        "doc": "x slides in from the right by `distance` px (default a quarter of the frame width), with a quick fade.",
        "params": {"distance": 0.0, "ease": "out-cubic", "fade": True}, "build": _slide(1)},
    "in.scale-pop": {
        "name": "Scale pop", "name_tr": "Büyüyerek belir", "group": "in", "default_at": "in", "kind": "in", "applies_to": ["video", "image", "text"], "default_dur": 0.4,
        "doc": "Scale from base*`from` to base with an overshoot (out-back) while fading in.",
        "params": {"from": 0.6, "ease": "out-back"}, "build": _scale_pop},
    "in.spin": {
        "name": "Spin in", "name_tr": "Dönerek gir", "group": "in", "default_at": "in", "kind": "in", "applies_to": ["video", "image", "text"], "default_dur": 0.6,
        "doc": "Rotation from `turns` (-0.5 = half a turn anticlockwise) to base, scale from base*`from`, fading in.",
        "params": {"turns": -0.5, "from": 0.3, "ease": "out-cubic"}, "build": _spin_in},
    "out.fade-down": {
        "name": "Fade down", "name_tr": "Aşağı kaybol", "group": "out", "default_at": "out", "kind": "out", "applies_to": ["video", "image", "text"], "default_dur": 0.5,
        "doc": "Opacity base -> 0 while the layer sinks `distance` px (in-cubic); default window ends at the layer end (`at: \"out\"`).",
        "params": {"distance": 60.0, "ease": "in-cubic"}, "build": _fade_down},
    "emph.pulse": {
        "name": "Pulse", "name_tr": "Nabız", "group": "emphasis", "default_at": "playhead", "kind": "emphasis", "applies_to": ["video", "image", "text"], "default_dur": 0.14,
        "doc": "A soft scale beat: base -> base*amount, brief hold, eases back.",
        "params": {"amount": 1.08, "ease": "out-expo", "hold": 0.04, "settle": 0.2, "settle_ease": "in-out-sine"}, "build": _punch},
    "emph.wobble": {
        "name": "Wobble", "name_tr": "Sallan", "group": "emphasis", "default_at": "playhead", "kind": "emphasis", "applies_to": ["video", "image", "text"], "default_dur": 0.7,
        "doc": "Decaying seeded wiggle on rotation (`amp` deg) during [at, at+dur).",
        "params": {"amp": 5.0, "freq": 7.0, "seed": 1, "decay": 5.0}, "build": _wobble},
}


def describe():
    """JSON-serialisable manifest (for agents / a future MCP list_presets)."""
    return [{"id": k, **{a: v[a] for a in ("name", "name_tr", "group", "default_at", "kind", "applies_to", "default_dur", "doc", "params") if a in v}} for k, v in PRESETS.items()]


# ---------------------------------------------------------------- validation
def validate_use(layer, tag):
    probs = []
    use = layer.get("use")
    if not isinstance(use, list):
        return [f"{tag}: use must be a list of {{preset, at, params}} objects"]
    for j, u in enumerate(use):
        ut = f"{tag}: use[{j}]"
        if not isinstance(u, dict) or u.get("preset") not in PRESETS:
            probs.append(f'{ut}: unknown preset {json.dumps(u.get("preset") if isinstance(u, dict) else u)} (have: {", ".join(PRESETS)})')
            continue
        P = PRESETS[u["preset"]]
        if layer.get("type") not in P["applies_to"]:
            probs.append(f'{ut}: {u["preset"]} applies to {", ".join(P["applies_to"])} layers, not {layer.get("type")}')
        for k in (u.get("params") or {}):
            if k not in P["params"]:
                probs.append(f'{ut}: {u["preset"]} has no param "{k}" (use {", ".join(P["params"])})')
        at = u.get("at", "in")
        ok = at in ("in", "out") or (isinstance(at, (int, float)) and not isinstance(at, bool)) or \
            (isinstance(at, list) and at and all(isinstance(a, (int, float)) and not isinstance(a, bool) for a in at))
        if not ok:
            probs.append(f'{ut}: at must be "in", "out", a time in seconds or a list of times')
        if "dur" in u and not (isinstance(u["dur"], (int, float)) and u["dur"] > 0):
            probs.append(f"{ut}: dur must be a positive number of seconds")
    return probs


# ---------------------------------------------------------------- baking
def _r(x):
    """Tidy JSON: round to 6 decimals, 3.0 -> 3."""
    x = round(float(x), 6)
    return int(x) if x.is_integer() and abs(x) < 1e12 else x


def _merge_keys(existing, new, ta, tb):
    new = [{**k, "t": _r(k["t"]), "v": _r(k["v"])} for k in new]
    keep = [k for k in existing if not (ta - 1e-9 <= k["t"] <= tb + 1e-9)]
    out = keep + new
    out.sort(key=lambda k: k["t"])           # stable: equal times keep insertion order
    return out


def bake_layer(layer, W=1920, H=1080):
    """Return a copy of `layer` with `use` expanded into keys / mods (invalid entries are skipped; validate() reports them)."""
    layer = copy.deepcopy(layer)
    use = layer.pop("use", None)
    if not isinstance(use, list):
        return layer
    start = _n(layer.get("start"), 0.0)
    end = _n(layer.get("end"), start + 3.0)
    jobs = []                                   # (window length, start, preset entry, params)
    for u in use:
        if not isinstance(u, dict) or u.get("preset") not in PRESETS:
            continue
        P = PRESETS[u["preset"]]
        if layer.get("type") not in P["applies_to"]:
            continue
        params = copy.deepcopy(P["params"])
        for k, v in (u.get("params") or {}).items():
            if k in params:
                params[k] = {**params[k], **v} if isinstance(params[k], dict) and isinstance(v, dict) else v
        at = u.get("at", "in")
        if at == "in":
            times = [start]
        elif at == "out":
            times = None
        elif isinstance(at, list):
            times = [float(a) for a in at if isinstance(a, (int, float)) and not isinstance(a, bool)]
        elif isinstance(at, (int, float)) and not isinstance(at, bool):
            times = [float(at)]
        else:
            continue
        dur = float(u["dur"]) if isinstance(u.get("dur"), (int, float)) and not isinstance(u.get("dur"), bool) else P["default_dur"]
        if times is None:
            dur = dur or 0.4
            times = [end - dur]
        for T in times:
            d = dur if dur else (end - T)
            if d > 0:
                jobs.append((d, T, P, params))
    # longest windows first (a ken-burns drift), short ones (punches, shakes) are laid on top: the result does not depend on list order
    jobs.sort(key=lambda j: (-j[0], j[1]))
    for d, T, P, params in jobs:
        ta, tb = T - start, T - start + d

        def base(prop, t, _layer=layer):
            dflt = {"x": W / 2, "y": H / 2}.get(prop, anim.DEFAULTS.get(prop, 0.0))
            return anim.value(_layer, prop, t, dflt)
        res, last = P["build"]({"base": base, "type": layer.get("type"), "W": W, "H": H}, ta, tb, params)
        for prop, ks in (res.get("keys") or {}).items():
            keys = layer.setdefault("keys", {})
            keys[prop] = _merge_keys(keys.get(prop, []), ks, ta, last)
        for prop, ms in (res.get("mods") or {}).items():
            layer.setdefault("mods", {}).setdefault(prop, []).extend(
                {k: (_r(v) if isinstance(v, float) else v) for k, v in m.items()} for m in ms)
    return layer


def bake_project(project, W=None, H=None):
    """Bake every layer's `use` (raw project dict, in place). Returns the number of layers changed."""
    W = W or project.get("width", 1920)
    H = H or project.get("height", 1080)
    n = 0
    for i, l in enumerate(project.get("layers", [])):
        if isinstance(l, dict) and "use" in l:
            project["layers"][i] = bake_layer(l, W, H)
            n += 1
    return n


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["list"]:
        print(json.dumps(describe(), indent=2))
        return 0
    if len(argv) == 2 and argv[0] == "bake":
        import schema
        path = schema.project_path(os.path.abspath(argv[1]))
        proj = json.load(open(path, encoding="utf-8"))
        n = bake_project(proj)
        schema.stamp_requires(proj)
        with open(path, "w", encoding="utf-8") as f:
            f.write(schema.dumps(proj))
        print(f"baked {n} layer(s)")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
