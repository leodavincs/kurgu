"""Kurgu project schema (SPEC.md §1, §2, §7).

Defaults + normalisation, validation, v1 (Turkish keys) -> v2 migration, `load_project(dir)`.
Imported by server.py and render.py; no third-party dependencies.

Decisions where the spec is silent:
- `normalize()` never drops unknown keys (forward compatibility); it only fills and coerces known ones.
- `normalize()` sorts `envelope` points by time and gives layers without an id a stable `layer-<n>` id.
- A layer's default `name` is its type capitalised ("Text", "Video", ...).
- Migration writes explicit values wherever the v1 default differs from the v2 default
  (image `fit`: v1 default was cover, v2 default is contain; text `font`: v1 default was "gill").
"""
import copy
import json
import os
import re
import unicodedata

import anim

VERSION = 2
LAYER_TYPES = ("video", "image", "text", "color", "audio")
VISUAL_TYPES = ("video", "image", "text", "color")
FITS = ("cover", "contain", "native")
ALIGNS = ("center", "left", "right")
EFFECT_TYPES = ("grayscale", "brightness", "contrast", "saturation", "blur", "zoom", "freeze")
# Motion (SPEC §1.8). Features a project can require; older builds refuse projects that require more than they know.
FEATURES = ("keys", "mods", "transform")
NEW_TRANSFORM_PROPS = ("rotation", "scale_x", "scale_y", "anchor_x", "anchor_y")
KEYABLE_VISUAL = ("x", "y", "scale", "scale_x", "scale_y", "rotation", "anchor_x", "anchor_y", "opacity")
KEYABLE_SOUND = ("volume_db",)
EFFECT_PARAMS = {"grayscale": ("amount",), "brightness": ("value",), "contrast": ("value",), "saturation": ("value",),
                 "blur": ("radius",), "zoom": ("from", "to")}
MOD_TYPES = ("wiggle", "noise")
DEFAULT_FONT = "inter-regular"
DEFAULT_TEXT_COLOR = "#f1efe9"

PROJECT_FILE = "project.json"
LEGACY_FILE = "proje.json"


class SchemaError(Exception):
    """Raised for files that cannot be read as a project at all."""


# ---------------------------------------------------------------- small coercion helpers
def _num(v, default):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v.replace(",", "."))
        except ValueError:
            return default
    return default


def _flt(v, default):
    r = _num(v, default)
    return float(r) if r is not None else None


def _bool(v, default):
    return v if isinstance(v, bool) else default


def _str(v, default):
    return v if isinstance(v, str) else default


def _enum(v, allowed, default):
    return v if v in allowed else default


def _clean_number(x):
    """3.0 -> 3 for tidy JSON when the float is integral."""
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e12:
        return int(x)
    return x


# ---------------------------------------------------------------- defaults
def default_project(name="Untitled"):
    """An empty template project (1920x1080, 30 fps, 10 s, no layers)."""
    return normalize({"name": name})


def default_global():
    return {"grain": 0, "vignette": 0, "loudness_lufs": -14, "fade_out": 0}


def default_crop():
    return {"top": 0, "bottom": 0, "left": 0, "right": 0}


def default_shadow():
    return {"enabled": True, "blur": 9, "opacity": 0.7}


# ---------------------------------------------------------------- normalisation
def _norm_effect(e):
    if not isinstance(e, dict):
        return None
    t = _str(e.get("type"), "")
    out = dict(e)
    out["type"] = t
    out["enabled"] = _bool(e.get("enabled"), True)
    if t == "grayscale":
        out["amount"] = _clean_number(_flt(e.get("amount"), 1.0))
    elif t in ("brightness", "contrast", "saturation"):
        out["value"] = _clean_number(_flt(e.get("value"), 0.0))
    elif t == "blur":
        out["radius"] = _clean_number(_flt(e.get("radius"), 0.0))
    elif t == "zoom":
        out["from"] = _clean_number(_flt(e.get("from"), 1.0))
        out["to"] = _clean_number(_flt(e.get("to"), 1.06))
    return out


def _norm_crop(c):
    c = c if isinstance(c, dict) else {}
    out = {}
    for k in ("top", "bottom", "left", "right"):
        out[k] = _clean_number(min(0.5, max(0.0, _flt(c.get(k), 0.0))))
    return out


def _norm_envelope(env):
    pts = []
    for p in env if isinstance(env, list) else []:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            t, db = _num(p[0], None), _num(p[1], None)
            if t is not None and db is not None:
                pts.append([_clean_number(float(t)), _clean_number(float(db))])
    pts.sort(key=lambda p: p[0])
    return pts


def _norm_keys(keys):
    """{prop: [{t, v, ease?, hold?}]}: numbers coerced, invalid entries dropped, sorted by t (stable), empty tracks removed."""
    out = {}
    if not isinstance(keys, dict):
        return out
    for prop, lst in keys.items():
        if not isinstance(prop, str) or not isinstance(lst, list):
            continue
        ks = []
        for k in lst:
            if not isinstance(k, dict):
                continue
            t, v = _num(k.get("t"), None), _num(k.get("v"), None)
            if t is None or v is None or isinstance(t, bool):
                continue
            nk = dict(k)
            nk["t"], nk["v"] = _clean_number(float(t)), _clean_number(float(v))
            if "hold" in nk:
                nk["hold"] = _bool(nk["hold"], False)
            ks.append(nk)
        ks.sort(key=lambda k: k["t"])
        if ks:
            out[prop] = ks
    return out


def _norm_mods(mods):
    out = {}
    if not isinstance(mods, dict):
        return out
    for prop, lst in mods.items():
        if not isinstance(prop, str) or not isinstance(lst, list):
            continue
        ms = []
        for m in lst:
            if not isinstance(m, dict):
                continue
            nm = dict(m)
            for k in ("amp", "freq", "octaves", "seed", "t0", "t1", "decay"):
                if k in nm:
                    x = _num(nm[k], None)
                    if x is None:
                        del nm[k]
                    else:
                        nm[k] = _clean_number(float(x))
            ms.append(nm)
        if ms:
            out[prop] = ms
    return out


def used_features(project):
    """Motion features the layers use (subset of FEATURES, sorted)."""
    f = set()
    for l in project.get("layers", []) if isinstance(project, dict) else []:
        if not isinstance(l, dict):
            continue
        if l.get("keys"):
            f.add("keys")
        if l.get("mods"):
            f.add("mods")
        if any(k in l for k in NEW_TRANSFORM_PROPS) or any(k in NEW_TRANSFORM_PROPS for k in (l.get("keys") or {})):
            f.add("transform")
    return sorted(f)


def stamp_requires(project):
    """Set project["requires"] to the motion features actually used (in place, raw or normalised). Returns the project."""
    keep = [r for r in project.get("requires", []) if isinstance(r, str) and r not in FEATURES] if isinstance(project.get("requires"), list) else []
    req = sorted(set(keep) | set(used_features(project)))
    if req:
        project["requires"] = req
    else:
        project.pop("requires", None)
    return project


def link_groups(project):
    """{link id: [layers]} for the layers that share a `link` with at least one other layer (SPEC 1.1a)."""
    g = {}
    for l in project.get("layers", []):
        if isinstance(l, dict) and isinstance(l.get("link"), str) and l["link"]:
            g.setdefault(l["link"], []).append(l)
    return {k: v for k, v in g.items() if len(v) > 1}


def link_offsets(project):
    """Sync offsets of linked clips: [(link, layer id, offset seconds)] for the members that are shifted against their master
    (the video of the pair, else the first layer). Same source: difference of (start - src_in); else of start. Empty = all in sync."""
    out = []
    tol = 0.5 / (_flt(project.get("fps"), 30.0) or 30.0)
    for lk, g in link_groups(project).items():
        m = next((x for x in g if x.get("type") == "video"), g[0])
        for x in g:
            if x is m:
                continue
            same = m.get("src") and m.get("src") == x.get("src")
            a = _flt(x.get("start"), 0.0) - (_flt(x.get("src_in"), 0.0) if same else 0.0)
            b = _flt(m.get("start"), 0.0) - (_flt(m.get("src_in"), 0.0) if same else 0.0)
            if abs(a - b) > tol:
                out.append((lk, x.get("id"), round(a - b, 4)))
    return out


def normalize_layer(layer, W=1920, H=1080, index=0):
    """Return a new layer dict with every known field present."""
    if not isinstance(layer, dict):
        layer = {}
    out = dict(layer)
    typ = layer.get("type") if layer.get("type") in LAYER_TYPES else _str(layer.get("type"), "video")
    out["type"] = typ
    out["id"] = _str(layer.get("id"), "") or f"layer-{index + 1}"
    out["name"] = _str(layer.get("name"), "") or typ.capitalize()
    out["group"] = _str(layer.get("group"), "")
    link = _str(layer.get("link"), "")
    if link:
        out["link"] = link                      # linked clips (Premiere-style): same id = move / trim / split / delete together
    else:
        out.pop("link", None)
    out["hidden"] = _bool(layer.get("hidden"), False)
    out["locked"] = _bool(layer.get("locked"), False)
    out["start"] = _clean_number(_flt(layer.get("start"), 0.0))
    out["end"] = _clean_number(_flt(layer.get("end"), out["start"] + 3))
    out["fade_in"] = _clean_number(max(0.0, _flt(layer.get("fade_in"), 0.0)))
    out["fade_out"] = _clean_number(max(0.0, _flt(layer.get("fade_out"), 0.0)))

    if typ in VISUAL_TYPES:
        out["x"] = _clean_number(_flt(layer.get("x"), W / 2))
        out["y"] = _clean_number(_flt(layer.get("y"), H / 2))
        out["scale"] = _clean_number(_flt(layer.get("scale"), 1.0))
        out["opacity"] = _clean_number(min(1.0, max(0.0, _flt(layer.get("opacity"), 1.0))))
        effs = [_norm_effect(e) for e in (layer.get("effects") if isinstance(layer.get("effects"), list) else [])]
        out["effects"] = [e for e in effs if e is not None]
        for k in NEW_TRANSFORM_PROPS:
            if k in layer:
                out[k] = _clean_number(_flt(layer.get(k), anim.DEFAULTS[k]))

    if typ in ("video", "image"):
        out["src"] = _str(layer.get("src"), "")
        out["fit"] = _enum(layer.get("fit"), FITS, "cover" if typ == "video" else "contain")
        out["crop"] = _norm_crop(layer.get("crop"))
    if typ in ("video", "audio"):
        out["src"] = _str(layer.get("src"), "")
        out["src_in"] = _clean_number(_flt(layer.get("src_in"), 0.0))
        out["volume_db"] = _clean_number(_flt(layer.get("volume_db"), 0.0))
        out["muted"] = _bool(layer.get("muted"), False)
        out["envelope"] = _norm_envelope(layer.get("envelope"))
    if typ == "text":
        out["text"] = _str(layer.get("text"), "Text")
        out["font"] = _str(layer.get("font"), "") or DEFAULT_FONT
        out["size"] = _clean_number(_flt(layer.get("size"), 48.0))
        out["color"] = _str(layer.get("color"), DEFAULT_TEXT_COLOR)
        out["letter_spacing"] = _clean_number(_flt(layer.get("letter_spacing"), 0.0))
        out["line_height"] = _clean_number(_flt(layer.get("line_height"), 1.25))
        out["align"] = _enum(layer.get("align"), ALIGNS, "center")
        sh = layer.get("shadow") if isinstance(layer.get("shadow"), dict) else {}
        d = default_shadow()
        out["shadow"] = {"enabled": _bool(sh.get("enabled"), d["enabled"]),
                         "blur": _clean_number(_flt(sh.get("blur"), d["blur"])),
                         "opacity": _clean_number(_flt(sh.get("opacity"), d["opacity"]))}
    if typ == "color":
        out["color"] = _str(layer.get("color"), "#000000")
    if "keys" in layer:
        out["keys"] = _norm_keys(layer.get("keys"))
        if not out["keys"]:
            del out["keys"]
    if "mods" in layer:
        out["mods"] = _norm_mods(layer.get("mods"))
        if not out["mods"]:
            del out["mods"]
    if "use" in layer:
        import presets                      # lazy import (presets needs anim only)
        out = presets.bake_layer(out, W, H)
    return out


def normalize(project):
    """Return a fully-populated v2 project (deep copy). Accepts v1 input too (migrated first)."""
    if not isinstance(project, dict):
        raise SchemaError("project must be a JSON object")
    if is_v1(project):
        project = migrate_v1(project)
    p = copy.deepcopy(project)
    p["version"] = VERSION
    p["name"] = _str(p.get("name"), "") or "Untitled"
    p["width"] = int(max(2, _num(p.get("width"), 1920)))
    p["height"] = int(max(2, _num(p.get("height"), 1080)))
    fps = _flt(p.get("fps"), 30.0)
    p["fps"] = _clean_number(fps if fps and fps > 0 else 30.0)
    p["duration"] = _clean_number(_flt(p.get("duration"), 10.0))
    p["background"] = _str(p.get("background"), "#000000")
    p["output"] = _str(p.get("output"), "") or "output.mp4"
    g = p.get("global") if isinstance(p.get("global"), dict) else {}
    d = default_global()
    ng = dict(g)
    ng["grain"] = _clean_number(_flt(g.get("grain"), d["grain"]))
    ng["vignette"] = _clean_number(_flt(g.get("vignette"), d["vignette"]))
    if "loudness_lufs" in g and g["loudness_lufs"] is None:
        ng["loudness_lufs"] = None
    else:
        ng["loudness_lufs"] = _clean_number(_flt(g.get("loudness_lufs"), d["loudness_lufs"]))
    ng["fade_out"] = _clean_number(_flt(g.get("fade_out"), d["fade_out"]))
    p["global"] = ng
    layers = p.get("layers") if isinstance(p.get("layers"), list) else []
    p["layers"] = [normalize_layer(l, p["width"], p["height"], i) for i, l in enumerate(layers)]
    if "groups" in p:
        gr = normalize_groups(p["groups"])
        if gr:
            p["groups"] = gr
        else:
            p.pop("groups")
    notes = []
    for i, n in enumerate(p.get("notes") if isinstance(p.get("notes"), list) else []):
        if isinstance(n, dict):
            notes.append(normalize_note(n, i))
    p["notes"] = notes
    stamp_requires(p)
    return p


GROUP_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_groups(g):
    """Timeline group colour overrides: {"<group name>": {"color": "#rrggbb"}}. Invalid entries are dropped (validate() reports them)."""
    out = {}
    if isinstance(g, dict):
        for name, v in g.items():
            if isinstance(name, str) and isinstance(v, dict) and isinstance(v.get("color"), str) and GROUP_COLOR_RE.match(v["color"]):
                out[name] = {"color": v["color"].lower()}
    return out


NOTE_AUTHORS = ("user", "agent")


def normalize_note(n, i=0):
    """Fill/repair one timeline note. Old notes (id, t, text, created) gain done=False, author="user", layer_ids=[], reply=""."""
    nn = dict(n)
    nn["id"] = _str(n.get("id"), "") or f"n_{i + 1}"
    nn["t"] = _clean_number(_flt(n.get("t"), 0.0))
    nn["text"] = _str(n.get("text"), "")
    nn["created"] = _str(n.get("created"), "")
    nn["done"] = n.get("done") is True
    nn["author"] = n.get("author") if n.get("author") in NOTE_AUTHORS else "user"
    nn["resolved_at"] = _str(n.get("resolved_at"), "") if nn["done"] else ""
    ids = n.get("layer_ids")
    nn["layer_ids"] = [x for x in ids if isinstance(x, str)] if isinstance(ids, list) else []
    nn["reply"] = _str(n.get("reply"), "")
    return nn


# ---------------------------------------------------------------- validation
def validate(project, font_ids=None):
    """Return a list of human-readable problems (empty = fine). Works on raw or normalised data.

    `font_ids`: optional iterable of known font ids; unknown ids are then reported."""
    probs = []
    if not isinstance(project, dict):
        return ["project is not a JSON object"]
    if is_v1(project):
        probs.append("project uses the v1 (Turkish) format; it will be migrated to v2")
        project = migrate_v1(project)
    if project.get("version", VERSION) != VERSION:
        probs.append(f"unsupported version {project.get('version')!r} (expected {VERSION})")
    for k in ("width", "height", "fps", "duration"):
        v = _num(project.get(k, 1), None)
        if v is None or v <= 0:
            probs.append(f"{k} must be a positive number")
    req = project.get("requires")
    if req is not None:
        if not isinstance(req, list) or any(not isinstance(r, str) for r in req):
            probs.append("requires must be a list of feature names")
        else:
            for r in req:
                if r not in FEATURES:
                    probs.append(f'requires: this Kurgu build does not know feature "{r}" (known: {", ".join(FEATURES)})')
    if "groups" in project:
        gr = project["groups"]
        if not isinstance(gr, dict):
            probs.append("groups must be an object {group name: {\"color\": \"#rrggbb\"}}")
        else:
            for name, v in gr.items():
                if not (isinstance(v, dict) and isinstance(v.get("color"), str) and GROUP_COLOR_RE.match(v["color"])):
                    probs.append(f'groups[{json.dumps(name, ensure_ascii=False)}]: color must be "#rrggbb"')
    layers = project.get("layers", [])
    if not isinstance(layers, list):
        return probs + ["layers must be a list"]
    known = set(font_ids) if font_ids is not None else None
    seen = set()
    dur = _flt(project.get("duration"), 0.0)
    for i, l in enumerate(layers):
        if not isinstance(l, dict):
            probs.append(f"layer #{i + 1} is not an object")
            continue
        lid = l.get("id")
        tag = f'layer "{l.get("name") or lid or "#" + str(i + 1)}" ({lid or "no id"})'
        if not lid or not isinstance(lid, str):
            probs.append(f"{tag}: missing id")
        elif lid in seen:
            probs.append(f"{tag}: duplicate id")
        seen.add(lid)
        typ = l.get("type")
        if typ not in LAYER_TYPES:
            probs.append(f'{tag}: unknown type {typ!r} (use {", ".join(LAYER_TYPES)})')
            continue
        s, e = _num(l.get("start"), None), _num(l.get("end"), None)
        if s is None or e is None:
            probs.append(f"{tag}: start and end are required numbers")
        elif e <= s:
            probs.append(f"{tag}: end ({e}) must be greater than start ({s})")
        elif dur and s >= dur:
            probs.append(f"{tag}: starts after the project ends ({s} >= {dur})")
        if l.get("link") is not None and (not isinstance(l.get("link"), str) or not l["link"]):
            probs.append(f"{tag}: link must be a non-empty string (the id shared by the clips that move together)")
        if typ in ("video", "image", "audio") and not _str(l.get("src"), ""):
            probs.append(f"{tag}: missing src")
        if l.get("fit") is not None and l.get("fit") not in FITS:
            probs.append(f'{tag}: fit must be one of {", ".join(FITS)}')
        if l.get("align") is not None and l.get("align") not in ALIGNS:
            probs.append(f'{tag}: align must be one of {", ".join(ALIGNS)}')
        if l.get("opacity") is not None and not (0 <= _num(l.get("opacity"), -1) <= 1):
            probs.append(f"{tag}: opacity must be between 0 and 1")
        if isinstance(l.get("crop"), dict):
            for k, v in l["crop"].items():
                if k not in ("top", "bottom", "left", "right") or not (0 <= _num(v, -1) <= 0.5):
                    probs.append(f"{tag}: crop.{k} must be top/bottom/left/right in 0..0.5")
        if l.get("envelope") is not None:
            env = l["envelope"]
            if not isinstance(env, list) or any(not (isinstance(p, (list, tuple)) and len(p) == 2
                                                      and _num(p[0], None) is not None and _num(p[1], None) is not None)
                                                 for p in env):
                probs.append(f"{tag}: envelope must be a list of [time, dB] pairs")
        for j, ef in enumerate(l.get("effects") or []):
            if not isinstance(ef, dict) or ef.get("type") not in EFFECT_TYPES:
                probs.append(f'{tag}: effect #{j + 1} has unknown type {ef.get("type") if isinstance(ef, dict) else ef!r}')
        _validate_motion(l, typ, tag, probs)
        if typ == "text" and known is not None:
            f = l.get("font") or DEFAULT_FONT
            if f not in known:
                probs.append(f'{tag}: font "{f}" not found (falls back to {DEFAULT_FONT})')
    notes = project.get("notes", [])
    if not isinstance(notes, list):
        probs.append("notes must be a list")
    else:
        nseen = set()
        for i, n in enumerate(notes):
            if not isinstance(n, dict):
                probs.append(f"note #{i + 1} is not an object")
                continue
            tag = f'note "{n.get("id") or "#" + str(i + 1)}"'
            if n.get("id") in nseen:
                probs.append(f"{tag}: duplicate id")
            nseen.add(n.get("id"))
            if _num(n.get("t"), None) is None or _num(n.get("t"), 0) < 0:
                probs.append(f"{tag}: t must be a number >= 0")
            if n.get("author") is not None and n.get("author") not in NOTE_AUTHORS:
                probs.append(f'{tag}: author must be "user" or "agent"')
            if n.get("done") is not None and not isinstance(n.get("done"), bool):
                probs.append(f"{tag}: done must be true or false")
    return probs


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_motion(l, typ, tag, probs):
    """keys / mods / new transform fields of one layer (SPEC §1.8)."""
    for k in NEW_TRANSFORM_PROPS:
        if k in l and not _isnum(l[k]) and _num(l[k], None) is None:
            probs.append(f"{tag}: {k} must be a number")
    if typ in ("audio", "color") and any(k in l for k in ("rotation", "scale_x", "scale_y", "anchor_x", "anchor_y")):
        probs.append(f"{tag}: rotation / scale_x / scale_y / anchor_x / anchor_y do not apply to {typ} layers")
    effects = [e for e in (l.get("effects") or []) if isinstance(e, dict)]
    ids = [e["id"] for e in effects if isinstance(e.get("id"), str) and e["id"]]
    for i in {x for x in ids if ids.count(x) > 1}:
        probs.append(f'{tag}: effect id "{i}" is used twice')
    eff_by_id = {e["id"]: e for e in effects if isinstance(e.get("id"), str) and e["id"]}
    valid = list(KEYABLE_SOUND if typ == "audio" else KEYABLE_VISUAL + (KEYABLE_SOUND if typ == "video" else ()))
    if typ == "color":
        valid = ["opacity"]
    valid_txt = ", ".join(valid) + ", fx.<effect id>.<param>"

    def check_prop(where, prop):
        if prop in valid:
            return True
        if prop.startswith("fx."):
            parts = prop.split(".")
            if len(parts) != 3:
                probs.append(f'{tag}: {where} "{prop}" must look like fx.<effect id>.<param>')
                return False
            e = eff_by_id.get(parts[1])
            if e is None:
                probs.append(f'{tag}: {where} "{prop}": no effect with id "{parts[1]}" (give the effect an "id"; have: {", ".join(eff_by_id) or "none"})')
                return False
            if parts[2] not in EFFECT_PARAMS.get(e.get("type"), ()):
                probs.append(f'{tag}: {where} "{prop}": {e.get("type")} has no numeric param "{parts[2]}" (use {", ".join(EFFECT_PARAMS.get(e.get("type"), ())) or "none"})')
                return False
            return True
        probs.append(f'{tag}: {where} "{prop}" cannot be animated on a {typ} layer (use {valid_txt})')
        return False

    keys = l.get("keys")
    if keys is not None:
        if not isinstance(keys, dict):
            probs.append(f'{tag}: keys must be an object {{"<prop>": [{{"t":0,"v":1}}, ...]}}')
        else:
            for prop, lst in keys.items():
                if not check_prop("keys", prop):
                    continue
                if not isinstance(lst, list) or not lst:
                    probs.append(f'{tag}: keys.{prop} must be a non-empty list of {{t, v}} objects')
                    continue
                last = None
                for j, k in enumerate(lst):
                    kt = f"{tag}: keys.{prop}[{j}]"
                    if not isinstance(k, dict) or not _isnum(k.get("t")) or not _isnum(k.get("v")):
                        probs.append(f"{kt} needs numeric t (layer-local seconds) and v")
                        continue
                    if last is not None and k["t"] < last:
                        probs.append(f"{kt}: times must not decrease (t={k['t']} after {last})")
                    last = k["t"]
                    if k.get("ease") is not None and anim.parse_ease(k["ease"]) is None:
                        probs.append(f'{kt}: unknown ease {json.dumps(k["ease"])} (use {", ".join(anim.named_eases()[:6])} ... '
                                     f'in|out|in-out-sine|quad|cubic|quart|quint|expo|circ|back|elastic|bounce, [x1,y1,x2,y2], cubic-bezier(..), steps(n))')
                    if "hold" in k and not isinstance(k["hold"], bool):
                        probs.append(f"{kt}: hold must be true or false")
                    if prop == "opacity" and not (0 <= k["v"] <= 1):
                        probs.append(f"{kt}: opacity keys must be between 0 and 1")
    mods = l.get("mods")
    if mods is not None:
        if not isinstance(mods, dict):
            probs.append(f'{tag}: mods must be an object {{"<prop>": [{{"type":"wiggle", ...}}]}}')
        else:
            for prop, lst in mods.items():
                if not check_prop("mods", prop):
                    continue
                if not isinstance(lst, list):
                    probs.append(f"{tag}: mods.{prop} must be a list")
                    continue
                for j, m in enumerate(lst):
                    mt = f"{tag}: mods.{prop}[{j}]"
                    if not isinstance(m, dict) or m.get("type") not in MOD_TYPES:
                        probs.append(f'{mt}: type must be one of {", ".join(MOD_TYPES)}')
                        continue
                    for f in ("amp", "freq", "octaves", "seed", "t0", "t1", "decay"):
                        if f in m and not _isnum(m[f]):
                            probs.append(f"{mt}: {f} must be a number")
                    if _isnum(m.get("freq")) and m["freq"] <= 0:
                        probs.append(f"{mt}: freq must be greater than 0")
                    if _isnum(m.get("octaves")) and not (1 <= m["octaves"] <= 6):
                        probs.append(f"{mt}: octaves must be 1..6")
                    if _isnum(m.get("decay")) and m["decay"] < 0:
                        probs.append(f"{mt}: decay must be >= 0")
                    if _isnum(m.get("t0")) and _isnum(m.get("t1")) and m["t1"] <= m["t0"]:
                        probs.append(f"{mt}: t1 must be greater than t0")
    if l.get("use") is not None:
        import presets
        probs.extend(presets.validate_use(l, tag))


# ---------------------------------------------------------------- v1 -> v2 migration (SPEC §7)
_TYPE_MAP = {"video": "video", "gorsel": "image", "yazi": "text", "renk": "color", "ses": "audio"}
_FIT_MAP = {"kapla": "cover", "sigdir": "contain", "dogal": "native"}
_ALIGN_MAP = {"orta": "center", "sol": "left", "sag": "right"}
_EFFECT_MAP = {"siyahbeyaz": "grayscale", "parlaklik": "brightness", "kontrast": "contrast",
               "doygunluk": "saturation", "bulaniklik": "blur", "yakinlas": "zoom", "dondur": "freeze"}
FONT_MAP_V1 = {
    "gill": "gill-sans-regular", "gill-light": "gill-sans-light", "gill-semibold": "gill-sans-semibold",
    "gill-bold": "gill-sans-bold", "futura": "futura-medium", "futura-bold": "futura-bold",
    "helvetica": "helvetica-neue-regular", "helvetica-light": "helvetica-neue-light",
    "helvetica-bold": "helvetica-neue-bold", "avenir": "avenir-next-regular",
    "avenir-demi": "avenir-next-demi-bold", "avenir-heavy": "avenir-next-heavy",
}


def is_v1(p):
    """A v1 project has Turkish top-level keys and no `version`."""
    return isinstance(p, dict) and "version" not in p and any(k in p for k in ("surum", "katmanlar", "genislik", "ad"))


def _copy_keys(src, dst, mapping):
    for old, new in mapping.items():
        if old in src:
            dst[new] = copy.deepcopy(src[old])


def _fade_v1(f):
    if isinstance(f, dict) and f.get("tur") == "fade":
        return _clean_number(_flt(f.get("sure"), 0.0))
    return 0


def _migrate_effect(e):
    if not isinstance(e, dict):
        return None
    t = e.get("tur")
    out = {"type": _EFFECT_MAP.get(t, t), "enabled": e.get("acik", True)}
    if t == "siyahbeyaz" and "miktar" in e:
        out["amount"] = e["miktar"]
    elif t in ("parlaklik", "kontrast", "doygunluk") and "deger" in e:
        out["value"] = e["deger"]
    elif t == "bulaniklik" and "yaricap" in e:
        out["radius"] = e["yaricap"]
    elif t == "yakinlas":
        if "bas" in e:
            out["from"] = e["bas"]
        if "son" in e:
            out["to"] = e["son"]
    return out


def _migrate_layer(l):
    out = {}
    tur = l.get("tur")
    out["type"] = _TYPE_MAP.get(tur, tur)
    _copy_keys(l, out, {"id": "id", "ad": "name", "grup": "group", "gizli": "hidden", "kilitli": "locked",
                        "baslangic": "start", "bitis": "end", "x": "x", "y": "y", "olcek": "scale",
                        "opaklik": "opacity", "kaynak": "src", "kaynak_bas": "src_in", "ses_duzeyi": "volume_db",
                        "sessiz": "muted", "zarf": "envelope", "metin": "text", "boyut": "size",
                        "harf_araligi": "letter_spacing", "satir_yuksekligi": "line_height"})
    if "renk" in l:
        out["color"] = l["renk"]
    out["fade_in"] = _fade_v1(l.get("giris"))
    out["fade_out"] = _fade_v1(l.get("cikis"))
    if "sigdir" in l:
        out["fit"] = _FIT_MAP.get(l["sigdir"], "cover")
    elif tur == "gorsel":
        out["fit"] = "cover"          # v1 default for images was cover; v2 default is contain
    if isinstance(l.get("kirp"), dict):
        k = l["kirp"]
        out["crop"] = {new: k[old] for old, new in (("ust", "top"), ("alt", "bottom"), ("sol", "left"), ("sag", "right"))
                       if old in k}
    if "hiza" in l:
        out["align"] = _ALIGN_MAP.get(l["hiza"], "center")
    if isinstance(l.get("golge"), dict):
        g = l["golge"]
        out["shadow"] = {new: g[old] for old, new in (("acik", "enabled"), ("bulaniklik", "blur"), ("opaklik", "opacity"))
                         if old in g}
    if tur == "yazi":
        out["font"] = FONT_MAP_V1.get(l.get("font", "gill"), l.get("font", "gill"))
    if "efektler" in l and isinstance(l["efektler"], list):
        out["effects"] = [e for e in (_migrate_effect(e) for e in l["efektler"]) if e is not None]
    return out


def migrate_v1(p):
    """Convert a v1 (Turkish-key) project dict to v2 shape. Pure; does not fill defaults."""
    out = {"version": VERSION}
    _copy_keys(p, out, {"ad": "name", "genislik": "width", "yukseklik": "height", "fps": "fps", "sure": "duration",
                        "arkaplan": "background", "cikti": "output"})
    if isinstance(p.get("genel"), dict):
        g = p["genel"]
        out["global"] = {new: copy.deepcopy(g[old]) for old, new in
                         (("gren", "grain"), ("vinyet", "vignette"), ("ses_hedef_lufs", "loudness_lufs"),
                          ("son_karartma", "fade_out")) if old in g}
    out["layers"] = [_migrate_layer(l) for l in p.get("katmanlar", []) if isinstance(l, dict)]
    out["notes"] = []
    for n in p.get("notlar", []):
        if isinstance(n, dict):
            nn = {}
            _copy_keys(n, nn, {"id": "id", "t": "t", "metin": "text", "zaman": "created"})
            out["notes"].append(nn)
    return out


# ---------------------------------------------------------------- loading
def project_path(pdir):
    return os.path.join(pdir, PROJECT_FILE)


def legacy_path(pdir):
    return os.path.join(pdir, LEGACY_FILE)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as e:
        raise SchemaError(f"cannot read {os.path.basename(path)}: {e}")


def dumps(project):
    return json.dumps(project, ensure_ascii=False, indent=2) + "\n"


def migrate_dir(pdir):
    """If only proje.json exists, write project.json (normalised v2) next to it; proje.json is never touched.
    Returns True if a migration happened."""
    if os.path.exists(project_path(pdir)) or not os.path.exists(legacy_path(pdir)):
        return False
    v2 = normalize(migrate_v1(_read_json(legacy_path(pdir))))
    tmp = project_path(pdir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(dumps(v2))
    os.replace(tmp, project_path(pdir))
    return True


def load_project(pdir):
    """Load `<pdir>/project.json` (or migrate legacy `proje.json` in memory) -> normalised v2 dict."""
    pj, lg = project_path(pdir), legacy_path(pdir)
    if os.path.exists(pj):
        return normalize(_read_json(pj))
    if os.path.exists(lg):
        return normalize(migrate_v1(_read_json(lg)))
    raise FileNotFoundError(f"no {PROJECT_FILE} in {pdir}")


def slugify_font_id(*parts):
    """Lowercase ASCII slug: spaces -> '-', anything else non-alphanumeric dropped."""
    s = " ".join(p for p in parts if p)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")
