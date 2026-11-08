"""Note anchors: a note follows the content it was written on (SPEC 1.0, "Anchors"). Python twin of web/js/anchor.js.

WHERE THE TRUTH LIVES. A note's `t` is a cache; the truth is its `anchor` plus the layers. `follow(project)` below is the ONE rule that turns
them into `t`. It runs inside the read step on both sides, `schema.normalize` here and `normalizeProject` / `change()` in web/js/state.js, so
no reader (editor, GET /api/project, MCP, the notes payload, NOTLAR.md) can see a note that did not follow, and inside the server's single
write funnel (`server.App._write_project`) and the rerender's direct write, so project.json on disk carries the followed times for anyone who
reads it raw. The browser cannot call Python for every edit (a drag fires dozens of edits per second, and the cascade must land in the SAME undo
step as the edit), so the rule exists twice, line for line, and tests/anchor_vectors.json (made by tests/make_anchor_vectors.py from this file)
pins both twins to the same answers. Any change here must be made in web/js/anchor.js too, then regenerate the vectors.

Anchor (optional on a note; notes without one behave exactly as before):
  {"unit": <source.unit>, "frame": <source frame>, "unit_start": <source.unit_start_frame then>, "t": <note t>}   a code-made unit
  {"layer": <layer id>, "dt": <media time: (t - start) * speed + src_in>, "sig": <signature>, "t": <note t>}      any other layer
Which layer: the top layer under the note's mark when it has one (what the user pointed at); without a mark the scene, i.e. the front-most
code-made unit, else the front-most video, image, text, colour, audio layer. A note without a mark is about the moment, and a logo or a title
that sits in front of the whole cut must not capture every note (it never moves when the scenes do).
`sig` (`sig()` below: type + src for media, the type alone for text and colour) guards against a different layer that got the same id later
(agents reuse "title", "v1"): a layer of another type counts as gone, and so does one with another src unless it still contains the anchored
moment (a relink of the same clip: the signature is then refreshed). Same type + moment inside is accepted as the same layer (a small risk). A split (the same file continuing in another layer,
contiguous media range, as the editor's split or an agent's hand split makes) is followed: the anchor moves to the layer that now shows that
media time.
  "stale": true is added while the anchored moment is not inside its layer (the layer is gone or trimmed past it); `t` is then left alone.
`anchor.t` is the note time the anchor last produced. A note whose `t` differs from it was moved by hand (any writer): it is anchored again there.

Determinism: only + - * / floor and comparisons on doubles; rounding is floor(x * 10^k + 0.5) / 10^k in both twins (not Python's round(), which
rounds halves to even, nor JS toFixed). Standard library only; no I/O.
"""
import math

EPS_T = 1e-4         # a resolved time this close to the note's time leaves `t` as it is (float noise, frame rounding of `frame`)
EPS_IN = 1e-6        # tolerance at a layer's start for "inside"
RANK = {"video": 1, "image": 2, "text": 3, "color": 4, "audio": 5}        # rank 0 = a code-made unit (video / image with a usable `source`)


def num(v):
    """A finite JSON number (not a bool) or None. Strings are not numbers here: normalize has already turned them into numbers."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def rnd(x, k):
    s = 10.0 ** k
    return math.floor(x * s + 0.5) / s


def tidy(x):
    """3.0 -> 3 so the JSON reads like the editor's (JS has one number type)."""
    return int(x) if x == math.floor(x) and abs(x) < 1e12 else x


def span(l):
    st = num(l.get("start"))
    st = 0.0 if st is None else st
    en = num(l.get("end"))
    return st, (st + 3.0 if en is None else en)                              # the defaults of schema.normalize_layer / state.js normalizeLayer


def src_in(l):
    v = num(l.get("src_in"))
    return 0.0 if v is None else v


def speed(l):
    v = num(l.get("speed"))
    return v if v is not None and v > 0 else 1.0


def unit_of(l):
    """(unit, fps, source) of a video / image layer made with code whose source frame can be computed (source_map.source_frame), else None."""
    s = l.get("source")
    if l.get("type") not in ("video", "image") or not isinstance(s, dict):
        return None
    u, fps = s.get("unit"), num(s.get("fps"))
    if not isinstance(u, str) or not u or fps is None or fps <= 0:
        return None
    return u, fps, s


def origin(s):
    """The source frame media time 0 is (same as source_map.source_frame)."""
    o = num(s.get("media_start_frame"))
    if o is None:
        o = num(s.get("unit_start_frame"))
    return 0.0 if o is None else o


def unit_start(s):
    v = num(s.get("unit_start_frame"))
    return 0.0 if v is None else v


def sig(l):
    """What makes a layer the same layer for an anchor: type + src for media (video, audio, image: also what finds the pieces of a split), the
    type alone for text and colour (the text or colour is exactly what a note asks to change: fixing it must not strand the note)."""
    typ = l.get("type") if isinstance(l.get("type"), str) else ""
    if typ in ("video", "audio", "image"):
        return typ + ":" + (l["src"] if isinstance(l.get("src"), str) else "")
    return typ


def _same_kind(sg, l):
    """Is `l` still the anchored layer? Same signature, or the same type when the src changed (a relink of the same clip): the caller then also
    needs the anchored moment inside `l`. A layer of another type under the id is another layer."""
    return sg is None or sig(l) == sg or sg.split(":", 1)[0] == (l.get("type") if isinstance(l.get("type"), str) else "")


def layer_anchor(l, t):
    """A layer anchor on layer `l` at project time `t` (t is taken as inside it)."""
    st, _en = span(l)
    return {"layer": l["id"], "dt": tidy(rnd((t - st) * speed(l) + src_in(l), 6)), "sig": sig(l), "t": tidy(t)}


def valid(a):
    """Is `a` a usable anchor shape? (schema.normalize drops the others, schema.validate reports them.)"""
    if not isinstance(a, dict):
        return False
    if isinstance(a.get("unit"), str) and a["unit"] and num(a.get("frame")) is not None:
        return True
    return isinstance(a.get("layer"), str) and bool(a["layer"]) and num(a.get("dt")) is not None


def _anchor_on(l, t):
    us = unit_of(l)
    if us:
        u, fps, s = us
        st, _en = span(l)
        f = origin(s) + (t - st + src_in(l)) * fps
        return {"unit": u, "frame": tidy(rnd(f, 3)), "unit_start": tidy(unit_start(s)), "t": tidy(t)}
    return layer_anchor(l, t)


def _on_screen(l, t):
    if not isinstance(l, dict) or l.get("hidden") is True or not isinstance(l.get("type"), str) or l["type"] not in RANK:
        return False
    if not isinstance(l.get("id"), str) or not l["id"]:
        return False
    st, en = span(l)
    return st <= t < en


def anchor_at(p, t, under=None):
    """The anchor for a note written at project time `t`. `under`: ids of the layers under the note's mark, top first (the editor's hit test,
    stored on the mark as `layers`); the first of them that is a picture layer on screen at `t` wins. Otherwise the scene: the front-most
    code-made unit, else the front-most video, image, text, colour, audio layer active at `t`. None when nothing is there."""
    t = num(t)
    if t is None:
        return None
    layers = p.get("layers") or []
    for lid in under if isinstance(under, list) else []:
        l = next((x for x in layers if isinstance(x, dict) and x.get("id") == lid), None)
        if l is not None and _on_screen(l, t) and l["type"] != "audio":
            return _anchor_on(l, t)
    best = None
    for i, l in enumerate(layers):
        if not _on_screen(l, t):
            continue
        r = 0 if unit_of(l) else RANK[l["type"]]
        if best is None or r < best[0]:
            best = (r, i, l)
    return None if best is None else _anchor_on(best[2], t)


def _media_end(l):
    st, en = span(l)
    return src_in(l) + (en - st) * speed(l)


def _split_chain(layers, a_layer, sg):
    """Layers that continue `a_layer`'s media (same signature, video / audio, contiguous src_in ranges, transitively): the pieces of a split."""
    if a_layer.get("type") not in ("video", "audio"):
        return []
    pool = [l for l in layers if l is not a_layer and l.get("type") in ("video", "audio") and sig(l) == sg and isinstance(l.get("id"), str)]
    chain, todo = [], [a_layer]
    while todo:
        x = todo.pop()
        for l in pool:
            if any(l is c for c in chain):                                 # identity, not equality: two identical clips are two layers
                continue
            if abs(_media_end(x) - src_in(l)) < 1e-3 or abs(_media_end(l) - src_in(x)) < 1e-3:
                chain.append(l)
                todo.append(l)
    return chain


def _inside(l, dt):
    st, en = span(l)
    tt = st + (dt - src_in(l)) / speed(l)
    return max(tt, st) if st - EPS_IN <= tt < en else None


def locate(p, a, t):
    """[time, layer id] the anchor `a` points at now; [None, None] when that moment is not inside its layer (layer gone, a different layer under
    the same id, trimmed past it). A unit can be on several layers (a split clip): the candidate nearest to the note's time `t` wins, the first
    one on a tie; the layer id is then None (unit anchors name no layer). A layer anchor whose moment left its layer follows a split piece."""
    t = num(t)
    t = 0.0 if t is None else t
    layers = [l for l in (p.get("layers") or []) if isinstance(l, dict)]
    if isinstance(a.get("unit"), str) and a["unit"] and num(a.get("frame")) is not None:
        best = None
        for l in layers:
            us = unit_of(l)
            if not us or us[0] != a["unit"]:
                continue
            _u, fps, s = us
            f = num(a["frame"])
            if num(a.get("unit_start")) is not None:                       # frames are counted from the unit's start: survive a renumbered film
                f = f - num(a["unit_start"]) + unit_start(s)
            st, en = span(l)
            tt = st + (f - origin(s)) / fps - src_in(l)
            if not (st - EPS_IN <= tt < en):
                continue
            tt = max(tt, st)
            if best is None or abs(tt - t) < abs(best - t):
                best = tt
        return [best, None]
    if isinstance(a.get("layer"), str) and a["layer"] and num(a.get("dt")) is not None:
        l = next((x for x in layers if x.get("id") == a["layer"]), None)
        sg = a["sig"] if isinstance(a.get("sig"), str) else None
        if l is None or not _same_kind(sg, l):
            return [None, None]
        dt = num(a["dt"])
        tt = _inside(l, dt)
        if tt is not None:
            return [tt, l["id"]]
        if sg is not None and sig(l) != sg:                                  # src changed AND the moment is not on it: not the same clip
            return [None, None]
        best = None
        for x in _split_chain(layers, l, sig(l)):
            tx = _inside(x, dt)
            if tx is not None and (best is None or abs(tx - t) < abs(best[0] - t)):
                best = [tx, x["id"]]
        return best or [None, None]
    return [None, None]


def resolve(p, a, t):
    """Project time the anchor `a` points at now, or None (see `locate`)."""
    return locate(p, a, t)[0]


def mark_layers(n):
    """Ids of the layers under the note's first mark, top first (the editor stores them on the mark as `layers: [{id, name}]`)."""
    m = n.get("marks")[0] if isinstance(n.get("marks"), list) and n.get("marks") else None
    ls = m.get("layers") if isinstance(m, dict) and isinstance(m.get("layers"), list) else []
    return [x["id"] for x in ls if isinstance(x, dict) and isinstance(x.get("id"), str) and x["id"]]


def follow(p):
    """Re-derive every anchored note's `t` from its anchor, in place. Returns the ids of the notes whose `t` changed.
    - no anchor: untouched (old projects behave as before);
    - `t` differs from `anchor.t`: moved by hand, anchored again where it is now (no layer there: the anchor is dropped);
    - the anchored moment is inside its layer: `t` = that moment (unchanged within EPS_T), `stale` cleared;
    - otherwise: `t` unchanged, `anchor.stale` = true (retried on every later change, so an undone delete heals)."""
    moved = []
    for n in p.get("notes") or []:
        if not isinstance(n, dict) or not isinstance(n.get("anchor"), dict):
            continue
        t = num(n.get("t"))
        if t is None:
            continue
        a = n["anchor"]
        if not valid(a):                                                    # a broken anchor is schema.normalize's to drop, not ours to mark
            continue
        if num(a.get("t")) is not None and num(a.get("t")) != t:
            na = anchor_at(p, t, mark_layers(n))
            if na:
                n["anchor"] = na
            else:
                del n["anchor"]
            continue
        tt, lid = locate(p, a, t)
        if tt is None:
            a["stale"] = True
            a["t"] = n["t"]
            continue
        tt = tidy(rnd(max(0.0, tt), 6))
        if abs(tt - t) > EPS_T:
            n["t"] = tt
            moved.append(n.get("id"))
        if lid is not None:
            lay = next(x for x in p["layers"] if isinstance(x, dict) and x.get("id") == lid)
            if lid != a.get("layer"):                                       # a split piece shows it now: anchor to that piece
                a = n["anchor"] = layer_anchor(lay, n["t"])
            elif isinstance(a.get("sig"), str) and a["sig"] != sig(lay):    # the same layer, relinked / older signature: keep it current
                a["sig"] = sig(lay)
        a.pop("stale", None)
        a["t"] = n["t"]
    return moved
