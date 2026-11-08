"""Code-made clips: layer `source` (schema), element-map sidecar (source_map), and the note payload / NOTLAR.md / `notes list` lines."""
import json
import os
import shutil
import subprocess
import sys

import pytest

import agent_notes
import schema
import source_map
from test_agent_notes import ROOT, seed

SRC = {"kind": "cinecode", "root": "film", "unit": "S03", "file": "engine/scene-s03.js", "fps": 24, "unit_start_frame": 144,
       "render": "cinecode-shot", "elements": "renders/S03.kurgu.json", "extra": {"keep": 1}}


def sidecar(boxes=None, fps=24, size=(1920, 1080)):
    return {"fps": fps, "size": list(size), "elements": [
        {"id": "baslik", "label": "Fena değil", "kind": "text", "file": "engine/scene-s03.js", "line": 41,
         "boxes": boxes or [{"f": 144, "l": 116, "t": 160, "r": 640, "b": 250}, {"f": 168, "l": 316, "t": 260, "r": 840, "b": 350}]},
        {"id": "logo", "label": "Logo", "kind": "shape", "file": "engine/scene-s03.js", "line": 77,
         "boxes": [{"f": 144, "l": 1700, "t": 20, "r": 1900, "b": 120}]}]}


def layer(**kw):
    l = {"id": "v", "type": "video", "src": "media/shot.mp4", "start": 2.0, "end": 8.0, "src_in": 1.0, "fit": "cover", "source": dict(SRC)}
    l.update(kw)
    return l


@pytest.fixture()
def cdir(project_dir):
    os.makedirs(os.path.join(project_dir, "renders"), exist_ok=True)
    json.dump(sidecar(), open(os.path.join(project_dir, "renders", "S03.kurgu.json"), "w"))
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=1920x1080:r=24:d=3", "-pix_fmt", "yuv420p",
                        os.path.join(project_dir, "media", "shot.mp4")], check=True)
    return project_dir


# ---------------------------------------------------------------- schema
def test_source_roundtrips_and_keeps_unknown_keys():
    n = schema.normalize_layer(layer())
    assert n["source"] == {**SRC, "fps": 24, "unit_start_frame": 144}
    assert schema.normalize_layer(n)["source"] == n["source"]
    assert "source" not in schema.normalize_layer({k: v for k, v in layer().items() if k != "source"})
    assert "source" not in schema.normalize_layer(layer(source="nope"))


def _probs(src):
    p = schema.normalize({"version": 2, "width": 1920, "height": 1080, "duration": 10, "layers": [layer(source=src)]})
    return [x for x in schema.validate(p) if "source" in x]


def test_source_validation():
    assert _probs(dict(SRC)) == []
    assert any("kind" in x for x in _probs({**SRC, "kind": "bash"}))
    assert any("relative" in x for x in _probs({**SRC, "root": "/abs/path"}))
    assert any("never a command" in x for x in _probs({**SRC, "render": "rm -rf /"}))
    assert any("fps" in x for x in _probs({**SRC, "fps": 0}))
    assert any("unit_start_frame" in x for x in _probs({**SRC, "unit_start_frame": -3}))


# ---------------------------------------------------------------- source_map
def test_time_to_source_frame_uses_start_src_in_and_fps():
    l = schema.normalize_layer(layer())
    assert source_map.source_frame(l, 2.0) == 144 + 1.0 * 24            # clip start -> src_in seconds into the unit
    assert source_map.source_frame(l, 3.0) == 144 + 2.0 * 24
    assert source_map.source_frame(layer(source={"kind": "html"}), 3.0) is None


def test_boxes_nearest_interpolated_and_mapped_to_project_px(cdir):
    W, H = 1920, 1080
    l = schema.normalize_layer(layer())
    r = source_map.elements_at(l, 2.0, W, H, cdir)       # frame 168: second sample of "baslik"
    by = {e["id"]: e for e in r["elements"]}
    assert by["baslik"]["box"] == [316.0, 260.0, 840.0, 350.0] and by["baslik"]["line"] == 41
    r = source_map.elements_at(l, 2.0 - 0.5, W, H, cdir)  # frame 156: halfway between the samples
    assert {e["id"]: e for e in r["elements"]}["baslik"]["box"] == [216.0, 210.0, 740.0, 300.0]
    assert source_map.elements_at(l, 7.0, W, H, cdir)["elements"] == []     # far past the last sample: nothing on screen
    # layer scaled 0.5 around the centre, 1920x1080 contain -> source px halve and shift by (480, 270)
    half = schema.normalize_layer(layer(fit="contain", scale=0.5))
    e = {e["id"]: e for e in source_map.elements_at(half, 1.5, W, H, cdir)["elements"]}["baslik"]
    assert [round(v) for v in e["box"]] == [480 + 108, 270 + 105, 480 + 370, 270 + 150]
    # cover on a vertical frame: the 16:9 clip is wider than the frame, boxes keep their fraction of the clip
    e2 = source_map.elements_at(schema.normalize_layer(layer(x=540, y=960)), 1.5, 1080, 1920, cdir)
    nat_w = 1920 * 1920 / 1080
    assert abs({e["id"]: e for e in e2["elements"]}["baslik"]["box"][0] - (540 - nat_w / 2 + 216 / 1920 * nat_w)) < 1


def test_sidecar_fps_conversion_and_crop(cdir):
    json.dump(sidecar(fps=48, boxes=[{"f": 288, "l": 0, "t": 0, "r": 960, "b": 540}]), open(os.path.join(cdir, "renders", "S03.kurgu.json"), "w"))
    l = schema.normalize_layer(layer(crop={"left": 0.25}))
    r = source_map.elements_at(l, 1.0, 1920, 1080, cdir)       # source frame 144 @24 = 288 @48
    b = {e["id"]: e for e in r["elements"]}["baslik"]["box"]
    assert [round(v) for v in b] == [-640, -180, 640, 540]            # crop.left 0.25: cover scales the 1440x1080 remainder to 1920x1440


def test_missing_and_corrupt_sidecars_degrade_to_error(cdir):
    l = schema.normalize_layer(layer(source={**SRC, "elements": "renders/nope.json"}))
    assert "not found" in source_map.elements_at(l, 3.0, 1920, 1080, cdir)["error"]
    p = os.path.join(cdir, "renders", "bad.json")
    open(p, "w").write("{oops")
    l = schema.normalize_layer(layer(source={**SRC, "elements": "renders/bad.json"}))
    r = source_map.elements_at(l, 3.0, 1920, 1080, cdir)
    assert "unreadable" in r["error"] and r["elements"] == []
    open(p, "w").write(json.dumps({"fps": 24, "elements": []}))
    assert "size" in source_map.elements_at(l, 3.0, 1920, 1080, cdir)["error"]
    assert "error" in source_map.elements_at(l, 3.0, 1920, 1080, None)


def test_a_rewrite_that_keeps_the_mtime_is_still_seen(cdir):
    """Windows (and coarse file systems) can give two writes in one clock tick the same mtime; the cache key carries the size too."""
    p = os.path.join(cdir, "renders", "same.json")
    with open(p, "w") as f:
        f.write("{oops")
    l = schema.normalize_layer(layer(source={**SRC, "elements": "renders/same.json"}))
    assert "unreadable" in source_map.elements_at(l, 3.0, 1920, 1080, cdir)["error"]
    ns = os.stat(p).st_mtime_ns
    with open(p, "w") as f:
        json.dump({"fps": 24, "size": [1920, 1080], "elements": []}, f)
    os.utime(p, ns=(ns, ns))                                                   # the same tick as the broken write
    r = source_map.elements_at(l, 3.0, 1920, 1080, cdir)
    assert "error" not in r and r["elements"] == []


def test_cache_follows_mtime(cdir):
    p = os.path.join(cdir, "renders", "S03.kurgu.json")
    l = schema.normalize_layer(layer())
    assert len(source_map.elements_at(l, 1.0, 1920, 1080, cdir)["elements"]) == 2
    json.dump({"fps": 24, "size": [1920, 1080], "elements": []}, open(p, "w"))
    os.utime(p, (1, 1))
    assert source_map.elements_at(l, 1.0, 1920, 1080, cdir)["elements"] == []


# ---------------------------------------------------------------- note payload
def project_with_source(d):
    p = json.load(open(os.path.join(d, "project.json"), encoding="utf-8"))
    p["layers"] = [layer(start=0.0, end=9.0, src_in=0.0, source={**SRC, "unit_start_frame": 144})] + [l for l in p["layers"] if l.get("type") != "video"]
    p["duration"] = max(p.get("duration", 0), 9)
    json.dump(p, open(os.path.join(d, "project.json"), "w"))
    return p


def test_payload_notlar_and_cli_list_carry_source_and_elements(cdir):
    project_with_source(cdir)
    mark = {"type": "ellipse", "x": 288, "y": 216, "w": 384, "h": 108, "nx": 0.15, "ny": 0.2, "nw": 0.2, "nh": 0.1, "color": "#ff0000"}      # px 288,216 size 384x108
    seed(cdir, [{"id": "n_s", "t": 1.0, "text": "bu yazıyı sağa al", "created": "2026-11-07T10:00:00", "marks": [mark],
                 "sent": "2026-11-07T10:05:00"}])
    proj = schema.normalize(json.load(open(os.path.join(cdir, "project.json"), encoding="utf-8")))
    v = agent_notes.note_view(cdir, proj["notes"][0], proj)
    lay = next(l for l in v["layers"] if l["id"] == "v")
    assert lay["source"]["frame"] == 168 and lay["source"]["time_in_unit"] == 1.0 and lay["source"]["render"] == "cinecode-shot"
    assert lay["source"]["file"] == "engine/scene-s03.js" and lay["source"]["kind"] == "cinecode"
    eu = v["marks"][0]["elements_under"]
    assert [e["id"] for e in eu] == ["baslik"] and eu[0]["file"] == "engine/scene-s03.js" and eu[0]["line"] == 41
    md = open(agent_notes.write_notes_md(cdir), encoding="utf-8").read()
    assert "- source: cinecode S03 frame 168 (engine/scene-s03.js), render: cinecode-shot" in md
    assert '  - under mark: "Fena değil" text [316,260→840,350] engine/scene-s03.js:41' in md
    r = subprocess.run([sys.executable, os.path.join(ROOT, "server.py"), "notes", "list", cdir], capture_output=True, text=True, encoding="utf-8")
    assert "source Video: cinecode S03 frame 168" in r.stdout and 'under mark: "Fena değil" text [316,260' in r.stdout
    j = subprocess.run([sys.executable, os.path.join(ROOT, "server.py"), "notes", "list", cdir, "--json"], capture_output=True, text=True, encoding="utf-8")
    assert json.loads(j.stdout)["notes"][0]["marks"][0]["elements_under"][0]["id"] == "baslik"


def test_layers_without_source_have_an_unchanged_payload(project_dir):
    seed(project_dir)
    proj = schema.normalize(json.load(open(os.path.join(project_dir, "project.json"), encoding="utf-8")))
    v = agent_notes.note_view(project_dir, proj["notes"][0], proj)
    assert all("source" not in l for l in v["layers"])
    assert "source:" not in open(agent_notes.write_notes_md(project_dir), encoding="utf-8").read()


def test_broken_sidecar_shows_error_line_not_crash(cdir):
    project_with_source(cdir)
    os.remove(os.path.join(cdir, "renders", "S03.kurgu.json"))
    seed(cdir, [{"id": "n_s", "t": 1.0, "text": "x", "created": "2026-11-07T10:00:00"}])
    md = open(agent_notes.write_notes_md(cdir), encoding="utf-8").read()
    assert "- source: cinecode S03" in md and "not found" in md


def test_guidance_mentions_source():
    import kurgu_cli
    assert "source" in kurgu_cli.WAKE_PROMPT and "elements_under" in kurgu_cli.WAKE_PROMPT and len(kurgu_cli.WAKE_PROMPT) < 1400
    for t in (kurgu_cli.AGENT_BLOCK, open(os.path.join(ROOT, "skills", "kurgu", "SKILL.md"), encoding="utf-8").read(),
              open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8").read()):
        assert "elements_under" in t and "re-render" in t


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_element_map_outside_the_project_is_not_read(cdir, tmp_path):
    outside = tmp_path / "evil.kurgu.json"
    outside.write_text(json.dumps(sidecar()))
    lay = layer(source=dict(SRC, root="", elements=os.path.relpath(str(outside), cdir)))
    r = source_map.elements_at(lay, 2.0, 1920, 1080, cdir)
    assert r["elements"] == [] and "outside" in r["error"]
    lay["source"]["elements"] = "renders/S03.kurgu.json"                       # inside the project: read
    assert source_map.elements_at(lay, 2.0, 1920, 1080, cdir)["elements"]
    link = os.path.join(cdir, "renders", "link.kurgu.json")
    os.symlink(str(outside), link)
    lay["source"]["elements"] = "renders/link.kurgu.json"                      # a symlink pointing out is refused too
    assert source_map.elements_at(lay, 2.0, 1920, 1080, cdir)["elements"] == []
