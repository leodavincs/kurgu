"""Notes follow the content they were written on (SPEC 1.0, "Anchors").

A note keeps its project time `t`, but it also carries an `anchor`: the code-made unit + source frame on screen when it was written, else the scene
layer + its media time. When the layers move (a scene made longer and the later scenes shifted, a unit re-rendered longer), every reader and every
server write re-derives `t` from the anchor. An anchor that no longer lands inside its layer is marked `stale` and `t` is left alone.
The rule has two twins, note_anchor.py and web/js/anchor.js, pinned by the shared vectors in tests/anchor_vectors.json."""
import copy
import json
import os
import shutil
import subprocess
import sys

import pytest

import agent_notes
import mcp_server
import schema

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VECTORS = os.path.join(HERE, "anchor_vectors.json")


def _write(d, proj):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
        json.dump(proj, f, indent=2)


def _raw(d):
    with open(os.path.join(d, "project.json"), encoding="utf-8") as f:
        return json.load(f)


def _notes_by_text(p):
    return {n["text"]: n for n in p["notes"]}


def three_scenes():
    """Three 4 s scenes back to back; a title over the first 2 s sits IN FRONT of scene 1 (index 0), music under everything."""
    return {"version": 2, "name": "three", "width": 1280, "height": 720, "fps": 30, "duration": 13, "notes": [], "layers": [
        {"id": "title", "type": "text", "text": "HELLO", "start": 0, "end": 2},
        {"id": "s1", "type": "video", "src": "media/a.mp4", "start": 0, "end": 4, "src_in": 0},
        {"id": "s2", "type": "video", "src": "media/b.mp4", "start": 4, "end": 8, "src_in": 2.5},
        {"id": "s3", "type": "video", "src": "media/a.mp4", "start": 8, "end": 12, "src_in": 0},
        {"id": "music", "type": "audio", "src": "media/m.wav", "start": 0, "end": 12},
    ]}


TEN = [0.5, 1.5, 3.2, 4.1, 5.0, 6.3, 7.9, 8.0, 10.25, 11.5]                    # 3 on scene 1, 4 on scene 2, 3 on scene 3


def _add_ten(d):
    """Half through the CLI (`kurgu notes add`, the human's path in scripts), half through MCP add_note (the agent's)."""
    for i, t in enumerate(TEN):
        if i % 2 == 0:
            agent_notes.add_user_note(d, t, f"note {i}", None, mcp_server._write_notes)
        else:
            mcp_server.tool_add_note({"project_dir": d, "t": t, "text": f"note {i}"}, None)


def _edit(d, **a):
    mcp_server.tool_edit_project(dict(a, project_dir=d), None)


def test_ten_notes_over_three_scenes_follow_a_longer_first_scene(tmp_path):
    d = str(tmp_path / "p")
    _write(d, three_scenes())
    _add_ten(d)
    before = _notes_by_text(_raw(d))
    assert all(n.get("anchor") for n in before.values()), "every new note is anchored"
    assert before["note 0"]["anchor"]["layer"] == "s1"                      # the scene, not the title in front of it and not the music
    assert before["note 4"]["anchor"] == {"layer": "s2", "dt": 3.5, "sig": "video:media/b.mp4", "t": 5.0}          # media time: 5.0 - 4 + src_in 2.5
    # the agent fixes note 2 by making scene 1 one second longer and shifting the later scenes (what AGENTS.md tells it to do)
    _edit(d, update=[{"id": "s1", "set": {"end": 5}}, {"id": "s2", "set": {"start": 5, "end": 9}}, {"id": "s3", "set": {"start": 9, "end": 13}},
                     {"id": "music", "set": {"end": 13}}])
    after = _notes_by_text(_raw(d))                                         # on disk, not only in a reader's view
    for i, t in enumerate(TEN):
        want = t if t < 4 else t + 1
        assert after[f"note {i}"]["t"] == pytest.approx(want, abs=1e-6), (i, after[f"note {i}"])
        assert not after[f"note {i}"]["anchor"].get("stale")
    loaded = _notes_by_text(schema.load_project(d))                         # every reader agrees
    assert [loaded[f"note {i}"]["t"] for i in range(10)] == [after[f"note {i}"]["t"] for i in range(10)]
    # the payload the agent gets points at the moved moment: note 4 is still on scene 2
    v = [x for x in agent_notes.list_views(d, "all") if x["text"] == "note 4"][0]
    assert v["t"] == pytest.approx(6.0) and [l["id"] for l in v["layers"]] == ["s2", "music"]


def test_a_hand_move_re_anchors_the_note(tmp_path):
    d = str(tmp_path / "p")
    _write(d, three_scenes())
    _add_ten(d)
    nid = _notes_by_text(_raw(d))["note 4"]["id"]
    mcp_server.tool_update_note({"project_dir": d, "id": nid, "t": 9.0}, None)       # moved by hand onto scene 3
    n = _notes_by_text(_raw(d))["note 4"]
    assert n["t"] == 9.0 and n["anchor"]["layer"] == "s3" and n["anchor"]["t"] == 9.0
    _edit(d, update=[{"id": "s3", "set": {"start": 8.5, "end": 12.5}}])
    assert _notes_by_text(_raw(d))["note 4"]["t"] == pytest.approx(9.5)            # it now follows scene 3, not scene 2


def test_deleting_the_anchored_layer_keeps_t_and_marks_the_anchor_stale(tmp_path):
    d = str(tmp_path / "p")
    _write(d, three_scenes())
    _add_ten(d)
    _edit(d, remove=["s2"])
    after = _notes_by_text(_raw(d))
    for i, t in enumerate(TEN):
        n = after[f"note {i}"]
        assert n["t"] == t, n                                                # nothing guessed
        assert bool(n["anchor"].get("stale")) == (4 <= t < 8), n
    assert not any("anchor" in p for p in schema.validate(_raw(d)))          # a stale anchor is a valid one
    # trimming the head of a clip (start and src_in move together, as the editor's trim does) so the anchored moment falls outside it: stale, t kept
    _edit(d, update=[{"id": "s3", "set": {"start": 10.5, "src_in": 2.5}}])
    after = _notes_by_text(_raw(d))
    assert after["note 7"]["t"] == 8.0 and after["note 7"]["anchor"]["stale"] is True
    assert after["note 9"]["t"] == 11.5 and not after["note 9"]["anchor"].get("stale")   # still on screen at the same moment
    # the layer comes back (undo outside the editor, the agent re-adds it): the very same anchor resolves again, nothing was guessed meanwhile
    _edit(d, add=[{"id": "s2", "type": "video", "src": "media/b.mp4", "start": 4, "end": 8, "src_in": 2.5}])
    assert not _notes_by_text(_raw(d))["note 4"]["anchor"].get("stale") and _notes_by_text(_raw(d))["note 4"]["t"] == 5.0


def test_an_old_project_without_anchors_is_unchanged(tmp_path):
    d = str(tmp_path / "p")
    p = three_scenes()
    p["notes"] = [{"id": f"n_{i}", "t": t, "text": f"old {i}", "created": "2026-10-01T10:00:00"} for i, t in enumerate(TEN)]
    _write(d, p)
    first = schema.load_project(d)["notes"]
    _edit(d, update=[{"id": "s1", "set": {"end": 5}}, {"id": "s2", "set": {"start": 5, "end": 9}}, {"id": "s3", "set": {"start": 9, "end": 13}}])
    raw = _raw(d)
    assert [n["t"] for n in raw["notes"]] == TEN and not any("anchor" in n for n in raw["notes"])
    assert schema.load_project(d)["notes"] == first
    assert schema.validate(raw) == []


def test_a_code_made_unit_anchors_by_unit_and_source_frame(tmp_path):
    """A unit re-pointed at its own file (src, src_in 0, media_start_frame) and made longer by a rerender keeps its notes on the same source frame;
    the later scene, shifted by the agent afterwards, takes its notes along."""
    import render_adaptors as RA
    d = str(tmp_path / "p")
    os.makedirs(os.path.join(d, "media"))
    film = {"kind": "cinecode", "fps": 24, "root": "film", "render": "cinecode-shot", "media_start_frame": 0}      # as `kurgu import` writes a shared film
    _write(d, {"version": 2, "width": 320, "height": 180, "fps": 24, "duration": 3, "notes": [], "layers": [
        {"id": "shot-a", "type": "video", "src": "media/film.mp4", "start": 0, "end": 1, "src_in": 0, "source": dict(film, unit="a", unit_start_frame=0, frames=24)},
        {"id": "shot-b", "type": "video", "src": "media/film.mp4", "start": 1, "end": 2, "src_in": 1, "source": dict(film, unit="b", unit_start_frame=24, frames=24)},
    ]})
    for t, txt in ((0.5, "on a"), (1.25, "on b"), (1.75, "late on b")):
        mcp_server.tool_add_note({"project_dir": d, "t": t, "text": txt}, None)
    n = _notes_by_text(_raw(d))
    assert n["on a"]["anchor"] == {"unit": "a", "frame": 12, "unit_start": 0, "t": 0.5}
    assert n["on b"]["anchor"] == {"unit": "b", "frame": 30, "unit_start": 24, "t": 1.25}
    raw = _raw(d)
    plan = {"unit": "a", "layers": [raw["layers"][0]], "frames": 36, "fps": 24, "start": 0, "retarget": True, "listed": True, "own": True,
            "media": os.path.join(d, "media", "film.a.mp4")}
    RA.apply_layer_updates(d, plan, {}, [])                                  # what a rerender writes: own file, src_in 0, longer end
    raw = _raw(d)
    assert raw["layers"][0]["src"] == "media/film.a.mp4" and raw["layers"][0]["end"] == 1.5
    assert _notes_by_text(raw)["on a"]["t"] == 0.5                          # same source frame, same moment
    _edit(d, update=[{"id": "shot-b", "set": {"start": 1.5, "end": 2.5}}])  # the agent shifts the later scene by +0.5 s
    n = _notes_by_text(_raw(d))
    assert n["on a"]["t"] == 0.5 and n["on b"]["t"] == pytest.approx(1.75) and n["late on b"]["t"] == pytest.approx(2.25)
    # b re-rendered later from a scene list that moved it to [36, 60): its own file starts at frame 36; the note stays on the same frame of b
    raw = _raw(d)
    plan = {"unit": "b", "layers": [raw["layers"][1]], "frames": 24, "fps": 24, "start": 36, "retarget": True, "listed": True, "own": True,
            "media": os.path.join(d, "media", "film.b.mp4")}
    RA.apply_layer_updates(d, plan, {}, [])
    n = _notes_by_text(_raw(d))
    assert n["on b"]["t"] == pytest.approx(1.75) and not n["on b"]["anchor"].get("stale")


def test_the_editor_sees_moved_notes_after_an_outside_write(tmp_path):
    """An agent that writes project.json itself (no server): every reader (GET /api/project, notes payload, MCP) derives the moved times."""
    d = str(tmp_path / "p")
    _write(d, three_scenes())
    _add_ten(d)
    raw = _raw(d)
    for l in raw["layers"]:
        if l["id"] in ("s2", "s3"):
            l["start"] += 1
            l["end"] += 1
    raw["layers"][1]["end"] = 5
    _write(d, raw)
    import server as kserver
    got = _notes_by_text(kserver.App(d).get_project()["project"])
    assert got["note 4"]["t"] == pytest.approx(6.0) and got["note 0"]["t"] == 0.5


def test_bad_anchors_are_dropped_and_reported():
    p = three_scenes()
    p["notes"] = [{"id": "a", "t": 1, "text": "x", "anchor": {"layer": 5}}, {"id": "b", "t": 2, "text": "y", "anchor": "s1"},
                  {"id": "c", "t": 3, "text": "z", "anchor": {"layer": "s1", "dt": 3}}]
    probs = schema.validate(p)
    assert sum("anchor" in x for x in probs) == 2
    n = schema.normalize(p)["notes"]
    assert "anchor" not in n[0] and "anchor" not in n[1] and n[2]["anchor"]["layer"] == "s1"


# ---------------------------------------------------------------- the two twins agree
def test_python_matches_shared_vectors():
    import note_anchor
    v = json.load(open(VECTORS, encoding="utf-8"))
    assert len(v["cases"]) >= 30
    for c in v["cases"]:
        if c["fn"] == "follow":
            p = copy.deepcopy(c["args"][0])
            note_anchor.follow(p)
            got = p["notes"]
        else:
            got = getattr(note_anchor, c["fn"])(*c["args"])
        assert got == c["out"], c


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_matches_shared_vectors(tmp_path):
    shutil.copy(os.path.join(ROOT, "web", "js", "anchor.js"), tmp_path / "anchor.mjs")
    r = subprocess.run(["node", os.path.join(HERE, "anchor_node_check.mjs"), str(tmp_path / "anchor.mjs"), VECTORS],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    res = json.loads(r.stdout)
    assert res["bad"] == [] and res["n"] >= 30


def test_vectors_are_generated_from_the_python_twin():
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_anchor_vectors.py"), "--check"], capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------- review fixes: splits, reused ids, what the user pointed at
def test_an_agent_split_keeps_the_note_on_the_second_half(tmp_path):
    """An agent splits scene 2 at 6 s the way the editor does (A ends at 6, B starts at 6 with src_in continuing A's media): the notes on the second
    half follow B when B moves, and are not stale."""
    d = str(tmp_path / "p")
    _write(d, three_scenes())
    _add_ten(d)
    _edit(d, update=[{"id": "s2", "set": {"end": 6}}],
          add=[{"id": "s2b", "type": "video", "src": "media/b.mp4", "start": 6, "end": 8, "src_in": 4.5}])
    n = _notes_by_text(_raw(d))
    for i in (5, 6):                                                         # 6.3 and 7.9 are on the second half
        assert n[f"note {i}"]["anchor"]["layer"] == "s2b" and not n[f"note {i}"]["anchor"].get("stale"), n[f"note {i}"]
    assert n["note 3"]["anchor"]["layer"] == "s2"                           # 4.1 stays on the first half
    _edit(d, update=[{"id": "s2b", "set": {"start": 7, "end": 9}}, {"id": "s3", "set": {"start": 9, "end": 13}}])
    n = _notes_by_text(_raw(d))
    assert n["note 5"]["t"] == pytest.approx(7.3) and n["note 6"]["t"] == pytest.approx(8.9) and n["note 3"]["t"] == 4.1


def test_the_same_file_elsewhere_is_not_a_split(tmp_path):
    """A trim past the note does not hand it to another use of the same file whose media range does not continue the anchored one."""
    d = str(tmp_path / "p")
    p = three_scenes()
    p["layers"][3]["src"] = "media/b.mp4"                                    # s3 also plays b.mp4, from 0 s: it covers media 3.5 s too
    _write(d, p)
    _add_ten(d)
    _edit(d, update=[{"id": "s2", "set": {"end": 4.5}}])                    # trimmed past note 4 (5.0 s, media 3.5 s)
    n = _notes_by_text(_raw(d))["note 4"]
    assert n["t"] == 5.0 and n["anchor"]["layer"] == "s2" and n["anchor"]["stale"] is True


def test_a_reused_layer_id_of_another_type_does_not_capture_a_stale_note(tmp_path):
    """Agents reuse ids ("title", "v1"): a layer of another TYPE under the old id must not adopt the note (a layer anchor carries a signature:
    type + src for media, the type alone for text and colour)."""
    d = str(tmp_path / "p")
    _write(d, three_scenes())
    _add_ten(d)
    assert _notes_by_text(_raw(d))["note 4"]["anchor"]["sig"] == "video:media/b.mp4"
    _edit(d, remove=["s2"])
    _edit(d, add=[{"id": "s2", "type": "text", "text": "a caption", "start": 3, "end": 9}])
    n = _notes_by_text(_raw(d))["note 4"]
    assert n["t"] == 5.0 and n["anchor"]["stale"] is True                   # would be 6.5 (3 + 3.5) if the caption were adopted
    _edit(d, remove=["s2"])
    _edit(d, add=[{"id": "s2", "type": "video", "src": "media/b.mp4", "start": 5, "end": 9, "src_in": 2.5}])   # the clip again: heals, follows
    n = _notes_by_text(_raw(d))["note 4"]
    assert n["t"] == 6.0 and not n["anchor"].get("stale")


def test_ordinary_edits_of_the_anchored_layer_keep_the_note_following(tmp_path):
    """Fixing the typo the note is about, recolouring a slate, relinking the clip to another file: the note still follows that layer."""
    d = str(tmp_path / "p")
    p = three_scenes()
    p["layers"][0].update(x=640, y=360, size=120)
    p["layers"].insert(0, {"id": "slate", "type": "color", "color": "#000000", "start": 12, "end": 13})
    _write(d, p)
    agent_notes.add_user_note(d, 1.0, "typo here", "540,300,200,120", mcp_server._write_notes)
    agent_notes.add_user_note(d, 12.5, "slate too dark", None, mcp_server._write_notes)
    mcp_server.tool_add_note({"project_dir": d, "t": 5.0, "text": "on two"}, None)
    n = _notes_by_text(_raw(d))
    assert n["typo here"]["anchor"]["sig"] == "text" and n["slate too dark"]["anchor"]["sig"] == "color"
    _edit(d, update=[{"id": "title", "set": {"text": "HELLO, fixed"}}, {"id": "slate", "set": {"color": "#333333"}},
                     {"id": "s2", "set": {"src": "media/b-graded.mp4"}}])
    _edit(d, update=[{"id": "title", "set": {"start": 0.5, "end": 2.5}}, {"id": "slate", "set": {"start": 12.5, "end": 13.5}},
                     {"id": "s2", "set": {"start": 5, "end": 9}}])
    n = _notes_by_text(_raw(d))
    assert n["typo here"]["t"] == 1.5 and not n["typo here"]["anchor"].get("stale")
    assert n["slate too dark"]["t"] == 13.0 and not n["slate too dark"]["anchor"].get("stale")
    assert n["on two"]["t"] == 6.0 and n["on two"]["anchor"]["sig"] == "video:media/b-graded.mp4"   # the signature follows a deliberate relink


def test_a_note_with_a_mark_anchors_to_the_layer_under_the_mark(tmp_path):
    """What the user pointed at wins: a rectangle drawn on the title anchors the note to the title, not to the scene behind it."""
    d = str(tmp_path / "p")
    p = three_scenes()
    p["layers"][0].update(x=640, y=360, size=120)
    _write(d, p)
    agent_notes.add_user_note(d, 1.0, "title too big", "540,300,200,120", mcp_server._write_notes)
    agent_notes.add_user_note(d, 1.5, "scene too dark", None, mcp_server._write_notes)
    n = _notes_by_text(_raw(d))
    assert n["title too big"]["anchor"]["layer"] == "title" and n["title too big"]["anchor"]["sig"] == "text"
    assert n["scene too dark"]["anchor"]["layer"] == "s1"
    _edit(d, update=[{"id": "title", "set": {"start": 0.5, "end": 2.5}}])
    n = _notes_by_text(_raw(d))
    assert n["title too big"]["t"] == 1.5 and n["scene too dark"]["t"] == 1.5
