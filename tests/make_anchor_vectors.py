"""Shared test vectors for the note-anchor twins (note_anchor.py <-> web/js/anchor.js), written to tests/anchor_vectors.json.

usage: python tests/make_anchor_vectors.py           (re)write the file from the Python twin
       python tests/make_anchor_vectors.py --check   exit 1 when the committed file differs from what the Python twin gives now
The JS twin is checked against the file by tests/anchor_node_check.mjs (tests/test_note_anchor.py runs both)."""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import note_anchor  # noqa: E402

OUT = os.path.join(HERE, "anchor_vectors.json")


def scenes(**over):
    """Title in front of scene 1, three scenes (scene 2 has src_in, scene 3 plays at 2x), a colour slate, music."""
    p = {"layers": [
        {"id": "title", "type": "text", "start": 0, "end": 2},
        {"id": "s1", "type": "video", "start": 0, "end": 4, "src_in": 0},
        {"id": "s2", "type": "video", "start": 4, "end": 8, "src_in": 2.5},
        {"id": "s3", "type": "video", "start": 8, "end": 12, "speed": 2},
        {"id": "slate", "type": "color", "start": 12, "end": 13},
        {"id": "music", "type": "audio", "start": 0, "end": 14, "src_in": 1},
        {"id": "ghost", "type": "image", "start": 0, "end": 14, "hidden": True},
    ], "notes": []}
    p.update(over)
    return p


def film():
    """Two units of one film (shared media, src_in) and a split clip of unit b (two layers), a hand-made unit with no media_start_frame."""
    src = {"kind": "cinecode", "fps": 24, "media_start_frame": 0}
    return {"layers": [
        {"id": "a", "type": "video", "start": 0, "end": 1, "src_in": 0, "source": dict(src, unit="A", unit_start_frame=0)},
        {"id": "b1", "type": "video", "start": 1, "end": 1.5, "src_in": 1, "source": dict(src, unit="B", unit_start_frame=24)},
        {"id": "b2", "type": "video", "start": 1.5, "end": 2, "src_in": 1.5, "source": dict(src, unit="B", unit_start_frame=24)},
        {"id": "h", "type": "image", "start": 2, "end": 3, "source": {"unit": "H", "fps": 30, "unit_start_frame": 100}},
        {"id": "nofps", "type": "video", "start": 3, "end": 4, "source": {"unit": "X"}},
        {"id": "aud", "type": "audio", "start": 0, "end": 4, "source": dict(src, unit="A", unit_start_frame=0)},
    ], "notes": []}


def shifted(p, ids, dt, end_of=None, new_end=None):
    p = copy.deepcopy(p)
    for l in p["layers"]:
        if l["id"] in ids:
            l["start"] += dt
            l["end"] += dt
        if l["id"] == end_of:
            l["end"] = new_end
    return p


def with_notes(p, times):
    p = copy.deepcopy(p)
    p["notes"] = []
    for i, t in enumerate(times):
        a = note_anchor.anchor_at(p, t)
        n = {"id": f"n{i}", "t": t, "text": str(i)}
        if a:
            n["anchor"] = a
        p["notes"].append(n)
    return p


def cases():
    out = []
    S, F = scenes(), film()
    times = [0, 0.5, 1.999, 2, 3.99, 4, 5, 7.999, 8, 9.25, 11.9, 12, 12.5, 13, 13.5, 14, -1]
    for t in times:
        out.append({"fn": "anchor_at", "args": [S, t]})
    for t in [0, 0.5, 0.999, 1, 1.25, 1.5, 1.75, 2, 2.5, 3.5, 4]:
        out.append({"fn": "anchor_at", "args": [F, t]})
    out.append({"fn": "anchor_at", "args": [{"layers": []}, 1]})
    out.append({"fn": "anchor_at", "args": [{"layers": [{"type": "video", "start": 0, "end": 2}]}, 1]})      # no id: not an anchor
    # resolve: the same anchors against changed projects
    S2 = shifted(S, {"s2", "s3", "slate"}, 1, "s1", 5)
    S3 = copy.deepcopy(S)
    S3["layers"] = [l for l in S3["layers"] if l["id"] != "s2"]
    S4 = copy.deepcopy(S)
    S4["layers"][2].update(start=6, src_in=4.5)                                                               # head of s2 trimmed by 2 s
    for t in [0.5, 3, 4, 5, 7.5, 9, 11.9, 12.5, 13.5]:
        a = note_anchor.anchor_at(S, t)
        for proj in (S, S2, S3, S4):
            out.append({"fn": "resolve", "args": [proj, a, t]})
    F2 = copy.deepcopy(F)
    F2["layers"][0].update(src="film.A.mp4", src_in=0, end=1.5)
    F2["layers"][0]["source"].update(media_start_frame=0, frames=36)
    F2 = shifted(F2, {"b1", "b2"}, 0.5)
    F3 = copy.deepcopy(F2)
    for l in F3["layers"][1:3]:                                                                                # B re-rendered at [36, 60): own file
        l["source"].update(unit_start_frame=36, media_start_frame=36)
        l["src_in"] = l["src_in"] - 1
    for t in [0.5, 0.99, 1.25, 1.6, 1.99, 2.5]:
        a = note_anchor.anchor_at(F, t)
        for proj in (F, F2, F3):
            out.append({"fn": "resolve", "args": [proj, a, t]})
    out.append({"fn": "resolve", "args": [F, {"unit": "B", "frame": 30}, 0]})                                 # hand-written: no unit_start, no t
    out.append({"fn": "resolve", "args": [S, {"layer": "nope", "dt": 1}, 0]})
    # follow: whole note lists
    base = with_notes(S, [0.5, 1.5, 3.2, 4.1, 5.0, 6.3, 7.9, 8.0, 10.25, 11.5, 12.5, 13.5])
    for proj in (S2, S3, S4):
        p = copy.deepcopy(proj)
        p["notes"] = copy.deepcopy(base["notes"])
        out.append({"fn": "follow", "args": [p]})
    moved = copy.deepcopy(base)
    moved["notes"][4]["t"] = 9.0                                                                              # moved by hand
    moved["notes"][5]["t"] = 13.9                                                                             # moved by hand to where only music plays
    out.append({"fn": "follow", "args": [moved]})
    gone = copy.deepcopy(base)
    gone["layers"] = []
    gone["notes"][0]["t"] = 99                                                                                 # moved by hand to nothing: anchor dropped
    out.append({"fn": "follow", "args": [gone]})
    old = copy.deepcopy(S2)
    old["notes"] = [{"id": "o1", "t": 5, "text": "old"}, {"id": "o2", "t": 6, "text": "bad", "anchor": {"layer": 3}}, {"id": "o3", "t": "x", "text": "t?", "anchor": {"layer": "s2", "dt": 1}}]
    out.append({"fn": "follow", "args": [old]})
    fb = with_notes(F, [0.5, 1.25, 1.75, 2.5])
    for proj in (F2, F3):
        p = copy.deepcopy(proj)
        p["notes"] = copy.deepcopy(fb["notes"])
        out.append({"fn": "follow", "args": [p]})
    stale = copy.deepcopy(S3)
    stale["notes"] = copy.deepcopy(base["notes"])
    note_anchor.follow(stale)
    healed = copy.deepcopy(S)
    healed["notes"] = stale["notes"]
    out.append({"fn": "follow", "args": [healed]})                                                           # the layer came back: stale cleared
    for v in (True, 1, "1", None, {"unit": "", "frame": 1}, {"unit": "A", "frame": "1"}, {"layer": "s1", "dt": 0}, {"unit": "A", "frame": 0.5}):
        out.append({"fn": "valid", "args": [v]})
    # review fixes: what the user pointed at (mark), signatures, splits, ids reused, prototype names as types
    under = copy.deepcopy(S)
    for t, ids in ((1, ["title", "s1"]), (1, ["music"]), (1, ["ghost", "s1"]), (1, ["nope"]), (5, ["title"]), (5, ["s2"]), ("x", ["s1"])):
        out.append({"fn": "anchor_at", "args": [under, t, ids]})
    marked = with_notes(S, [1.0])
    marked["notes"][0]["marks"] = [{"type": "rect", "x": 0, "y": 0, "w": 1, "h": 1, "layers": [{"id": "title", "name": "Title"}]}]
    marked["notes"][0]["t"] = 1.5                                                                             # moved by hand: anchored again ON THE TITLE
    out.append({"fn": "follow", "args": [marked]})
    for n in ({}, {"marks": []}, {"marks": [{"layers": [{"id": "a"}, {"name": "b"}, 3, {"id": ""}, {"id": "c"}]}]}, {"marks": "x"}):
        out.append({"fn": "mark_layers", "args": [n]})
    for l in ({"type": "video", "src": "m/a.mp4"}, {"type": "audio"}, {"type": "image", "src": 3}, {"type": "text", "text": "Fena değil 🎬"},
              {"type": "text"}, {"type": "text", "text": "\ud800x"}, {"type": "color", "color": "#ff0000"}, {"type": 5}, {}):
        out.append({"fn": "sig", "args": [l]})
    out.append({"fn": "anchor_at", "args": [{"layers": [{"id": "x", "type": "toString", "start": 0, "end": 2}, {"id": "y", "type": "constructor", "start": 0, "end": 2}]}, 1]})
    out.append({"fn": "anchor_at", "args": [{"layers": [{"id": "x", "type": "toString", "start": 0, "end": 2}]}, 1, ["x"]]})
    split = with_notes(S, [4.1, 5.0, 6.3, 7.9])                                                               # an agent splits s2 at 6
    split["layers"][2]["end"] = 6
    split["layers"].insert(3, {"id": "s2b", "type": "video", "start": 6, "end": 8, "src_in": 4.5})
    out.append({"fn": "follow", "args": [split]})
    split2 = copy.deepcopy(split)                                                                             # ... and splits the piece again, then moves the last one
    split2["layers"][3]["end"] = 7
    split2["layers"].insert(4, {"id": "s2c", "type": "video", "start": 9, "end": 10, "src_in": 5.5})
    out.append({"fn": "follow", "args": [split2]})
    for proj in (split, split2):
        for t in (5.0, 6.3, 7.9):
            out.append({"fn": "locate", "args": [proj, note_anchor.anchor_at(S, t), t]})
    elsewhere = with_notes(S, [5.0])                                                                          # the same file elsewhere, not contiguous
    elsewhere["layers"][2]["end"] = 4.5
    elsewhere["layers"][3]["src_in"] = 0
    out.append({"fn": "follow", "args": [elsewhere]})
    reused = with_notes(S, [5.0, 1.0, 12.5])                                                                  # ids reused for layers of another type
    for l in reused["layers"]:
        if l["id"] == "s2":
            l.update(type="text", text="caption", start=3, end=9)
        if l["id"] == "title":
            l.update(type="image", src="x.png", start=0.5)
        if l["id"] == "slate":
            l.update(type="video", src="v.mp4", start=11)
    reused["notes"][1]["anchor"] = note_anchor.anchor_at(S, 1.0, ["title"])
    reused["notes"][1]["anchor"]["t"] = 1.0
    out.append({"fn": "follow", "args": [reused]})
    edited = with_notes(S, [5.0, 1.0, 12.5, 6.0])                                                             # ordinary edits: text, colour, relink
    edited["notes"][1]["anchor"] = note_anchor.anchor_at(S, 1.0, ["title"])
    for l in edited["layers"]:
        if l["id"] == "title":
            l.update(text="fixed typo", start=0.5)
        if l["id"] == "slate":
            l.update(color="#00ff00", start=12.5, end=13.5)
        if l["id"] == "s2":
            l.update(src="graded.mp4", start=4.5)
    edited["notes"][3]["anchor"]["sig"] = "video:old.mp4"
    edited["notes"][3]["anchor"]["dt"] = 99                                                                   # relinked AND its moment gone: stale
    out.append({"fn": "follow", "args": [edited]})
    oldsig = with_notes(S, [1.0])                                                                             # an anchor from the text-hash days
    oldsig["notes"][0]["anchor"] = dict(note_anchor.anchor_at(S, 1.0, ["title"]), sig="text:32543b0b")
    out.append({"fn": "follow", "args": [oldsig]})
    nosig = with_notes(S, [5.0])                                                                              # an anchor written before signatures: still followed
    del nosig["notes"][0]["anchor"]["sig"]
    nosig["layers"][2]["start"] = 5
    out.append({"fn": "follow", "args": [nosig]})
    for t in (1.25, 9.5):
        out.append({"fn": "layer_anchor", "args": [S["layers"][3], t]})
    for c in out:
        if c["fn"] == "follow":
            p = copy.deepcopy(c["args"][0])
            note_anchor.follow(p)
            c["out"] = p["notes"]
        else:
            c["out"] = getattr(note_anchor, c["fn"])(*copy.deepcopy(c["args"]))
    return out


def render():
    return json.dumps({"cases": cases()}, ensure_ascii=True, separators=(",", ":")) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        cur = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if cur != text:
            print("tests/anchor_vectors.json is out of date: run python tests/make_anchor_vectors.py")
            sys.exit(1)
        sys.exit(0)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"{len(cases())} cases -> {OUT}")
