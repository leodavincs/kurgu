"""Linked clips (SPEC 1.1a): schema, and the render proof that a separated pair sounds exactly like the legacy single layer."""
import json
import os
import subprocess
import sys
import wave

import numpy as np

import schema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_normalize_keeps_a_link_and_drops_an_empty_one():
    p = schema.normalize({"layers": [{"id": "a", "type": "audio", "src": "x.wav", "start": 0, "end": 1, "link": "lk_1"},
                                     {"id": "b", "type": "video", "src": "x.mp4", "start": 0, "end": 1, "link": ""},
                                     {"id": "c", "type": "video", "src": "x.mp4", "start": 0, "end": 1, "link": 5}]})
    ls = {l["id"]: l for l in p["layers"]}
    assert ls["a"]["link"] == "lk_1" and "link" not in ls["b"] and "link" not in ls["c"]
    assert schema.normalize(json.loads(json.dumps(p))) == p            # idempotent


def test_validate_reports_a_bad_link():
    probs = schema.validate({"duration": 5, "layers": [{"id": "a", "type": "text", "start": 0, "end": 1, "link": 7}]})
    assert any("link must be a non-empty string" in x for x in probs)
    assert not any("link" in x for x in schema.validate({"duration": 5, "layers": [{"id": "a", "type": "text", "start": 0, "end": 1, "link": "lk"}]}))


def test_link_offsets_use_source_alignment():
    v = {"id": "v", "type": "video", "src": "a.mp4", "start": 2, "end": 6, "src_in": 0, "link": "k", "muted": True}
    a = {"id": "a", "type": "audio", "src": "a.mp4", "start": 2, "end": 6, "src_in": 0, "link": "k"}
    P = {"fps": 30, "layers": [v, a]}
    assert schema.link_offsets(P) == []
    a["start"], a["src_in"] = 3, 1                                       # trimmed 1 s at the head: still aligned to the picture
    assert schema.link_offsets(P) == []
    a["start"] = 2.4
    assert schema.link_offsets(P) == [("k", "a", -0.6)]
    a["link"] = None
    assert schema.link_groups(P) == {}


def _wav(path):
    with wave.open(path) as w:
        return w.getframerate(), w.readframes(w.getnframes())


def _render_audio(pdir, layers, out):
    proj = {"version": 2, "name": "t", "width": 320, "height": 180, "fps": 30, "duration": 5, "background": "#000000",
            "global": {"loudness_lufs": None}, "layers": layers}
    with open(os.path.join(pdir, "project.json"), "w", encoding="utf-8") as f:
        json.dump(schema.normalize(proj), f)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "render.py"), pdir, "--codec", "wav", "--output", out], capture_output=True, text=True, timeout=110)
    assert r.returncode == 0, r.stdout + r.stderr
    return out


def test_separated_pair_renders_sample_identical_audio_to_the_legacy_single_layer(project_dir, tmp_path):
    common = {"src": "media/a.mp4", "src_in": 0.5, "start": 1, "end": 4.5, "fade_in": 0.4, "fade_out": 0.3, "hidden": False, "locked": False}
    sound = {"volume_db": -4, "envelope": [[1.5, 0], [2.5, -6], [3.5, -2]]}
    single = dict(common, id="v", type="video", name="clip", group="Picture", fit="cover", muted=False, **sound)
    pv = dict(common, id="v", type="video", name="clip", group="Picture", fit="cover", muted=True, volume_db=0, envelope=[], link="lk_1")
    pa = dict(common, id="v_a", type="audio", name="clip (audio)", group="Sound", muted=False, link="lk_1", **sound)
    a = _render_audio(project_dir, [single], str(tmp_path / "single.wav"))
    b = _render_audio(project_dir, [pv, pa], str(tmp_path / "pair.wav"))
    (ra, da), (rb, db) = _wav(a), _wav(b)
    assert ra == rb and len(da) == len(db) > 0
    xa, xb = np.frombuffer(da, np.int16), np.frombuffer(db, np.int16)
    assert np.abs(xa.astype(np.int32)).max() > 100                        # not silence
    assert np.array_equal(xa, xb)                                        # sample-identical
    # and `link` itself changes nothing: same pair without link ids
    c = _render_audio(project_dir, [dict(pv, link=None), dict(pa, link=None)], str(tmp_path / "nolink.wav"))
    assert _wav(c)[1] == db
