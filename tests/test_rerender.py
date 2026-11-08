"""`kurgu rerender`: adaptors come from Kurgu or the USER's config, never from project.json; atomic replace; html-frames end to end (headless Chrome)."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

import pytest

import kurgu_cli
import render_adaptors as RA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
NEED = pytest.mark.skipif(not shutil.which("ffmpeg") or not RA.find_chrome(), reason="needs ffmpeg and Chrome")

PAGE = """<!doctype html><meta charset=utf-8><body style="margin:0"><canvas id=c width=320 height=180></canvas><script>
const COLOR = '%s';
const cv = document.getElementById('c'), g = cv.getContext('2d');
window.renderFrame = (i) => { g.fillStyle = COLOR; g.fillRect(0, 0, 320, 180); g.fillStyle = '#fff'; g.fillRect(i * 10, 0, 8, 180); };
%s
window.READY = Promise.resolve({frames: 48, fps: 24, width: 320, height: 180});
</script>"""
CAPTURE = "window.captureFrame = (i) => { window.renderFrame(i); return cv.toDataURL('image/png'); };"


def sh(*a):
    subprocess.run(list(a), check=True, stdin=subprocess.DEVNULL, capture_output=True)


_n = [0]


def mkproj(tmp_path, source=None, layer_extra=None, audio=True, make_media=True):
    _n[0] += 1
    d = tmp_path / f"p{_n[0]}"
    (d / "gen").mkdir(parents=True)
    (d / "media").mkdir()
    (d / "gen" / "page.html").write_text(PAGE % ("#ff0000", ""))
    if make_media:
        a = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"] if audio else []
        sh("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=1", *a, "-pix_fmt", "yuv420p",
           *(["-c:a", "aac", "-shortest"] if audio else []), str(d / "media" / "shot.mp4"))
    src = {"kind": "html", "root": "gen", "unit": "S01", "fps": 24, "unit_start_frame": 0, "render": "html-frames", "page": "page.html"}
    src.update(source or {})
    layer = {"id": "v", "type": "video", "src": "media/shot.mp4", "start": 0, "end": 1, "source": src}
    layer.update(layer_extra or {})
    (d / "project.json").write_text(json.dumps({"version": 2, "width": 320, "height": 180, "fps": 24, "duration": 1, "layers": [layer]}))
    return str(d)


def color_at(path, t=0.0):
    from PIL import Image
    import io
    png = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"],
                         capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGB").getpixel((315, 170))


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    c = tmp_path / "userconf"
    c.mkdir()
    monkeypatch.setenv("KURGU_CONFIG", str(c))
    for k in [k for k in os.environ if k.startswith("KURGU_RENDER_")]:
        monkeypatch.delenv(k)
    return c


def refuse(d, ident="S01", match=""):
    with pytest.raises(RA.RerenderError, match=match):
        RA.rerender(d, ident, dry_run=True)


# ---------------------------------------------------------------- security
def test_project_supplied_command_is_never_an_adaptor(tmp_path, cfg):
    d = mkproj(tmp_path, {"render": "touch pwned"})
    refuse(d, match="never a command")
    d = mkproj(tmp_path, {"render": "rm -rf /;x"})
    refuse(d, match="never a command")
    d = mkproj(tmp_path, {"render": "bash"})
    refuse(d, match="unknown render adaptor")
    # a project-folder config does not define adaptors either
    os.makedirs(os.path.join(d, ".kurgu"), exist_ok=True)
    json.dump({"render_adaptors": {"bash": ["touch", "pwned"]}}, open(os.path.join(d, ".kurgu", "config.json"), "w"))
    json.dump({"render_adaptors": {"bash": ["touch", "pwned"]}}, open(os.path.join(d, "config.json"), "w"))
    refuse(d, match="unknown render adaptor")
    assert not os.path.exists(os.path.join(d, "pwned"))


def test_unit_root_and_path_injection_are_rejected(tmp_path, cfg):
    for bad in ("x; rm -rf", "a b", "$(id)", "-rf", "x\nrm", "../up", ""):
        refuse(mkproj(tmp_path, {"unit": bad, "render": "html-frames"}), ident="v", match="source.unit")
    for root in ("../..", "../", "/etc", "~/x", "C:\\x"):
        refuse(mkproj(tmp_path, {"root": root}), ident="v", match="source.root")
    refuse(mkproj(tmp_path, {"page": "../../etc/passwd.html"}), match="leaves source.root")
    refuse(mkproj(tmp_path, {"page": "/etc/hosts"}), match="relative")
    refuse(mkproj(tmp_path, {"params": "a=1;rm -rf"}), match="params")
    refuse(mkproj(tmp_path, {"fps": 0}), match="fps")
    refuse(mkproj(tmp_path, {"unit_start_frame": -3}), match="unit_start_frame")
    refuse(mkproj(tmp_path, {"frames": "9; ls"}), match="frames")
    refuse(mkproj(tmp_path, layer_extra={"src": "/tmp/elsewhere.mp4"}), match="outside the project")
    refuse(mkproj(tmp_path, layer_extra={"src": "../../escape.mp4"}), match="outside the project")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_out_of_root_is_rejected(tmp_path, cfg):
    d = mkproj(tmp_path)
    os.symlink("/etc/hosts", os.path.join(d, "gen", "evil.html"))
    p = json.load(open(os.path.join(d, "project.json")))
    p["layers"][0]["source"]["page"] = "evil.html"
    json.dump(p, open(os.path.join(d, "project.json"), "w"))
    refuse(d, match="leaves source.root")


def test_root_outside_project_needs_user_trust(tmp_path, cfg, monkeypatch):
    film = tmp_path / "film"
    film.mkdir()
    (film / "page.html").write_text(PAGE % ("#00f", ""))
    d = mkproj(tmp_path, {"root": "../film"})
    refuse(d, match="render_trusted_roots")
    (cfg / "config.json").write_text(json.dumps({"render_trusted_roots": [str(film)]}))
    res = RA.rerender(d, "S01", dry_run=True)
    assert str(film) in "\n".join(res["plans"][0])
    (cfg / "config.json").write_text("{}")
    monkeypatch.setenv("KURGU_RENDER_ROOTS", str(film))
    assert RA.rerender(d, "S01", dry_run=True)["plans"]


def test_unknown_adaptor_and_unknown_target(tmp_path, cfg):
    refuse(mkproj(tmp_path, {"render": "nope"}), match="unknown render adaptor 'nope'")
    refuse(mkproj(tmp_path), ident="missing", match="no layer")
    refuse(mkproj(tmp_path, {"render": ""}), match="no source.render")


def test_user_adaptor_from_config_and_env(tmp_path, cfg, monkeypatch):
    d = mkproj(tmp_path, {"render": "mine"})
    assert RA.user_adaptor("mine") is None
    (cfg / "config.json").write_text(json.dumps({"render_adaptors": {"mine": ["echo", "{unit}", "{start_frame}", "{end_frame}", "{fps}"], "bad": "echo hi", "worse": []}}))
    assert RA.user_adaptor("mine")[0] == "echo" and RA.user_adaptor("bad") is None and RA.user_adaptor("worse") is None
    monkeypatch.setenv("KURGU_RENDER_MINE", json.dumps(["env-tool", "{out}"]))
    assert RA.user_adaptor("mine") == ["env-tool", "{out}"]                           # env wins
    monkeypatch.setenv("KURGU_RENDER_MINE", "not json")
    assert RA.user_adaptor("mine") is None
    monkeypatch.setenv("KURGU_RENDER_MINE", '"echo hi"')
    assert RA.user_adaptor("mine") is None
    monkeypatch.setenv("KURGU_RENDER_MY_TOOL.X", json.dumps(["a"]))
    monkeypatch.setenv("KURGU_RENDER_MY_TOOL_X", json.dumps(["tool-x"]))
    assert RA.user_adaptor("my-tool.x") == ["tool-x"]
    monkeypatch.delenv("KURGU_RENDER_MINE")
    plan = RA.rerender(d, "S01", dry_run=True)["plans"][0]
    assert any(l.startswith("run     echo S01 0 24 24") and "(user adaptor)" in l for l in plan)


def test_placeholders_are_substituted_without_a_shell(tmp_path, cfg):
    d = mkproj(tmp_path, {"render": "mine", "unit": "S.01-a"})
    (cfg / "config.json").write_text(json.dumps({"render_adaptors": {"mine": ["tool", "--r={root}", "{unit}", "--out", "{out}", "$(id)", "{fps};ls"]}}))
    argv = RA.expand(RA.user_adaptor("mine"), RA._spec_for(d, json.load(open(os.path.join(d, "project.json")))["layers"][0]))
    assert argv[0] == "tool" and argv[2] == "S.01-a" and argv[5] == "$(id)" and argv[6] == "24;ls"      # literal text, never interpreted
    assert argv[4].endswith(".part.mp4") and argv[1] == "--r=" + os.path.realpath(os.path.join(d, "gen"))


def test_user_adaptor_runs_and_replaces_atomically(tmp_path, cfg):
    d = mkproj(tmp_path, {"render": "mine"})
    ff = shutil.which("ffmpeg")
    (cfg / "config.json").write_text(json.dumps({"render_adaptors": {"mine": [ff, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=green:s=320x180:r={fps}:d=1",
                                                                              "-pix_fmt", "yuv420p", "{out}"]}}))
    media = os.path.join(d, "media", "shot.mp4")
    before = open(media, "rb").read()
    res = RA.rerender(d, "v")
    r = res["results"][0]
    assert r["path"] == os.path.realpath(media) and r["adaptor"] == "mine" and open(media, "rb").read() != before
    assert RA.probe(media)["audio"], "the old audio stream is kept for adaptors that write video only"
    assert not os.path.exists(RA.part_path(media))
    assert color_at(media)[1] > 100 and color_at(media)[0] < 60


# ---------------------------------------------------------------- dry run, atomicity, mismatch
def test_dry_run_prints_argv_and_runs_nothing(tmp_path, cfg, capsys):
    d = mkproj(tmp_path)
    media = os.path.join(d, "media", "shot.mp4")
    before = (open(media, "rb").read(), os.stat(media).st_mtime_ns)
    assert RA.main([d, "S01", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "html-frames" in out and "frames [0, 24) at 24 fps" in out and "ffmpeg" in out and "-framerate 24" in out and "page.html?capture=1" in out
    assert re.search(r"shot\.[0-9a-f]{16}\.part\.mp4", out) and "audio   copied from the old media" in out and "dry run" in out
    assert (open(media, "rb").read(), os.stat(media).st_mtime_ns) == before and not os.path.exists(RA.part_path(media))
    assert RA.main([d, "nothing", "--dry-run"]) == 1


def test_failure_keeps_old_file_and_cleans_part(tmp_path, cfg):
    d = mkproj(tmp_path, {"render": "mine"})
    (cfg / "config.json").write_text(json.dumps({"render_adaptors": {"mine": [PY, "-c", "import sys; open(sys.argv[1], 'wb').write(b'junk'); sys.exit(3)", "{out}"]}}))
    media = os.path.join(d, "media", "shot.mp4")
    before = open(media, "rb").read()
    with pytest.raises(RA.RerenderError, match="exited with 3"):
        RA.rerender(d, "S01")
    assert open(media, "rb").read() == before and not os.path.exists(RA.part_path(media))
    (cfg / "config.json").write_text(json.dumps({"render_adaptors": {"mine": [PY, "-c", "import sys; open(sys.argv[1], 'wb').write(b'not a video')", "{out}"]}}))
    with pytest.raises(RA.RerenderError, match="cannot be read"):                  # exit 0 but garbage: still not swapped in
        RA.rerender(d, "S01")
    assert open(media, "rb").read() == before and not os.path.exists(RA.part_path(media))


def test_old_file_is_intact_until_the_replace(tmp_path, cfg):
    d = mkproj(tmp_path, {"render": "mine"})
    media = os.path.join(d, "media", "shot.mp4")
    probe_script = ("import sys, os; m = sys.argv[2]; assert os.path.getsize(m) > 1000, 'old file must still be there while rendering';"
                    "import shutil; shutil.copy(m, sys.argv[1])")
    (cfg / "config.json").write_text(json.dumps({"render_adaptors": {"mine": [PY, "-c", probe_script, "{out}", media]}}))
    before = open(media, "rb").read()
    RA.rerender(d, "S01")
    assert open(media, "rb").read() == before and not os.path.exists(RA.part_path(media))     # copy of itself swapped in: same bytes


def test_duration_mismatch_is_reported_not_stretched(tmp_path, cfg):
    d = mkproj(tmp_path, {"render": "mine"})
    (cfg / "config.json").write_text(json.dumps({"render_adaptors": {"mine": [shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                                                                              "color=c=red:s=320x180:r=24:d=2", "-pix_fmt", "yuv420p", "{out}"]}}))
    pj = os.path.join(d, "project.json")
    before = open(pj).read()
    res = RA.rerender(d, "S01")
    r = res["results"][0]
    assert r["duration_changed"] and abs(r["old_duration"] - 1) < 0.1 and abs(r["duration"] - 2) < 0.1
    w = " ".join(res["warnings"])
    assert "CLIP LENGTH DIFFERS" in w and "v (0-1 s)" in w and "1 s" in w and "NOT changed" not in w
    assert open(pj).read() == before                                                      # nothing needed changing: the layer already has the unit's length
    assert "WARNING: CLIP LENGTH DIFFERS" in RA.format_result(res)


# ---------------------------------------------------------------- html-frames end to end
@NEED
@pytest.mark.parametrize("capture", [False, True])
def test_html_frames_end_to_end(tmp_path, cfg, capture):
    d = mkproj(tmp_path)
    media = os.path.join(d, "media", "shot.mp4")
    page = os.path.join(d, "gen", "page.html")
    open(page, "w").write(PAGE % ("#ff0000", CAPTURE if capture else ""))
    t0 = time.time()
    res = RA.rerender(d, "S01")
    took = time.time() - t0
    r = res["results"][0]
    print(f"\n24-frame html-frames rerender ({'captureFrame' if capture else 'screenshot'}): {took:.1f} s wall, {r['elapsed']} s reported")
    j = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", media]))
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    assert v["nb_frames"] == "24" and v["r_frame_rate"] == "24/1" and (v["width"], v["height"]) == (320, 180)
    assert abs(float(j["format"]["duration"]) - 1.0) < 0.1 and abs(r["duration"] - 1.0) < 0.1 and not r["duration_changed"]
    assert any(s["codec_type"] == "audio" and s["codec_name"] == "aac" for s in j["streams"]), "audio kept from the old file"
    red = color_at(media, 0.5)
    assert red[0] > 200 and red[1] < 60 and red[2] < 60
    assert not os.path.exists(RA.part_path(os.path.realpath(media)))
    # edit the page: the colour changes in the new media
    open(page, "w").write(PAGE % ("#00ff00", CAPTURE if capture else ""))
    old_bytes = open(media, "rb").read()
    RA.rerender(d, "v")
    assert open(media, "rb").read() != old_bytes
    g = color_at(media, 0.5)
    assert g[1] > 200 and g[0] < 60 and g[2] < 60


@NEED
def test_html_frames_unit_start_and_page_limits(tmp_path, cfg):
    d = mkproj(tmp_path, {"unit_start_frame": 30, "frames": 12})
    res = RA.rerender(d, "S01")
    assert abs(res["results"][0]["duration"] - 0.5) < 0.1 and res["results"][0]["duration_changed"]     # frames=12 -> 0.5 s
    assert any("end was updated" in w and "-0.5" in w for w in res["warnings"])                           # the unit is 0.5 s: its layer's end followed
    assert abs(json.load(open(os.path.join(d, "project.json")))["layers"][0]["end"] - 0.5) < 0.01
    d2 = mkproj(tmp_path, {"unit_start_frame": 40})
    with pytest.raises(RA.RerenderError, match="48 frames"):
        RA.rerender(d2, "S01")
    assert not os.path.exists(RA.part_path(os.path.join(os.path.realpath(d2), "media", "shot.mp4")))


@NEED
def test_page_cannot_reach_the_network(tmp_path, cfg):
    import http.server
    import threading
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        d = mkproj(tmp_path)
        open(os.path.join(d, "gen", "page.html"), "w").write(
            PAGE % ("#ff0000", f"fetch('http://127.0.0.1:{srv.server_port}/leak').catch(()=>{{}}); new Image().src='http://127.0.0.1:{srv.server_port}/img';"))
        RA.rerender(d, "S01")
        time.sleep(0.5)
        assert hits == []
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- cinecode-shot, CLI, MCP, wake
def test_cinecode_shot_uses_user_configured_capture(tmp_path, cfg, monkeypatch):
    cc = tmp_path / "cc" / "scripts"
    cc.mkdir(parents=True)
    (cc / "capture.mjs").write_text("// stub")
    d = mkproj(tmp_path, {"render": "cinecode-shot", "kind": "cinecode", "unit_start_frame": 144, "frames": 24, "page": None})
    p = json.load(open(os.path.join(d, "project.json")))
    p["layers"][0]["source"].pop("page")
    json.dump(p, open(os.path.join(d, "project.json"), "w"))
    open(os.path.join(d, "gen", "film.html"), "w").write(PAGE % ("#f00", ""))
    plan = "\n".join(RA.rerender(d, "S01", dry_run=True)["plans"][0])
    assert "falling back to html-frames" in plan                               # no cinecode folder configured: never the project's own tools/
    monkeypatch.setenv("KURGU_CINECODE", str(tmp_path / "cc"))
    plan = "\n".join(RA.rerender(d, "S01", dry_run=True)["plans"][0])
    assert "capture.mjs" in plan and "--start 144 --end 167" in plan and "--video" in plan and str(tmp_path / "cc") in plan and "film.html" in plan


def test_cli_and_guidance(tmp_path, cfg):
    d = mkproj(tmp_path)
    r = subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "rerender", d, "S01", "--dry-run"], capture_output=True, text=True)
    assert r.returncode == 0 and "html-frames" in r.stdout
    r = subprocess.run([PY, os.path.join(ROOT, "kurgu_cli.py"), "rerender", d, "S01"], capture_output=True, text=True, env=dict(os.environ, KURGU_CONFIG=str(cfg)))
    assert r.returncode in (0, 1)
    assert "Bash(kurgu rerender:*)" in kurgu_cli.CLAUDE_TOOLS
    for t in (kurgu_cli.AGENT_BLOCK, kurgu_cli.WAKE_PROMPT, open(os.path.join(ROOT, "skills", "kurgu", "SKILL.md"), encoding="utf-8").read(),
              open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8").read()):
        assert "kurgu rerender" in t
        assert "until `kurgu rerender` exists" not in t and "when your Kurgu has it" not in t


def test_mcp_rerender_dry_run(tmp_path, cfg):
    import mcp_server
    d = mkproj(tmp_path)
    out = mcp_server.tool_rerender({"project_dir": d, "target": "S01", "dry_run": True}, {})
    assert "dry run" in out[0]["text"] and "html-frames" in out[0]["text"]
    with pytest.raises(mcp_server.ToolError, match="unknown render adaptor"):
        mcp_server.tool_rerender({"project_dir": mkproj(tmp_path, {"render": "bash"}), "target": "S01"}, {})


def test_versions_snapshot_called_before_replace(tmp_path, cfg, monkeypatch):
    d = mkproj(tmp_path, {"render": "mine"})
    (cfg / "config.json").write_text(json.dumps({"render_adaptors": {"mine": [shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                                                                              "color=c=red:s=320x180:r=24:d=1", "-pix_fmt", "yuv420p", "{out}"]}}))
    media = os.path.realpath(os.path.join(d, "media", "shot.mp4"))
    before = open(media, "rb").read()
    seen = []

    class V:
        @staticmethod
        def snapshot(pd, path, st=None, sync=False):
            assert sync is True                                       # the CLI needs the copy before it replaces the file
            seen.append((pd, path, open(path, "rb").read() == before))      # the OLD bytes are still in place
    monkeypatch.setattr(RA, "versions", V)
    RA.rerender(d, "S01")
    assert seen == [(os.path.realpath(d), media, True)] and open(media, "rb").read() != before

    class Bad:
        @staticmethod
        def snapshot(pd, path):
            raise OSError("disk")
    monkeypatch.setattr(RA, "versions", Bad)
    RA.rerender(d, "S01")                                                    # a failing snapshot never blocks the render


# ---------------------------------------------------------------- element map refresh + import trust
DOMPAGE = """<!doctype html><meta charset=utf-8><body style="margin:0;background:#00f"><h1 id=t data-kurgu-id="baslik" style="position:absolute;margin:0;top:40px;left:%dpx;font:24px sans-serif;color:#fff">Hello</h1><script>
window.renderFrame = (i) => {}; window.captureFrame = (i) => '';
window.READY = Promise.resolve({frames: 48, fps: 24, width: 320, height: 180});
</script>"""


def _dom_project(tmp_path, left):
    import elements as el
    d = mkproj(tmp_path, {"elements": "elements/S01.kurgu.json"})
    page = os.path.join(d, "gen", "page.html")
    open(page, "w").write(DOMPAGE % left)
    res = el.measure(page, [0, 6, 12, 18, 23], size=(320, 180), fps=24, params="capture=1")
    el.write_map(os.path.join(d, "elements", "S01.kurgu.json"), res)
    return d, page


def _title_left(d):
    m = json.load(open(os.path.join(d, "elements", "S01.kurgu.json")))
    return m, {e["id"]: e for e in m["elements"]}["baslik"]["boxes"]


@NEED
def test_rerender_remeasures_the_sidecar(tmp_path, cfg):
    d, page = _dom_project(tmp_path, 10)
    assert {b["l"] for b in _title_left(d)[1]} == {10}
    open(page, "w").write(DOMPAGE % 100)
    res = RA.rerender(d, "S01")
    assert res["results"][0]["sidecar"] == "refreshed" and not any("not regenerated" in w for w in res["warnings"])
    m, boxes = _title_left(d)
    assert {b["l"] for b in boxes} == {100} and not m.get("stale") and m["size"] == [320, 180]
    assert min(b["f"] for b in boxes) == 0 and max(b["f"] for b in boxes) == 23
    import source_map
    lay = json.load(open(os.path.join(d, "project.json")))["layers"][0]
    r = source_map.elements_at(lay, 0.5, 320, 180, d)
    assert not r.get("error") and r["elements"][0]["box"][0] == 100


@NEED
def test_rerender_marks_sidecar_stale_when_measuring_fails(tmp_path, cfg, monkeypatch):
    import elements as el
    d, page = _dom_project(tmp_path, 10)
    open(page, "w").write(DOMPAGE % 100)

    def boom(*a, **k):
        raise el.ElementsError("no chrome today")
    monkeypatch.setattr(el, "measure", boom)
    res = RA.rerender(d, "S01")
    assert res["results"][0]["sidecar"] == "stale" and any("stale" in w for w in res["warnings"])
    m, boxes = _title_left(d)
    assert m["stale"] is True and {b["l"] for b in boxes} == {10}            # old boxes kept
    import source_map
    lay = json.load(open(os.path.join(d, "project.json")))["layers"][0]
    assert "STALE" in source_map.elements_at(lay, 0.5, 320, 180, d)["error"]
    import agent_notes
    assert "STALE" in agent_notes.source_view(lay, 0.5, {"width": 320, "height": 180}, d)["elements_error"]


def _film(tmp_path):
    f = tmp_path / "film"
    f.mkdir()
    (f / "page.html").write_text(PAGE % ("#00f", ""))
    sh("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=24:d=2", "-pix_fmt", "yuv420p", str(f / "film.mp4"))
    (f / "scenes.json").write_text(json.dumps({"fps": 24, "video": "film.mp4", "scenes": [
        {"id": "S01", "start_frame": 0, "end_frame": 24, "file": "page.html"}]}))
    return f


@NEED
def test_import_trusts_the_folder_in_user_config_only(tmp_path, cfg, capsys):
    import scene_import
    f = _film(tmp_path)
    (cfg / "config.json").write_text(json.dumps({"on_send": ["echo", "x"], "render_trusted_roots": ["/some/other"]}))
    out = tmp_path / "proj"
    assert scene_import.main([str(f), "--out", str(out), "--no-trust"]) == 0
    assert json.load(open(cfg / "config.json"))["render_trusted_roots"] == ["/some/other"]       # --no-trust: untouched
    refuse(str(out), ident="S01", match="render_trusted_roots")
    assert scene_import.main([str(f), "--out", str(out), "--force"]) == 0
    assert "render_trusted_roots" in capsys.readouterr().err
    conf = json.load(open(cfg / "config.json"))
    assert {"root": os.path.realpath(str(f)), "project": os.path.realpath(str(out))} in conf["render_trusted_roots"]
    assert "/some/other" in conf["render_trusted_roots"] and conf["on_send"] == ["echo", "x"]
    assert RA.rerender(str(out), "S01", dry_run=True)["plans"]
    assert scene_import.main([str(f), "--out", str(out), "--force"]) == 0                         # no duplicate entry
    assert len(json.load(open(cfg / "config.json"))["render_trusted_roots"]) == 2


@NEED
def test_import_creates_missing_user_config_and_project_file_cannot_trust(tmp_path, cfg):
    import scene_import
    f = _film(tmp_path)
    assert not (cfg / "config.json").exists()
    out = tmp_path / "proj"
    assert scene_import.main([str(f), "--out", str(out)]) == 0
    assert json.load(open(cfg / "config.json"))["render_trusted_roots"] == [{"root": os.path.realpath(str(f)), "project": os.path.realpath(str(out))}]
    # a project.json (or a config next to it) naming a root does not make it trusted
    other = tmp_path / "other"
    other.mkdir()
    (other / "page.html").write_text(PAGE % ("#00f", ""))
    p = json.load(open(out / "project.json"))
    p["layers"][0]["source"]["root"] = "../other"
    p["layers"][0]["source"]["render_trusted_roots"] = [str(other)]
    json.dump(p, open(out / "project.json", "w"))
    json.dump({"render_trusted_roots": [str(other)]}, open(out / "config.json", "w"))
    refuse(str(out), ident="S01", match="render_trusted_roots")


def test_launcher_routes_rerender(tmp_path, cfg):
    """`kurgu rerender` must reach the CLI through the launcher (it was once opened as a project folder named 'rerender')."""
    from test_agent_notes import launch
    d = mkproj(tmp_path)
    r = launch("rerender", d, "S01", "--dry-run", env=dict(os.environ, KURGU_CONFIG=str(cfg)))
    assert r.returncode == 0 and "html-frames" in r.stdout and "No such folder" not in r.stderr
    assert 'rerender' in open(os.path.join(ROOT, "kurgu.cmd")).read()


@NEED
def test_wake_adds_only_user_trusted_source_roots(tmp_path, cfg):
    """A woken Claude run can edit the source folder of a code-made clip when it lies outside the project, but only if the USER trusts that folder."""
    import scene_import
    f = _film(tmp_path)
    out = tmp_path / "proj"
    assert scene_import.main([str(f), "--out", str(out), "--no-trust"]) == 0
    flat = lambda: [x for _, argv in kurgu_cli.wake_attempts("claude", str(out)) for x in argv]
    assert "--add-dir" not in flat()
    assert scene_import.main([str(f), "--out", str(out), "--force"]) == 0
    for _, argv in kurgu_cli.wake_attempts("claude", str(out)):
        assert argv[-2:] == ["--add-dir", os.path.realpath(str(f))]
    # a root a project file names itself is not added
    other = tmp_path / "other"
    other.mkdir()
    p = json.load(open(out / "project.json"))
    p["layers"][0]["source"]["root"] = "../other"
    json.dump(p, open(out / "project.json", "w"))
    assert "--add-dir" not in flat()


@NEED
def test_codex_wake_gets_writable_roots_for_trusted_source_folders_only(tmp_path, cfg):
    import scene_import
    f = _film(tmp_path)
    out = tmp_path / "proj"
    assert scene_import.main([str(f), "--out", str(out), "--no-trust"]) == 0
    flat = lambda: " ".join(x for _, argv in kurgu_cli.wake_attempts("codex", str(out)) for x in argv)
    assert "writable_roots" not in flat()
    assert scene_import.main([str(f), "--out", str(out), "--force"]) == 0
    for _, argv in kurgu_cli.wake_attempts("codex", str(out)):         # resume has no --add-dir; the config key is accepted by both forms
        i = argv.index("sandbox_workspace_write.writable_roots=" + json.dumps([os.path.realpath(str(f))]))
        assert argv[i - 1] == "-c" and "--add-dir" not in argv


def test_launcher_routes_subcommands_without_enough_args(tmp_path):
    """`kurgu frame <dir>` with a missing argument must print usage, not open a folder called 'frame' (or fail with 'No such folder')."""
    from test_agent_notes import launch
    for args in (["frame"], ["frame", "x"], ["strip", "x", "1"], ["elements"], ["import"], ["rerender"], ["rerender", "x"], ["detect"]):
        r = launch(*args, cwd=str(tmp_path))
        assert r.returncode != 0 and "No such folder" not in r.stderr and ("usage" in r.stderr.lower() or "error" in r.stderr.lower()), (args, r.stderr)
    assert not os.path.exists(tmp_path / "frame")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_rerender_and_detect_require_a_real_project(tmp_path):
    (tmp_path / "project.json").write_text("{\"layers\": 3}")
    with pytest.raises(RA.RerenderError):
        RA.rerender(str(tmp_path), "S01", dry_run=True)
    d = mkproj(tmp_path)
    os.makedirs(os.path.join(tmp_path, "real"))
    shutil.move(os.path.join(d, ".kurgu"), os.path.join(tmp_path, "real")) if os.path.exists(os.path.join(d, ".kurgu")) else None
    os.symlink(str(tmp_path / "real"), os.path.join(d, ".kurgu"))
    with pytest.raises(RA.RerenderError, match="symlink"):
        RA.rerender(d, "S01", dry_run=True)


@NEED
def test_html_frames_page_cannot_use_webrtc_udp(tmp_path, cfg):
    import socket
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.2)
    d = mkproj(tmp_path)
    js = ("try { var pc = new RTCPeerConnection({iceServers: [{urls: 'stun:127.0.0.1:%d'}]}); pc.createDataChannel('x');"
          " pc.createOffer().then(function (o) { return pc.setLocalDescription(o); }); } catch (e) {}" % udp.getsockname()[1])
    open(os.path.join(d, "gen", "page.html"), "w").write(PAGE % ("#ff0000", js))
    RA.rerender(d, "S01")
    time.sleep(0.5)
    got = []
    try:
        while True:
            got.append(udp.recvfrom(2048))
    except OSError:
        pass
    assert got == []
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in RA.chrome_flags("/tmp/p", 10, 10)
    assert "--no-sandbox" not in RA.chrome_flags("/tmp/p", 10, 10) or os.environ.get("KURGU_NO_SANDBOX") == "1" or (hasattr(os, "geteuid") and os.geteuid() == 0)


def test_no_sandbox_only_as_root_or_by_override(monkeypatch):
    import kurgu_paths
    monkeypatch.delenv("KURGU_NO_SANDBOX", raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    assert kurgu_paths.no_sandbox_flags() == []
    monkeypatch.setenv("KURGU_NO_SANDBOX", "1")
    assert "--no-sandbox" in kurgu_paths.no_sandbox_flags()
    monkeypatch.delenv("KURGU_NO_SANDBOX")
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert "--no-sandbox" in kurgu_paths.no_sandbox_flags()


@NEED
def test_trust_is_bound_to_the_importing_project(tmp_path, cfg):
    """Another project cannot point source.root at a folder the user trusted for a different project."""
    import scene_import
    f = _film(tmp_path)
    out = tmp_path / "proj"
    assert scene_import.main([str(f), "--out", str(out)]) == 0
    assert RA.rerender(str(out), "S01", dry_run=True)["plans"]
    copy = tmp_path / "other-project"                                      # same project.json (root ../film), different project folder
    shutil.copytree(str(out), str(copy))
    p = json.load(open(copy / "project.json"))
    assert p["layers"][0]["source"]["root"] == "../film"
    refuse(str(copy), ident="S01", match="render_trusted_roots")
    assert RA.trusted_roots(str(copy)) == [] and RA.trusted_roots(str(out)) == [os.path.realpath(str(f))]
    assert RA.trusted_roots() == []                                        # no project: nothing bound applies


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_scenes_json_paths_stay_inside_the_folder(tmp_path):
    import scene_import
    d = tmp_path / "work"
    d.mkdir()
    (tmp_path / "secret.mp4").write_bytes(b"x")
    for bad in ({"video": "../secret.mp4"}, {"video": str(tmp_path / "secret.mp4")}, {"video": "ok.mp4", "file": "../x.html"}, {"video": "ok.mp4", "file": "/etc/hosts.html"}):
        scene = {"id": "a", "start_frame": 0, "end_frame": 10}
        if "file" in bad:
            scene["file"] = bad["file"]
        (d / "scenes.json").write_text(json.dumps({"fps": 24, "video": bad["video"], "scenes": [scene]}))
        with pytest.raises(scene_import.SceneImportError, match="relative|leaves"):
            scene_import.read_scenes(str(d), str(d / "scenes.json"))
    os.symlink(str(tmp_path / "secret.mp4"), str(d / "link.mp4"))
    (d / "scenes.json").write_text(json.dumps({"fps": 24, "video": "link.mp4", "scenes": [{"id": "a", "start_frame": 0, "end_frame": 10}]}))
    with pytest.raises(scene_import.SceneImportError, match="leaves"):
        scene_import.read_scenes(str(d), str(d / "scenes.json"))


def test_every_chrome_launch_path_avoids_the_keychain_prompt(monkeypatch):
    """Chrome asks macOS for the keychain unless told not to ("Keychain not found" pop-ups): every Chrome Kurgu or its tests start passes both flags."""
    import cdp
    import elements
    flags = [RA.chrome_flags("/tmp/p", 10, 10), cdp.chrome_flags(1, "/tmp/p", 10, 10)]
    launched = []

    class Boom(Exception):
        pass

    def fake_popen(argv, *a, **k):
        launched.append(list(argv))
        raise Boom()
    monkeypatch.setattr(elements.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(elements, "find_chrome", lambda: "/x/chrome")
    with pytest.raises(Boom):
        elements.Browser(10, 10)
    flags.append(launched[0])
    assert len(flags) == 3
    for f in flags:
        assert "--use-mock-keychain" in f and "--password-store=basic" in f and "--mute-audio" in f and any(x.startswith("--headless") for x in f)


# ---------------------------------------------------------------- planted symlinks on temp-file names (security review S2)
def _plant(path, victim):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.symlink(victim, path)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_planted_part_symlink_is_never_written_through(tmp_path, cfg):
    import elements
    import scene_import
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    gone = str(tmp_path / "does-not-exist-yet.mp4")                        # a dangling target: os.path.exists(part) is False for it
    # (a) elements.write_map
    out = tmp_path / "film.kurgu.json"
    for n in (".part", ".tmp"):
        _plant(str(out) + n, str(victim))
    elements.write_map(str(out), {"fps": 24, "size": [1, 1], "elements": []})
    assert victim.read_text() == "keep" and json.load(open(out))["fps"] == 24
    # (b) rerender: media.part.mp4 and friends point at a file that does not exist
    d = mkproj(tmp_path, {"render": "mine"})
    media = os.path.realpath(os.path.join(d, "media", "shot.mp4"))
    base = os.path.splitext(media)[0]
    for n in (".part.mp4", ".tmp.mp4", ".part"):
        _plant(base + n, gone)
    (cfg / "config.json").write_text(json.dumps({"render_adaptors": {"mine": [shutil.which("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
                                                                              "color=c=red:s=320x180:r=24:d=1", "-pix_fmt", "yuv420p", "{out}"]}}))
    RA.rerender(d, "S01")
    assert not os.path.exists(gone) and not os.path.islink(media)
    # (c) scene_import._write_atomic: project.json.part and scenes.md.part in the target folder
    f = _film(tmp_path)
    proj = tmp_path / "proj"
    os.makedirs(proj)
    for n in ("project.json.part", "scenes.md.part", "project.json.tmp"):
        _plant(str(proj / n), str(victim))
    assert scene_import.main([str(f), "--out", str(proj), "--force", "--no-trust"]) == 0
    assert victim.read_text() == "keep" and (proj / "project.json").is_file() and not (proj / "project.json").is_symlink()


# ---------------------------------------------------------------- one unit of a shared film (C2) and a unit whose length changed (T1)
SHARED_PAGE = """<!doctype html><meta charset=utf-8><body style="margin:0"><canvas id=c width=320 height=180></canvas><script>
const cv = document.getElementById('c'), g = cv.getContext('2d');
window.renderFrame = (i) => { g.fillStyle = i < 24 ? '#ff0000' : '#00ff00'; g.fillRect(0, 0, 320, 180); g.fillStyle = '#fff'; g.fillRect(i * 6, 0, 6, 180); };
window.READY = Promise.resolve({frames: 72, fps: 24, width: 320, height: 180});
</script>"""


def _two_scene_film(tmp_path):
    import scene_import
    f = tmp_path / "film2"
    f.mkdir()
    (f / "page.html").write_text(SHARED_PAGE)
    sh("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:r=24:d=3", "-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-shortest", str(f / "film.mp4"))
    (f / "scenes.json").write_text(json.dumps({"fps": 24, "video": "film.mp4", "width": 320, "height": 180, "page_params": "capture=1", "scenes": [
        {"id": "a", "start_frame": 0, "end_frame": 24, "file": "page.html"}, {"id": "b", "start_frame": 24, "end_frame": 48, "file": "page.html"}]}))
    out = tmp_path / "proj2"
    assert scene_import.main([str(f), "--out", str(out)]) == 0
    return f, out


def _frame_md5(path, n):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-frames:v", str(n), "-f", "framemd5", "-"], capture_output=True, text=True).stdout


@NEED
def test_rerendering_one_unit_never_rewrites_the_shared_film(tmp_path, cfg):
    f, out = _two_scene_film(tmp_path)
    film = f / "film.mp4"
    before, frames_a = film.read_bytes(), _frame_md5(str(film), 24)
    res = RA.rerender(str(out), "b")
    assert [r["unit"] for r in res["results"]] == ["b"]
    assert film.read_bytes() == before and _frame_md5(str(film), 24) == frames_a                  # scene a (and the film itself) untouched
    p = json.load(open(out / "project.json"))
    la, lb = [l for l in p["layers"] if l["id"] == "shot-a"][0], [l for l in p["layers"] if l["id"] == "shot-b"][0]
    assert la["src"].endswith("film.mp4") and lb["src"].endswith("film.b.mp4") and lb["src_in"] == 0 and lb["source"]["frames"] == 24
    unit = f / "film.b.mp4"
    info = RA.probe(str(unit))
    assert unit.is_file() and abs(info["dur"] - 1.0) < 0.1 and info["audio"]                        # exactly the unit's frames, with that slice of the film's sound
    assert "DURATION" not in " ".join(res["warnings"])


@NEED
def test_a_unit_that_got_longer_or_moved_follows_the_scene_list_not_the_old_media(tmp_path, cfg):
    f, out = _two_scene_film(tmp_path)
    RA.rerender(str(out), "b")                                                                         # b owns film.b.mp4 now (24 frames)
    lst = json.loads((f / "scenes.json").read_text())
    lst["scenes"][1].update(start_frame=24, end_frame=60)                                              # b is 36 frames long now
    (f / "scenes.json").write_text(json.dumps(lst))
    res = RA.rerender(str(out), "b")
    assert abs(RA.probe(str(f / "film.b.mp4"))["dur"] - 1.5) < 0.1 and res["results"][0]["duration_changed"]
    p = json.load(open(out / "project.json"))
    lb = [l for l in p["layers"] if l["id"] == "shot-b"][0]
    assert abs((lb["end"] - lb["start"]) - 1.5) < 0.01 and lb["source"]["frames"] == 36
    assert any("shift" in w and "+0.5" in w for w in res["warnings"])
    assert not any("DURATION CHANGED" in w or "NOT changed" in w or "CLIP LENGTH DIFFERS" in w for w in res["warnings"])      # the layer was updated: no contradicting warning
    lst["scenes"][1].update(start_frame=36, end_frame=72)                                              # b moved to [36, 72): still inside the page's 72 frames
    (f / "scenes.json").write_text(json.dumps(lst))
    res = RA.rerender(str(out), "b")
    assert abs(RA.probe(str(f / "film.b.mp4"))["dur"] - 1.5) < 0.1 and json.load(open(out / "project.json"))["layers"][0]["source"] is not None
    lst["scenes"][1].update(start_frame=36, end_frame=90)                                              # beyond the page: a clear error, nothing replaced
    (f / "scenes.json").write_text(json.dumps(lst))
    before = (f / "film.b.mp4").read_bytes()
    with pytest.raises(RA.RerenderError, match="frames"):
        RA.rerender(str(out), "b")
    assert (f / "film.b.mp4").read_bytes() == before


def test_chrome_is_not_started_inside_codex_s_macos_sandbox(tmp_path, cfg, monkeypatch):
    """Under `codex sandbox` Chrome aborts at start (SIGABRT, a "quit unexpectedly" dialog, whatever the flags): say so instead of launching it."""
    import elements
    import kurgu_paths
    launched = []
    real_popen = subprocess.Popen

    def guarded(argv, *a, **k):
        if "hrome" in str(argv[0]):
            launched.append(argv)
            raise AssertionError("Chrome must not be launched")
        return real_popen(argv, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", guarded)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    assert "sandbox" in kurgu_paths.chrome_blocked_reason() and "do not mark the note done" in kurgu_paths.chrome_blocked_reason()
    d = mkproj(tmp_path)
    with pytest.raises(RA.RerenderError, match="Codex's macOS sandbox"):
        RA.rerender(d, "S01")
    with pytest.raises(elements.ElementsError, match="Codex's macOS sandbox"):
        elements.Browser(10, 10)
    assert launched == []
    monkeypatch.delenv("CODEX_SANDBOX")
    assert kurgu_paths.chrome_blocked_reason() is None


# ---------------------------------------------------------------- the html-frames Chrome cannot read local files (security review S1)
@NEED
def test_html_frames_page_cannot_show_local_files_in_the_video(tmp_path, cfg):
    """A scene page with <iframe src="file:///..."> used to render local files and directory listings into the output video."""
    from PIL import Image
    import io
    secret = tmp_path / "secret-folder"
    secret.mkdir()
    (secret / "TOPSECRET-FILENAME.txt").write_text("x")
    d = mkproj(tmp_path)
    page = (PAGE % ("#0000ff", "")).replace("<canvas", f'<iframe src="file://{secret}/" style="position:absolute;left:0;top:0;width:320px;height:180px;border:0;background:#fff"></iframe><iframe src="file://{secret}/TOPSECRET-FILENAME.txt" style="position:absolute;left:0;top:0;width:320px;height:180px;border:0;background:#fff"></iframe><canvas', 1)
    page = page.replace("g.fillRect(i * 10, 0, 8, 180);", "").replace("g.fillStyle = COLOR; g.fillRect(0, 0, 320, 180);", "g.fillStyle = COLOR; g.fillRect(0, 0, 320, 180);")
    open(os.path.join(d, "gen", "page.html"), "w").write(page)
    RA.rerender(d, "S01")
    media = os.path.join(d, "media", "shot.mp4")
    png = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.3", "-i", media, "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True).stdout
    shot = tmp_path / "frame.png"
    shot.write_bytes(png)
    import ocr_boxes
    res = ocr_boxes.read_frame(str(shot), objects=False)
    if not res["backend"]["text"]:
        pytest.skip("no OCR engine to read the frame")
    seen = " ".join(t["text"] for t in res["text"]).upper()
    assert "TOPSECRET" not in seen and "SECRET-FOLDER" not in seen and "INDEX OF" not in seen, seen          # neither the file nor the folder listing reached the video
    assert "<port>" in " ".join(RA.rerender(d, "S01", dry_run=True)["plans"][0]) and "file://" not in " ".join(RA.rerender(d, "S01", dry_run=True)["plans"][0]).replace("no file:// access", "")


def test_a_stuck_chrome_becomes_a_rerender_error_not_a_traceback(monkeypatch):
    class W:
        def send(self, t):
            raise socket.timeout("timed out")
    b = RA._Browser.__new__(RA._Browser)
    b.ws, b.n = W(), 0
    with pytest.raises(RA.RerenderError, match="did not answer"):
        b.call("Page.enable")


# ---------------------------------------------------------------- the scene list is project-side text (security review N2)
@NEED
def test_a_huge_or_negative_unit_range_from_the_scene_list_is_refused(tmp_path, cfg):
    f, out = _two_scene_film(tmp_path)
    for start, end in ((24, 2_000_000_000), (-5, 24), (24, 24)):
        lst = json.loads((f / "scenes.json").read_text())
        lst["scenes"][1].update(start_frame=start, end_frame=end)
        (f / "scenes.json").write_text(json.dumps(lst))
        with pytest.raises(RA.RerenderError):
            RA.rerender(str(out), "b", dry_run=True)


@NEED
@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_sigterm_in_the_middle_of_a_rerender_leaves_no_part_file(tmp_path, cfg):
    import signal as _sig
    d = mkproj(tmp_path, {"render": "slow"})
    slow = ("import sys,time; open(sys.argv[1],'wb').write(b'partial'); sys.stdout.write('go\\\\n'); sys.stdout.flush(); time.sleep(60)")
    env = dict(os.environ, KURGU_CONFIG=str(cfg), KURGU_RENDER_SLOW=json.dumps([sys.executable, "-c", slow, "{out}"]))
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "kurgu_cli.py"), "rerender", d, "S01"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    media = os.path.join(d, "media")
    for _ in range(100):                                                     # wait until the adaptor has created its part file
        if any(".part" in n for n in os.listdir(media)):
            break
        time.sleep(0.1)
    assert any(".part" in n for n in os.listdir(media))
    p.send_signal(_sig.SIGTERM)
    p.wait(20)
    assert p.returncode == 143 and not any(".part" in n for n in os.listdir(media)), os.listdir(media)


# ---------------------------------------------------------------- the last unit of a film, and the source frame of a later shot (trial findings R1, R2)
@NEED
def test_the_last_user_of_a_film_gets_its_own_file_too_and_scenes_md_follows(tmp_path, cfg):
    f, out = _two_scene_film(tmp_path)
    film, before = f / "film.mp4", (f / "film.mp4").read_bytes()
    RA.rerender(str(out), "a")
    RA.rerender(str(out), "b")                                                    # b is the only layer left on film.mp4
    assert film.read_bytes() == before                                            # the shared film is never overwritten, even by its last user
    p = json.load(open(out / "project.json"))
    lb = [l for l in p["layers"] if l["id"] == "shot-b"][0]
    assert lb["src"].endswith("film.b.mp4") and lb["src_in"] == 0 and lb["source"]["media_start_frame"] == 24
    assert abs(RA.probe(str(f / "film.b.mp4"))["dur"] - 1.0) < 0.1
    lst = json.loads((f / "scenes.json").read_text())
    lst["scenes"][1].update(start_frame=24, end_frame=60)
    (f / "scenes.json").write_text(json.dumps(lst))
    RA.rerender(str(out), "b")
    assert "(frames 24-60)" in (out / "scenes.md").read_text()


@NEED
def test_the_source_frame_of_a_note_in_a_later_shot_is_not_the_shot_start_twice(tmp_path, cfg):
    import agent_notes
    import source_map
    f, out = _two_scene_film(tmp_path)
    p = json.load(open(out / "project.json"))
    lb = [l for l in p["layers"] if l["id"] == "shot-b"][0]
    assert lb["start"] == 1.0 and lb["src_in"] == 1.0 and lb["source"]["unit_start_frame"] == 24
    assert source_map.source_frame(lb, 1.5) == 36                                 # 12 frames into scene b of the film: frame 36 (it was 60: start added twice)
    assert agent_notes.source_view(lb, 1.5, p, str(out))["frame"] == 36
    RA.rerender(str(out), "b")                                                    # the unit now has its own file: same note time, same frame
    p = json.load(open(out / "project.json"))
    lb = [l for l in p["layers"] if l["id"] == "shot-b"][0]
    assert lb["src_in"] == 0 and source_map.source_frame(lb, 1.5) == 36
    assert agent_notes.source_view(lb, 1.5, p, str(out))["time_in_unit"] == 0.5


DOM_PAGE = """<!doctype html><meta charset=utf-8><body style="margin:0;background:#003"><h1 id=t data-kurgu-id="title" style="position:absolute;margin:0;top:50px;left:10px;font:24px sans-serif;color:#fff">Hello</h1><script>
window.renderFrame = (i) => { document.getElementById('t').style.left = (10 + i) + 'px'; };
window.READY = Promise.resolve({frames: 72, fps: 24, width: 320, height: 180});
</script>"""


@NEED
def test_after_a_unit_moved_its_element_map_holds_the_new_range(tmp_path, cfg):
    import scene_import
    f = tmp_path / "film3"
    f.mkdir()
    (f / "page.html").write_text(DOM_PAGE)
    sh("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:r=24:d=3", "-pix_fmt", "yuv420p", str(f / "film.mp4"))
    (f / "scenes.json").write_text(json.dumps({"fps": 24, "video": "film.mp4", "width": 320, "height": 180, "page_params": "capture=1", "scenes": [
        {"id": "a", "start_frame": 0, "end_frame": 24, "file": "page.html"}, {"id": "b", "start_frame": 24, "end_frame": 48, "file": "page.html"}]}))
    out = tmp_path / "proj3"
    assert scene_import.main([str(f), "--out", str(out), "--elements", "--elements-step", "6"]) == 0
    mp = out / "elements" / "b.kurgu.json"
    frames = lambda: sorted({b["f"] for e in json.load(open(mp))["elements"] for b in e["boxes"]})
    assert frames()[0] == 24 and frames()[-1] == 47
    lst = json.loads((f / "scenes.json").read_text())
    lst["scenes"][1].update(start_frame=36, end_frame=60)
    (f / "scenes.json").write_text(json.dumps(lst))
    res = RA.rerender(str(out), "b")
    assert res["results"][0]["sidecar"] == "refreshed"
    fr = frames()
    assert fr[0] == 36 and fr[-1] == 59 and all(36 <= x < 60 for x in fr), fr                 # nothing left from the old range [24, 48)
    assert "(frames 36-60)" in (out / "scenes.md").read_text()
