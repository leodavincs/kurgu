"""kurgu strip (one contact-sheet JPEG of n frames), MCP render_strip, and layer keys in note payloads / NOTLAR.md."""
import base64
import json
import os

import pytest
from PIL import Image

import agent_notes
import mcp_server
from test_agent_notes import launch, seed, cli
from test_mcp import mcp, editor, Mcp  # noqa: F401  (fixtures)


def size(path):
    with Image.open(path) as im:
        return im.size


def look_dir(d):
    import pathlib
    p = pathlib.Path(d) / ".kurgu" / "look"
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_strip(d, *args):
    r = launch("strip", d, *args, timeout=180)
    return r, (r.stdout.strip().splitlines() or [""])[0]


@pytest.mark.parametrize("n,cols,rows", [(2, 2, 1), (6, 3, 2), (12, 3, 4)])
def test_strip_grid_size(project_dir, tmp_path, n, cols, rows):
    out = str(look_dir(project_dir) / f"s{n}.jpg")
    r, path = run_strip(project_dir, "0.5", "4.5", "--n", str(n), "-o", out)
    assert r.returncode == 0, r.stderr
    assert os.path.samefile(path, out) and open(out, "rb").read(3) == b"\xff\xd8\xff"
    tw = 640
    w, h = size(out)
    assert w == cols * tw + (cols - 1) * 4 and w <= 2000
    th = (h - (rows - 1) * 4) // rows
    assert h == rows * th + (rows - 1) * 4 and abs(th - tw * 9 / 16) <= 1
    assert f"{cols}x{rows} tiles of {tw}x{th} px" in r.stderr and "project px" in r.stderr


def test_strip_default_path_and_distinct_timestamps(project_dir):
    r, path = run_strip(project_dir, "1", "4")
    assert r.returncode == 0, r.stderr
    assert path.replace("\\", "/").endswith(".kurgu/look/strip-1-4.jpg") and os.path.exists(path)
    assert "1.00, 1.60, 2.20, 2.80, 3.40, 4.00" in r.stderr                  # evenly spaced, inclusive, all different
    jpg, info = mcp_server.render_strip(project_dir, 1, 4, 6, draft=True)
    assert len(set(info["times"])) == 6 and info["times"][0] == 1 and info["times"][-1] == 4
    # tiles differ (the title fades in/out over this stretch): the first and the third tile are not the same picture
    import io
    im = Image.open(io.BytesIO(jpg)).convert("RGB")
    tw, th = info["tile_w"], info["tile_h"]
    a, b = im.crop((0, 0, tw, th)), im.crop((2 * (tw + 4), 0, 2 * (tw + 4) + tw, th))
    assert a.tobytes() != b.tobytes()


def test_strip_full_uses_project_size_tiles(project_dir, tmp_path):
    out = str(look_dir(project_dir) / "full.jpg")
    r, _ = run_strip(project_dir, "1", "2", "--n", "2", "--full", "-o", out)
    assert r.returncode == 0, r.stderr
    assert size(out) == (2 * 640 + 4, 360)


@pytest.mark.parametrize("args", [("3", "1"), ("2", "2"), ("1", "3", "--n", "1"), ("1", "3", "--n", "13"), ("-1", "2")])
def test_strip_rejects_bad_args(project_dir, tmp_path, args):
    out = str(look_dir(project_dir) / "bad.jpg")
    r = launch("strip", project_dir, *args, "-o", out)
    assert r.returncode != 0 and not os.path.exists(out)
    assert r.stderr.strip()


def test_strip_with_editor_running(project_dir, editor, tmp_path):   # noqa: F811
    assert mcp_server.editor_port(project_dir)                       # the draft path goes through the running server
    out = str(look_dir(project_dir) / "up.jpg")
    r, _ = run_strip(project_dir, "1", "3", "--n", "3", "-o", out)
    assert r.returncode == 0, r.stderr
    assert size(out) == (3 * 640 + 8, 360)


def test_strip_without_editor(project_dir, tmp_path):
    assert mcp_server.editor_port(project_dir) is None
    out = str(look_dir(project_dir) / "down.jpg")
    r, _ = run_strip(project_dir, "1", "3", "--n", "3", "-o", out)
    assert r.returncode == 0, r.stderr and size(out) == (3 * 640 + 8, 360)


def test_strip_missing_project(tmp_path):
    r = launch("strip", str(tmp_path), "0", "1")
    assert r.returncode == 1 and "No project.json" in r.stderr


def test_mcp_render_strip(mcp):   # noqa: F811
    content = mcp.ok("render_strip", t0=1, t1=4, n=4)
    img = next(c for c in content if c["type"] == "image")
    assert img["mimeType"] == "image/jpeg" and base64.b64decode(img["data"])[:3] == b"\xff\xd8\xff"
    assert "2x2 tiles" in next(c["text"] for c in content if c["type"] == "text")
    assert mcp.tool("render_strip", t0=3, t1=1)["isError"]
    assert mcp.tool("render_strip", t0=0, t1=1, n=13)["isError"]
    assert mcp.tool("render_strip", t0=0, t1=1, n=1)["isError"]


# ------------------------------------------------------------------ layer keys in the payload
def seed_keys(d, n_keys=3):
    p = seed(d)
    title = next(l for l in p["layers"] if l["id"] == "k_buyuk")           # start 1, end 4.5
    title["keys"] = {"scale": [{"t": 0.3 * i, "v": 0.5 + 0.1 * i, "ease": "out-back"} for i in range(n_keys)],
                     "x": [{"t": 0, "v": 100.123456}, {"t": 1, "v": 300}]}
    json.dump(p, open(os.path.join(d, "project.json"), "w", encoding="utf-8"))
    return title


def view_of(d, nid="n_a"):
    proj, notes = agent_notes.load_notes(d)
    return agent_notes.note_view(d, next(n for n in notes if n["id"] == nid), proj)


def test_payload_layers_carry_start_end_keys_in_project_seconds(project_dir):
    seed_keys(project_dir)
    lay = next(l for l in view_of(project_dir)["layers"] if l["id"] == "k_buyuk")
    assert lay["start"] == 1 and lay["end"] == 4.5
    assert lay["keys"][0] == {"t": 1.0, "scale": 0.5, "x": 100.123}          # layer-local 0 -> project 1 s, rounded to 3
    assert lay["keys"][1] == {"t": 1.3, "scale": 0.6} and lay["keys"][2]["t"] == 1.6 and lay["keys"][3] == {"t": 2.0, "x": 300}
    assert "keys_note" not in lay
    plain = next(l for l in view_of(project_dir)["layers"] if l["id"] != "k_buyuk")
    assert "keys" not in plain and "start" in plain                          # un-animated layers: timing only, no keys


def test_payload_keys_are_capped_with_a_note(project_dir):
    seed_keys(project_dir, n_keys=30)
    lay = next(l for l in view_of(project_dir)["layers"] if l["id"] == "k_buyuk")
    assert len(lay["keys"]) == agent_notes.MAX_KEYS == 20
    assert "first 20 of 31" in lay["keys_note"]


def test_notlar_md_and_cli_list_show_keys(project_dir):
    seed_keys(project_dir)
    md = open(agent_notes.write_notes_md(project_dir), encoding="utf-8").read()
    line = next(l for l in md.splitlines() if "keys:" in l)
    assert "Big title keys: scale 0.5@1s→0.6@1.3s→0.7@1.6s" in line and "x 100.123@1s→300@2s" in line
    r = cli("list", project_dir, "--all")
    assert r.returncode == 0 and "keys Big title: scale 0.5@1s" in r.stdout
    js = json.loads(cli("list", project_dir, "--all", "--json").stdout)
    assert any(l.get("keys") for n in (js if isinstance(js, list) else js.get("notes", [])) for l in n["layers"])
    seed_keys(project_dir, n_keys=30)
    md = open(agent_notes.write_notes_md(project_dir), encoding="utf-8").read()
    assert "first 20 of 31" in md


def test_mark_layer_bounds_carry_keys(project_dir):
    seed_keys(project_dir)
    proj, notes = agent_notes.load_notes(project_dir)
    n = next(n for n in notes if n["id"] == "n_a")
    mk = agent_notes.mark_view({"type": "ellipse", "x": 800, "y": 400, "w": 300, "h": 200, "layers": [{"id": "k_buyuk", "name": "Big title"}]}, 1920, 1080, proj, n["t"], n, project_dir)
    lb = next(x for x in mk["layer_bounds"] if x["id"] == "k_buyuk")
    assert lb["start"] == 1 and lb["keys"][0]["t"] == 1.0


def test_launcher_dispatches_strip():
    raw = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kurgu.cmd"), "rb").read().decode()
    assert 'kurgu_cli.py" %*' in raw


def test_output_is_confined_to_the_look_folder(project_dir, tmp_path):
    outside = str(tmp_path / "x.jpg")
    r = launch("frame", project_dir, "1", "-o", outside)
    assert r.returncode == 1 and "look" in r.stderr and not os.path.exists(outside)
    r = launch("strip", project_dir, "0", "2", "-o", str(tmp_path / "x.jpg"))
    assert r.returncode == 1 and not os.path.exists(outside)
    r = launch("frame", project_dir, "1", "-o", os.path.join(project_dir, ".kurgu", "look", "..", "..", "evil.jpg"))
    assert r.returncode == 1 and not os.path.exists(os.path.join(project_dir, "evil.jpg"))
    r = launch("frame", project_dir, "1", "-o", "plain.jpg")                         # a bare name lands in the look folder
    assert r.returncode == 0 and os.path.exists(os.path.join(project_dir, ".kurgu", "look", "plain.jpg"))
    r = launch("frame", project_dir, "1", "-o", "notes.txt")
    assert r.returncode == 1


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinks_and_non_projects_are_refused(project_dir, tmp_path):
    victim = tmp_path / "victim.jpg"
    victim.write_bytes(b"keep")
    look = os.path.join(project_dir, ".kurgu", "look")
    os.makedirs(look, exist_ok=True)
    os.symlink(str(victim), os.path.join(look, "link.jpg"))
    r = launch("frame", project_dir, "1", "-o", "link.jpg")
    assert r.returncode == 1 and victim.read_bytes() == b"keep"
    other = tmp_path / "elsewhere"
    other.mkdir()
    shutil_move = __import__("shutil").move
    shutil_move(os.path.join(project_dir, ".kurgu"), str(other / "k"))
    os.symlink(str(other / "k"), os.path.join(project_dir, ".kurgu"))
    r = launch("frame", project_dir, "1")
    assert r.returncode == 1 and "symlink" in r.stderr
    (tmp_path / "notproj").mkdir()
    (tmp_path / "notproj" / "project.json").write_text("[1,2]")
    for cmd in (["frame", str(tmp_path / "notproj"), "1"], ["strip", str(tmp_path / "notproj"), "0", "1"]):
        assert launch(*cmd).returncode == 1


def test_times_past_the_end_are_clamped_with_a_warning(project_dir):
    dur = json.load(open(os.path.join(project_dir, "project.json")))["duration"]
    r = launch("frame", project_dir, str(dur + 50))
    assert r.returncode == 0 and "past the end" in r.stderr
    r = launch("strip", project_dir, "0", str(dur + 50), "--n", "2")
    assert r.returncode == 0 and "past the end" in r.stderr
