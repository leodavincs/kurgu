"""kurgu elements (box measurement of frame-function pages) and kurgu import (cinecode shots.json / scenes.json / Remotion message).

The fixture is a tiny cinecode-like film folder built in a temp dir: shots.json, a generated mp4 in renders/, a frame-function
film.html with one moving DOM text. Chrome tests skip when there is no Chrome; they run one after the other (no xdist).
"""
import json
import os
import subprocess
import sys

import pytest

import elements
import media_tools
import scene_import
import schema
from test_agent_notes import launch

HAVE_CHROME = True
try:
    elements.find_chrome()
except elements.ElementsError:
    HAVE_CHROME = False
needs_chrome = pytest.mark.skipif(not HAVE_CHROME, reason="no Chrome / Chromium")

FPS = 24
SHOTS = [("S01", 0, 24, ["Hello"]), ("S02", 24, 72, ["Second one", "and more"]), ("S03", 72, 96, [])]

PAGE = """<!doctype html><html><body style="margin:0;background:#111;color:#fff">
<h1 id="title" data-kurgu-id="baslik" data-kurgu-src="engine/scene-s02.js:41" style="position:absolute;margin:0;font:32px sans-serif">Hello</h1>
<div id="logo" data-kurgu-id="logo" style="position:absolute;left:250px;top:140px;width:40px;height:20px;background:red"></div>
<script>
window.READY = Promise.resolve({fps: 24, width: 320, height: 180, frames: 96});
window.renderFrame = function (i) {
  var t = document.getElementById('title');
  t.textContent = i < 72 ? 'Hello' : 'Bye';
  t.style.left = (10 + i * 2) + 'px'; t.style.top = '40px';
  document.getElementById('logo').style.display = i < 60 ? 'block' : 'none';
};
window.captureFrame = function (i) { window.renderFrame(i); return ''; };
</script></body></html>
"""

CANVAS_PAGE = """<!doctype html><body style="margin:0"><canvas id="c" width="320" height="180" style="width:320px;height:180px"></canvas>
<script>var c=document.getElementById('c').getContext('2d');
window.renderFrame=function(i){c.putImageData(c.createImageData(320,180),0,0);};</script>"""      # pixels only: the 2D calls the capture records are not used


def make_mp4(path, seconds=4, size="320x180", audio=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = [media_tools.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={FPS}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"] + (["-c:a", "aac", "-shortest"] if audio else []) + [path]
    subprocess.run(cmd, check=True)


def make_wav(path, seconds=2):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([media_tools.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}", path], check=True)


@pytest.fixture()
def film(tmp_path):
    d = tmp_path / "film"
    d.mkdir()
    shots = {"title": "Tiny film", "fps": FPS, "width": 320, "height": 180, "duration_frames": 96,
             "shots": [{"id": i, "start": s, "end": e, "description": f"shot {i}", "vo": f"voice for {i}",
                        "text_on_screen": [{"text": t, "t": [0, 1]} for t in texts]} for i, s, e, texts in SHOTS]}
    (d / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
    (d / "film.html").write_text(PAGE, encoding="utf-8")
    make_mp4(str(d / "renders" / "draft-v1.mp4"))
    return d


# ---------------------------------------------------------------- frames / sizes
def test_parse_frames():
    assert elements.parse_frames("0,24,48") == [0, 24, 48]
    assert elements.parse_frames("0:10:5") == [0, 5, 10]
    assert elements.parse_frames("0:9:5") == [0, 5, 9]            # the last frame of a range is always measured
    assert elements.parse_frames("3:5") == [3, 4, 5]
    assert elements.parse_frames("0:4:2,10") == [0, 2, 4, 10]
    for bad in ("", "a", "5:1", "1:5:0", "-3"):
        with pytest.raises(elements.ElementsError):
            elements.parse_frames(bad)
    assert elements.parse_size("1920x1080") == (1920, 1080)
    with pytest.raises(elements.ElementsError):
        elements.parse_size("big")


# ---------------------------------------------------------------- kurgu elements (headless Chrome)
@needs_chrome
def test_elements_moving_text_and_marked_objects(film, tmp_path):
    out = tmp_path / "e.kurgu.json"
    r = launch("elements", str(film / "film.html"), "--frames", "0:95:5", "-o", str(out), timeout=120)
    assert r.returncode == 0, r.stderr
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["fps"] == 24 and m["size"] == [320, 180] and m["measured"] == "dom"
    by = {e["id"]: e for e in m["elements"]}
    assert set(by) == {"baslik", "logo"}                           # data-kurgu-id wins as the id
    t = by["baslik"]
    assert t["kind"] == "text" and t["label"] == "Hello" and t["file"] == "engine/scene-s02.js" and t["line"] == 41
    boxes = t["boxes"]
    assert [b["f"] for b in boxes][:3] == [0, 5, 10]
    assert boxes[1]["l"] - boxes[0]["l"] == 10                     # moves 2 px per frame
    assert all(set(b) == {"f", "l", "t", "r", "b"} for b in boxes)
    assert boxes[0]["r"] > boxes[0]["l"] and boxes[0]["b"] > boxes[0]["t"]
    logo = by["logo"]
    assert logo["kind"] == "element" and logo["boxes"][0] == {"f": 0, "l": 250, "t": 140, "r": 290, "b": 160}
    lf = [b["f"] for b in logo["boxes"]]
    assert lf[0] == 0 and lf[-1] == 55 and max(b - a for a, b in zip(lf, lf[1:])) <= 12      # unchanged box repeated at most every 12 frames; last visible sample is written
    assert all(b == logo["boxes"][0] | {"f": b["f"]} for b in logo["boxes"])
    assert "ms/frame" in r.stderr


@needs_chrome
def test_elements_scales_to_video_size_and_text_change(film, tmp_path):
    res = elements.measure(str(film / "film.html"), [0, 80], size=(640, 360))
    assert res["size"] == [640, 360]
    t = {e["id"]: e for e in res["elements"]}["baslik"]
    assert t["boxes"][0]["l"] == 20 and t["boxes"][0]["t"] == 80    # 10 px and 40 px in the 320x180 page, x2
    assert res["elements"][0]["label"] == "Hello"


@needs_chrome
def test_elements_canvas_text_is_reported_as_unmeasurable(tmp_path):
    p = tmp_path / "c.html"
    p.write_text(CANVAS_PAGE, encoding="utf-8")
    res = elements.measure(str(p), [0, 10], size=(320, 180), fps=24)
    assert res["elements"] == []
    assert any("canvas" in w and "not captured" in w for w in res["warnings"])
    out = tmp_path / "c.kurgu.json"
    r = launch("elements", str(p), "--frames", "0,10", "--size", "320x180", "--fps", "24", "-o", str(out), timeout=120)
    assert r.returncode == 0 and "canvas" in r.stderr


@needs_chrome
def test_elements_rejects_page_without_frame_function(tmp_path):
    p = tmp_path / "plain.html"
    p.write_text("<p>hi</p>", encoding="utf-8")
    r = launch("elements", str(p), "--frames", "0", timeout=120)
    assert r.returncode == 1 and "renderFrame" in r.stderr


@needs_chrome
def test_elements_100_frames_timing(film, tmp_path):
    res = elements.measure(str(film / "film.html"), list(range(100)))
    assert res["seconds"] < 60
    assert len(res["elements"][0]["boxes"]) >= 90               # moves every frame: one box per frame
    print(f"elements: 100 frames in {res['seconds']} s")


# ---------------------------------------------------------------- kurgu import: cinecode
def project_layers(out, kind="video"):
    p = schema.load_project(str(out))
    return [l for l in p["layers"] if l["type"] == kind], p


def test_import_cinecode(film, tmp_path):
    out = tmp_path / "proj"
    r = launch("import", str(film), "--out", str(out), timeout=120)
    assert r.returncode == 0, r.stderr
    assert os.path.samefile(r.stdout.strip(), out)
    raw = json.loads((out / "project.json").read_text(encoding="utf-8"))
    vids = [l for l in raw["layers"] if l["type"] == "video"]
    assert [l["id"] for l in vids] == ["shot-S01", "shot-S02", "shot-S03"]
    assert [(l["start"], l["end"], l["src_in"]) for l in vids] == [(0, 1, 0), (1, 3, 1), (3, 4, 3)]     # cut at the shot frame bounds
    assert all(l["src"] == "../film/renders/draft-v1.mp4" and not l["muted"] for l in vids)      # referenced in place; no audio/mix.json: the mp4's own sound
    assert vids[1]["name"] == "S02 · Second one"
    s = vids[1]["source"]
    assert s == {"kind": "cinecode", "root": "../film", "unit": "S02", "fps": 24, "unit_start_frame": 24, "frames": 48, "media_start_frame": 0, "list": "shots.json", "file": "film.html", "render": "html-frames"}
    assert "elements" not in s
    assert (raw["width"], raw["height"], raw["fps"], raw["duration"]) == (320, 180, 24, 4)
    assert not (out / "media").exists()                          # nothing is copied by default
    assert os.path.isfile(os.path.join(out, vids[0]["src"]))
    notes = (out / "scenes.md").read_text(encoding="utf-8")
    assert "voice-over: voice for S02" in notes and "text on screen: Second one / and more" in notes
    assert raw["notes"] == []                                    # vo / text are metadata, not timeline notes
    assert not schema.validate(schema.load_project(str(out)))


def test_import_cinecode_picks_newest_render_and_video_flag(film, tmp_path):
    older = film / "renders" / "draft-v1.mp4"
    make_mp4(str(film / "renders" / "film-v2.mp4"))
    os.utime(older, (1, 1))
    out = tmp_path / "p"
    scene_import.build(str(film), out=str(out), log=lambda *_: None)
    assert json.loads((out / "project.json").read_text())["layers"][0]["src"] == "../film/renders/film-v2.mp4"
    out2 = tmp_path / "p2"
    scene_import.build(str(film), out=str(out2), video=str(older), log=lambda *_: None)
    assert json.loads((out2 / "project.json").read_text())["layers"][0]["src"] == "../film/renders/draft-v1.mp4"


def test_import_refuses_to_overwrite(film, tmp_path):
    out = tmp_path / "proj"
    assert launch("import", str(film), "--out", str(out), timeout=120).returncode == 0
    before = (out / "project.json").read_text(encoding="utf-8")
    (out / "project.json").write_text(before.replace("Tiny film", "Edited by hand"), encoding="utf-8")
    r = launch("import", str(film), "--out", str(out), timeout=120)
    assert r.returncode == 1 and "already exists" in r.stderr and "--force" in r.stderr
    assert "Edited by hand" in (out / "project.json").read_text(encoding="utf-8")
    r = launch("import", str(film), "--out", str(out), "--force", timeout=120)
    assert r.returncode == 0 and "Tiny film" in (out / "project.json").read_text(encoding="utf-8")


def test_import_audio_mix_layers(film, tmp_path):
    make_wav(str(film / "audio" / "vo" / "VO1.wav"), 2)
    make_wav(str(film / "audio" / "bed.wav"), 3)
    mix = {"duration": 4, "layers": [{"file": "audio/bed.wav", "at": 0, "gain_db": -20, "fade_in": 0.4, "bus": "ambience"},
                                     {"file": "audio/vo/VO1.wav", "at": 1.5, "trim": [0.5, 1.5], "gain_db": -2, "bus": "dialogue"},
                                     {"file": "audio/missing.wav", "at": 0}]}
    (film / "audio" / "mix.json").write_text(json.dumps(mix), encoding="utf-8")
    out = tmp_path / "p"
    proj = scene_import.build(str(film), out=str(out), log=lambda *_: None)
    auds = [l for l in proj["layers"] if l["type"] == "audio"]
    assert [(a["start"], a["end"], a["src_in"], a["volume_db"]) for a in auds] == [(0, 3, 0, -23), (1.5, 2.5, 0.5, -2)]
    assert auds[0]["fade_in"] == 0.4 and os.path.isfile(os.path.join(out, auds[1]["src"])) and auds[1]["src"] == "../film/audio/vo/VO1.wav"
    assert all(l["muted"] for l in proj["layers"] if l["type"] == "video")        # the sound now comes from the layers
    assert any("missing.wav" in w for w in proj["_warnings"])


def test_import_without_video_says_so(film, tmp_path):
    os.remove(film / "renders" / "draft-v1.mp4")
    r = launch("import", str(film), "--out", str(tmp_path / "p"), timeout=60)
    assert r.returncode == 1 and "no rendered video" in r.stderr and "--video" in r.stderr
    assert not (tmp_path / "p" / "project.json").exists()


def test_import_cuts_shots_that_run_past_the_video(film, tmp_path):
    shots = json.loads((film / "shots.json").read_text())
    shots["shots"][2]["end"] = 120                                  # video is 4 s = 96 frames
    (film / "shots.json").write_text(json.dumps(shots))
    proj = scene_import.build(str(film), out=str(tmp_path / "p"), log=lambda *_: None)
    assert proj["layers"][2]["end"] == 4 and any("cut short" in w for w in proj["_warnings"])


@needs_chrome
def test_import_cinecode_with_elements(film, tmp_path):
    out = tmp_path / "proj"
    r = launch("import", str(film), "--out", str(out), "--elements", "--elements-step", "4", timeout=240)
    assert r.returncode == 0, r.stderr
    raw = json.loads((out / "project.json").read_text(encoding="utf-8"))
    s2 = [l for l in raw["layers"] if l["id"] == "shot-S02"][0]["source"]
    assert s2["elements"] == "elements/S02.kurgu.json"
    m = json.loads((out / s2["elements"]).read_text(encoding="utf-8"))
    title = {e["id"]: e for e in m["elements"]}["baslik"]
    fs = [b["f"] for b in title["boxes"]]
    assert fs[0] == 24 and fs[-1] == 71 and all(24 <= f < 72 for f in fs)       # absolute film frames of that shot
    assert len({b["l"] for b in title["boxes"]}) == len(title["boxes"])           # the box moves in every sample
    assert m["size"] == [320, 180] and m["fps"] == 24


# ---------------------------------------------------------------- kurgu import: scenes.json and Remotion
def test_import_scenes_json(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    make_mp4(str(d / "out" / "film.mp4"), seconds=3, audio=False)
    (d / "scenes.json").write_text(json.dumps({"fps": 24, "video": "out/film.mp4", "scenes": [
        {"id": "intro", "start_frame": 0, "end_frame": 36, "file": "scenes/intro.html"},
        {"id": "outro", "start_frame": 36, "end_frame": 72}]}), encoding="utf-8")
    out = tmp_path / "proj"
    r = launch("import", str(d), "--out", str(out), timeout=60)
    assert r.returncode == 0, r.stderr
    vids = json.loads((out / "project.json").read_text())["layers"]
    assert [(l["id"], l["start"], l["end"], l["src_in"]) for l in vids] == [("shot-intro", 0, 1.5, 0), ("shot-outro", 1.5, 3, 1.5)]
    assert vids[0]["source"] == {"kind": "html", "root": "../work", "unit": "intro", "fps": 24, "unit_start_frame": 0, "frames": 36, "media_start_frame": 0, "list": "scenes.json", "file": "scenes/intro.html",
                                 "render": "html-frames"}
    assert vids[1]["source"] == {"kind": "html", "root": "../work", "unit": "outro", "fps": 24, "unit_start_frame": 36, "frames": 36, "media_start_frame": 0, "list": "scenes.json"}


def test_import_scenes_json_errors(tmp_path):
    d = tmp_path / "w"
    d.mkdir()
    (d / "scenes.json").write_text(json.dumps({"scenes": [{"id": "a", "start_frame": 0, "end_frame": 5}]}))
    r = launch("import", str(d), timeout=30)
    assert r.returncode == 1 and "fps" in r.stderr
    empty = tmp_path / "empty"
    empty.mkdir()
    r = launch("import", str(empty), timeout=30)
    assert r.returncode == 1 and "scenes.json" in r.stderr and "shots.json" in r.stderr


def test_import_remotion_is_not_supported_yet(tmp_path):
    d = tmp_path / "rem"
    d.mkdir()
    (d / "package.json").write_text(json.dumps({"dependencies": {"remotion": "4.0.0", "react": "18"}}))
    r = launch("import", str(d), timeout=30)
    assert r.returncode == 1
    assert "Remotion" in r.stderr and "not supported yet" in r.stderr and "scenes.json" in r.stderr and "start_frame" in r.stderr
    assert not (d / "kurgu").exists()
    (d / "package.json").write_text("{}")
    (d / "remotion.config.ts").write_text("export {}")
    assert launch("import", str(d), timeout=30).returncode == 1
    assert "Remotion" in launch("import", str(d), timeout=30).stderr


@needs_chrome
def test_elements_offset_gives_absolute_frames(film):
    res = elements.measure(str(film / "film.html"), [0, 6], offset=100)
    assert [b["f"] for b in res["elements"][0]["boxes"]] == [100, 106]


def test_import_copy_flag_and_default_out_is_relative(film, tmp_path):
    r = launch("import", str(film), timeout=120)                                  # default out = <film>/kurgu
    assert r.returncode == 0, r.stderr
    raw = json.loads((film / "kurgu" / "project.json").read_text())
    assert raw["layers"][0]["src"] == "../renders/draft-v1.mp4" and raw["layers"][0]["source"]["root"] == ".."
    out = tmp_path / "c"
    assert launch("import", str(film), "--out", str(out), "--copy", timeout=120).returncode == 0
    raw = json.loads((out / "project.json").read_text())
    assert raw["layers"][0]["src"] == "media/draft-v1.mp4" and (out / "media" / "draft-v1.mp4").is_file()


HOOK_PAGE = """<!doctype html><body style="margin:0"><canvas id="c" width="320" height="180"></canvas>
<script>var c=document.getElementById('c').getContext('2d');
window.READY=Promise.resolve({fps:24,width:320,height:180});
window.renderFrame=function(i){c.clearRect(0,0,320,180);c.font='30px sans-serif';c.fillText('drawn',10+i*2,90);};
window.kurguElements=function(i){return [{id:'drawn',label:'drawn',kind:'text',l:10+i*2,t:65,r:90+i*2,b:95,file:'engine/draw.js',line:12}];};</script>"""


@needs_chrome
def test_elements_kurgu_elements_hook_for_canvas_pages(tmp_path):
    p = tmp_path / "h.html"
    p.write_text(HOOK_PAGE, encoding="utf-8")
    res = elements.measure(str(p), [0, 5, 10])
    assert "warnings" not in res
    e = res["elements"][0]
    assert (e["id"], e["label"], e["kind"], e["file"], e["line"]) == ("drawn", "drawn", "text", "engine/draw.js", 12)
    assert [(b["f"], b["l"], b["r"]) for b in e["boxes"]] == [(0, 10, 90), (5, 20, 100), (10, 30, 110)]
    p2 = tmp_path / "c.html"
    p2.write_text(CANVAS_PAGE, encoding="utf-8")
    assert "kurguElements" in elements.measure(str(p2), [0], size=(320, 180), fps=24)["warnings"][0]       # the warning names the hook


# ---------------------------------------------------------------- a hostile scene page must not read local files or reach the network (security review F1, F2)
HOSTILE = """<!doctype html><body style="margin:0"><div id="r" data-kurgu-id="res" style="position:absolute;left:0;top:0;font:20px sans-serif">init</div><script>
var out = {};
function t(k, p) { return p.then(function (x) { out[k] = 'LEAK'; }, function () { out[k] = 'ok'; }); }
var jobs = [
  t('f', fetch('file://%(secret)s').then(function (r) { return r.text(); })),
  t('o', fetch('http://127.0.0.1:%(other)d/leak').then(function (r) { return r.text(); })),
  t('x', fetch('http://example.com/').then(function (r) { return r.text(); })),
  t('u', fetch(location.origin + '/%%2e%%2e/%(secretname)s').then(function (r) { if (!r.ok) throw 0; return r.text(); }))];
new Image().src = 'http://127.0.0.1:%(other)d/img';
out.rtc = typeof RTCPeerConnection;
try { var pc = new RTCPeerConnection({iceServers: [{urls: 'stun:127.0.0.1:%(udp)d'}]}); pc.createDataChannel('x'); pc.createOffer().then(function (o) { return pc.setLocalDescription(o); }); out.rtc = 'LEAK'; } catch (e) {}
window.READY = Promise.all(jobs).then(function () {
  return new Promise(function (res) { setTimeout(function () {
    document.getElementById('r').textContent = 'f:' + out.f + ' o:' + out.o + ' x:' + out.x + ' u:' + out.u + ' r:' + out.rtc; res({frames: 2, fps: 24, width: 320, height: 180}); }, 1500); });
});
window.renderFrame = function (i) {};
</script></body></html>"""


@needs_chrome
def test_hostile_page_cannot_read_files_or_reach_anything(tmp_path):
    import http.server
    import socket
    import threading
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"secret")

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.2)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    film = tmp_path / "film"
    film.mkdir()
    page = film / "p.html"
    page.write_text(HOSTILE % {"secret": str(secret), "secretname": "secret.txt", "other": srv.server_port, "udp": udp.getsockname()[1]})
    try:
        res = elements.measure(str(page), [0, 1], size=(320, 180), fps=24)
    finally:
        srv.shutdown()
    got = []
    try:
        while True:
            got.append(udp.recvfrom(2048))
    except OSError:
        pass
    label = next(e["label"] for e in res["elements"] if e["id"] == "res")
    assert "f:ok" in label and "o:ok" in label and "x:ok" in label and "u:ok" in label, label     # every attempt failed
    assert "r:undefined" in label                                                                  # WebRTC is gone
    assert hits == [] and got == []                                                                # nothing reached the local listener or the UDP socket


def test_a_page_outside_the_served_folder_is_refused(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "p.html").write_text("<html></html>")
    with pytest.raises(elements.ElementsError, match="outside"):
        elements.measure(str(tmp_path / "b" / "p.html"), [0], root=str(tmp_path / "a"))


@pytest.mark.skipif(os.name == "nt", reason="the fake Chrome is a #!/bin/sh script")
def test_a_chrome_that_dies_is_explained_with_its_own_last_words(tmp_path, monkeypatch):
    import stat
    import elements
    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\necho 'Failed to connect to the keychain' >&2\nexit 7\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("KURGU_CHROME", str(fake))
    with pytest.raises(elements.ElementsError) as ei:
        elements.Browser()
    m = str(ei.value)
    assert "failed to start or crashed" in m and "exit code 7" in m and "Failed to connect to the keychain" in m and "KURGU_CHROME" in m
    assert "closed the DevTools socket" not in m


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS writes the crash reports")
@needs_chrome
def test_closing_the_product_chrome_leaves_no_crash_report(tmp_path):
    """Chrome that is killed or loses its profile while running aborts ("Google Chrome quit unexpectedly"): the product asks it to quit first."""
    import glob
    import time as _t
    reports = os.path.expanduser("~/Library/Logs/DiagnosticReports")
    before = set(glob.glob(os.path.join(reports, "Google Chrome*.ips")))
    for _ in range(3):
        b = elements.Browser(320, 180, root=str(tmp_path))
        proc, profile = b.proc, b.profile
        b.close()
        assert proc.poll() is not None and not os.path.exists(profile)
    _t.sleep(2)                                              # the system writes a report a moment after the crash
    assert set(glob.glob(os.path.join(reports, "Google Chrome*.ips"))) - before == set()


def test_a_units_element_map_holds_only_that_units_frames_and_dom_text_gets_file_and_line(tmp_path):
    items = {"elements": [{"id": "a", "label": "x", "kind": "text", "boxes": [{"f": 0, "l": 1, "t": 1, "r": 2, "b": 2}, {"f": 216, "l": 1, "t": 1, "r": 2, "b": 2}]},
                          {"id": "b", "label": "y", "kind": "text", "boxes": [{"f": 504, "l": 1, "t": 1, "r": 2, "b": 2}]}], "sampled": [0, 504, 3]}
    clipped = elements.clip_to_frames(items, 0, 144)
    assert [e["id"] for e in clipped["elements"]] == ["a"] and [b["f"] for b in clipped["elements"][0]["boxes"]] == [0] and clipped["sampled"] == [0, 0, 3]
    page = tmp_path / "film.html"
    page.write_text('<div id="sub"></div><div id="cap"></div>\n<script>\nconst s = document.getElementById("sub");\nsub.textContent = "Ankara\'dan Nallihan\'a";\n</script>')
    els = [{"id": "sub", "label": "Ankara'dan Nallihan'a", "kind": "text", "boxes": []}, {"id": "cap", "label": "no literal here", "kind": "text", "boxes": []},
           {"id": "cv-123", "label": "canvas text", "kind": "text", "boxes": []}]
    elements._attach_dom_sources(str(page), els)
    assert els[0]["file"] == "film.html" and els[0]["line"] == 4                       # the literal text
    assert els[1]["file"] == "film.html" and els[1]["line"] == 1                       # no literal: the element's id in the markup
    assert "file" not in els[2]


@needs_chrome
def test_import_elements_writes_only_the_units_frames_and_dom_text_has_a_source_line(film, tmp_path):
    out = tmp_path / "proj"
    r = launch("import", str(film), "--out", str(out), "--elements", "--elements-step", "4", timeout=240)
    assert r.returncode == 0, r.stderr
    for unit, (lo, hi) in {"S01": (0, 24), "S02": (24, 72), "S03": (72, 96)}.items():
        m = json.loads((out / "elements" / f"{unit}.kurgu.json").read_text(encoding="utf-8"))
        fs = [b["f"] for e in m["elements"] for b in e["boxes"]]
        assert fs and min(fs) >= lo and max(fs) < hi, (unit, min(fs), max(fs))
