"""Media hot-reload + versions + before/after, in headless Chrome and in-process.

A clip is re-rendered to the SAME path (temp file + rename, the way `kurgu rerender` writes): the editor must show the new frame without a page
reload, keep the playhead, and `B` must flip to the previous version. Own server (free port) and own Chrome; skipped without Chrome / ffmpeg."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import media_tools  # noqa: E402

try:
    import websocket  # noqa: F401
    HAVE_WS = True
except Exception:  # pragma: no cover
    HAVE_WS = False
HAVE_FF = bool(shutil.which("ffmpeg") or os.environ.get("KURGU_FFMPEG"))
needs_chrome = pytest.mark.skipif(not (cdp.find_chrome() and HAVE_WS and HAVE_FF), reason="Chrome / websocket-client / ffmpeg not available")
READY = "window.__kurgu && __kurgu.S.project && document.querySelector('#canvas')"
PIX = "(()=>{const c=document.getElementById('canvas');const d=c.getContext('2d').getImageData(c.width>>1,c.height>>1,1,1).data;return [d[0],d[1],d[2]]})()"


def make_clip(path, color, secs=20):
    """Atomic like a re-render: write beside, then rename over the target."""
    tmp = path + ".tmp.mp4"
    subprocess.run([media_tools.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=10:d={secs}", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-g", "5", tmp], check=True)
    os.replace(tmp, path)


def make_png(path, color):
    tmp = path + ".tmp.png"
    subprocess.run([media_tools.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=100x100", "-frames:v", "1", tmp], check=True)
    os.replace(tmp, path)


def seed(p):
    p["duration"] = 20
    p["notes"] = []
    p["layers"] = [{"type": "image", "id": "i1", "name": "pic", "group": "Picture", "start": 0, "end": 20, "x": 200, "y": 200, "scale": 1, "src": "pic.png",
                    "src_in": 0, "fade_in": 0, "fade_out": 0, "fit": "native", "effects": [], "hidden": False, "locked": False, "opacity": 1,
                    "crop": {"top": 0, "bottom": 0, "left": 0, "right": 0}, "volume_db": 0, "envelope": []},
                   {"type": "video", "id": "v1", "name": "clip", "group": "Picture", "start": 0, "end": 20, "x": 960, "y": 540, "scale": 1, "src": "clip.mp4",
                    "src_in": 0, "muted": True, "fade_in": 0, "fade_out": 0, "fit": "cover", "effects": [], "hidden": False, "locked": False, "opacity": 1,
                    "crop": {"top": 0, "bottom": 0, "left": 0, "right": 0}, "volume_db": 0, "envelope": []}]


def dominant(rgb):
    return "red" if rgb[0] > 150 and rgb[2] < 100 else "blue" if rgb[2] > 150 and rgb[0] < 100 else "green" if rgb[1] > 100 and rgb[0] < 100 and rgb[2] < 100 else str(rgb)


@pytest.fixture(scope="module")
def chrome():
    c = cdp.Chrome(width=1440, height=900)
    yield c
    c.close()


@pytest.fixture(scope="module")
def server():
    s = cdp.Server(prepare=seed)
    make_clip(os.path.join(s.dir, "clip.mp4"), "red")
    make_png(os.path.join(s.dir, "pic.png"), "green")
    yield s
    s.close()


@pytest.fixture(scope="module")
def page(chrome, server):
    p = chrome.new_page()
    p.open(server.url("/"), lang="en", mode="edit", ready=READY)
    yield p
    p.close()


def colour(page):
    return dominant(page.eval(PIX))


def pix_at(x, y):
    return ("(()=>{const c=document.getElementById('canvas');const k=c.width/__kurgu.S.project.width;"
            f"const d=c.getContext('2d').getImageData(Math.round({x}*k),Math.round({y}*k),1,1).data;return [d[0],d[1],d[2]]}})()")


def get(server, path):
    return json.loads(urllib.request.urlopen(server.url(path), timeout=10).read())


REC = """window.__rec=[];(function f(){const c=document.getElementById('canvas');const d=c.getContext('2d').getImageData(c.width>>1,c.height>>1,1,1).data;
window.__rec.push(d[0]+d[1]+d[2]);if(!window.__stopRec)requestAnimationFrame(f)})()"""


@needs_chrome
def test_1_rerender_to_same_path_shows_new_frame_without_reload(page, server):
    page.eval("__kurgu.S.t=1.0; __kurgu.emit('time')")
    page.wait_for("(" + PIX + ")[0]>150", 15)
    assert colour(page) == "red"
    origin = page.eval("performance.timeOrigin")
    page.eval("window.__stopRec=false;" + REC)
    make_clip(os.path.join(server.dir, "clip.mp4"), "blue")
    page.wait_for("(" + PIX + ")[2]>150", 15)                       # new frame, found by the 1 s poll
    assert colour(page) == "blue"
    time.sleep(0.6)
    page.eval("window.__stopRec=true")
    assert page.eval("performance.timeOrigin") == origin             # no page reload
    assert abs(page.eval("__kurgu.S.t") - 1.0) < 0.01                # playhead kept
    rec = page.eval("window.__rec")
    assert len(rec) > 10 and min(rec) > 60, "a black frame was drawn during the swap"     # red / blue are bright, black is 0


@needs_chrome
def test_2_image_reloads_too(page, server):
    page.wait_for("(" + pix_at(200, 200) + ")[1]>100", 10)           # green
    make_png(os.path.join(server.dir, "pic.png"), "red")
    page.wait_for("(" + pix_at(200, 200) + ")[0]>150", 15)


@needs_chrome
def test_3_versions_api_keeps_old_bytes(page, server):
    v = get(server, "/api/versions?path=clip.mp4")["versions"]
    assert len(v) == 2 and v[0]["current"] and not v[1]["current"]
    old = urllib.request.urlopen(server.url(v[1]["url"])).read()
    new = urllib.request.urlopen(server.url(v[0]["url"])).read()
    assert old != new and len(old) > 1000
    files = os.listdir(os.path.join(server.dir, ".kurgu", "versions"))
    assert sum(f.startswith("clip~") for f in files) == 2
    info = get(server, "/api/media-info?path=clip.mp4")
    assert info["mtime_ns"] == os.stat(os.path.join(server.dir, "clip.mp4")).st_mtime_ns
    assert get(server, "/api/version")["media"]["clip.mp4"]["m"] == info["mtime_ns"]


@needs_chrome
def test_4_b_key_flips_to_the_previous_version(page, server):
    page.wait_for("__kurgu.abState().available", 10)
    assert page.eval("!document.getElementById('b-ab').hidden")
    page.key("b", code="KeyB", text="b")
    page.wait_for("(" + PIX + ")[0]>150", 10)                       # the old (red) frame at the same time
    assert page.eval("__kurgu.abState().on") and abs(page.eval("__kurgu.S.t") - 1.0) < 0.01
    badge = page.eval("document.getElementById('ab-badge').textContent")
    assert not page.eval("document.getElementById('ab-badge').hidden") and "before" in badge and "after" in badge
    assert page.eval("document.querySelector('#ab-badge .on').textContent") == "before"
    page.click_sel("#b-ab")                                          # the button flips back
    page.wait_for("(" + PIX + ")[2]>150", 10)
    assert page.eval("document.querySelector('#ab-badge .on').textContent") == "after"
    page.key("b", code="KeyB", text="b")                              # B again, then B off
    page.wait_for("(" + PIX + ")[0]>150", 10)
    page.key("b", code="KeyB", text="b")
    page.wait_for("(" + PIX + ")[2]>150", 10)


@needs_chrome
def test_5_closed_note_button_jumps_and_shows_the_picture_from_when_it_was_sent(page, server):
    from datetime import datetime
    time.sleep(1.2)
    sent = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")             # after blue, before green
    time.sleep(1.2)
    make_clip(os.path.join(server.dir, "clip.mp4"), "green")
    page.wait_for("(" + PIX + ")[1]>100", 15)
    page.eval("__kurgu.S.t=5; __kurgu.emit('time')")
    note = {"id": "n_x", "t": 1.0, "text": "make it blue", "created": sent, "done": True, "author": "user", "resolved_at": sent, "layer_ids": ["v1"], "reply": "", "sent": sent}
    page.eval("__kurgu.change(p=>{p.notes.push(%s)})" % json.dumps(note))
    page.click_sel("#b-notes") if not page.eval("document.getElementById('notes-panel')") else None
    page.wait_for("document.querySelector('.np-row .ab-note')", 10)
    assert page.eval("document.querySelector('.np-row .ab-note').textContent") == "before / after"
    page.click_sel(".np-row .ab-note")
    page.wait_for("(" + PIX + ")[2]>150", 10)                       # blue: what the note saw (not red, not the current green)
    assert abs(page.eval("__kurgu.S.t") - 1.0) < 0.01 and page.eval("__kurgu.abState().on")
    page.eval("__kurgu.toggleAB()")
    page.wait_for("(" + PIX + ")[1]>100", 10)


@needs_chrome
def test_6_playing_keeps_playing_through_a_rerender(page, server):
    page.eval("__kurgu.toggleAB && __kurgu.abState().on && __kurgu.toggleAB()")
    page.eval("__kurgu.S.t=1.0; __kurgu.emit('time')")
    page.click_sel("#b-play")
    page.wait_for("__kurgu.S.playing", 5)
    t0 = page.eval("__kurgu.S.t")
    make_clip(os.path.join(server.dir, "clip.mp4"), "red")
    page.wait_for("(" + PIX + ")[0]>150", 15)
    assert page.eval("__kurgu.S.playing") and page.eval("__kurgu.S.t") > t0
    page.click_sel("#b-play")
    page.wait_for("!__kurgu.S.playing", 5)
    assert len([f for f in os.listdir(os.path.join(server.dir, ".kurgu", "versions")) if f.startswith("clip~")]) == 3    # pruned to the last 3


# ---------------------------------------------------------------- versions.py (no browser)
def _write(path, data, mtime):
    tmp = path + ".new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.utime(tmp, ns=(mtime, mtime))
    os.replace(tmp, path)                                            # atomic, like a re-render


def test_snapshot_hardlinks_and_keeps_old_bytes_across_atomic_replace(tmp_path):
    import versions
    proj = str(tmp_path)
    clip = os.path.join(proj, "clip.mp4")
    _write(clip, b"first", 1_000_000_000_000)
    v1 = versions.snapshot(proj, clip)
    assert os.stat(v1).st_nlink == 2 and os.path.samefile(v1, clip)    # a hardlink, no copy
    assert os.path.basename(v1).startswith("clip~") and "@1000000000000.mp4" in v1
    assert versions.snapshot(proj, clip) == v1                         # idempotent
    _write(clip, b"second", 2_000_000_000_000)
    v2 = versions.snapshot(proj, clip)
    assert open(v1, "rb").read() == b"first" and open(v2, "rb").read() == b"second"
    lst = versions.list_versions(proj, clip)
    assert [v["current"] for v in lst] == [True, False] and lst[0]["mtime_ns"] == 2_000_000_000_000


def test_versions_are_pruned_to_the_last_three(tmp_path):
    import versions
    proj = str(tmp_path)
    clip = os.path.join(proj, "a.mov")
    other = os.path.join(proj, "sub", "a.mov")                         # same file name in another folder: its own series
    os.makedirs(os.path.dirname(other))
    _write(other, b"o", 1_000_000_000_000)
    versions.snapshot(proj, other)
    for i in range(1, 6):
        _write(clip, b"v%d" % i, i * 1_000_000_000_000)
        versions.snapshot(proj, clip)
    lst = versions.list_versions(proj, clip)
    assert len(lst) == versions.KEEP == 3
    assert [open(v["file"], "rb").read() for v in lst] == [b"v5", b"v4", b"v3"]
    assert len(versions.list_versions(proj, other)) == 1


def test_in_place_overwrite_loses_the_old_version(tmp_path):
    import versions
    proj = str(tmp_path)
    clip = os.path.join(proj, "x.png")
    _write(clip, b"old", 1_000_000_000_000)
    versions.snapshot(proj, clip)
    with open(clip, "r+b") as f:                                       # same inode rewritten: the hardlink would show the NEW bytes
        f.write(b"new")
    os.utime(clip, ns=(2_000_000_000_000, 2_000_000_000_000))
    versions.snapshot(proj, clip)
    lst = versions.list_versions(proj, clip)
    assert len(lst) == 1 and lst[0]["current"] and open(lst[0]["file"], "rb").read() == b"new"


def test_copy_fallback_and_big_file_skip(tmp_path, monkeypatch):
    import versions
    proj = str(tmp_path)
    clip = os.path.join(proj, "c.mp3")
    _write(clip, b"audio", 1_000_000_000_000)

    def nolink(a, b):
        raise OSError("cross-device")
    monkeypatch.setattr(os, "link", nolink)
    v = versions.snapshot(proj, clip, sync=True)
    assert os.stat(v).st_nlink == 1 and open(v, "rb").read() == b"audio"     # copied (sync: the caller needs it before it replaces the file)
    _write(clip, b"audio2", 2_000_000_000_000)
    monkeypatch.setattr(versions, "MAX_COPY_BYTES", 3)
    assert versions.snapshot(proj, clip) is None                             # too big to copy: skipped, not an error
    assert len(versions.list_versions(proj, clip)) == 1


# ---------------------------------------------------------------- /api/version cost
def test_version_poll_stats_each_media_file_once(tmp_path, monkeypatch):
    import server
    proj = tmp_path / "p"
    proj.mkdir()
    for n in ("a.mp4", "b.png", "c.mp3"):
        (proj / n).write_bytes(b"x" * 10)
    layers = [{"type": "video", "id": "v", "src": "a.mp4"}, {"type": "video", "id": "v2", "src": "a.mp4"},     # the same file twice
              {"type": "image", "id": "i", "src": "b.png"}, {"type": "audio", "id": "a", "src": "c.mp3"}]
    (proj / "project.json").write_text(json.dumps({"version": 2, "name": "t", "width": 1920, "height": 1080, "fps": 30, "duration": 5, "layers": layers, "notes": []}))
    app = server.App(str(proj))
    real = {os.path.realpath(str(proj / n)) for n in ("a.mp4", "b.png", "c.mp3")}
    calls = []
    orig = os.stat

    def counting(p, *a, **k):
        if isinstance(p, (str, bytes, os.PathLike)) and os.path.realpath(os.fsdecode(p)) in real:
            calls.append(os.fsdecode(p))
        return orig(p, *a, **k)
    monkeypatch.setattr(os, "stat", counting)
    for poll in range(3):                                                    # first sight (snapshots) and two quiet polls
        calls.clear()
        stamps = app.media_stamps()
        assert set(stamps) == {"a.mp4", "b.png", "c.mp3"}
        assert len(calls) == 3, f"poll {poll}: {len(calls)} stats for 3 files: {calls}"
    monkeypatch.setattr(os, "stat", orig)
    assert len(os.listdir(proj / ".kurgu" / "versions")) == 3


def test_copy_never_runs_in_the_callers_thread(tmp_path, monkeypatch):
    """The editor's /api/version handler calls snapshot(): when hardlinks fail it must return at once and copy in the background."""
    import threading
    import versions
    proj = str(tmp_path)
    clip = os.path.join(proj, "c.mp3")
    _write(clip, b"audio", 1_000_000_000_000)
    monkeypatch.setattr(os, "link", lambda a, b: (_ for _ in ()).throw(OSError("cross-device")))
    copies = []
    real_copy = versions.shutil.copy2
    monkeypatch.setattr(versions.shutil, "copy2", lambda a, b, **k: (copies.append(threading.current_thread()), real_copy(a, b, **k))[1])
    assert versions.snapshot(proj, clip) is None                        # returned without a version: the copy runs elsewhere
    for _ in range(100):
        if versions.list_versions(proj, clip):
            break
        time.sleep(0.05)
    lst = versions.list_versions(proj, clip)
    assert len(lst) == 1 and open(lst[0]["file"], "rb").read() == b"audio"
    assert copies and all(t is not threading.current_thread() for t in copies)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_big_copy_is_capped_at_200_mb_and_symlinked_media_is_skipped(tmp_path, monkeypatch):
    import versions
    assert versions.MAX_COPY_BYTES == 200 << 20
    proj = str(tmp_path / "p")
    os.makedirs(proj)
    real = str(tmp_path / "outside.mp3")
    _write(real, b"secret", 1_000_000_000_000)
    link = os.path.join(proj, "l.mp3")
    os.symlink(real, link)
    assert versions.snapshot(proj, link) is None and versions.list_versions(proj, real) == []
