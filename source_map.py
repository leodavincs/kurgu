"""Element maps for clips made with code (SPEC 1.9).

A `source` on a layer says which tool and unit made the clip; its `elements` sidecar says where each on-screen element is, per source frame,
plus the file:line that draws it. This module reads the sidecar and answers two questions for the note payload:

- `source_frame(layer, t)`: which frame of the unit's source video is on screen at project time `t`;
- `elements_at(layer, t, W, H, d)`: the elements visible then, with their boxes mapped into PROJECT pixels.

Sidecar (`*.kurgu.json`): {"fps": 24, "size": [1920, 1080], "elements": [{"id", "label", "kind", "file", "line",
"boxes": [{"f": frame, "l", "t", "r", "b"}]}]}. `f` is a frame of the SOURCE VIDEO (the same numbering as `unit_start_frame`), the box is in
pixels of `size`. Sampled boxes are interpolated linearly between the two neighbouring samples. Nothing here runs a command; every failure
becomes an `error` string. Standard library only (geometry is borrowed lazily from agent_notes)."""
import json
import os

_cache = {}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _resolve(d, source, key):
    """Absolute path of source[key] (relative to the project folder, then to source.root), or None."""
    rel = source.get(key)
    if not d or not isinstance(rel, str) or not rel:
        return None
    base = os.path.realpath(d)
    cands = [os.path.join(base, rel)]
    if isinstance(source.get("root"), str) and source["root"]:
        cands.append(os.path.join(base, source["root"], rel))
    allowed = [base]
    try:
        import render_adaptors
        allowed += render_adaptors.trusted_roots(base)
    except Exception:
        pass
    for c in cands:
        c = os.path.realpath(c)
        if not any(c == a or c.startswith(a + os.sep) for a in allowed):          # the element map must live in the project or a folder the USER trusts
            continue
        if os.path.isfile(c):
            return c
    first = os.path.realpath(cands[0])
    return first if any(first == a or first.startswith(a + os.sep) for a in allowed) else None


def load(path):
    """(map, error). map = {"fps", "size", "elements"} with boxes sorted by frame; cached by path + mtime + size (a rewrite inside one
    clock tick keeps the mtime on Windows and on coarse file systems, the size almost never)."""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return None, f"element map not found: {os.path.basename(path or '')}"
    if key in _cache:
        return _cache[key]
    res = _parse(path)
    for k in [k for k in _cache if k[0] == path]:
        del _cache[k]
    _cache[key] = res
    return res


def _parse(path):
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"element map unreadable ({os.path.basename(path)}): {type(e).__name__}"
    if not isinstance(raw, dict) or not isinstance(raw.get("elements"), list):
        return None, f"element map {os.path.basename(path)} has no elements list"
    size = raw.get("size")
    size = [_num(size[0]), _num(size[1])] if isinstance(size, (list, tuple)) and len(size) == 2 else [None, None]
    if not size[0] or not size[1] or size[0] <= 0 or size[1] <= 0:
        return None, f"element map {os.path.basename(path)} needs size [width, height]"
    els = []
    for e in raw["elements"]:
        if not isinstance(e, dict):
            continue
        boxes = []
        for b in e.get("boxes") if isinstance(e.get("boxes"), list) else []:
            v = [_num(b.get(k)) for k in ("f", "l", "t", "r", "b")] if isinstance(b, dict) else [None]
            if None not in v:
                boxes.append(tuple(v))
        if boxes:
            boxes.sort(key=lambda x: x[0])
            ln = _num(e.get("line"))
            els.append({"id": str(e.get("id", "")), "label": str(e.get("label", "")), "kind": str(e.get("kind", "")),
                        "file": str(e.get("file", "")), "line": int(ln) if ln is not None else None,
                        "approx": bool(e.get("approx")), "boxes": boxes})
    return {"fps": _num(raw.get("fps")), "size": size, "elements": els, "stale": bool(raw.get("stale")), "stale_reason": str(raw.get("stale_reason") or "")}, None


def source_frame(layer, t):
    """Frame of the unit's source video at project time `t`: unit_start_frame + (t - start + src_in) * fps. None without a usable fps."""
    s = layer.get("source") if isinstance(layer.get("source"), dict) else {}
    fps = _num(s.get("fps"))
    if not fps or fps <= 0:
        return None
    t0 = _num(layer.get("start")) or 0.0
    origin = _num(s.get("media_start_frame"))                       # the source frame media time 0 is: 0 for a whole film, the unit's first frame for a unit's own file
    if origin is None:
        origin = _num(s.get("unit_start_frame")) or 0.0              # hand-made projects: the media IS the unit
    return origin + (t - t0 + (_num(layer.get("src_in")) or 0.0)) * fps


def _sample(boxes, f, tol):
    """Box (l, t, r, b) at frame f: linear between the neighbouring samples, the nearest one within `tol` frames outside the sampled range, else None."""
    if f < boxes[0][0] - tol or f > boxes[-1][0] + tol:
        return None
    prev = nxt = None
    for b in boxes:
        if b[0] <= f:
            prev = b
        if b[0] >= f:
            nxt = b
            break
    if prev is None:
        return nxt[1:]
    if nxt is None or nxt[0] == prev[0]:
        return prev[1:]
    a = (f - prev[0]) / (nxt[0] - prev[0])
    return tuple(p + (q - p) * a for p, q in zip(prev[1:], nxt[1:]))


def to_project(layer, W, H, t, d, box, size):
    """Box (l, t, r, b) in source pixels of `size` -> project pixels, using the geometry of the layer (fit, scale, x / y, keys at `t`, crop).
    Rotation is ignored (the layer's axis-aligned box is used)."""
    import agent_notes
    x0, y0, x1, y1 = agent_notes._box_of(layer, W, H, t, d)
    c = layer.get("crop") if isinstance(layer.get("crop"), dict) else {}
    cl, cr, ct, cb = (_num(c.get(k)) or 0.0 for k in ("left", "right", "top", "bottom"))
    fw, fh = max(1e-6, 1 - cl - cr), max(1e-6, 1 - ct - cb)
    l, tp, r, b = box
    u0, u1 = ((l / size[0]) - cl) / fw, ((r / size[0]) - cl) / fw
    v0, v1 = ((tp / size[1]) - ct) / fh, ((b / size[1]) - ct) / fh
    w, h = x1 - x0, y1 - y0
    return x0 + u0 * w, y0 + v0 * h, x0 + u1 * w, y0 + v1 * h


def elements_at(layer, t, W=1920, H=1080, d=None):
    """{"frame", "elements": [{id, label, kind, file, line, box: [l, t, r, b] in project px}], "error"?} for a layer with `source.elements` at project time t.
    Never raises; a missing / corrupt sidecar gives `error`. Boxes outside the visible frame are kept (agents see what is cropped)."""
    out = {"frame": None, "elements": []}
    try:
        src = layer.get("source") if isinstance(layer.get("source"), dict) else None
        if not src or not src.get("elements"):
            return out
        f = source_frame(layer, t)
        out["frame"] = f
        if f is None:
            out["error"] = "source.fps is missing, cannot map time to a source frame"
            return out
        path = _resolve(d, src, "elements")
        if not path:
            out["error"] = "source.elements cannot be used: no project folder, or the file is outside the project and the folders you trust"
            return out
        m, err = load(path)
        if err:
            out["error"] = err
            return out
        if m.get("stale"):
            out["error"] = "element map is STALE (the clip was re-rendered and the map could not be re-measured): boxes may be wrong" + (f" ({m['stale_reason']})" if m.get("stale_reason") else "")
        fps = _num(src.get("fps"))
        mf = f * m["fps"] / fps if m["fps"] and m["fps"] != fps else f      # sidecar frames count at the sidecar's own fps
        for e in m["elements"]:
            gaps = sorted(b[0] - a[0] for a, b in zip(e["boxes"], e["boxes"][1:]) if b[0] > a[0])
            tol = min(gaps[len(gaps) // 2], 12) if gaps else 1
            bx = _sample(e["boxes"], mf, tol)
            if bx is None:
                continue
            px = to_project(layer, W, H, t, d, bx, m["size"])
            row = {k: e[k] for k in ("id", "label", "kind", "file", "line")}
            row["box"] = [round(v, 1) for v in px]
            if e["approx"]:
                row["approx"] = True
            out["elements"].append(row)
    except Exception as ex:                     # a bonus; never break the note
        out["error"] = f"element map failed: {type(ex).__name__}"
    return out


def intersects(box, rects):
    """True when the project-px box [l, t, r, b] overlaps one of the (x0, y0, x1, y1) rects."""
    return any(box[0] <= r[2] and box[2] >= r[0] and box[1] <= r[3] and box[3] >= r[1] for r in rects)
